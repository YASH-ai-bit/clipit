"""
input_handler.py — Validate local paths or download from YouTube/Twitch via yt-dlp.

Returns a normalized local video file path to the rest of the pipeline.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Supported video file extensions for local file validation
_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv",
}

# Patterns that identify supported streaming URLs
_URL_PATTERN = re.compile(
    r"^https?://(www\.)?(youtube\.com|youtu\.be|twitch\.tv|clips\.twitch\.tv)",
    re.IGNORECASE,
)


def is_url(source: str) -> bool:
    """Return True if *source* looks like a supported streaming URL."""
    return bool(_URL_PATTERN.match(source.strip()))


def validate_local_file(path: str) -> Path:
    """
    Check that *path* exists and appears to be a video file.

    Returns a resolved :class:`Path` on success.
    Raises :class:`ValueError` with a human-readable message on failure.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise ValueError(f"File not found: {path}")
    if not p.is_file():
        raise ValueError(f"Path is not a file: {path}")
    if p.suffix.lower() not in _VIDEO_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{p.suffix}'. "
            f"Supported: {', '.join(sorted(_VIDEO_EXTENSIONS))}"
        )
    return p


def _yt_dlp_available() -> bool:
    """Return True if yt-dlp is callable."""
    try:
        subprocess.run(
            ["yt-dlp", "--version"],
            check=True,
            capture_output=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def download_url(
    url: str,
    max_duration_seconds: Optional[int] = None,
    cache_dir: Optional[str] = None,
) -> Path:
    """
    Download *url* using yt-dlp and return the local video path.

    Parameters
    ----------
    url:
        A YouTube or Twitch URL.
    max_duration_seconds:
        If provided, only download the first N seconds of the video using
        yt-dlp's ``--download-sections`` to avoid fetching a massive file
        when only a short portion is needed.
    cache_dir:
        Directory where the download is stored.  Defaults to a ``downloads/``
        sub-directory next to where the script is run.

    Raises
    ------
    ValueError
        For unsupported or malformed URLs and yt-dlp failures.
    """
    if not _URL_PATTERN.match(url.strip()):
        raise ValueError(
            f"Unsupported URL: {url}\n"
            "Auto-Clipper supports YouTube (youtube.com, youtu.be) "
            "and Twitch (twitch.tv) URLs."
        )

    if not _yt_dlp_available():
        raise RuntimeError(
            "yt-dlp is not installed or not on PATH.\n"
            "Install it with:  pip install yt-dlp"
        )

    # Resolve download directory
    dl_dir = Path(cache_dir) if cache_dir else Path("downloads")
    dl_dir.mkdir(parents=True, exist_ok=True)

    # Output template — yt-dlp fills in title/id/ext
    output_template = str(dl_dir / "%(id)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
        "--merge-output-format", "mp4",
        "--output", output_template,
        "--print", "after_move:filepath",   # print the final path to stdout
        "--no-warnings",
    ]

    if max_duration_seconds is not None:
        # Download only the first N seconds to keep dev cycles fast
        cmd += ["--download-sections", f"*0-{max_duration_seconds}"]

    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp executable not found.  Install with:  pip install yt-dlp"
        )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "Private video" in stderr or "This video is unavailable" in stderr:
            raise ValueError(
                "The video is private or unavailable.  "
                "Check that the URL is public and correct."
            )
        if "No video formats found" in stderr or "Requested format is not available" in stderr:
            raise ValueError(
                "No suitable video+audio format found for this URL.\n"
                f"yt-dlp said: {stderr}"
            )
        raise ValueError(
            f"yt-dlp failed (exit {result.returncode}).\n{stderr}"
        )

    # The last non-empty line of stdout is the filepath printed by --print
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        raise ValueError(
            "yt-dlp did not report the downloaded file path.  "
            "The download may have failed silently."
        )

    downloaded = Path(lines[-1])
    if not downloaded.exists():
        raise ValueError(
            f"yt-dlp reported path '{downloaded}' but file does not exist."
        )

    return downloaded.resolve()


def get_video_path(
    source: str,
    max_duration_seconds: Optional[int] = None,
    cache_dir: Optional[str] = None,
    verbose: bool = False,
) -> Path:
    """
    Main entry point for input handling.

    Accepts a local file path or a supported URL and returns a resolved
    local :class:`Path` to a video file suitable for transcription and
    rendering.

    Parameters
    ----------
    source:
        Local path or YouTube/Twitch URL.
    max_duration_seconds:
        Limit processing to the first N seconds (applied during download
        for URLs; callers are responsible for trimming local files if
        needed).
    cache_dir:
        Where to store downloaded files.
    verbose:
        Print extra diagnostic information.
    """
    source = source.strip()

    if is_url(source):
        if verbose:
            print(f"[input] Detected URL: {source}")
        return download_url(source, max_duration_seconds=max_duration_seconds, cache_dir=cache_dir)
    else:
        if verbose:
            print(f"[input] Detected local file: {source}")
        return validate_local_file(source)
