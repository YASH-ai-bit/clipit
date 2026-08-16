/**
 * app.js — Auto-Clipper Web UI Client Logic
 */

let activeMode = 'url';
let uploadedFilePath = null;
let currentJobId = null;
let pollInterval = null;

document.addEventListener('DOMContentLoaded', () => {
  loadClips();
  setupDragAndDrop();
});

// ---------------------------------------------------------------------------
// Input Mode Switching
// ---------------------------------------------------------------------------

function switchInputMode(mode) {
  activeMode = mode;
  const tabUrl = document.getElementById('tab-url');
  const tabFile = document.getElementById('tab-file');
  const urlPane = document.getElementById('url-input-container');
  const filePane = document.getElementById('file-input-container');

  if (mode === 'url') {
    tabUrl.classList.add('active');
    tabFile.classList.remove('active');
    urlPane.classList.remove('hidden');
    filePane.classList.add('hidden');
  } else {
    tabFile.classList.add('active');
    tabUrl.classList.remove('active');
    filePane.classList.remove('hidden');
    urlPane.classList.add('hidden');
  }
}

// ---------------------------------------------------------------------------
// File Upload & Drag and Drop
// ---------------------------------------------------------------------------

function setupDragAndDrop() {
  const dropzone = document.getElementById('dropzone');

  ['dragenter', 'dragover'].forEach(name => {
    dropzone.addEventListener(name, (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach(name => {
    dropzone.addEventListener(name, (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
    });
  });

  dropzone.addEventListener('drop', (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadFile(e.dataTransfer.files[0]);
    }
  });
}

function handleFileSelect(e) {
  if (e.target.files && e.target.files[0]) {
    uploadFile(e.target.files[0]);
  }
}

async function uploadFile(file) {
  const label = document.getElementById('dropzone-label');
  const hint = document.getElementById('dropzone-hint');

  label.textContent = `Uploading ${file.name}...`;
  hint.textContent = `File size: ${(file.size / (1024 * 1024)).toFixed(1)} MB`;

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/upload', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error('File upload failed');

    const data = await res.json();
    uploadedFilePath = data.local_path;

    label.textContent = `✓ Selected: ${file.name}`;
    hint.textContent = `Ready for clipping`;
    showToast(`Uploaded ${file.name} successfully`);
  } catch (err) {
    label.textContent = 'Upload failed. Click to try again.';
    hint.textContent = err.message;
    showToast(`Upload error: ${err.message}`, true);
  }
}

// ---------------------------------------------------------------------------
// Start Clipping Pipeline
// ---------------------------------------------------------------------------

async function startGeneration() {
  let source = '';

  if (activeMode === 'url') {
    source = document.getElementById('video-url').value.trim();
    if (!source) {
      showToast('Please enter a YouTube or Twitch URL', true);
      return;
    }
  } else {
    if (!uploadedFilePath) {
      showToast('Please select or upload a video file first', true);
      return;
    }
    source = uploadedFilePath;
  }

  const numClips = parseInt(document.getElementById('num-clips').value, 10);
  const maxDurationRaw = document.getElementById('max-duration').value;
  const maxDuration = maxDurationRaw ? parseFloat(maxDurationRaw) : null;
  const model = document.getElementById('whisper-model').value;
  const llmModel = document.getElementById('llm-model').value;

  const btn = document.getElementById('generate-btn');
  btn.disabled = true;
  btn.querySelector('.btn-text').textContent = 'Processing Pipeline...';

  // Show progress card
  const progressCard = document.getElementById('progress-card');
  progressCard.classList.remove('hidden');
  resetProgressUI();

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
      throw new Error(err.detail || 'Failed to start pipeline');
    }

    const data = await res.json();
    currentJobId = data.job_id;

    // Start polling
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollJobProgress, 1200);

  } catch (err) {
    showToast(`Error starting job: ${err.message}`, true);
    btn.disabled = false;
    btn.querySelector('.btn-text').textContent = 'Generate Vertical Clips';
  }
}

// ---------------------------------------------------------------------------
// Progress Polling
// ---------------------------------------------------------------------------

async function pollJobProgress() {
  if (!currentJobId) return;

  try {
    const res = await fetch(`/api/jobs/${currentJobId}`);
    if (!res.ok) return;

    const job = await res.json();
    updateProgressUI(job);

    if (job.status === 'completed') {
      clearInterval(pollInterval);
      pollInterval = null;
      document.getElementById('generate-btn').disabled = false;
      document.getElementById('generate-btn').querySelector('.btn-text').textContent = 'Generate Vertical Clips';
      showToast('🎉 All clips rendered successfully!');
      loadClips();
    } else if (job.status === 'failed') {
      clearInterval(pollInterval);
      pollInterval = null;
      document.getElementById('generate-btn').disabled = false;
      document.getElementById('generate-btn').querySelector('.btn-text').textContent = 'Generate Vertical Clips';
      showToast(`Pipeline failed: ${job.error}`, true);
    }
  } catch (err) {
    console.error('Polling error:', err);
  }
}

function resetProgressUI() {
  document.getElementById('progress-percentage').textContent = '5%';
  document.getElementById('progress-bar-fill').style.width = '5%';
  document.getElementById('progress-step-title').textContent = 'Initializing pipeline...';
  document.getElementById('progress-step-desc').textContent = 'Preparing video media';

  ['step-download', 'step-transcribe', 'step-candidates', 'step-score', 'step-render'].forEach(id => {
    const el = document.getElementById(id);
    el.classList.remove('active', 'done');
  });
  document.getElementById('step-download').classList.add('active');

  const term = document.getElementById('log-terminal');
  term.innerHTML = '<div class="log-line text-muted">[System] Pipeline launched. Processing...</div>';
}

function updateProgressUI(job) {
  document.getElementById('progress-percentage').textContent = `${job.progress_pct}%`;
  document.getElementById('progress-bar-fill').style.width = `${job.progress_pct}%`;
  document.getElementById('progress-step-title').textContent = job.current_step;

  // Step highlight logic
  const stepMap = [
    { id: 'step-download', min: 10, max: 29 },
    { id: 'step-transcribe', min: 30, max: 54 },
    { id: 'step-candidates', min: 55, max: 69 },
    { id: 'step-score', min: 70, max: 84 },
    { id: 'step-render', min: 85, max: 100 },
  ];

  stepMap.forEach(s => {
    const el = document.getElementById(s.id);
    if (job.progress_pct >= s.max || (job.status === 'completed' && s.min <= 100)) {
      el.classList.remove('active');
      el.classList.add('done');
    } else if (job.progress_pct >= s.min) {
      el.classList.add('active');
      el.classList.remove('done');
    } else {
      el.classList.remove('active', 'done');
    }
  });

  // Terminal log stream
  const term = document.getElementById('log-terminal');
  if (job.logs && job.logs.length > 0) {
    term.innerHTML = job.logs.map(line => `<div class="log-line">${escapeHtml(line)}</div>`).join('');
    term.scrollTop = term.scrollHeight;
  }
}

// ---------------------------------------------------------------------------
// Load & Render Clips Gallery
// ---------------------------------------------------------------------------

async function loadClips(manualRefresh = false) {
  try {
    const res = await fetch('/api/clips');
    if (!res.ok) return;

    const data = await res.json();
    const grid = document.getElementById('clips-grid');
    const empty = document.getElementById('empty-gallery');

    if (!data.clips || data.clips.length === 0) {
      grid.innerHTML = '';
      empty.classList.remove('hidden');
      return;
    }

    empty.classList.add('hidden');
    grid.innerHTML = data.clips.map(clip => renderClipCard(clip)).join('');

    if (manualRefresh) {
      showToast(`Loaded ${data.clips.length} clip(s)`);
    }
  } catch (err) {
    console.error('Error loading clips:', err);
  }
}

function renderClipCard(clip) {
  const scoreFormatted = clip.score ? `${clip.score.toFixed(1)}/10` : 'AI Pick';
  const timestampFormatted = clip.timestamp || 'Clip';
  const sizeMb = clip.size_mb ? `${clip.size_mb} MB` : '';

  return `
    <div class="clip-card" id="card-${clip.id}">
      <div class="clip-video-wrapper">
        <video class="clip-video" controls playsinline preload="metadata">
          <source src="${clip.video_url}" type="video/mp4">
          Your browser does not support the video tag.
        </video>
      </div>
      <div class="clip-body">
        <div class="clip-meta-row">
          <span class="badge badge-score">★ ${scoreFormatted}</span>
          <span class="badge badge-pill">${timestampFormatted}</span>
        </div>
        <h4 class="clip-title">${escapeHtml(clip.title || clip.filename)}</h4>
        ${clip.caption ? `
          <div class="clip-caption-box">
            <p>${escapeHtml(clip.caption)}</p>
          </div>
        ` : ''}
        ${clip.reasoning ? `
          <p class="clip-reasoning">${escapeHtml(clip.reasoning)}</p>
        ` : ''}
        <div class="clip-actions">
          <button class="btn-action btn-accent" onclick="copyCaption('${escapeJs(clip.caption || clip.title)}')">
            <span>📋</span> Copy Caption
          </button>
          <a class="btn-action" href="${clip.video_url}" download="${clip.filename}">
            <span>⬇</span> Download ${sizeMb}
          </a>
        </div>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function copyCaption(text) {
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    showToast('Copied caption to clipboard! 📋');
  }).catch(() => {
    showToast('Failed to copy', true);
  });
}

function showToast(message, isError = false) {
  const toast = document.getElementById('toast');
  const msgEl = document.getElementById('toast-message');

  msgEl.textContent = message;
  toast.style.borderColor = isError ? 'var(--accent-danger)' : 'var(--accent-primary)';
  toast.classList.remove('hidden');

  setTimeout(() => {
    toast.classList.add('hidden');
  }, 3500);
}

function escapeHtml(text) {
  if (!text) return '';
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function escapeJs(text) {
  if (!text) return '';
  return text.replace(/'/g, "\\'").replace(/"/g, '\\"').replace(/\n/g, ' ');
}
