"""
render.py — Video processing, 9:16 vertical conversion, and dynamic caption burning via ffmpeg & libass.

Design decisions:
- Captions are burned in using Advanced SubStation Alpha (ASS) subtitles with libass.
  This provides bulletproof cross-platform rendering (no fontconfig crashes on Windows),
  dynamic word-by-word highlight effects (karaoke-style), and crisp outlines.
- ffmpeg is invoked via subprocess for reliability and zero extra wrapper dependencies.
- The 9:16 transformation scales and crops the original video centered.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional

from score import ScoredClip
from transcribe import WordEntry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Target dimensions for 9:16 vertical video (1080×1920 is standard)
TARGET_W = 1080
TARGET_H = 1920

# Caption config
WORDS_PER_GROUP = 4          # words shown per caption frame
FONT_NAME = "Arial"
FONT_SIZE = 64               # ASS font size for 1080x1920 canvas
MARGIN_V = 280               # bottom margin (lower third placement)


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

def check_ffmpeg() -> str:
    """
    Return the path to ffmpeg if it is available, otherwise raise RuntimeError.
    """
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError(
            "ffmpeg is not installed or not on PATH.\n"
            "Install it from https://ffmpeg.org/download.html and ensure it is on PATH.\n"
            "On Windows you can install via:  winget install Gyan.FFmpeg"
        )
    return path


def _run_ffmpeg(args: List[str], verbose: bool = False) -> None:
    """
    Run ffmpeg with *args*.  Raises :class:`RuntimeError` on failure.
    """
    cmd = ["ffmpeg", "-y"] + args
    if verbose:
        print(f"[render] ffmpeg {' '.join(args[:8])} ...")

    result = subprocess.run(
        cmd,
        capture_output=not verbose,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}).\n"
            f"Command: {' '.join(cmd[:10])} ...\n"
            f"stderr: {stderr[-1000:]}"
        )


# ---------------------------------------------------------------------------
# ASS Subtitle Generation (Dynamic Word Highlight)
# ---------------------------------------------------------------------------

def _format_ass_time(seconds: float) -> str:
    """
    Format *seconds* into ASS timestamp format: H:MM:SS.cs (centiseconds).
    """
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        secs += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _build_word_groups(
    words: List[WordEntry],
    clip_start: float,
    words_per_group: int = WORDS_PER_GROUP,
) -> List[Tuple[float, float, str, List[WordEntry]]]:
    """
    Group *words* into caption chunks of *words_per_group* words.

    Returns a list of (group_start, group_end, group_text, word_entries) tuples,
    where all times are relative to *clip_start*.
    """
    if not words:
        return []

    groups = []
    for i in range(0, len(words), words_per_group):
        chunk = words[i : i + words_per_group]
        group_start = max(0.0, chunk[0]["start"] - clip_start)
        group_end = max(group_start + 0.05, chunk[-1]["end"] - clip_start)
        group_text = " ".join(w["word"] for w in chunk)
        groups.append((group_start, group_end, group_text, chunk))

    return groups


def generate_ass_content(
    words: List[WordEntry],
    clip_start: float,
    font_name: str = FONT_NAME,
    font_size: int = FONT_SIZE,
    margin_v: int = MARGIN_V,
) -> str:
    """
    Generate an Advanced SubStation Alpha (.ass) subtitle file content with
    word-level dynamic highlight effects (active word highlighted in yellow).
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {TARGET_W}
PlayResY: {TARGET_H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    groups = _build_word_groups(words, clip_start=clip_start)

    for _, _, _, chunk in groups:
        for active_idx, active_word in enumerate(chunk):
            word_start = max(0.0, active_word["start"] - clip_start)
            word_end = max(word_start + 0.05, active_word["end"] - clip_start)

            # Build line with current word highlighted in yellow (&H0000FFFF&) and others in white (&H00FFFFFF&)
            line_parts = []
            for j, w in enumerate(chunk):
                # Clean up word text for ASS (strip braces / backslashes)
                w_text = w["word"].replace("{", "").replace("}", "").replace("\\", "")
                if not w_text:
                    continue
                if j == active_idx:
                    line_parts.append(f"{{\\c&H0000FFFF&}}{w_text}{{\\c&H00FFFFFF&}}")
                else:
                    line_parts.append(w_text)

            dialogue_text = " ".join(line_parts)
            start_str = _format_ass_time(word_start)
            end_str = _format_ass_time(word_end)
            events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{dialogue_text}")

    return header + "\n".join(events) + "\n"


# ---------------------------------------------------------------------------
# Main rendering function
# ---------------------------------------------------------------------------

def render_clip(
    source_video: Path,
    clip: ScoredClip,
    output_path: Path,
    verbose: bool = False,
) -> None:
    """
    Cut, reframe to 9:16, add word-highlighted captions, and encode *clip* from *source_video*.

    Parameters
    ----------
    source_video:
        Original (full) video file path.
    clip:
        The selected clip with timing and word data.
    output_path:
        Where to write the final MP4.
    verbose:
        Pass ffmpeg output through to stdout.

    Raises
    ------
    RuntimeError
        On ffmpeg failure.
    """
    start = clip.start
    duration = clip.duration
    words = clip.words

    # Create temporary .ass subtitle file
    ass_path = output_path.with_suffix(".temp.ass")
    has_captions = bool(words)

    if has_captions:
        ass_content = generate_ass_content(words, clip_start=start)
        ass_path.write_text(ass_content, encoding="utf-8")

    # Build filtergraph: scale + crop to 9:16 vertical
    vf_parts = [
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase",
        f"crop={TARGET_W}:{TARGET_H}",
    ]

    # Add ASS subtitles filter if captions are present
    if has_captions:
        # ffmpeg requires escaping backslashes and colons on Windows
        escaped_ass = ass_path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
        vf_parts.append(f"ass=filename='{escaped_ass}'")

    vf = ",".join(vf_parts)

    args = [
        "-ss", str(start),
        "-i", str(source_video),
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-map", "0:v:0",
        "-map", "0:a:0",
        str(output_path),
    ]

    try:
        _run_ffmpeg(args, verbose=verbose)
    finally:
        # Clean up temporary ASS file
        if ass_path.exists():
            try:
                ass_path.unlink()
            except OSError:
                pass


def render_all_clips(
    source_video: Path,
    clips: List[ScoredClip],
    output_dir: Path,
    verbose: bool = False,
) -> List[Tuple[Path, Path]]:
    """
    Render all *clips* and write output files to *output_dir*.

    Returns a list of (mp4_path, txt_path) tuples for each rendered clip.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    results: List[Tuple[Path, Path]] = []

    for i, clip in enumerate(clips, 1):
        label = f"clip_{i:02d}"
        mp4_path = output_dir / f"{label}.mp4"
        txt_path = output_dir / f"{label}.txt"

        print(f"Rendering clip {i} of {len(clips)}: [{clip.start:.1f}s - {clip.end:.1f}s] ...")

        try:
            render_clip(
                source_video=source_video,
                clip=clip,
                output_path=mp4_path,
                verbose=verbose,
            )
        except RuntimeError as e:
            print(f"  [!] Render failed for clip {i}: {e}", file=sys.stderr)
            continue

        # Write suggested title + caption
        try:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"Title: {clip.title}\n\n")
                f.write(f"Caption: {clip.caption}\n\n")
                f.write(f"Timestamp: {clip.start:.1f}s - {clip.end:.1f}s\n")
                f.write(f"Score: {clip.score:.1f}/10\n")
                if clip.reasoning:
                    f.write(f"Reasoning: {clip.reasoning}\n")
        except OSError as e:
            print(f"  [!] Could not write {txt_path}: {e}", file=sys.stderr)

        results.append((mp4_path, txt_path))
        print(f"  + Saved: {mp4_path.name}")

    return results


if __name__ == "__main__":
    import sys
    from pathlib import Path as P

    if len(sys.argv) < 2:
        print("Usage: python render.py <video_path> [start_sec] [end_sec]")
        sys.exit(1)

    check_ffmpeg()

    vpath = P(sys.argv[1])
    start = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    end   = float(sys.argv[3]) if len(sys.argv) > 3 else start + 10.0

    test_clip = ScoredClip(
        index=0, start=start, end=end, words=[],
        score=9.0, title="Test Clip", caption="Generated by render.py smoke test",
    )

    out_dir = P("output")
    render_all_clips(vpath, [test_clip], out_dir, verbose=True)
    print("Done.")
