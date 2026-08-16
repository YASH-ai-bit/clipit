"""
tests/test_score.py — Unit tests for score.py

Tests candidate generation, score parsing, overlap detection, and clip selection.
Does NOT call the Anthropic API.
"""

import sys
import pathlib
import json
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from score import (
    generate_candidates,
    _parse_llm_response,
    _overlap_ratio,
    select_clips,
    CandidateWindow,
    ScoredClip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_words(n: int, duration: float = 60.0):
    """Generate n fake word entries spread over [0, duration]."""
    step = duration / n
    return [
        {"word": f"word{i}", "start": round(i * step, 3), "end": round((i + 0.8) * step, 3)}
        for i in range(n)
    ]


def _make_scored(index: int, start: float, end: float, score: float) -> ScoredClip:
    return ScoredClip(
        index=index, start=start, end=end, words=[],
        score=score, title=f"Clip {index}", caption="",
    )


# ---------------------------------------------------------------------------
# generate_candidates
# ---------------------------------------------------------------------------

class TestGenerateCandidates:
    def test_empty_words_returns_empty(self):
        assert generate_candidates([]) == []

    def test_too_short_returns_empty(self):
        words = _make_words(3, duration=5.0)
        candidates = generate_candidates(words, min_duration=30.0, max_duration=60.0)
        assert candidates == []

    def test_produces_candidates_for_long_video(self):
        words = _make_words(200, duration=300.0)
        candidates = generate_candidates(words, min_duration=30.0, max_duration=60.0, step=15.0)
        assert len(candidates) > 0

    def test_candidate_fields(self):
        words = _make_words(200, duration=300.0)
        candidates = generate_candidates(words)
        for c in candidates:
            assert isinstance(c.start, float)
            assert isinstance(c.end, float)
            assert c.end > c.start
            assert len(c.words) > 0
            assert c.index >= 0

    def test_max_candidates_respected(self):
        words = _make_words(500, duration=600.0)
        candidates = generate_candidates(words, max_candidates=10)
        assert len(candidates) <= 10

    def test_ordered_by_start(self):
        words = _make_words(200, duration=300.0)
        candidates = generate_candidates(words)
        starts = [c.start for c in candidates]
        assert starts == sorted(starts)

    def test_text_property(self):
        words = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.5, "end": 1.0},
        ]
        # Not enough for a 30s window, but check CandidateWindow.text directly
        c = CandidateWindow(index=0, start=0.0, end=1.0, words=words)
        assert c.text == "Hello world"


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------

class TestParseLlmResponse:
    def test_valid_response(self):
        data = [
            {"index": 0, "score": 8.5, "title": "Great moment", "caption": "You won't believe this", "reasoning": "Strong hook"},
            {"index": 1, "score": 6.0, "title": "Another clip", "caption": "Interesting", "reasoning": "OK content"},
        ]
        result = _parse_llm_response(json.dumps(data))
        assert len(result) == 2
        assert result[0]["score"] == 8.5
        assert result[0]["title"] == "Great moment"

    def test_strips_markdown_fences(self):
        data = [{"index": 0, "score": 7.0, "title": "T", "caption": "C", "reasoning": "R"}]
        raw = f"```json\n{json.dumps(data)}\n```"
        result = _parse_llm_response(raw)
        assert len(result) == 1

    def test_no_array_raises(self):
        with pytest.raises(ValueError, match="No JSON array"):
            _parse_llm_response('{"not": "an array"}')

    def test_missing_required_fields_filtered(self):
        data = [
            {"index": 0, "score": 9.0},  # missing title/caption → skipped
            {"index": 1, "score": 7.0, "title": "T", "caption": "C"},  # valid
        ]
        result = _parse_llm_response(json.dumps(data))
        assert len(result) == 1
        assert result[0]["index"] == 1

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="No JSON array"):
            _parse_llm_response("not json at all")

    def test_score_clamped_to_float(self):
        data = [{"index": 0, "score": "9", "title": "T", "caption": "C"}]
        result = _parse_llm_response(json.dumps(data))
        assert isinstance(result[0]["score"], float)

    def test_title_truncated_at_60_chars(self):
        long_title = "A" * 100
        data = [{"index": 0, "score": 5.0, "title": long_title, "caption": "C"}]
        result = _parse_llm_response(json.dumps(data))
        assert len(result[0]["title"]) <= 60


# ---------------------------------------------------------------------------
# _overlap_ratio
# ---------------------------------------------------------------------------

class TestOverlapRatio:
    def test_no_overlap(self):
        a = _make_scored(0, 0.0, 30.0, 8.0)
        b = _make_scored(1, 40.0, 70.0, 7.0)
        assert _overlap_ratio(a, b) == 0.0

    def test_full_overlap(self):
        a = _make_scored(0, 0.0, 30.0, 8.0)
        b = _make_scored(1, 0.0, 30.0, 7.0)
        assert _overlap_ratio(a, b) == pytest.approx(1.0)

    def test_partial_overlap(self):
        a = _make_scored(0, 0.0, 40.0, 8.0)
        b = _make_scored(1, 20.0, 60.0, 7.0)
        # overlap = 20s / 40s = 0.5
        assert _overlap_ratio(a, b) == pytest.approx(0.5)

    def test_adjacent_no_overlap(self):
        a = _make_scored(0, 0.0, 30.0, 8.0)
        b = _make_scored(1, 30.0, 60.0, 7.0)
        assert _overlap_ratio(a, b) == 0.0


# ---------------------------------------------------------------------------
# select_clips
# ---------------------------------------------------------------------------

class TestSelectClips:
    def test_selects_top_n(self):
        scored = [_make_scored(i, i * 100.0, (i + 1) * 100.0, 10.0 - i) for i in range(10)]
        selected = select_clips(scored, n=3)
        assert len(selected) == 3

    def test_removes_overlapping(self):
        # Two nearly-identical windows — only the higher-scored one should be kept
        a = _make_scored(0, 0.0, 60.0, 9.0)
        b = _make_scored(1, 5.0, 65.0, 7.0)   # 91% overlap with a → should be dropped
        selected = select_clips([a, b], n=2, max_overlap=0.5)
        assert len(selected) == 1
        assert selected[0].index == 0

    def test_output_sorted_by_start_time(self):
        scored = [
            _make_scored(0, 200.0, 240.0, 9.0),
            _make_scored(1, 50.0, 90.0, 8.0),
            _make_scored(2, 120.0, 160.0, 7.0),
        ]
        selected = select_clips(scored, n=3)
        starts = [c.start for c in selected]
        assert starts == sorted(starts)

    def test_fewer_than_n_when_all_overlap(self):
        # All clips overlap heavily — only 1 can be selected
        clips = [_make_scored(i, 0.0, 60.0, 10.0 - i) for i in range(5)]
        selected = select_clips(clips, n=5)
        assert len(selected) == 1

    def test_empty_input(self):
        assert select_clips([], n=5) == []
