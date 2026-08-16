"""
render.py — Video processing, 9:16 conversion, and caption burning via ffmpeg.

Design decisions:
- ffmpeg is invoked via subprocess for reliability and no extra dependencies.
- Captions are burned in as word groups using ffmpeg drawtext filters.
- Each clip is processed independently so failures don't affect other clips.
- The 9:16 transformation uses centered crop + blur pad to preserve content.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
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
FONT_SIZE = 72               # drawtext font size
CAPTION_Y_FRACTION = 0.75    # vertical position as fraction of height
FONT_COLOR = "white"
HIGHLIGHT_COLOR = "yellow"   # color for the currently-spoken word
BOX_COLOR = "black@0.45"     # semi-transparent background box


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
            "On Windows you can also install via:  winget install Gyan.FFmpeg"
        )
    return path


def _run_ffmpeg(args: List[str], verbose: bool = False) -> None:
    """
    Run ffmpeg with *args*.  Raises :class:`RuntimeError` on failure.
    """
    cmd = ["ffmpeg", "-y"] + args
    if verbose:
        print(f"[render] ffmpeg {' '.join(args[:6])} ...")

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
# Caption helpers
# ---------------------------------------------------------------------------

def _escape_drawtext(text: str) -> str:
    """
    Escape a string for safe use inside an ffmpeg drawtext filter value.

    ffmpeg drawtext uses its own escaping rules:
      - Backslash must be doubled: \\ → \\\\
      - Single-quote must be escaped: ' → \\'
      - Colon must be escaped: : → \\:
    We also strip characters that commonly break filter chains.
    """
    # Replace backslash first (before adding new backslashes)
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\u2019")   # Replace apostrophe with right single quote (unicode, safe)
    text = text.replace(":", "\\:")
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace(",", "\\,")
    text = text.replace(";", "\\;")
    text = text.replace("=", "\\=")
    # Remove control characters
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text


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
        group_start = chunk[0]["start"] - clip_start
        group_end = chunk[-1]["end"] - clip_start
        group_text = " ".join(w["word"] for w in chunk)
        groups.append((max(0.0, group_start), max(0.0, group_end), group_text, chunk))

    return groups


def _drawtext_filters_for_clip(
    words: List[WordEntry],
    clip_start: float,
    video_w: int = TARGET_W,
    video_h: int = TARGET_H,
) -> List[str]:
    """
    Build a list of ffmpeg drawtext filter strings for the given clip words.

    Each word group gets a background box + text filter.
    The currently-speaking word gets an additional highlighted overlay.
    """
    filters: List[str] = []
    groups = _build_word_groups(words, clip_start)
    y_pos = int(video_h * CAPTION_Y_FRACTION)

    for group_start, group_end, group_text, chunk in groups:
        escaped_text = _escape_drawtext(group_text)
        if not escaped_text.strip():
            continue

        # Enable condition: show only during this group's time window
        enable = f"between(t\\,{group_start:.3f}\\,{group_end:.3f})"

        # Background box (drawn behind text via box=1)
        filters.append(
            f"drawtext=text='{escaped_text}'"
            f":fontsize={FONT_SIZE}"
            f":fontcolor={FONT_COLOR}"
            f":box=1:boxcolor={BOX_COLOR}:boxborderw=12"
            f":x=(w-text_w)/2:y={y_pos}"
            f":enable='{enable}'"
        )

        # Per-word highlight: draw individual words in yellow at the exact x offset.
        # This is approximate because ffmpeg drawtext has limited text-metric access.
        # We use a second drawtext overlay with the word text on a transparent bg.
        for word_idx, w in enumerate(chunk):
            word_text = _escape_drawtext(w["word"])
            if not word_text.strip():
                continue
            word_start = max(0.0, w["start"] - clip_start)
            word_end = max(word_start + 0.05, w["end"] - clip_start)
            word_enable = f"between(t\\,{word_start:.3f}\\,{word_end:.3f})"

            # Approximate x offset: prefix chars * half font size from center
            # This is intentionally approximate — ffmpeg has no text-metrics API
            prefix = " ".join(_escape_drawtext(chunk[i]["word"]) for i in range(word_idx))
            if prefix:
                x_expr = f"(w-text_w)/2+(({len(prefix)})*{FONT_SIZE//2})"
            else:
                x_expr = "(w-text_w)/2"

            filters.append(
                f"drawtext=text='{word_text}'"
                f":fontsize={FONT_SIZE}"
                f":fontcolor={HIGHLIGHT_COLOR}"
                f":x={x_expr}:y={y_pos}"
                f":enable='{word_enable}'"
            )

    return filters


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
    Cut, reframe to 9:16, add captions, and encode *clip* from *source_video*.

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

    # 1. Build caption filter chain
    caption_filters = _drawtext_filters_for_clip(words, clip_start=start)

    # 2. Build the full ffmpeg filtergraph
    #
    # The pipeline is:
    #   [0:v] → scale to cover 1080×1920 → crop center → blur-pad → captions → [vout]
    #   [0:a] → copy → [aout]
    #
    # We use the "scale2ref" approach:
    #   - Scale so the shorter dimension fits 1080 (for landscape source),
    #     or the longer dimension fits 1920 (for portrait source).
    #   Actually, we use a simpler + safer approach:
    #   - Scale to fill: scale=w=max(W, H*9/16):h=max(H, W*16/9) then crop.
    #
    # Simpler robust approach for a hackathon:
    #   scale to height=1920, then crop width=1080 from center.
    #   If the source is already taller than wide, scale to width=1080 first.

    vf_parts = [
        # Scale so that height is at least 1920 (preserving aspect)
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase",
        # Crop the center 1080×1920
        f"crop={TARGET_W}:{TARGET_H}",
    ]

    # Add caption drawtext filters
    vf_parts.extend(caption_filters)

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

    _run_ffmpeg(args, verbose=verbose)


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

        print(f"Rendering clip {i} of {len(clips)}: [{clip.start:.1f}s – {clip.end:.1f}s] ...")

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
                f.write(f"Timestamp: {clip.start:.1f}s – {clip.end:.1f}s\n")
                f.write(f"Score: {clip.score:.1f}/10\n")
                if clip.reasoning:
                    f.write(f"Reasoning: {clip.reasoning}\n")
        except OSError as e:
            print(f"  [!] Could not write {txt_path}: {e}", file=sys.stderr)

        results.append((mp4_path, txt_path))
        print(f"  ✓ Saved: {mp4_path.name}")

    return results


if __name__ == "__main__":
    """Quick smoke-test: render a 5-second clip from a local video."""
    import sys
    from pathlib import Path as P

    if len(sys.argv) < 2:
        print("Usage: python render.py <video_path> [start_sec] [end_sec]")
        sys.exit(1)

    check_ffmpeg()

    vpath = P(sys.argv[1])
    start = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    end   = float(sys.argv[3]) if len(sys.argv) > 3 else start + 10.0

    # Fake clip with no captions for smoke test
    from score import ScoredClip
    test_clip = ScoredClip(
        index=0, start=start, end=end, words=[],
        score=9.0, title="Test Clip", caption="Generated by render.py smoke test",
    )

    out_dir = P("output")
    render_all_clips(vpath, [test_clip], out_dir, verbose=True)
    print("Done.")
