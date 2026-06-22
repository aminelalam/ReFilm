const videoForm = document.querySelector("#video-form");
const imageForm = document.querySelector("#image-form");
const artForm = document.querySelector("#art-form");
const videoResult = document.querySelector("#video-result");
const imageResult = document.querySelector("#image-result");
const artResult = document.querySelector("#art-result");
const jobs = document.querySelector("#jobs");
const refresh = document.querySelector("#refresh");
const sidebarRefresh = document.querySelector("#sidebar-refresh");
const activeJobsCount = document.querySelector("#active-jobs-count");
const autoIndicator = document.querySelector("#auto-indicator");

const state = {
  filter: "all",
  jobs: [],
  audits: [],
  expandedJobs: new Set(),
  detailsCache: new Map(),
  detailsPromises: new Map(),
};

initBootScreen();
initFileDrops();
initPills();
initFilters();
initSidebar();
initCursor();
bindForms();

refresh?.addEventListener("click", () => loadJobs({ manual: true }));
sidebarRefresh?.addEventListener("click", () => loadJobs({ manual: true }));
window.setInterval(() => {
  if (hasVisibleExpandedDetails()) return;
  loadJobs({ auto: true });
}, 5000);
loadJobs();

function initBootScreen() {
  const bootScreen = document.querySelector("#boot-screen");
  const bootLines = document.querySelector("#boot-lines");
  const bootBar = document.querySelector("#boot-bar");
  const bootPercent = document.querySelector("#boot-percent");
  const appContainer = document.querySelector("#app-container");
  if (!bootScreen || !bootLines || !bootBar || !bootPercent) return;

  const lines = [
    "LIVING ARCHIVE - Digital Restoration System",
    "Version 3.2.1 - Build 2026.06.04",
    "Initializing AI modules...",
    "Loading profiles: FAST / QUALITY / REAL-ESRGAN",
    "Mounting job registry...",
    "System ready.",
  ];

  lines.forEach((line, index) => {
    window.setTimeout(() => {
      const row = document.createElement("p");
      row.textContent = `> ${line}`;
      bootLines.appendChild(row);
    }, 180 + index * 260);
  });

  let percent = 0;
  const totalBlocks = 16;
  const interval = window.setInterval(() => {
    percent = Math.min(100, percent + 4);
    const filled = Math.round((percent / 100) * totalBlocks);
    bootBar.textContent = `${"\u2588".repeat(filled)}${"\u2591".repeat(totalBlocks - filled)}`;
    bootPercent.textContent = `${String(percent).padStart(2, "0")}%`;

    if (percent >= 100) {
      window.clearInterval(interval);
      window.setTimeout(() => {
        bootScreen.classList.add("closing");
        window.setTimeout(() => {
          bootScreen.remove();
          document.body.classList.remove("is-booting");
          appContainer?.classList.add("boot-complete");
        }, 430);
      }, 420);
    }
  }, 86);
}

function initFileDrops() {
  document.querySelectorAll('.file-drop input[type="file"]').forEach((input) => {
    input.addEventListener("change", () => updateFileDrop(input));
  });
}

function updateFileDrop(input) {
  const drop = input.closest(".file-drop");
  const text = drop?.querySelector(".file-drop-text");
  if (!drop || !text) return;

  if (input.files.length > 0) {
    text.textContent = input.files[0].name;
    drop.classList.add("has-file");
    return;
  }

  text.textContent = text.dataset.default || "Select file";
  drop.classList.remove("has-file");
}

function resetFileDrops(form) {
  form.querySelectorAll('.file-drop input[type="file"]').forEach((input) => {
    input.value = "";
    updateFileDrop(input);
  });
}

function initPills() {
  document.querySelectorAll(".pill input").forEach((input) => {
    input.addEventListener("change", () => updatePillGroup(input.name, input.form));
  });
  document.querySelectorAll("form").forEach(updatePillsInForm);
}

function updatePillsInForm(form) {
  if (!form) return;
  const names = new Set([...form.querySelectorAll(".pill input")].map((input) => input.name));
  names.forEach((name) => updatePillGroup(name, form));
}

function updatePillGroup(name, form) {
  if (!name || !form) return;
  form.querySelectorAll(`.pill input[name="${name}"]`).forEach((input) => {
    input.closest(".pill")?.classList.toggle("selected", input.checked);
  });
}

function initFilters() {
  document.querySelectorAll(".job-filter").forEach((button) => {
    button.addEventListener("click", () => {
      state.filter = button.dataset.filter || "all";
      document.querySelectorAll(".job-filter").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      renderJobs();
    });
  });
}

function initSidebar() {
  document.querySelectorAll(".sidebar-item[href]").forEach((item) => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".sidebar-item[href]").forEach((link) => link.classList.remove("active"));
      item.classList.add("active");
    });
  });
}

function initCursor() {
  const cursor = document.querySelector("#custom-cursor");
  if (!cursor || !window.matchMedia("(pointer: fine)").matches) return;

  window.addEventListener("mousemove", (event) => {
    cursor.style.left = `${event.clientX}px`;
    cursor.style.top = `${event.clientY}px`;
  });

  document.addEventListener("mouseover", (event) => {
    const target = event.target;
    const isInteractive = target.closest("a, button, label, input, video, .file-drop, .pill");
    cursor.classList.toggle("hover", Boolean(isInteractive));
  });
}

function bindForms() {
  videoForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    setResult(videoResult, "Uploading video to the restoration registry...");

    const formData = new FormData(videoForm);
    const colorMode = videoForm.color_mode.value;
    formData.set("color_mode", colorMode);
    formData.set("colorize", colorMode === "none" ? "false" : "true");

    try {
      const response = await fetch("/api/videos", { method: "POST", body: formData });
      const payload = await response.json();
      setResult(
        videoResult,
        response.ok ? `Job created: ${payload.job_id}` : payload.detail || "The job could not be created.",
        !response.ok,
      );
      if (response.ok) {
        videoForm.reset();
        resetFileDrops(videoForm);
        updatePillsInForm(videoForm);
        loadJobs({ manual: true });
      }
    } catch (error) {
      setResult(videoResult, `Network error: ${error.message}`, true);
    }
  });

  imageForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    setResult(imageResult, "Uploading photo to the restoration registry...");

    try {
      const response = await fetch("/api/images", {
        method: "POST",
        body: new FormData(imageForm),
      });
      const payload = await response.json();
      setResult(
        imageResult,
        response.ok ? `Job created: ${payload.job_id}` : payload.detail || "The job could not be created.",
        !response.ok,
      );
      if (response.ok) {
        imageForm.reset();
        resetFileDrops(imageForm);
        updatePillsInForm(imageForm);
        loadJobs({ manual: true });
      }
    } catch (error) {
      setResult(imageResult, `Network error: ${error.message}`, true);
    }
  });

  artForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    setResult(artResult, "Comparing images and calculating the difference heatmap...");

    const referenceName = artForm.reference.files[0]?.name || "historical reference";
    const currentName = artForm.current.files[0]?.name || "current capture";

    try {
      const response = await fetch("/api/art-audits", {
        method: "POST",
        body: new FormData(artForm),
      });
      const payload = await response.json();
      if (!response.ok) {
        setResult(artResult, payload.detail || "The audit could not be completed.", true);
        return;
      }

      const heatmapUrl = `/api/art-audits/${encodeURIComponent(payload.id)}/heatmap`;
      artResult.classList.remove("error");
      artResult.innerHTML = `
        <span class="risk-badge ${riskClass(payload.risk)}">${riskLabel(payload.risk)}</span>
        <span>SSIM: ${formatNumber(payload.ssim)}</span>
        <a href="${heatmapUrl}">Difference heatmap</a>
      `;

      state.audits.unshift({
        id: payload.id,
        filename: `${referenceName} / ${currentName}`,
        media_type: "audit",
        status: "restored",
        risk: payload.risk,
        ssim: payload.ssim,
        heatmapUrl,
      });

      artForm.reset();
      resetFileDrops(artForm);
      renderJobs();
    } catch (error) {
      setResult(artResult, `Network error: ${error.message}`, true);
    }
  });
}

function setResult(element, message, isError = false) {
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("error", isError);
}

async function loadJobs(options = {}) {
  if (!jobs) return;
  pulseRefresh(options);
  refresh?.setAttribute("disabled", "disabled");

  try {
    const response = await fetch("/api/jobs");
    const payload = await response.json();
    state.jobs = Array.isArray(payload) ? payload : [];
    renderJobs();
  } catch (error) {
    jobs.innerHTML = emptyState(`ERROR: ${escapeHtml(error.message)}`);
  } finally {
    refresh?.removeAttribute("disabled");
  }
}

function pulseRefresh(options) {
  if (!jobs) return;
  jobs.classList.add("is-refreshing");
  window.setTimeout(() => jobs.classList.remove("is-refreshing"), 460);

  if (options.auto && autoIndicator) {
    autoIndicator.classList.add("flash");
    window.setTimeout(() => autoIndicator.classList.remove("flash"), 520);
  }
}

function renderJobs() {
  if (!jobs) return;
  preserveOpenDetails();

  const allJobs = [...state.audits, ...state.jobs];
  const filteredJobs = allJobs.filter(matchesFilter);
  updateActiveCount(allJobs);

  jobs.innerHTML = "";
  if (!filteredJobs.length) {
    jobs.innerHTML = emptyState(emptyMessage());
    return;
  }

  filteredJobs.forEach((job) => {
    jobs.appendChild(job.media_type === "audit" ? auditCard(job) : restorationJobCard(job));
  });
}

function preserveOpenDetails() {
  jobs.querySelectorAll(".job-card").forEach((card) => {
    const key = card.dataset.jobId;
    const details = card.querySelector(".job-details");
    if (!key || !details || details.hidden) return;

    state.expandedJobs.add(key);
    if (details.dataset.loaded === "true" && details.innerHTML.trim()) {
      state.detailsCache.set(key, details.innerHTML);
    }
  });
}

function hasVisibleExpandedDetails() {
  return Boolean(jobs?.querySelector(".job-details:not([hidden])"));
}

function restorationJobCard(job) {
  const mediaType = job.media_type || "video";
  const progress = progressInfo(job);
  const item = document.createElement("article");
  item.className = `job-card ${statusClass(job.status)}`;
  item.dataset.jobId = job.id || "";
  item.dataset.mediaType = mediaType;
  item.innerHTML = `
    <div class="job-title">
      <strong class="job-filename">${escapeHtml(job.filename)}</strong>
      <span class="status-badge ${statusClass(job.status)}">${statusLabel(job.status)}</span>
    </div>
    <div class="job-meta">${job.error ? escapeHtml(job.error) : jobSummary(job, mediaType)}</div>
    <div class="step-meter">
      <span class="step-blocks">[${progress.blocks}]</span>
      <span>${progress.label}</span>
      <span>${progress.currentStep}</span>
    </div>
    <div class="progress-track" aria-label="Progress ${progress.percent}%">
      <div class="progress-bar ${progress.percent >= 100 ? "complete" : ""}" style="width: ${progress.percent}%"></div>
    </div>
    <div class="links">${jobLinks(job, mediaType)}</div>
    ${job.status === "restored" ? '<button class="metrics-toggle" type="button"><span class="chevron">\u25b8</span> View metrics and comparison</button>' : ""}
    <div class="job-details" hidden></div>
  `;

  const button = item.querySelector(".metrics-toggle");
  if (button) {
    button.addEventListener("click", () => loadJobDetails(job.id, item, button, mediaType));
    restoreExpandedDetails(job.id, item, button, mediaType);
  }
  return item;
}

function restoreExpandedDetails(jobId, item, button, mediaType) {
  const key = String(jobId);
  if (!state.expandedJobs.has(key)) return;

  const details = item.querySelector(".job-details");
  const cachedHtml = state.detailsCache.get(key);
  if (!details || !cachedHtml) {
    loadJobDetails(jobId, item, button, mediaType, { restore: true });
    return;
  }

  details.innerHTML = cachedHtml;
  details.dataset.loaded = "true";
  details.hidden = false;
  button.classList.add("open");
  button.innerHTML = '<span class="chevron">\u25b8</span> Hide metrics';
}

function auditCard(job) {
  const item = document.createElement("article");
  item.className = "job-card done";
  item.dataset.jobId = job.id || "";
  item.innerHTML = `
    <div class="job-title">
      <strong class="job-filename">${escapeHtml(job.filename)}</strong>
      <span class="status-badge done">AUDIT</span>
    </div>
    <div class="job-meta">AUDIT \u00b7 HISTORICAL COMPARISON \u00b7 HEATMAP</div>
    <div class="audit-readout">
      <span class="risk-badge ${riskClass(job.risk)}">${riskLabel(job.risk)}</span>
      <span class="metric-chip">SSIM: ${formatNumber(job.ssim)}</span>
    </div>
    <div class="progress-track" aria-label="Progress 100%">
      <div class="progress-bar complete" style="width: 100%"></div>
    </div>
    <div class="links">
      <a class="action-link primary-action" href="${job.heatmapUrl}">\u2193 HEATMAP</a>
    </div>
  `;
  return item;
}

function matchesFilter(job) {
  const mediaType = job.media_type || "video";
  const status = statusClass(job.status);
  if (state.filter === "all") return true;
  if (state.filter === "done") return status === "done";
  if (state.filter === "processing") return status === "processing" || status === "queued";
  return mediaType === state.filter;
}

function updateActiveCount(allJobs) {
  if (!activeJobsCount) return;
  const activeCount = allJobs.filter((job) => {
    const status = statusClass(job.status);
    return status === "processing" || status === "queued";
  }).length;
  activeJobsCount.textContent = `${String(activeCount).padStart(2, "0")} ${activeCount === 1 ? "ACTIVE" : "ACTIVES"}`;
}

function emptyState(message) {
  return `<p class="jobs-empty"><strong>READY</strong>${message}</p>`;
}

function emptyMessage() {
  const labels = {
    all: "System ready. No jobs are registered.",
    done: "No completed jobs in the current registry.",
    processing: "No jobs are currently processing.",
    video: "No video jobs in the current registry.",
    image: "No photo jobs in the current registry.",
    audit: "No audits in this session.",
  };
  return labels[state.filter] || labels.all;
}

function jobSummary(job, mediaType) {
  const colorMode = job.color_mode || (Number(job.colorize) ? "ai_natural" : "none");
  const profile = job.processing_profile || (mediaType === "image" ? "image_quality" : "quality");
  return `${mediaLabel(mediaType)} \u00b7 ${profileLabel(profile)} \u00b7 ${colorModeLabel(colorMode)}`;
}

function jobLinks(job, mediaType) {
  const id = encodeURIComponent(job.id);
  if (mediaType === "image") {
    return `
      <a class="action-link" href="/api/images/${id}/download/original">\u2193 ORIGINAL</a>
      ${job.status === "restored" ? `<a class="action-link" href="/api/images/${id}/download/restored">\u2193 RESTORED</a>` : ""}
      ${job.status === "restored" && job.color_mode === "ai_natural" ? `<a class="action-link" href="/api/images/${id}/download/colorized">\u2193 COLORIZED</a>` : ""}
      ${job.status === "restored" ? `<a class="action-link primary-action" href="/api/images/${id}/download/final">\u2193 FINAL</a>` : ""}
      ${job.status === "restored" ? `<a class="action-link" href="/api/images/${id}/download/comparison">\u2193 COMPARISON</a>` : ""}
      ${job.status === "restored" ? `<a class="action-link" href="/api/images/${id}/download/audit">{ } JSON</a>` : ""}
    `;
  }
  return `
    <a class="action-link" href="/api/jobs/${id}/download/original">\u2193 ORIGINAL</a>
    ${job.status === "restored" ? `<a class="action-link primary-action" href="/api/jobs/${id}/download/final">\u2193 RESTORED</a>` : ""}
    ${job.status === "restored" ? `<a class="action-link" href="/api/jobs/${id}/download/comparison">\u2193 COMPARISON</a>` : ""}
    ${job.status === "restored" ? `<a class="action-link" href="/api/jobs/${id}/download/audit">{ } JSON</a>` : ""}
  `;
}

async function loadJobDetails(jobId, item, button, mediaType, options = {}) {
  const details = item.querySelector(".job-details");
  if (!details) return;
  const key = String(jobId);

  if (details.dataset.loaded === "true") {
    details.hidden = options.restore ? false : !details.hidden;
    button.classList.toggle("open", !details.hidden);
    button.innerHTML = details.hidden
      ? '<span class="chevron">\u25b8</span> View metrics and comparison'
      : '<span class="chevron">\u25b8</span> Hide metrics';
    if (details.hidden) {
      state.expandedJobs.delete(key);
    } else {
      state.expandedJobs.add(key);
    }
    return;
  }

  state.expandedJobs.add(key);
  button.disabled = true;
  button.innerHTML = '<span class="chevron">\u25b8</span> Loading metrics...';

  try {
    const html = await fetchJobDetailsHtml(jobId, mediaType);
    button.disabled = false;
    details.innerHTML = html;
    details.dataset.loaded = "true";
    details.hidden = false;
    state.detailsCache.set(key, details.innerHTML);
    button.classList.add("open");
    button.innerHTML = '<span class="chevron">\u25b8</span> Hide metrics';
  } catch (error) {
    button.disabled = false;
    state.expandedJobs.delete(key);
    button.textContent = `Error: ${error.message}`;
  }
}

async function fetchJobDetailsHtml(jobId, mediaType) {
  const key = String(jobId);
  const cachedHtml = state.detailsCache.get(key);
  if (cachedHtml) return cachedHtml;

  const pending = state.detailsPromises.get(key);
  if (pending) return pending;

  const detailUrl = mediaType === "image"
    ? `/api/images/${encodeURIComponent(jobId)}`
    : `/api/jobs/${encodeURIComponent(jobId)}`;

  const promise = (async () => {
    const response = await fetch(detailUrl);
    const job = await response.json();
    if (!response.ok || !job.quality) {
      throw new Error("Metrics are not available");
    }

    return mediaType === "image"
      ? imageDetails(jobId, job)
      : videoDetails(jobId, job);
  })();

  state.detailsPromises.set(key, promise);
  try {
    const html = await promise;
    state.detailsCache.set(key, html);
    return html;
  } finally {
    state.detailsPromises.delete(key);
  }
}

function videoDetails(jobId, job) {
  const quality = job.quality;
  const original = quality.original;
  const restored = quality.restored;
  const change = quality.change;
  const elapsed = job.metrics?.find((metric) => metric.metric_name === "elapsed_seconds")?.metric_value;
  const steps = job.steps || [];
  return `
    <div class="metrics-grid">
      ${metricCard("Final resolution", `${restored.width}x${restored.height}`, `${formatNumber(change.resolution_scale)}x`)}
      ${metricCard("Sharpness", formatNumber(restored.sharpness), ratio(change.sharpness_gain))}
      ${metricCard("Contrast", formatNumber(restored.contrast), ratio(change.contrast_gain))}
      ${metricCard("Estimated noise", formatNumber(restored.noise_estimate), signed(change.noise_delta))}
      ${metricCard("Saturation", formatNumber(restored.saturation), signed(change.saturation_delta))}
      ${metricCard("Render", elapsed === undefined ? "n/a" : `${formatNumber(elapsed)} s`, `${restored.sampled_frames} measured frames`)}
    </div>
    <p class="metrics-note">Original baseline: ${original.width}x${original.height}. Metrics are approximate indicators.</p>
    ${stepList(steps)}
    <video class="comparison-video" controls preload="metadata" src="/api/jobs/${encodeURIComponent(jobId)}/preview/comparison"></video>
  `;
}

function imageDetails(jobId, job) {
  const quality = job.quality;
  const original = quality.original;
  const restored = quality.restored;
  const change = quality.change;
  const elapsed = job.metrics?.find((metric) => metric.metric_name === "elapsed_seconds")?.metric_value;
  return `
    <div class="metrics-grid">
      ${metricCard("Final size", `${restored.width}x${restored.height}`, `Original ${original.width}x${original.height}`)}
      ${metricCard("Sharpness", formatNumber(restored.sharpness), ratio(change.sharpness_gain))}
      ${metricCard("Contrast", formatNumber(restored.contrast), ratio(change.contrast_gain))}
      ${metricCard("Estimated noise", formatNumber(restored.noise_estimate), signed(change.noise_delta))}
      ${metricCard("Saturation", formatNumber(restored.saturation), signed(change.saturation_delta))}
      ${metricCard("Chrominance", formatNumber(restored.chrominance), signed(change.chrominance_delta))}
      ${metricCard("Render", elapsed === undefined ? "n/a" : `${formatNumber(elapsed)} s`, "Local process")}
    </div>
    ${stepList(job.steps || [])}
    <img class="comparison-image" alt="Restored image comparison" src="/api/images/${encodeURIComponent(jobId)}/preview/comparison" />
  `;
}

function stepList(steps) {
  if (!steps.length) return "";
  return `<div class="step-list"><strong>Recorded steps</strong>${steps.map((step, index) => {
    const done = step.status === "completed" || step.status === "done";
    return `<span>[${done ? "\u2588" : "\u2591"}] ${String(index + 1).padStart(2, "0")} \u00b7 ${escapeHtml(step.step_name)} \u00b7 ${escapeHtml(step.status)} \u00b7 ${formatDuration(step.duration_seconds)}</span>`;
  }).join("")}</div>`;
}

function progressInfo(job) {
  const progress = job.progress || {};
  const status = statusClass(job.status);
  const recorded = Math.max(Number(progress.recorded_steps) || 5, 1);
  let completed = Number(progress.completed_steps) || 0;

  if (status === "done") completed = recorded;
  if (status === "queued") completed = Math.max(completed, 0);
  if (status === "processing") completed = Math.max(completed, 1);

  const percent = status === "error" ? Math.min(100, Math.round((completed / recorded) * 100)) : Math.round((completed / recorded) * 100);
  const visiblePercent = status === "queued" ? Math.max(percent, 8) : Math.min(100, Math.max(percent, 0));
  const totalBlocks = 5;
  const filled = Math.min(totalBlocks, Math.round((visiblePercent / 100) * totalBlocks));
  const currentStep = progress.current_step ? escapeHtml(progress.current_step) : statusText(status);

  return {
    percent: visiblePercent,
    blocks: `${"\u2588".repeat(filled)}${"\u2591".repeat(totalBlocks - filled)}`,
    label: `STEP ${Math.min(completed, recorded)}/${recorded}`,
    currentStep,
  };
}

function metricCard(label, value, change) {
  return `<div class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(change)}</small></div>`;
}

function signed(value, suffix = "") {
  if (value === null || value === undefined) return "n/a";
  const prefix = Number(value) > 0 ? "+" : "";
  return `${prefix}${formatNumber(value)}${suffix}`;
}

function ratio(value) {
  return value === null || value === undefined ? "n/a" : `${formatNumber(value)}x`;
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(2);
}

function formatDuration(value) {
  return value === null || value === undefined ? "in progress" : `${formatNumber(value)} s`;
}

function statusClass(status) {
  if (status === "restored" || status === "completed" || status === "done") return "done";
  if (status === "processing" || status === "running") return "processing";
  if (status === "error" || status === "failed") return "error";
  return "queued";
}

function statusLabel(status) {
  const labels = {
    restored: "RESTORED",
    completed: "COMPLETED",
    done: "COMPLETED",
    processing: "PROCESSING",
    running: "PROCESSING",
    pending: "QUEUED",
    queued: "QUEUED",
    error: "ERROR",
    failed: "ERROR",
  };
  return labels[status] || String(status || "QUEUED").toUpperCase();
}

function statusText(status) {
  const labels = {
    done: "Process completed.",
    processing: "Process in progress.",
    queued: "Job queued.",
    error: "Error recorded.",
  };
  return labels[status] || "Status recorded.";
}

function mediaLabel(mediaType) {
  const labels = {
    video: "VIDEO",
    image: "PHOTO",
    audit: "AUDIT",
  };
  return labels[mediaType] || String(mediaType || "VIDEO").toUpperCase();
}

function profileLabel(profile) {
  const labels = {
    fast: "FAST",
    quality: "QUALITY",
    premium: "PREMIUM",
    ai_realesrgan: "AI REAL-ESRGAN",
    image_quality: "IMAGE QUALITY",
  };
  return labels[profile] || String(profile || "QUALITY").toUpperCase();
}

function colorModeLabel(mode) {
  const labels = {
    ai_natural: "NATURAL AI",
    enhance: "CLASSIC ENHANCE",
    none: "NO COLOR",
  };
  return labels[mode] || String(mode || "NO COLOR").toUpperCase();
}

function riskClass(risk) {
  const normalized = String(risk || "").toLowerCase();
  if (normalized.includes("alto") || normalized.includes("high")) return "risk-high";
  if (normalized.includes("medio") || normalized.includes("medium")) return "risk-medium";
  return "risk-low";
}

function riskLabel(risk) {
  const normalized = String(risk || "").toLowerCase();
  if (normalized.includes("alto") || normalized.includes("high")) return "\u26a0 HIGH RISK";
  if (normalized.includes("medio") || normalized.includes("medium")) return "\u25b3 MEDIUM RISK";
  return "\u2713 LOW RISK";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[character]);
}
