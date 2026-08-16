# Auto-Clipper Build Progress

## Status: ✅ COMPLETE

## Pipeline
`
input → transcribe → score → render → output
`

## Files

| File | Status | Notes |
|------|--------|-------|
| `progress.md` | ✅ | This file |
| `requirements.txt` | ✅ | Python 3.9-3.12 recommended note added |
| `.env.example` | ✅ | ANTHROPIC_API_KEY |
| `input_handler.py` | ✅ | Local + URL (yt-dlp) support |
| `transcribe.py` | ✅ | faster-whisper, JSON cache |
| `score.py` | ✅ | Candidate gen + Anthropic LLM batch scoring |
| `render.py` | ✅ | ffmpeg, 9:16 centered crop, word-level captions |
| `clipper.py` | ✅ | CLI entrypoint |
| `README.md` | ✅ | Full docs |
| `tests/test_input.py` | ✅ | 18 tests |
| `tests/test_score.py` | ✅ | 27 tests |
| `tests/test_render.py` | ✅ | 17 tests |

## Test Results

| Test | Status |
|------|--------|
| Syntax check (all .py) | ✅ All OK |
| Unit tests (62 total) | ✅ 62/62 passed |
| ffmpeg detection | ✅ Works (shows install msg if missing) |
| CLI --help | ✅ All options shown correctly |
| Error handling (bad input) | ✅ Clean human-readable error |

## Python 3.15 Note
This machine runs Python 3.15.0b1 (beta). `ctranslate2` and `av`
(required by faster-whisper) do not have pre-built wheels for 3.15 yet.
Use Python 3.9-3.12 for full functionality. All other packages work fine.

## Key Decisions Made
- Model default: `base` (fast for hackathon)
- Clip duration windows: 30s, 45s, 60s with 15s step
- Caption style: word groups of 4, current word highlighted yellow
- Transcript cached as `<video>.base.transcript.json`
- Anthropic model: `claude-sonnet-4-5-20251001`
- ffmpeg via subprocess only, checked before any processing
- python-dotenv for env loading (gracefully optional)
