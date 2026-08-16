# Auto-Clipper 🎬

Automatically turn long-form video into multiple short, vertical, ready-to-post social media clips — complete with synchronized word-level captions and AI-generated titles.

## What it does

Auto-Clipper takes a long video (local file or YouTube/Twitch URL) and runs it through a fully automated pipeline:

1. **Download** (if URL) via yt-dlp
2. **Transcribe** with faster-whisper (word-level timestamps)
3. **Generate** candidate clip windows from the transcript
4. **Score** candidates using Claude (Anthropic) — evaluates hook strength, self-containment, payoff, and short-form suitability
5. **Rank & deduplicate** to select the best non-overlapping moments
6. **Render** each clip: cuts the original video, converts to 9:16 vertical format, burns in animated word-level captions
7. **Output** numbered MP4 clips + TXT files with suggested titles/captions

---

## System Requirements

| Requirement | Details |
|-------------|---------|
| Python | 3.9 or newer |
| ffmpeg | Must be on PATH (see below) |
| Internet | Required for URL downloads and Anthropic API |
| Disk space | ~2 GB+ depending on source video length |

### ffmpeg Installation

- **Windows**: `winget install Gyan.FFmpeg` or download from https://ffmpeg.org/download.html
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg` / `sudo dnf install ffmpeg`

After installation, verify: `ffmpeg -version`

---

## Installation

```bash
git clone https://github.com/YASH-ai-bit/clipit.git
cd clipit

# Create a virtual environment (recommended)
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
```

---

## Environment Setup

Copy `.env.example` to `.env` and fill in your OpenRouter API key:

```bash
cp .env.example .env
```

Edit `.env`:
```
OPENROUTER_API_KEY=sk-or-v1-your-key-here
# Optional: choose scoring model (defaults to ultra-cheap Gemini 2.0 Flash)
OPENROUTER_MODEL=google/gemini-2.0-flash-001
```

Get an API key from: https://openrouter.ai/keys

Alternatively, export the variable directly:
```bash
export OPENROUTER_API_KEY=sk-or-v1-your-key-here   # macOS/Linux
$env:OPENROUTER_API_KEY="sk-or-v1-..."             # Windows PowerShell
```

---

## Usage

### Basic

```bash
# From a local video file
python clipper.py --input my_podcast.mp4

# From a YouTube URL
python clipper.py --input "https://www.youtube.com/watch?v=VIDEO_ID"

# From a Twitch clip
python clipper.py --input "https://clips.twitch.tv/CLIP_ID"
```

### Options

```
--input, -i        Local video path or YouTube/Twitch URL  (required)
--num-clips, -n    Number of clips to generate (default: 5)
--max-duration     Process only the first N minutes (default: full video)
--model            Whisper model: tiny/base/small/medium/large-v2 (default: base)
--llm-model        OpenRouter LLM model route (default: google/gemini-2.0-flash-001)
--output-dir       Output directory (default: ./output)
--no-cache         Force re-transcription (ignore cached transcript)
--verbose          Show detailed ffmpeg/debug output
```

### Cost-Effective Model Recommendations

With just **$1 of OpenRouter credits**, you can process thousands of videos by choosing a lightweight model:

| Model | Cost per Run (~20 segments) | Runs per $1 Credit |
|-------|----------------------------|--------------------|
| `openai/gpt-4o-mini` *(Default)* | **~$0.0005** | **~2,000 runs** |
| `openai/gpt-4o` | **~$0.008** | **~125 runs** |
| `meta-llama/llama-3.3-70b-instruct:free` | **$0.00** | **Unlimited (Free)** |
| `anthropic/claude-sonnet-4-5` | **~$0.010** | **~100 runs** |

### Examples

```bash
# Fast test: 3 clips from first 5 minutes using ultra-cheap default model
python clipper.py --input "https://youtu.be/EXAMPLE" --num-clips 3 --max-duration 5

# Use a 100% free model on OpenRouter
python clipper.py --input video.mp4 --llm-model "meta-llama/llama-3.3-70b-instruct:free"

# Use GPT-4o Mini
python clipper.py --input podcast.mp4 --llm-model "openai/gpt-4o-mini"
```

---

## Expected Output

```
output/
    clip_01.mp4    ← vertical 9:16 MP4 with burned-in captions
    clip_01.txt    ← suggested title, caption, score, timestamp
    clip_02.mp4
    clip_02.txt
    ...
```

Each `.txt` file contains:
```
Title: The moment that changed everything

Caption: You won't believe what happened at 12:30 into this episode...

Timestamp: 732.1s – 789.4s
Score: 8.7/10
Reasoning: Strong hook with immediate tension, self-contained story with clear resolution.
```

---

## Transcript Caching

To avoid expensive re-transcription, the transcript is cached as:
```
<video>.base.transcript.json
```
alongside the video file. Delete this file or use `--no-cache` to force a fresh transcription.

---

## Architecture

```
clipper.py          ← CLI entrypoint + orchestration
input_handler.py    ← Local file validation / yt-dlp downloading
transcribe.py       ← faster-whisper word-level transcription
score.py            ← Candidate generation + Anthropic LLM scoring + dedup
render.py           ← ffmpeg: cut, 9:16 crop, caption overlay, encode
```

### Pipeline Detail

1. `input_handler.get_video_path()` → local `Path`
2. `transcribe.transcribe()` → `[{word, start, end}, ...]`
3. `score.generate_candidates()` → overlapping time windows
4. `score.score_candidates()` → LLM scores + titles (batched Anthropic API call)
5. `score.select_clips()` → top-N non-overlapping clips
6. `render.render_all_clips()` → MP4 + TXT files

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

Tests cover: URL detection, local file validation, candidate generation, LLM response parsing, overlap removal, caption escaping, and word grouping. No external services or large files required.

---

## Troubleshooting

| Error | Fix |
|-------|-----|
| `ffmpeg is not installed` | Install ffmpeg and ensure it's on PATH |
| `ANTHROPIC_API_KEY is not set` | Add key to `.env` or export the env var |
| `yt-dlp is not installed` | `pip install yt-dlp` |
| `No words transcribed` | Video may have no speech; try `--verbose` to see more |
| `No suitable candidate segments` | Video may be too short; try a longer input |
| Captions are misaligned | Use `--model small` or larger for better timing accuracy |
| `Private video` | The video URL is private or geo-restricted |
| Rate limit errors | Anthropic API is busy; the tool will retry automatically |

---

## Supported Input Formats

**Local files:** `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, `.flv`, `.ts`, `.m4v`, `.wmv`

**URLs:** YouTube (`youtube.com`, `youtu.be`) and Twitch (`twitch.tv`, `clips.twitch.tv`)

---

## Notes

- The `base` Whisper model is fast and works well for most English content. Use `small` or `medium` for non-English or technical speech.
- LLM scoring uses Claude via the Anthropic API — you need a paid API key.
- Very long videos (>2 hours) may take 10+ minutes to transcribe with `base`.
- The 9:16 transformation uses a centered crop + scale approach. Content at the extreme edges of the frame may be cropped.
