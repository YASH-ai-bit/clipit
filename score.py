"""
score.py — Candidate generation, LLM scoring, ranking, and deduplication.

Pipeline:
1. generate_candidates()  → list of CandidateWindow
2. score_candidates()     → ranked list of ScoredClip (calls Anthropic once)
3. select_clips()         → top-N non-overlapping ScoredClip
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

from transcribe import WordEntry


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CandidateWindow:
    """A time window extracted from the transcript."""
    index: int
    start: float          # seconds
    end: float            # seconds
    words: List[WordEntry]

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(w["word"] for w in self.words)


@dataclass
class ScoredClip:
    """A candidate that has been scored by the LLM."""
    index: int            # matches CandidateWindow.index
    start: float
    end: float
    words: List[WordEntry]
    score: float          # 0–10
    title: str
    caption: str
    reasoning: str = ""

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def text(self) -> str:
        return " ".join(w["word"] for w in self.words)


# ---------------------------------------------------------------------------
# 1. Candidate generation
# ---------------------------------------------------------------------------

def generate_candidates(
    words: List[WordEntry],
    min_duration: float = 30.0,
    max_duration: float = 60.0,
    step: float = 15.0,
    max_candidates: int = 50,
) -> List[CandidateWindow]:
    """
    Produce overlapping time windows from *words* that might make good clips.

    The algorithm slides a window across the transcript, moving by *step*
    seconds each time and including all words within [start, start+window_size].
    Multiple window sizes between *min_duration* and *max_duration* are used.

    Parameters
    ----------
    words:
        Word-level transcript entries.
    min_duration / max_duration:
        Clip length range in seconds.
    step:
        Slide step in seconds.
    max_candidates:
        Hard cap to avoid excessive LLM costs.

    Returns
    -------
    List of :class:`CandidateWindow` ordered by start time.
    """
    if not words:
        return []

    total_duration = words[-1]["end"]
    window_sizes = [min_duration, (min_duration + max_duration) / 2, max_duration]
    # Deduplicate window sizes
    window_sizes = sorted(set(window_sizes))

    candidates: List[CandidateWindow] = []
    seen_starts: set[float] = set()

    idx = 0
    for window in window_sizes:
        t = 0.0
        while t + window <= total_duration + step:
            win_start = round(t, 3)
            win_end = round(min(t + window, total_duration), 3)

            # Snap to actual word boundaries
            window_words = [
                w for w in words
                if w["start"] >= win_start and w["end"] <= win_end + 0.5
            ]
            if len(window_words) < 5:
                t += step
                continue

            actual_start = window_words[0]["start"]
            actual_end = window_words[-1]["end"]

            # Skip very short or duplicate windows
            if (actual_end - actual_start) < min_duration * 0.7:
                t += step
                continue
            if actual_start in seen_starts:
                t += step
                continue

            seen_starts.add(actual_start)
            candidates.append(CandidateWindow(
                index=idx,
                start=actual_start,
                end=actual_end,
                words=window_words,
            ))
            idx += 1
            t += step

    # Sort by start time, cap
    candidates.sort(key=lambda c: c.start)
    return candidates[:max_candidates]


# ---------------------------------------------------------------------------
# 2. LLM scoring
# ---------------------------------------------------------------------------

_SCORE_SYSTEM_PROMPT = """You are an expert short-form video editor and social media strategist.
You evaluate transcript segments to decide which make the best TikTok/Reels/Shorts clips.
Always respond with valid JSON matching the exact schema requested."""

_SCORE_USER_TEMPLATE = """Evaluate the following {n} transcript segments for their potential as short-form social media clips.

For EACH segment, rate it on a scale of 0–10 and provide a suggested title and caption.

Scoring criteria (weight each equally):
- Hook strength: does it open with something immediately interesting?
- Self-contained: does it make sense without surrounding context?
- Payoff: does it have a meaningful insight, punchline, surprise, or conclusion?
- Short-form fit: is the energy and pacing suitable for social media?

Return ONLY a JSON array of objects with this exact schema (one object per segment, in order):
[
  {{
    "index": <integer matching the segment index>,
    "score": <float 0-10>,
    "title": <concise title string, max 60 chars>,
    "caption": <engaging caption/description, max 150 chars>,
    "reasoning": <one sentence explaining the score>
  }},
  ...
]

SEGMENTS:
{segments_json}
"""


def _build_segments_payload(candidates: List[CandidateWindow]) -> str:
    """Serialize candidates to a compact JSON string for the prompt."""
    payload = []
    for c in candidates:
        payload.append({
            "index": c.index,
            "start_seconds": round(c.start, 1),
            "end_seconds": round(c.end, 1),
            "duration_seconds": round(c.duration, 1),
            "transcript": c.text,
        })
    return json.dumps(payload, indent=2)


def _parse_llm_response(response_text: str) -> List[Dict[str, Any]]:
    """
    Extract and validate the JSON array from the LLM response.

    Returns a list of score dicts.  Raises :class:`ValueError` on failure.
    """
    text = response_text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            line for line in lines
            if not line.strip().startswith("```")
        ).strip()

    # Find the JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in LLM response:\n{response_text[:500]}")

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON from LLM: {e}\nRaw:\n{text[start:end+1][:500]}")

    if not isinstance(data, list):
        raise ValueError("LLM response is not a JSON array.")

    required = {"index", "score", "title", "caption"}
    validated = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not required.issubset(item.keys()):
            continue
        try:
            item["score"] = float(item["score"])
            item["index"] = int(item["index"])
            item["title"] = str(item.get("title", "")).strip()[:60]
            item["caption"] = str(item.get("caption", "")).strip()[:150]
            item["reasoning"] = str(item.get("reasoning", "")).strip()
        except (TypeError, ValueError):
            continue
        validated.append(item)

    return validated


# OpenRouter base URL (OpenAI-compatible)
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def score_candidates(
    candidates: List[CandidateWindow],
    api_key: Optional[str] = None,
    model: str = "anthropic/claude-sonnet-4-5",  # OpenRouter model route for Claude Sonnet
    batch_size: int = 20,
    verbose: bool = False,
) -> List[ScoredClip]:
    """
    Score *candidates* using OpenRouter (OpenAI-compatible API) and return
    :class:`ScoredClip` list.

    The candidates are sent in batches (default 20) to stay within context limits.
    Each batch is one API call.

    Parameters
    ----------
    candidates:
        Output of :func:`generate_candidates`.
    api_key:
        OpenRouter API key.  Falls back to ``OPENROUTER_API_KEY`` env var.
    model:
        OpenRouter model route, e.g. ``anthropic/claude-sonnet-4-5``.
        See https://openrouter.ai/models for available routes.
    batch_size:
        Number of candidates per API call.
    verbose:
        Print extra info.

    Raises
    ------
    RuntimeError
        If the API key is missing or the API call fails after retries.
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set.\n"
            "Add it to your .env file:  OPENROUTER_API_KEY=sk-or-v1-...\n"
            "Get a key at https://openrouter.ai/keys"
        )

    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        raise RuntimeError(
            "openai package is not installed.\n"
            "Install it with:  pip install openai"
        )

    client = OpenAI(
        api_key=key,
        base_url=_OPENROUTER_BASE_URL,
    )

    # Build an index for quick lookup
    candidate_map: Dict[int, CandidateWindow] = {c.index: c for c in candidates}
    all_scores: Dict[int, Dict[str, Any]] = {}

    # Process in batches
    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start : batch_start + batch_size]
        if verbose:
            print(f"[score] Scoring batch {batch_start // batch_size + 1} "
                  f"({len(batch)} candidates) via OpenRouter ({model}) ...")

        segments_json = _build_segments_payload(batch)
        user_msg = _SCORE_USER_TEMPLATE.format(
            n=len(batch),
            segments_json=segments_json,
        )

        # Retry up to 3 times on transient errors
        last_error: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=model,
                    max_tokens=4096,
                    messages=[
                        {"role": "system", "content": _SCORE_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                )
                response_text = response.choices[0].message.content or ""
                scores = _parse_llm_response(response_text)
                for s in scores:
                    all_scores[s["index"]] = s
                last_error = None
                break
            except (ValueError, KeyError) as e:
                # Parse / structure errors — log and break (retrying won't help)
                print(f"[score] Warning: could not parse LLM response: {e}")
                last_error = e
                break
            except Exception as e:
                err_str = str(e)
                if "rate" in err_str.lower() or "429" in err_str or "overloaded" in err_str.lower():
                    wait = 10 * (attempt + 1)
                    print(f"[score] Rate limited. Waiting {wait}s before retry ...")
                    time.sleep(wait)
                    last_error = e
                else:
                    raise RuntimeError(f"OpenRouter API error: {e}") from e

        if last_error and isinstance(last_error, Exception) and (
            "rate" in str(last_error).lower() or "429" in str(last_error)
        ):
            raise RuntimeError(
                f"OpenRouter rate limit exceeded after retries: {last_error}"
            )

    # Assemble ScoredClip objects
    scored: List[ScoredClip] = []
    for idx, cand in candidate_map.items():
        if idx not in all_scores:
            # Assign neutral score for unscored candidates
            scored.append(ScoredClip(
                index=idx,
                start=cand.start,
                end=cand.end,
                words=cand.words,
                score=0.0,
                title=f"Clip at {cand.start:.0f}s",
                caption="",
                reasoning="Not scored by LLM",
            ))
        else:
            s = all_scores[idx]
            scored.append(ScoredClip(
                index=idx,
                start=cand.start,
                end=cand.end,
                words=cand.words,
                score=s["score"],
                title=s["title"],
                caption=s["caption"],
                reasoning=s.get("reasoning", ""),
            ))

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# 3. Ranking and deduplication
# ---------------------------------------------------------------------------

def _overlap_ratio(a: ScoredClip, b: ScoredClip) -> float:
    """Return the fraction of *a* that overlaps with *b* (0–1)."""
    overlap_start = max(a.start, b.start)
    overlap_end = min(a.end, b.end)
    overlap = max(0.0, overlap_end - overlap_start)
    return overlap / a.duration if a.duration > 0 else 0.0


def select_clips(
    scored: List[ScoredClip],
    n: int = 5,
    max_overlap: float = 0.3,
    verbose: bool = False,
) -> List[ScoredClip]:
    """
    Select the top *n* clips from *scored*, removing clips that overlap
    more than *max_overlap* (fraction of the shorter clip) with an already-
    selected clip.

    The input is assumed to be sorted by descending score.

    Returns
    -------
    List of selected :class:`ScoredClip` sorted by start time.
    """
    selected: List[ScoredClip] = []

    for candidate in scored:
        if len(selected) >= n:
            break
        # Check overlap with already-selected clips
        overlap_ok = True
        for chosen in selected:
            if _overlap_ratio(candidate, chosen) > max_overlap or \
               _overlap_ratio(chosen, candidate) > max_overlap:
                if verbose:
                    print(
                        f"[score] Skipping candidate {candidate.index} "
                        f"(overlaps {chosen.index} by >{max_overlap*100:.0f}%)"
                    )
                overlap_ok = False
                break
        if overlap_ok:
            selected.append(candidate)

    # Sort by start time for natural output ordering
    selected.sort(key=lambda c: c.start)

    if verbose:
        print(f"[score] Selected {len(selected)} clips:")
        for i, clip in enumerate(selected, 1):
            print(f"  {i}. [{clip.start:.1f}s - {clip.end:.1f}s] "
                  f"score={clip.score:.1f}  '{clip.title}'")

    return selected


if __name__ == "__main__":
    import sys
    import json as _json
    from transcribe import transcribe

    if len(sys.argv) < 2:
        print("Usage: python score.py <video_path>")
        sys.exit(1)

    video = sys.argv[1]
    from pathlib import Path
    words = transcribe(Path(video), verbose=True)
    candidates = generate_candidates(words)
    print(f"Generated {len(candidates)} candidates.")
    scored = score_candidates(candidates, verbose=True)
    selected = select_clips(scored, n=5, verbose=True)
    print("\nFinal selection:")
    for i, clip in enumerate(selected, 1):
        print(f"  {i}. {clip.start:.1f}s – {clip.end:.1f}s | {clip.title}")
