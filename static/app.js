/**
 * app.js — CLIPIT Ultra-Minimal Client Controller
 */

let activeMode = 'url';
let uploadedFilePath = null;
let currentJobId = null;
let pollTimer = null;

document.addEventListener('DOMContentLoaded', () => {
  fetchClips();
  initDropzone();
});

// ---------------------------------------------------------------------------
// Mode Toggle (URL vs Local File)
// ---------------------------------------------------------------------------

function switchMode(mode) {
  activeMode = mode;
  const tabUrl = document.getElementById('tab-url');
  const tabFile = document.getElementById('tab-file');
  const urlBox = document.getElementById('url-box');
  const fileBox = document.getElementById('file-box');

  if (mode === 'url') {
    tabUrl.classList.add('active');
    tabFile.classList.remove('active');
    urlBox.classList.remove('hidden');
    fileBox.classList.add('hidden');
  } else {
    tabFile.classList.add('active');
    tabUrl.classList.remove('active');
    fileBox.classList.remove('hidden');
    urlBox.classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// File Dropzone
// ---------------------------------------------------------------------------

function initDropzone() {
  const dropzone = document.getElementById('dropzone');

  ['dragenter', 'dragover'].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
    });
  });

  dropzone.addEventListener('drop', (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadVideoFile(e.dataTransfer.files[0]);
    }
  });
}

function handleFile(e) {
  if (e.target.files && e.target.files[0]) {
    uploadVideoFile(e.target.files[0]);
  }
}

async function uploadVideoFile(file) {
  const textEl = document.getElementById('dropzone-text');
  textEl.textContent = `UPLOADING: ${file.name}...`;

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error('Upload failed');

    const data = await res.json();
    uploadedFilePath = data.local_path;

    textEl.textContent = `READY: ${file.name}`;
    notify('FILE READY');
  } catch (err) {
    textEl.textContent = 'UPLOAD FAILED. RETRY.';
    notify(`ERROR: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// Trigger Clipping Pipeline
// ---------------------------------------------------------------------------

async function startClipping() {
  let source = '';

  if (activeMode === 'url') {
    source = document.getElementById('video-url').value.trim();
    if (!source) {
      notify('ENTER A VALID URL');
      return;
    }
  } else {
    if (!uploadedFilePath) {
      notify('SELECT A VIDEO FILE');
      return;
    }
    source = uploadedFilePath;
  }

  const numClips = parseInt(document.getElementById('num-clips').value, 10);
  const maxDurationVal = document.getElementById('max-duration').value;
  const maxDuration = maxDurationVal ? parseFloat(maxDurationVal) : null;
  const model = document.getElementById('whisper-model').value;
  const llmModel = document.getElementById('llm-model').value;

  const btn = document.getElementById('clip-btn');
  const btnText = document.getElementById('clip-btn-text');
  btn.disabled = true;
  btnText.textContent = 'PROCESSING...';

  // Show progress panel
  const progressPanel = document.getElementById('progress-panel');
  progressPanel.classList.remove('hidden');
  resetProgressTrack();

  try {
    const res = await fetch('/api/clip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source,
        num_clips: numClips,
        max_duration: maxDuration,
        model,
        llm_model: llmModel,
      }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to start');
    }

    const data = await res.json();
    currentJobId = data.job_id;

    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollJob, 1000);

  } catch (err) {
    notify(`ERROR: ${err.message}`);
    btn.disabled = false;
    btnText.textContent = 'CLIP';
  }
}

// ---------------------------------------------------------------------------
// Polling Pipeline Progress
// ---------------------------------------------------------------------------

async function pollJob() {
  if (!currentJobId) return;

  try {
    const res = await fetch(`/api/jobs/${currentJobId}`);
    if (!res.ok) return;

    const job = await res.json();
    updateProgressState(job);

    if (job.status === 'completed') {
      clearInterval(pollTimer);
      pollTimer = null;
      document.getElementById('clip-btn').disabled = false;
      document.getElementById('clip-btn-text').textContent = 'CLIP';
      notify('CLIPS GENERATED');
      fetchClips();
    } else if (job.status === 'failed') {
      clearInterval(pollTimer);
      pollTimer = null;
      document.getElementById('clip-btn').disabled = false;
      document.getElementById('clip-btn-text').textContent = 'CLIP';
      notify(`FAILED: ${job.error}`);
    }
  } catch (err) {
    console.error('Poll error:', err);
  }
}

function resetProgressTrack() {
  document.getElementById('progress-val').textContent = '5%';
  document.getElementById('meter-fill').style.width = '5%';
  document.getElementById('progress-step-text').textContent = 'INITIALIZING';

  ['p-prep', 'p-transcribe', 'p-moments', 'p-score', 'p-render'].forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('active', 'done');
  });
  document.getElementById('p-prep').classList.add('active');

  const logBox = document.getElementById('log-box');
  logBox.innerHTML = '<div class="log-row text-dim">[00:00:00] Initializing pipeline...</div>';
}

function updateProgressState(job) {
  document.getElementById('progress-val').textContent = `${job.progress_pct}%`;
  document.getElementById('meter-fill').style.width = `${job.progress_pct}%`;
  document.getElementById('progress-step-text').textContent = (job.current_step || 'PROCESSING').toUpperCase();

  const nodes = [
    { id: 'p-prep', min: 10, max: 29 },
    { id: 'p-transcribe', min: 30, max: 54 },
    { id: 'p-moments', min: 55, max: 69 },
    { id: 'p-score', min: 70, max: 84 },
    { id: 'p-render', min: 85, max: 100 },
  ];

  nodes.forEach(n => {
    const el = document.getElementById(n.id);
    if (job.progress_pct >= n.max || (job.status === 'completed' && n.min <= 100)) {
      el.classList.remove('active');
      el.classList.add('done');
    } else if (job.progress_pct >= n.min) {
      el.classList.add('active');
      el.classList.remove('done');
    } else {
      el.classList.remove('active', 'done');
    }
  });

  const logBox = document.getElementById('log-box');
  if (job.logs && job.logs.length > 0) {
    logBox.innerHTML = job.logs.map(line => `<div class="log-row">${escapeHtml(line)}</div>`).join('');
    logBox.scrollTop = logBox.scrollHeight;
  }
}

// ---------------------------------------------------------------------------
// Gallery Management
// ---------------------------------------------------------------------------

async function fetchClips(manual = false) {
  try {
    const res = await fetch('/api/clips');
    if (!res.ok) return;

    const data = await res.json();
    const grid = document.getElementById('clips-grid');
    const empty = document.getElementById('empty-state');

    if (!data.clips || data.clips.length === 0) {
      grid.innerHTML = '';
      empty.classList.remove('hidden');
      return;
    }

    empty.classList.add('hidden');
    grid.innerHTML = data.clips.map(clip => buildClipCard(clip)).join('');

    if (manual) {
      notify(`LOADED ${data.clips.length} CLIPS`);
    }
  } catch (err) {
    console.error('Fetch clips error:', err);
  }
}

function buildClipCard(clip) {
  const scoreBadge = clip.score ? `★ ${clip.score.toFixed(1)}` : 'AI PICK';
  const timeBadge = clip.timestamp || 'CLIP';
  const sizeText = clip.size_mb ? `${clip.size_mb}MB` : 'MP4';

  return `
    <div class="clip-card" id="card-${clip.id}">
      <div class="video-frame">
        <video class="video-player" controls playsinline preload="metadata">
          <source src="${clip.video_url}" type="video/mp4">
        </video>
      </div>
      <div class="clip-content">
        <div class="clip-tags">
          <span class="tag-score">${scoreBadge}</span>
          <span class="tag-time">${timeBadge}</span>
        </div>
        <div class="clip-heading">${escapeHtml(clip.title || clip.filename)}</div>
        ${clip.caption ? `
          <div class="caption-wrapper">
            <p>${escapeHtml(clip.caption)}</p>
          </div>
        ` : ''}
        <div class="card-actions">
          <button class="action-btn btn-copy" onclick="copyText('${escapeJs(clip.caption || clip.title)}')">
            COPY
          </button>
          <a class="action-btn" href="${clip.video_url}" download="${clip.filename}">
            GET ${sizeText}
          </a>
        </div>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function copyText(text) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    notify('COPIED TO CLIPBOARD');
  }).catch(() => {
    notify('COPY FAILED');
  });
}

function notify(msg) {
  const toast = document.getElementById('toast');
  const text = document.getElementById('toast-text');

  text.textContent = msg;
  toast.classList.remove('hidden');

  setTimeout(() => {
    toast.classList.add('hidden');
  }, 2500);
}

function escapeHtml(str) {
  if (!str) return '';
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeJs(str) {
  if (!str) return '';
  return str.replace(/'/g, "\\'").replace(/"/g, '\\"').replace(/\n/g, ' ');
}
