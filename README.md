<div align="center">
  <img src="logo.gif" width="120" height="120" alt="CLIPIT Logo" />
  <h1>CLIPIT</h1>
  <p><b>Autonomous AI Engine for Long-to-Short Video Repurposing</b></p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.12-white?style=flat-square&logo=python&logoColor=black" alt="Python 3.12" />
    <img src="https://img.shields.io/badge/Inference-Faster--Whisper-black?style=flat-square" alt="Faster Whisper" />
    <img src="https://img.shields.io/badge/FFmpeg-libass%20Enabled-white?style=flat-square&logo=ffmpeg&logoColor=black" alt="FFmpeg" />
    <img src="https://img.shields.io/badge/LLM-OpenRouter%20%2F%20ChatGPT-black?style=flat-square" alt="OpenRouter" />
    <img src="https://img.shields.io/badge/UI-Ultra--Minimal-white?style=flat-square" alt="Ultra-Minimal UI" />
  </p>
</div>

---

## Overview

**CLIPIT** automatically transforms long-form video (podcasts, lectures, interviews, and streams) into high-retention, vertical **9:16 social clips** optimized for TikTok, Instagram Reels, and YouTube Shorts.

It executes a complete local-to-cloud autonomous pipeline: downloading media, generating millisecond-accurate word-level transcripts, detecting candidate moments, scoring segments with an LLM, deduplicating overlapping selections, and burning animated karaoke-style captions onto 9:16 vertical video in a single FFmpeg pass.

---

## Technical Workflow & Pipeline Architecture

```
                                      [ Input Media ]
                               (YouTube / Twitch / Local File)
                                             │
                                             ▼
                      ┌─────────────────────────────────────────────┐
                      │    Stage 1: Ingestion & Normalization       │
                      │    - Stream extraction via yt-dlp           │
                      │    - Resample audio to 16kHz mono PCM       │
                      └──────────────────────┬──────────────────────┘
                                             │
                                             ▼
                      ┌─────────────────────────────────────────────┐
                      │    Stage 2: Acoustic Speech Transcription   │
                      │    - faster-whisper on quantized CTranslate2│
                      │    - Token-level start & end timestamps     │
                      │    - Local JSON caching layer               │
                      └──────────────────────┬──────────────────────┘
                                             │
                                             ▼
                      ┌─────────────────────────────────────────────┐
                      │    Stage 3: Multi-Scale Window Generation   │
                      │    - Dynamic sliding windows (30s, 45s, 60s)│
                      │    - 15-second temporal stride              │
                      │    - Dense word timestamp mapping           │
                      └──────────────────────┬──────────────────────┘
                                             │
                                             ▼
                      ┌─────────────────────────────────────────────┐
                      │    Stage 4: LLM Scoring & Temporal NMS      │
                      │    - 4D evaluation (Hook, Context, Payoff)  │
                      │    - Compact minified prompt serialization  │
                      │    - Temporal Non-Maximum Suppression (IoU) │
                      └──────────────────────┬──────────────────────┘
                                             │
                                             ▼
                      ┌─────────────────────────────────────────────┐
                      │    Stage 5: Reframing & Caption Compositing │
                      │    - 9:16 center-crop (1080×1920)           │
                      │    - Advanced SubStation Alpha (.ass) script│
                      │    - Single-pass FFmpeg + libass render     │
                      └──────────────────────┬──────────────────────┘
                                             │
                                             ▼
                                  [ Final Outputs ]
                        ├── clip_01.mp4  (9:16 Vertical Video)
                        └── clip_01.txt  (Title + Caption Copy)
```

---

### Detailed Stage Breakdown

#### 1. Ingestion & Audio Normalization
- Extracts audio streams from local files or video URLs using `yt-dlp`.
- Resamples audio to **16kHz single-channel mono PCM**, matching the native transformer token sampling frequency and minimizing memory overhead.

#### 2. Sub-Second Acoustic Transcription
- Powered by `faster-whisper` running on the **CTranslate2** quantized inference engine.
- Employs **INT8/FP16 transformer quantization**, running **4x faster** than vanilla Whisper with a fraction of the RAM/VRAM footprint.
- Emits word-level timestamp boundaries (`start`, `end`, `word`) cached as `<video>.transcript.json` for O(1) re-runs.

#### 3. Multi-Scale Sliding Candidate Windows
- Generates overlapping multi-scale temporal windows across 3 standard short-form brackets: **30s, 45s, and 60s** with a **15s stride**.
- Captures continuous thought blocks without breaking speech cadence.

#### 4. Multidimensional LLM Scoring & Temporal NMS
- Minifies candidate transcripts into a zero-whitespace JSON matrix to maximize token efficiency (~30% prompt cost reduction).
- Evaluates segments across 4 key social dimensions:
  1. **Hook Strength (0–2.5)**: Immediate cognitive intrigue in the first 3 seconds.
  2. **Self-Containment (0–2.5)**: Coherent and understandable without exterior context.
  3. **Payoff & Climax (0–2.5)**: Meaningful punchline, emotional resonance, or insight.
  4. **Short-Form Fit (0–2.5)**: Overall narrative pacing.
- **Temporal Non-Maximum Suppression (NMS)**: Calculates temporal Intersection-over-Union (IoU) across candidates and drops overlapping redundant segments to pick the top N diverse moments.

#### 5. Intelligent 9:16 Reframing & Dynamic ASS Caption Burning
- Spatially scales and crops 16:9 landscape to **1080×1920 portrait**.
- Compiles an **Advanced SubStation Alpha (`.ass`) vector script** rendered via native C `libass`:
  - Millisecond-accurate word karaoke tags (`{\c&H0000FFFF&}` active yellow highlight).
  - Bold typography, 6px solid black outline, and bottom-center alignment.
- Encodes output via `libx264` + `aac` in a single high-speed FFmpeg pass.

---

## Cost Efficiency

Because transcription runs locally and prompt payloads are compressed, scoring an entire video costs fractions of a cent:

| Model | Cost per Run (~20 segments) | Runs per $1.00 Credit |
|---|---|---|
| **`openai/gpt-4o-mini` (Default)** | **~$0.0005** | **~2,000 complete runs** |
| `meta-llama/llama-3.3-70b-instruct:free` | **$0.00** | **Unlimited (100% Free)** |
| `deepseek/deepseek-chat` | **~$0.0004** | **~2,500 complete runs** |
| `openai/gpt-4o` | **~$0.0080** | **~125 runs** |
| `anthropic/claude-sonnet-4-5` | **~$0.0100** | **~100 runs** |

---

## Quickstart

### 1. Prerequisites
- **Python 3.12** (recommended for pre-built CTranslate2 and PyAV wheels)
- **FFmpeg 7.0+** with `libass` support on PATH (`winget install Gyan.FFmpeg` on Windows, or `brew install ffmpeg` on macOS)

### 2. Installation
```bash
git clone https://github.com/YASH-ai-bit/clipit.git
cd clipit

# Create & activate virtual environment
python -m venv venv312
venv312\Scripts\activate     # Windows
# source venv312/bin/activate # macOS/Linux

pip install -r requirements.txt
```

### 3. Configure API Key
Create a `.env` file in the root directory:
```env
OPENROUTER_API_KEY=sk-or-v1-your_openrouter_api_key_here
OPENROUTER_MODEL=openai/gpt-4o-mini
```

---

## Usage

### Option A: Ultra-Minimal Web UI
Launch the sleek, monochrome web dashboard:
```bash
python app.py
# or
python clipper.py --ui
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

---

### Option B: Headless CLI

```bash
# Clip from a YouTube URL (first 10 minutes, 3 clips)
python clipper.py --input "https://www.youtube.com/watch?v=UF8uR6Z6KLc" --num-clips 3 --max-duration 10

# Clip from a local video file
python clipper.py --input "downloads/podcast.mp4" --num-clips 5

# Use a 100% free model on OpenRouter
python clipper.py --input video.mp4 --llm-model "meta-llama/llama-3.3-70b-instruct:free"

# Verbose debug mode
python clipper.py --input video.mp4 --verbose
```

---

## Output Structure

```
output/
  ├── clip_01.mp4    # 1080x1920 vertical video with burned-in animated captions
  ├── clip_01.txt    # Suggested title, caption, timestamp, and AI reasoning
  ├── clip_02.mp4
  ├── clip_02.txt
  └── ...
```

Example `clip_01.txt`:
```yaml
Title: What Matters Most
Caption: In the face of death, only what truly matters remains. #Inspiration #LifeChoices
Timestamp: 570.1s - 599.4s
Score: 9.5/10
Reasoning: Culmination of profound insights about life; deeply impactful with strong takeaway.
```

---

## Testing

Run the full unit test suite:
```bash
pytest tests/ -v
```
All 57 unit tests pass with 0 external dependencies (testing URL detection, candidate window math, LLM JSON schema parsers, temporal overlap algorithms, and ASS subtitle generation).

---

## License
MIT License. Built for hackathons and production video pipelines.
