"""
clipper.py — CLI entrypoint and pipeline orchestrator for Auto-Clipper.

Usage:
    python clipper.py --input <path-or-url> [options]

Options:
    --input          Local video file path or YouTube/Twitch URL  (required)
    --num-clips      Number of clips to generate (default: 5)
    --max-duration   Limit processing to the first N minutes (default: none)
    --model          Whisper model size: tiny/base/small/medium/large-v2 (default: base)
    --output-dir     Output directory (default: ./output)
    --no-cache       Ignore cached transcript and re-transcribe
    --verbose        Print verbose/debug output
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


# Configure UTF-8 encoding on standard streams if possible
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Load .env if present (non-fatal if python-dotenv is not installed)
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Attempt to load .env from the current directory."""
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except ImportError:
        pass  # dotenv optional; user may export env vars manually


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_step(msg: str) -> None:
    """Print a formatted pipeline step message."""
    print(f"\n[>] {msg}")


def _print_done(msg: str) -> None:
    print(f"    + {msg}")



def _fatal(msg: str, code: int = 1) -> None:
    """Print a human-readable error and exit."""
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipper",
        description=(
            "Auto-Clipper: Turn long-form video into short vertical social-media clips.\n"
            "Supports local files and YouTube/Twitch URLs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Local video file path or YouTube/Twitch URL.",
    )
    parser.add_argument(
        "--num-clips", "-n",
        type=int,
        default=5,
        help="Number of clips to generate (default: 5).",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        metavar="MINUTES",
        help=(
            "Limit processing to the first N minutes of the video.  "
            "Useful for fast development/testing on long videos."
        ),
    )
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large-v2", "large-v3"],
        help="Whisper model size (default: base).  Larger = more accurate but slower.",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help=(
            "OpenRouter LLM model route for scoring (default: openai/gpt-4o-mini, "
            "or OPENROUTER_MODEL env var)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory for generated clips (default: ./output).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached transcript and re-transcribe.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose/debug output including ffmpeg logs.",
    )
    return parser


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    _load_dotenv()

    # ── 0. Pre-flight checks ────────────────────────────────────────────────
    from render import check_ffmpeg
    try:
        check_ffmpeg()
    except RuntimeError as e:
        _fatal(str(e))

    # ── 1. Input handling ───────────────────────────────────────────────────
    _print_step("Preparing video...")

    max_sec = int(args.max_duration * 60) if args.max_duration else None

    from input_handler import get_video_path, is_url
    source = args.input

    if is_url(source):
        _print_step("Downloading source...")

    try:
        video_path = get_video_path(
            source,
            max_duration_seconds=max_sec,
            verbose=args.verbose,
        )
    except (ValueError, RuntimeError) as e:
        _fatal(str(e))
    except Exception as e:
        _fatal(f"Input error: {e}")

    _print_done(f"Video ready: {video_path}")

    # ── 2. Transcription ────────────────────────────────────────────────────
    _print_step("Transcribing...")
    t0 = time.time()

    from transcribe import transcribe
    try:
        words = transcribe(
            video_path,
            model_size=args.model,
            use_cache=not args.no_cache,
            verbose=args.verbose,
        )
    except (RuntimeError, FileNotFoundError) as e:
        _fatal(str(e))
    except Exception as e:
        _fatal(f"Transcription error: {e}")

    if not words:
        _fatal(
            "Transcription produced no words.  "
            "The video may have no speech, or the audio track may be silent/missing."
        )

    elapsed = time.time() - t0
    _print_done(f"{len(words)} words transcribed in {elapsed:.1f}s")

    # Optionally trim words to max_duration for local files
    if max_sec is not None:
        original_count = len(words)
        words = [w for w in words if w["start"] < max_sec]
        if args.verbose and len(words) < original_count:
            print(f"   [verbose] Trimmed transcript to {len(words)} words (≤{max_sec}s)")

    # ── 3. Candidate generation ─────────────────────────────────────────────
    _print_step("Generating candidate segments...")

    from score import generate_candidates, score_candidates, select_clips
    candidates = generate_candidates(words)

    if not candidates:
        _fatal(
            "No suitable candidate segments found.  "
            "The video may be too short or the transcript too sparse."
        )

    _print_done(f"{len(candidates)} candidates generated")

    # ── 4. LLM scoring ──────────────────────────────────────────────────────
    _print_step("Scoring candidate segments...")

    try:
        scored = score_candidates(
            candidates,
            model=args.llm_model,
            verbose=args.verbose,
        )
    except RuntimeError as e:
        _fatal(str(e))
    except Exception as e:
        _fatal(f"Scoring error: {e}")

    _print_done(f"{len(scored)} candidates scored")

    # ── 5. Clip selection ───────────────────────────────────────────────────
    _print_step("Selecting best clips...")

    selected = select_clips(scored, n=args.num_clips, verbose=args.verbose)

    if not selected:
        _fatal(
            "No clips could be selected after deduplication.  "
            "Try increasing --num-clips or using a longer video."
        )

    _print_done(f"{len(selected)} clips selected")

    # ── 6. Rendering ────────────────────────────────────────────────────────
    output_dir = Path(args.output_dir)

    from render import render_all_clips
    rendered = render_all_clips(
        source_video=video_path,
        clips=selected,
        output_dir=output_dir,
        verbose=args.verbose,
    )

    # ── 7. Summary ──────────────────────────────────────────────────────────
    print(f"\n{'-'*50}")
    print(f"  Completed successfully.")
    print(f"  {len(rendered)} clip(s) saved to: {output_dir.resolve()}")
    print(f"{'-'*50}")
    for mp4, txt in rendered:
        print(f"  * {mp4.name}  +  {txt.name}")
    print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Basic validation
    if args.num_clips < 1:
        parser.error("--num-clips must be at least 1.")
    if args.max_duration is not None and args.max_duration <= 0:
        parser.error("--max-duration must be a positive number of minutes.")

    try:
        run(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n[Interrupted by user]", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        # Catch-all: show a clean message rather than a traceback
        print(f"\n[UNEXPECTED ERROR] {e}", file=sys.stderr)
        if "--verbose" in sys.argv or "-v" in sys.argv:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
