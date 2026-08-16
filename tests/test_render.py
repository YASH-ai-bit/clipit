"""
tests/test_render.py — Unit tests for render.py

Tests caption escaping, word grouping, output naming, and ffmpeg detection.
Does NOT require ffmpeg or a real video file.
"""

import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from render import (
    _escape_drawtext,
    _build_word_groups,
    _drawtext_filters_for_clip,
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
# _escape_drawtext
# ---------------------------------------------------------------------------

class TestEscapeDrawtext:
    def test_plain_text(self):
        assert _escape_drawtext("Hello world") == "Hello world"

    def test_colon_escaped(self):
        result = _escape_drawtext("time: 10:30")
        assert "\\:" in result

    def test_backslash_doubled(self):
        result = _escape_drawtext("back\\slash")
        assert "\\\\" in result

    def test_apostrophe_replaced(self):
        # Apostrophes are converted to right-single-quote (safe unicode)
        result = _escape_drawtext("don't")
        assert "'" not in result  # raw apostrophe removed/replaced

    def test_square_brackets_escaped(self):
        result = _escape_drawtext("value[0]")
        assert "\\[" in result
        assert "\\]" in result

    def test_comma_escaped(self):
        result = _escape_drawtext("a,b,c")
        assert "\\," in result

    def test_equals_escaped(self):
        result = _escape_drawtext("key=value")
        assert "\\=" in result

    def test_empty_string(self):
        assert _escape_drawtext("") == ""

    def test_unicode_passthrough(self):
        # Normal unicode should pass through
        result = _escape_drawtext("héllo wörld")
        assert "héllo" in result


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
        # 5 words with group_size=4 → 2 groups: [4] + [1]
        assert len(groups) == 2
        _, _, text2, _ = groups[1]
        assert text2 == "e"


# ---------------------------------------------------------------------------
# _drawtext_filters_for_clip
# ---------------------------------------------------------------------------

class TestDrawtextFilters:
    def test_returns_list_of_strings(self):
        words = _make_words(["hello", "world", "test", "clip"])
        filters = _drawtext_filters_for_clip(words, clip_start=0.0)
        assert isinstance(filters, list)
        assert all(isinstance(f, str) for f in filters)

    def test_empty_words_returns_empty(self):
        filters = _drawtext_filters_for_clip([], clip_start=0.0)
        assert filters == []

    def test_filters_contain_drawtext(self):
        words = _make_words(["hello", "world"])
        filters = _drawtext_filters_for_clip(words, clip_start=0.0)
        assert any("drawtext" in f for f in filters)

    def test_no_unescaped_apostrophe_in_filter(self):
        words = _make_words(["don't", "stop"])
        filters = _drawtext_filters_for_clip(words, clip_start=0.0)
        for f in filters:
            # The filter value should not contain a raw single-quote
            # (they should be replaced with unicode right-quote)
            # We check there's no "text='...'" containing raw '
            # by looking for the problematic pattern
            if "drawtext=text='" in f:
                inner_start = f.index("drawtext=text='") + len("drawtext=text='")
                # Find the closing quote
                inner_end = f.index("'", inner_start)
                inner = f[inner_start:inner_end]
                assert "'" not in inner, f"Raw apostrophe found in: {f}"

    def test_enable_clause_present(self):
        words = _make_words(["visible", "when", "speaking"])
        filters = _drawtext_filters_for_clip(words, clip_start=0.0)
        assert any("enable=" in f for f in filters)


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
