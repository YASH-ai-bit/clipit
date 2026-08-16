"""
tests/test_input.py — Unit tests for input_handler.py

Does NOT require network access or a real video file for most tests.
"""

import sys
import os
import tempfile
import pathlib
import pytest

# Make sure the project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from input_handler import is_url, validate_local_file


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------

class TestIsUrl:
    def test_youtube_full(self):
        assert is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_youtube_short(self):
        assert is_url("https://youtu.be/dQw4w9WgXcQ")

    def test_twitch_clip(self):
        assert is_url("https://clips.twitch.tv/SomeClipName")

    def test_twitch_channel(self):
        assert is_url("https://www.twitch.tv/some_streamer")

    def test_youtube_no_www(self):
        assert is_url("https://youtube.com/watch?v=abc123")

    def test_local_path(self):
        assert not is_url("/path/to/video.mp4")

    def test_relative_path(self):
        assert not is_url("video.mp4")

    def test_unsupported_url(self):
        assert not is_url("https://vimeo.com/12345")

    def test_empty_string(self):
        assert not is_url("")

    def test_http_youtube(self):
        # HTTP (not HTTPS) should still match
        assert is_url("http://www.youtube.com/watch?v=abc")

    def test_whitespace_stripped(self):
        assert is_url("  https://youtu.be/abc123  ")


# ---------------------------------------------------------------------------
# Local file validation
# ---------------------------------------------------------------------------

class TestValidateLocalFile:
    def test_nonexistent_file(self):
        with pytest.raises(ValueError, match="not found"):
            validate_local_file("/nonexistent/path/video.mp4")

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "video.txt"
        f.write_text("not a video")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            validate_local_file(str(f))

    def test_directory_path(self, tmp_path):
        with pytest.raises(ValueError, match="not a file"):
            validate_local_file(str(tmp_path))

    def test_valid_mp4(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"\x00" * 100)  # fake bytes, just needs to exist
        result = validate_local_file(str(f))
        assert result == f.resolve()

    def test_valid_mkv(self, tmp_path):
        f = tmp_path / "movie.mkv"
        f.write_bytes(b"\x00" * 100)
        result = validate_local_file(str(f))
        assert result == f.resolve()

    def test_valid_mov(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(b"\x00" * 100)
        result = validate_local_file(str(f))
        assert result == f.resolve()

    def test_case_insensitive_extension(self, tmp_path):
        f = tmp_path / "video.MP4"
        f.write_bytes(b"\x00" * 100)
        result = validate_local_file(str(f))
        assert result == f.resolve()
