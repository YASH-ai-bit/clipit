"""
transcribe.py — Word-level transcription using faster-whisper.

Each transcript entry is a plain dict:
    {"word": str, "start": float, "end": float}

The full transcript is cached alongside the source video as
``<video_path>.transcript.json`` so re-runs skip expensive re-transcription.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

# Normalized word dict type
WordEntry = Dict[str, Any]  # {"word": str, "start": float, "end": float}


def _cache_path(video_path: Path, model_size: str) -> Path:
    """Return the JSON cache file path for *video_path* and *model_size*."""
    return video_path.parent / (video_path.name + f".{model_size}.transcript.json")


def _load_cache(cache_file: Path) -> Optional[List[WordEntry]]:
    """Return cached words if the cache file exists and is valid, else None."""
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data and "word" in data[0]:
            return data
    except (json.JSONDecodeError, KeyError, IndexError):
        pass
    return None


def _save_cache(cache_file: Path, words: List[WordEntry]) -> None:
    """Persist *words* to *cache_file*."""
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(words, f, ensure_ascii=False, indent=2)
    except OSError as e:
        # Non-fatal: just warn; transcription result is already in memory
        print(f"[transcribe] Warning: could not write transcript cache: {e}")


def transcribe(
    video_path: Path,
    model_size: str = "base",
    language: Optional[str] = None,
    use_cache: bool = True,
    verbose: bool = False,
) -> List[WordEntry]:
    """
    Transcribe *video_path* and return a list of word-level timing dicts.

    Parameters
    ----------
    video_path:
        Path to the local video file.
    model_size:
        faster-whisper model name: ``tiny``, ``base``, ``small``, ``medium``,
        ``large-v2``, ``large-v3``.  ``base`` is the default for hackathon use
        (good speed/quality trade-off).
    language:
        ISO-639-1 language code (e.g. ``"en"``).  Pass ``None`` to let
        faster-whisper auto-detect.
    use_cache:
        If ``True``, load from the JSON cache when available and save after
        transcription.
    verbose:
        Print extra diagnostic messages.

    Returns
    -------
    list of dicts
        Each dict has ``word`` (str), ``start`` (float), ``end`` (float).
        Returns an empty list if transcription produces no words.

    Raises
    ------
    RuntimeError
        If faster-whisper is not installed or the model cannot be loaded.
    FileNotFoundError
        If *video_path* does not exist.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cache_file = _cache_path(video_path, model_size)

    if use_cache:
        cached = _load_cache(cache_file)
        if cached is not None:
            if verbose:
                print(f"[transcribe] Loaded {len(cached)} words from cache: {cache_file}")
            return cached

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        raise RuntimeError(
            "faster-whisper is not installed.\n"
            "Install it with:  pip install faster-whisper"
        )

    if verbose:
        print(f"[transcribe] Loading model '{model_size}' ...")

    # Use CPU with int8 quantization for broad compatibility on hackathon hardware.
    # If a CUDA GPU is available, faster-whisper will use it automatically when
    # device="auto" is set — but we default to CPU to avoid driver assumptions.
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    if verbose:
        print(f"[transcribe] Transcribing {video_path.name} ...")

    segments, info = model.transcribe(
        str(video_path),
        word_timestamps=True,
        language=language,
        beam_size=5,
        vad_filter=True,          # skip silent regions
        vad_parameters=dict(min_silence_duration_ms=500),
    )

    words: List[WordEntry] = []
    for segment in segments:
        if segment.words is None:
            continue
        for w in segment.words:
            text = w.word.strip()
            if not text:
                continue
            words.append({
                "word": text,
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
            })

    if verbose:
        duration = info.duration if hasattr(info, "duration") else "?"
        print(
            f"[transcribe] Got {len(words)} words "
            f"(video duration ≈ {duration:.1f}s)" if isinstance(duration, float)
            else f"[transcribe] Got {len(words)} words"
        )

    if use_cache and words:
        _save_cache(cache_file, words)
        if verbose:
            print(f"[transcribe] Transcript cached to {cache_file}")

    return words


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <video_path> [model_size]")
        sys.exit(1)

    vpath = Path(sys.argv[1])
    msize = sys.argv[2] if len(sys.argv) > 2 else "base"

    result = transcribe(vpath, model_size=msize, verbose=True)
    if result:
        print(f"\nFirst 10 words:")
        for entry in result[:10]:
            print(f"  {entry['start']:7.3f}s – {entry['end']:7.3f}s  {entry['word']}")
    else:
        print("No words transcribed.")
