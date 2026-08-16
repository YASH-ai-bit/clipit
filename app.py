"""
app.py — Ultra-minimal FastAPI backend for the Auto-Clipper Web UI.

Features:
- REST API for queuing clipping jobs
- Real-time job status & log streaming
- Clip gallery endpoint with parsed metadata (titles, captions, scores)
- File upload support
- Serves static assets and rendered video output
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Load environment
from clipper import _load_dotenv
_load_dotenv()

from input_handler import get_video_path, is_url
from transcribe import transcribe
from score import generate_candidates, score_candidates, select_clips, DEFAULT_OPENROUTER_MODEL
from render import check_ffmpeg, render_all_clips, ScoredClip

# App setup
app = FastAPI(title="Auto-Clipper UI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure directories exist
STATIC_DIR = Path(__file__).parent / "static"
OUTPUT_DIR = Path(__file__).parent / "output"
DOWNLOADS_DIR = Path(__file__).parent / "downloads"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Mount outputs & static files
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

class JobState:
    def __init__(self, job_id: str, source: str):
        self.job_id = job_id
        self.source = source
        self.status = "queued"  # queued, running, completed, failed
        self.current_step = "Initializing..."
        self.progress_pct = 5
        self.logs: List[str] = []
        self.error: Optional[str] = None
        self.created_at = time.time()
        self.completed_at: Optional[float] = None
        self.clips: List[Dict[str, Any]] = []

    def log(self, message: str, step: Optional[str] = None, progress: Optional[int] = None):
        timestamp = time.strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        if step:
            self.current_step = step
        if progress is not None:
            self.progress_pct = progress

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source": self.source,
            "status": self.status,
            "current_step": self.current_step,
            "progress_pct": self.progress_pct,
            "logs": self.logs,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "clips": self.clips,
        }


JOBS: Dict[str, JobState] = {}


# ---------------------------------------------------------------------------
# Metadata Parser for Output Clips
# ---------------------------------------------------------------------------

def parse_clip_txt(txt_file: Path) -> Dict[str, Any]:
    """Parse title, caption, timestamp, and score from generated .txt file."""
    data = {
        "title": "Social Clip",
        "caption": "",
        "timestamp": "",
        "score": 0.0,
        "reasoning": "",
    }
    if not txt_file.exists():
        return data

    try:
        content = txt_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith("Title:"):
                data["title"] = line.replace("Title:", "").strip()
            elif line.startswith("Caption:"):
                data["caption"] = line.replace("Caption:", "").strip()
            elif line.startswith("Timestamp:"):
                data["timestamp"] = line.replace("Timestamp:", "").strip()
            elif line.startswith("Score:"):
                score_str = line.replace("Score:", "").split("/")[0].strip()
                try:
                    data["score"] = float(score_str)
                except ValueError:
                    pass
            elif line.startswith("Reasoning:"):
                data["reasoning"] = line.replace("Reasoning:", "").strip()
    except Exception as e:
        print(f"Error parsing {txt_file}: {e}")

    return data


def list_existing_clips() -> List[Dict[str, Any]]:
    """Scan output/ directory and return list of available clips."""
    clips = []
    if not OUTPUT_DIR.exists():
        return clips

    mp4_files = sorted(OUTPUT_DIR.glob("clip_*.mp4"), reverse=True)
    for mp4 in mp4_files:
        txt = mp4.with_suffix(".txt")
        meta = parse_clip_txt(txt)
        stat = mp4.stat()
        clips.append({
            "id": mp4.stem,
            "filename": mp4.name,
            "video_url": f"/output/{mp4.name}",
            "size_mb": round(stat.st_size / (1024 * 1024), 1),
            "modified_time": stat.st_mtime,
            **meta,
        })
    return clips


# ---------------------------------------------------------------------------
# Pipeline Execution Task
# ---------------------------------------------------------------------------

def execute_pipeline(
    job: JobState,
    source: str,
    num_clips: int,
    max_duration: Optional[float],
    model: str,
    llm_model: Optional[str],
    no_cache: bool,
):
    job.status = "running"
    try:
        # Pre-flight check
        check_ffmpeg()

        # Step 1: Input handling
        job.log("Preparing video...", step="Downloading / Validating Source", progress=10)
        max_sec = int(max_duration * 60) if max_duration else None

        if is_url(source):
            job.log(f"Downloading from URL: {source}", step="Downloading Media", progress=15)

        video_path = get_video_path(source, max_duration_seconds=max_sec)
        job.log(f"Video ready: {video_path.name}", progress=25)

        # Step 2: Transcription
        job.log(f"Transcribing audio with Whisper ({model})...", step="Transcribing Audio", progress=30)
        words = transcribe(
            video_path,
            model_size=model,
            use_cache=not no_cache,
        )

        if not words:
            raise RuntimeError("Transcription produced no words. The audio may be silent.")

        job.log(f"Transcribed {len(words)} words with word-level timestamps.", progress=50)

        # Trim words if needed
        if max_sec is not None:
            words = [w for w in words if w["start"] < max_sec]

        # Step 3: Candidate Generation
        job.log("Generating candidate clip windows...", step="Finding Viral Moments", progress=55)
        candidates = generate_candidates(words)
        if not candidates:
            raise RuntimeError("No candidate moments found in the transcript.")

        job.log(f"Generated {len(candidates)} candidate moments.", progress=65)

        # Step 4: LLM Scoring
        chosen_llm = llm_model or os.environ.get("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        job.log(f"Scoring candidates using AI ({chosen_llm})...", step="AI Semantic Scoring", progress=70)
        scored = score_candidates(candidates, model=chosen_llm)

        # Step 5: Selection
        selected = select_clips(scored, n=num_clips)
        if not selected:
            raise RuntimeError("No clips could be selected after deduplication.")

        job.log(f"Selected top {len(selected)} non-overlapping clips.", progress=80)

        # Step 6: Rendering
        job.log("Rendering 9:16 vertical videos with burned-in animated captions...", step="Rendering Vertical Video", progress=85)
        rendered = render_all_clips(
            source_video=video_path,
            clips=selected,
            output_dir=OUTPUT_DIR,
        )

        # Populate output clips in job state
        for mp4, txt in rendered:
            meta = parse_clip_txt(txt)
            stat = mp4.stat()
            job.clips.append({
                "id": mp4.stem,
                "filename": mp4.name,
                "video_url": f"/output/{mp4.name}",
                "size_mb": round(stat.st_size / (1024 * 1024), 1),
                "modified_time": stat.st_mtime,
                **meta,
            })

        job.log("All clips rendered successfully!", step="Completed", progress=100)
        job.status = "completed"
        job.completed_at = time.time()

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        job.log(f"Error: {e}", step="Failed", progress=100)
        print(f"[Pipeline Error] {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

class ClipRequest(BaseModel):
    source: str
    num_clips: int = 3
    max_duration: Optional[float] = 10.0
    model: str = "base"
    llm_model: Optional[str] = None
    no_cache: bool = False


@app.get("/")
def get_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend index.html not found.")
    return FileResponse(str(index_file))


@app.get("/api/clips")
def get_clips():
    """Return all previously generated clips."""
    return {"clips": list_existing_clips()}


@app.post("/api/clip")
def start_clip_job(req: ClipRequest, background_tasks: BackgroundTasks):
    """Start a new video clipping pipeline in the background."""
    job_id = str(uuid.uuid4())[:8]
    job = JobState(job_id=job_id, source=req.source)
    JOBS[job_id] = job

    background_tasks.add_task(
        execute_pipeline,
        job=job,
        source=req.source,
        num_clips=req.num_clips,
        max_duration=req.max_duration,
        model=req.model,
        llm_model=req.llm_model,
        no_cache=req.no_cache,
    )

    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    """Poll status of a clipping job."""
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return JOBS[job_id].to_dict()


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Upload a local video file to downloads/ directory."""
    filename = Path(file.filename or "uploaded_video.mp4").name
    dest = DOWNLOADS_DIR / filename

    with open(dest, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"filename": filename, "local_path": str(dest.resolve())}


# ---------------------------------------------------------------------------
# CLI / Direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print("\n=======================================================")
    print("  🎬 Auto-Clipper Web UI starting on http://localhost:8000")
    print("=======================================================\n")
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
