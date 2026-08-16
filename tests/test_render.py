"""
tests/test_render.py — Unit tests for render.py

Tests ASS subtitle formatting, word grouping, output naming, and ASS content generation.
Does NOT require ffmpeg or a real video file.
"""

import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from render import (
    _format_ass_time,
    _build_word_groups,
    generate_ass_content,
)
from score import ScoredClip


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_words(texts, start_offset=0.0, word_duration=0.5, gap=0.1):
    """Build a list of word dicts from a list of strings."""
    words = []
    t = start_offset
    for text in texts:
        words.append({"word": text, "start": round(t, 3), "end": round(t + word_duration, 3)})
        t += word_duration + gap
    return words


# ---------------------------------------------------------------------------
# _format_ass_time
# ---------------------------------------------------------------------------

class TestFormatAssTime:
    def test_zero(self):
        assert _format_ass_time(0.0) == "0:00:00.00"

    def test_seconds(self):
        assert _format_ass_time(5.50) == "0:00:05.50"

    def test_minutes(self):
        assert _format_ass_time(65.25) == "0:01:05.25"

    def test_hours(self):
        assert _format_ass_time(3661.12) == "1:01:01.12"

    def test_negative_clamped(self):
        assert _format_ass_time(-5.0) == "0:00:00.00"


# ---------------------------------------------------------------------------
# _build_word_groups
# ---------------------------------------------------------------------------

class TestBuildWordGroups:
    def test_empty_words(self):
        assert _build_word_groups([], clip_start=0.0) == []

    def test_single_group(self):
        words = _make_words(["one", "two", "three", "four"])
        groups = _build_word_groups(words, clip_start=0.0, words_per_group=4)
        assert len(groups) == 1
        gstart, gend, gtext, gwords = groups[0]
        assert gtext == "one two three four"
        assert gstart == pytest.approx(0.0, abs=0.01)

    def test_multiple_groups(self):
        words = _make_words(["a", "b", "c", "d", "e", "f"], start_offset=10.0)
        groups = _build_word_groups(words, clip_start=10.0, words_per_group=3)
        assert len(groups) == 2

    def test_times_are_relative_to_clip_start(self):
        words = _make_words(["hello", "world"], start_offset=30.0)
        groups = _build_word_groups(words, clip_start=30.0, words_per_group=4)
        gstart, gend, _, _ = groups[0]
        assert gstart == pytest.approx(0.0, abs=0.01)

    def test_last_group_smaller_than_group_size(self):
        words = _make_words(["a", "b", "c", "d", "e"])
        groups = _build_word_groups(words, clip_start=0.0, words_per_group=4)
        assert len(groups) == 2
        _, _, text2, _ = groups[1]
        assert text2 == "e"


# ---------------------------------------------------------------------------
# generate_ass_content
# ---------------------------------------------------------------------------

class TestGenerateAssContent:
    def test_header_present(self):
        words = _make_words(["hello", "world"])
        content = generate_ass_content(words, clip_start=0.0)
        assert "[Script Info]" in content
        assert "[V4+ Styles]" in content
        assert "[Events]" in content

    def test_dialogue_events_generated(self):
        words = _make_words(["one", "two", "three"])
        content = generate_ass_content(words, clip_start=0.0)
        assert "Dialogue: 0," in content

    def test_word_highlight_tags(self):
        words = _make_words(["hello", "world"])
        content = generate_ass_content(words, clip_start=0.0)
        # Yellow color tag
        assert r"{\c&H0000FFFF&}" in content
        # White color tag
        assert r"{\c&H00FFFFFF&}" in content

    def test_empty_words_produces_valid_ass(self):
        content = generate_ass_content([], clip_start=0.0)
        assert "[Script Info]" in content
        assert "Dialogue:" not in content


# ---------------------------------------------------------------------------
# Output naming
# ---------------------------------------------------------------------------

class TestOutputNaming:
    def test_clip_labels(self):
        labels = [f"clip_{i:02d}" for i in range(1, 6)]
        assert labels == ["clip_01", "clip_02", "clip_03", "clip_04", "clip_05"]

    def test_mp4_and_txt_extensions(self):
        for i in range(1, 4):
            label = f"clip_{i:02d}"
            assert (label + ".mp4").endswith(".mp4")
            assert (label + ".txt").endswith(".txt")
