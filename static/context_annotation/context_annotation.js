const CTX = {
  sessions: [],
  filter: "all",
  sort: "filename",
  detail: null,
  telemetry: null,
  visibleSignals: new Set(),
  chartRows: [],
  xMin: 0,
  xMax: 1,
  hoverT: null,
  drag: null,
  editSegmentId: null,
  undoStack: [],
  lastLabels: null,
  fetchTimer: null,
  suggestions: null,
  selectedSuggestions: new Set(),
  suggestionPage: 0,
  editSuggestionId: null,
  lastInspectedSuggestionId: null,
  editEventId: null,
  legacyEdit: false,
  reviewElapsed: 0,
  reviewClock: null,
  macro: null,
  macroAxis: "operating",
  macroPage: 0,
  macroId: null,
  lastMacroId: null,
  macroElapsed: 0,
  macroClock: null,
  macroSaving: false,
  macroTelemetry: null,
};

document.addEventListener("visibilitychange", () => {
  if (CTX.reviewClock !== null) CTX.reviewElapsed += (performance.now() - CTX.reviewClock) / 1000;
  CTX.reviewClock = !document.hidden && CTX.editSuggestionId && qs("#annotationDialog")?.open ? performance.now() : null;
  if (CTX.macroClock !== null) CTX.macroElapsed += (performance.now() - CTX.macroClock) / 1000;
  CTX.macroClock = !document.hidden && CTX.macroId && qs("#macroDialog")?.open ? performance.now() : null;
});

function takeReviewTime() {
  if (CTX.reviewClock !== null) CTX.reviewElapsed += (performance.now() - CTX.reviewClock) / 1000;
  const seconds = Math.min(3600, Math.max(0, CTX.reviewElapsed));
  CTX.reviewElapsed = 0;
  CTX.reviewClock = null;
  return seconds;
}

function qs(selector, root = document) {
  return root.querySelector(selector);
}

function qsa(selector, root = document) {
  return Array.from(root.querySelectorAll(selector));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtSeconds(value) {
  if (!Number.isFinite(value)) return "0.000s";
  if (value >= 600) return `${(value / 60).toFixed(1)}m`;
  return `${value.toFixed(value >= 10 ? 1 : 3)}s`;
}

function fmtCount(value) {
  return Number(value || 0).toLocaleString();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  return response.json();
}

document.addEventListener("DOMContentLoaded", () => {
  if (document.body.dataset.page === "index") initIndex();
  if (document.body.dataset.page === "session") runAction(initSession);
});

async function initIndex() {
  qsa("#statusFilters button").forEach((button) => {
    button.addEventListener("click", () => {
      CTX.filter = button.dataset.filter;
      qsa("#statusFilters button").forEach((item) => item.classList.toggle("active", item === button));
      renderSessionTable();
    });
  });
  qs("#sortSelect").addEventListener("change", (event) => {
    CTX.sort = event.target.value;
    renderSessionTable();
  });
  qs("#exportManifestBtn").addEventListener("click", async () => {
    await api("/context-annotation/api/export/manifest", { method: "POST" });
    await loadSessions();
  });
  qs("#exportCampaignBtn").addEventListener("click", async () => {
    await api("/context-annotation/api/export/campaign-status", { method: "POST" });
    await loadSessions();
  });
  qs("#exportRecordsBtn").addEventListener("click", async () => {
    await api("/context-annotation/api/export/context-records", { method: "POST" });
  });
  await loadSessions();
}

async function loadSessions() {
  const payload = await api("/context-annotation/api/sessions");
  CTX.sessions = payload.sessions;
  renderSummary(payload.summary);
  renderSessionTable();
}

function renderSummary(summary) {
  const items = [
    ["Total", summary.total_sessions],
    ["Complete", summary.fully_annotated],
    ["Partial", summary.partially_annotated],
    ["Unannotated", summary.unannotated],
    ["Parse failures", summary.parse_failures],
  ];
  qs("#summaryStrip").innerHTML = items
    .map(
      ([label, value]) => `
        <div class="summary-item">
          <span class="summary-value">${fmtCount(value)}</span>
          <span class="summary-label">${escapeHtml(label)}</span>
        </div>`
    )
    .join("");
}

function statusMatchesFilter(session) {
  if (CTX.filter === "all") return true;
  if (CTX.filter === "error") {
    return [
      "parse_error",
      "unsupported",
      "missing_required_signals",
      "source_changed",
      "source_missing",
      "source_hash_mismatch",
    ].includes(session.status);
  }
  return session.status === CTX.filter;
}

function sortedSessions() {
  const rows = CTX.sessions.filter(statusMatchesFilter);
  const sorters = {
    filename: (a, b) => a.filename.localeCompare(b.filename),
    duration: (a, b) => b.duration_s - a.duration_s || a.filename.localeCompare(b.filename),
    progress: (a, b) => b.annotation_coverage - a.annotation_coverage || a.filename.localeCompare(b.filename),
    capture_time: (a, b) => String(b.capture_time || "").localeCompare(String(a.capture_time || "")),
  };
  return rows.sort(sorters[CTX.sort] || sorters.filename);
}

function renderSessionTable() {
  const body = qs("#sessionTableBody");
  const rows = sortedSessions();
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="6" class="muted">No sessions match this filter.</td></tr>`;
    return;
  }
  body.innerHTML = rows
    .map((session) => {
      const signals = Object.entries(session.available_signals || {})
        .filter(([, count]) => count > 0)
        .map(([key, count]) => `${key} ${fmtCount(count)}`)
        .join(", ");
      const percent = Math.round((session.annotation_coverage || 0) * 100);
      const href = `/context-annotation/session/${encodeURIComponent(session.source_id)}`;
      return `
        <tr>
          <td>
            <a href="${href}">${escapeHtml(session.filename)}</a>
            <div class="muted">${escapeHtml(session.session_id)}</div>
            ${session.error ? `<div class="muted">${escapeHtml(session.error)}</div>` : ""}
          </td>
          <td><span class="status-pill ${escapeHtml(session.status)}">${escapeHtml(session.status)}</span></td>
          <td>
            <div class="progress" aria-label="${percent}% annotated"><span style="width:${percent}%"></span></div>
            <div class="muted">${percent}%</div>
          </td>
          <td>${fmtSeconds(session.duration_s)}</td>
          <td>${fmtCount(session.sample_count)}</td>
          <td>${escapeHtml(signals || "none")}</td>
        </tr>`;
    })
    .join("");
}

async function initSession() {
  CTX.sourceId = window.__CTX_SOURCE_ID__;
  qs("#reviewerName").value = localStorage.getItem("ctxReviewer") || "";
  bindSessionControls();
  await loadSessionDetail();
  await loadTelemetry();
  qs("#reviewEnd").value = CTX.telemetry.duration_s.toFixed(3);
  qs("#reviewStart").value = "0";
  renderSuggestions();
  window.addEventListener("resize", () => {
    drawAllCharts();
    if (CTX.macroTelemetry && qs("#macroDialog").open) drawMacroSignals(CTX.macroTelemetry);
  });
}

async function loadSessionDetail() {
  CTX.detail = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}`);
  const session = CTX.detail.session;
  CTX.suggestions = CTX.detail.suggestions;
  CTX.macro = CTX.detail.macro_review;
  if (CTX.macro?.episodes.length && !CTX.macro.episodes.some((e) => ["pending", "uncertain"].includes(e.review_status))) qs("#macroFilter").value = "accepted";
  if (CTX.suggestions && [...CTX.suggestions.segments, ...CTX.suggestions.events].every((s) => s.review_status === "accepted")) qs("#reviewFilter").value = "accepted";
  qs("#sessionTitle").textContent = session.filename || session.session_id;
  qs("#sessionMeta").textContent = `${session.sample_count.toLocaleString()} frames, ${fmtSeconds(
    session.duration_s
  )}, ${session.source_format || "source"}`;
  qs("#sourceChangedBanner").classList.toggle("hidden", !CTX.detail.source_changed);
  const unavailable = CTX.detail.source_changed || session.status !== "ok";
  qs("#autoLabelBtn").disabled = unavailable;
  qs("#sessionError").textContent = session.error || "";
  qs("#sessionError").classList.toggle("hidden", !session.error);
  qs("#legacyStatus").textContent = CTX.detail.legacy_segment_count ? `${CTX.detail.legacy_segment_count} CTX1 legacy segments` : "";
  wireNeighbor("#previousSession", CTX.detail.previous_source_id);
  wireNeighbor("#nextSession", CTX.detail.next_source_id);
  fillSessionLabels();
  const provenance = CTX.detail.capture_provenance;
  qs("#captureProvenance").classList.toggle("hidden", !provenance);
  const fields = provenance?.error ? {Error: provenance.error} : provenance ? {
    Basis: "Data collector declaration; not independently machine verified",
    Date: provenance.attestation_date,
    "Physical capture group": provenance.physical_capture_group_id,
    ...Object.fromEntries(Object.entries(provenance.confirmed_session_metadata).map(([k,v]) => [k.replaceAll("_", " "), v])),
  } : {};
  qs("#captureProvenanceFields").innerHTML = Object.entries(fields).map(([k,v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("");
  renderAnnotationLanes();
  renderMacros();
}

function wireNeighbor(selector, sourceId) {
  const link = qs(selector);
  if (!sourceId) {
    link.classList.add("disabled");
    link.href = "#";
  } else {
    link.classList.remove("disabled");
    link.href = `/context-annotation/session/${encodeURIComponent(sourceId)}`;
  }
}

async function loadTelemetry(start = null, end = null) {
  const params = new URLSearchParams({ max_points: "20000" });
  if (start !== null) params.set("start_s", start.toFixed(6));
  if (end !== null) params.set("end_s", end.toFixed(6));
  CTX.telemetry = await api(
    `/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/telemetry?${params.toString()}`
  );
  const speed = CTX.telemetry.speed_provenance || {};
  qs("#speedStatus").textContent = speed.available_count ?
    `Speed: rider-corroborated candidate, not GPS-calibrated | ${speed.available_count} samples | ${speed.decoder_version}` :
    `Speed unavailable: ${speed.reason || "no verified original signal"}`;
  if (!CTX.chartRows.length) {
    CTX.xMin = 0;
    CTX.xMax = Math.max(CTX.telemetry.duration_s || 1, 1);
    const defaults = new Set(["rpm", "tps_voltage", "vehicle_speed_kmh", "ect_c"]);
    if (!CTX.telemetry.signals.some((signal) => signal.key === "tps_voltage" && signal.available_count > 0)) defaults.add("tps_raw");
    CTX.visibleSignals = new Set(CTX.telemetry.signals.filter((signal) => signal.available_count > 0 && defaults.has(signal.key)).map((signal) => signal.key));
    renderSignalToggles();
    renderCharts();
  }
  drawAllCharts();
  renderAnnotationLanes();
}

function scheduleTelemetryRefresh() {
  clearTimeout(CTX.fetchTimer);
  CTX.fetchTimer = setTimeout(() => {
    loadTelemetry(CTX.xMin, CTX.xMax).catch((error) => console.error(error));
  }, 180);
}

function renderSignalToggles() {
  qs("#signalToggles").innerHTML = CTX.telemetry.signals
    .map(
      (signal) => `
        <label class="signal-toggle">
          <input type="checkbox" data-signal="${escapeHtml(signal.key)}" ${
            CTX.visibleSignals.has(signal.key) ? "checked" : ""
          } ${signal.available_count === 0 ? "disabled" : ""}>
          ${escapeHtml(signal.label)} <span class="muted">${escapeHtml(signal.unit)}</span>
        </label>`
    )
    .join("");
  qsa("#signalToggles input").forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) CTX.visibleSignals.add(input.dataset.signal);
      else CTX.visibleSignals.delete(input.dataset.signal);
      renderCharts();
      drawAllCharts();
    });
  });
}

function renderCharts() {
  const stack = qs("#chartStack");
  const visible = CTX.telemetry.signals.filter((signal) => CTX.visibleSignals.has(signal.key));
  stack.innerHTML = visible
    .map(
      (signal) => `
        <div class="chart-row" data-signal="${escapeHtml(signal.key)}">
          <div class="chart-label">
            <strong>${escapeHtml(signal.label)}</strong>
            <span>${escapeHtml(signal.unit)} · ${fmtCount(signal.available_count)} samples</span>
          </div>
          <canvas class="chart-canvas" height="122"></canvas>
        </div>`
    )
    .join("");
  CTX.chartRows = qsa(".chart-row").map((row) => ({
    key: row.dataset.signal,
    canvas: qs("canvas", row),
  }));
  CTX.chartRows.forEach(({ canvas }) => {
    canvas.addEventListener("mousemove", onChartMouseMove);
    canvas.addEventListener("mouseleave", () => {
      CTX.hoverT = null;
      drawAllCharts();
      qs("#cursorReadout").textContent = "";
    });
    canvas.addEventListener("mousedown", onChartMouseDown);
    canvas.addEventListener("wheel", onChartWheel, { passive: false });
  });
  window.addEventListener("mousemove", onWindowMouseMove);
  window.addEventListener("mouseup", onWindowMouseUp);
}

function signalByKey(key) {
  return CTX.telemetry.signals.find((signal) => signal.key === key);
}

function canvasMetrics(canvas) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.floor(rect.width * dpr));
  const height = Math.max(1, Math.floor(rect.height * dpr));
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  return { rect, dpr, width, height, padL: 44 * dpr, padR: 12 * dpr, padT: 10 * dpr, padB: 24 * dpr };
}

function tToX(t, metrics) {
  const span = Math.max(0.000001, CTX.xMax - CTX.xMin);
  return metrics.padL + ((t - CTX.xMin) / span) * (metrics.width - metrics.padL - metrics.padR);
}

function xToT(x, metrics) {
  const span = Math.max(0.000001, CTX.xMax - CTX.xMin);
  return CTX.xMin + ((x - metrics.padL) / (metrics.width - metrics.padL - metrics.padR)) * span;
}

function yRange(points) {
  const visible = points.filter(
    (point) => point.y !== null && point.t >= CTX.xMin && point.t <= CTX.xMax && Number.isFinite(point.y)
  );
  const use = visible.length ? visible : points.filter((point) => point.y !== null && Number.isFinite(point.y));
  if (!use.length) return [0, 1];
  let min = Math.min(...use.map((point) => point.y));
  let max = Math.max(...use.map((point) => point.y));
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.08;
  return [min - pad, max + pad];
}

function yToPx(y, minY, maxY, metrics) {
  const plotH = metrics.height - metrics.padT - metrics.padB;
  return metrics.padT + (1 - (y - minY) / (maxY - minY)) * plotH;
}

function drawAllCharts() {
  CTX.chartRows.forEach(drawChart);
  renderAnnotationLanes();
}

function drawChart(row) {
  const signal = signalByKey(row.key);
  if (!signal) return;
  const canvas = row.canvas;
  const metrics = canvasMetrics(canvas);
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, metrics.width, metrics.height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, metrics.width, metrics.height);

  const [minY, maxY] = yRange(signal.points);
  ctx.strokeStyle = "#d7dde3";
  ctx.lineWidth = 1 * metrics.dpr;
  ctx.beginPath();
  ctx.moveTo(metrics.padL, metrics.padT);
  ctx.lineTo(metrics.padL, metrics.height - metrics.padB);
  ctx.lineTo(metrics.width - metrics.padR, metrics.height - metrics.padB);
  ctx.stroke();

  ctx.fillStyle = "#65727f";
  ctx.font = `${11 * metrics.dpr}px sans-serif`;
  ctx.fillText(maxY.toFixed(1), 4 * metrics.dpr, metrics.padT + 4 * metrics.dpr);
  ctx.fillText(minY.toFixed(1), 4 * metrics.dpr, metrics.height - metrics.padB);
  ctx.fillText(`${CTX.xMin.toFixed(2)}s`, metrics.padL, metrics.height - 6 * metrics.dpr);
  ctx.fillText(`${CTX.xMax.toFixed(2)}s`, metrics.width - metrics.padR - 72 * metrics.dpr, metrics.height - 6 * metrics.dpr);

  drawSegmentBackdrops(ctx, metrics);

  const points = signal.points.filter((point) => point.t >= CTX.xMin && point.t <= CTX.xMax);
  ctx.strokeStyle = "#166a8f";
  ctx.lineWidth = 1.5 * metrics.dpr;
  ctx.beginPath();
  let previous = null;
  points.forEach((point) => {
    if (point.y === null) { previous = null; return; }
    const x = tToX(point.t, metrics);
    const y = yToPx(point.y, minY, maxY, metrics);
    const gap = CTX.suggestions?.timestamp_integrity?.gap_threshold_s || 3;
    if (!previous || point.t - previous.t > gap) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
    previous = point;
  });
  ctx.stroke();

  if (CTX.hoverT !== null) {
    const x = tToX(CTX.hoverT, metrics);
    ctx.strokeStyle = "#a76313";
    ctx.beginPath();
    ctx.moveTo(x, metrics.padT);
    ctx.lineTo(x, metrics.height - metrics.padB);
    ctx.stroke();
  }

  if (CTX.drag && CTX.drag.mode === "select") {
    const start = Math.min(CTX.drag.startT, CTX.drag.currentT);
    const end = Math.max(CTX.drag.startT, CTX.drag.currentT);
    const x1 = tToX(start, metrics);
    const x2 = tToX(end, metrics);
    ctx.fillStyle = "rgb(22 106 143 / 18%)";
    ctx.fillRect(x1, metrics.padT, x2 - x1, metrics.height - metrics.padT - metrics.padB);
  }
}

function drawSegmentBackdrops(ctx, metrics) {
  const annotation = CTX.detail?.annotation;
  if (!annotation) return;
  ctx.fillStyle = "rgb(47 125 88 / 8%)";
  annotation.segments.forEach((segment) => {
    const start = Math.max(segment.start_s, CTX.xMin);
    const end = Math.min(segment.end_s, CTX.xMax);
    if (end <= start) return;
    const x1 = tToX(start, metrics);
    const x2 = tToX(end, metrics);
    ctx.fillRect(x1, metrics.padT, x2 - x1, metrics.height - metrics.padT - metrics.padB);
  });
  ctx.fillStyle = "rgb(22 106 143 / 7%)";
  (CTX.suggestions?.segments || []).filter((s) => ["pending", "uncertain"].includes(s.review_status)).forEach((s) => {
    const a = Math.max(s.start_s, CTX.xMin), b = Math.min(s.end_s, CTX.xMax);
    if (b > a) ctx.fillRect(tToX(a, metrics), metrics.padT, tToX(b, metrics) - tToX(a, metrics), 8 * metrics.dpr);
  });
  ctx.strokeStyle = "#c24c63";
  ctx.setLineDash([3 * metrics.dpr, 3 * metrics.dpr]);
  [...(CTX.suggestions?.events || []).filter((e) => ["pending", "uncertain"].includes(e.review_status)), ...(annotation.events || [])].forEach((e) => {
    if (e.event !== "throttle_burst" || e.start_s < CTX.xMin || e.start_s > CTX.xMax) return;
    const x = tToX(e.start_s, metrics);
    ctx.beginPath(); ctx.moveTo(x, metrics.padT); ctx.lineTo(x, metrics.height - metrics.padB); ctx.stroke();
  });
  ctx.setLineDash([]);
}

function eventToTime(event, canvas) {
  const metrics = canvasMetrics(canvas);
  const x = (event.clientX - metrics.rect.left) * metrics.dpr;
  return clamp(xToT(x, metrics), 0, CTX.telemetry.duration_s || 0);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function onChartMouseMove(event) {
  CTX.hoverT = eventToTime(event, event.currentTarget);
  updateReadout();
  if (CTX.drag?.mode === "select") {
    CTX.drag.currentT = CTX.hoverT;
    qs("#selectionReadout").textContent = `${fmtSeconds(Math.min(CTX.drag.startT, CTX.drag.currentT))} → ${fmtSeconds(
      Math.max(CTX.drag.startT, CTX.drag.currentT)
    )}`;
  }
  drawAllCharts();
}

function onChartMouseDown(event) {
  const startT = eventToTime(event, event.currentTarget);
  if (event.shiftKey) {
    CTX.drag = { mode: "pan", startX: event.clientX, startMin: CTX.xMin, startMax: CTX.xMax };
  } else {
    CTX.drag = { mode: "select", startT, currentT: startT };
  }
}

function onWindowMouseMove(event) {
  if (!CTX.drag || CTX.drag.mode !== "pan") return;
  const row = CTX.chartRows[0];
  if (!row) return;
  const metrics = canvasMetrics(row.canvas);
  const deltaPx = (event.clientX - CTX.drag.startX) * metrics.dpr;
  const span = CTX.drag.startMax - CTX.drag.startMin;
  const deltaT = -(deltaPx / (metrics.width - metrics.padL - metrics.padR)) * span;
  setXRange(CTX.drag.startMin + deltaT, CTX.drag.startMax + deltaT);
  drawAllCharts();
}

function onWindowMouseUp() {
  if (!CTX.drag) return;
  if (CTX.drag.mode === "select") {
    const start = Math.min(CTX.drag.startT, CTX.drag.currentT);
    const end = Math.max(CTX.drag.startT, CTX.drag.currentT);
    if (end - start > 0.01) openSegmentDialog({ start_s: start, end_s: end });
  } else if (CTX.drag.mode === "pan") {
    scheduleTelemetryRefresh();
  }
  CTX.drag = null;
  qs("#selectionReadout").textContent = "";
  drawAllCharts();
}

function onChartWheel(event) {
  event.preventDefault();
  const center = eventToTime(event, event.currentTarget);
  const span = CTX.xMax - CTX.xMin;
  const factor = event.deltaY < 0 ? 0.82 : 1.22;
  const newSpan = clamp(span * factor, 0.25, Math.max(CTX.telemetry.duration_s || 1, 1));
  const leftRatio = (center - CTX.xMin) / span;
  setXRange(center - newSpan * leftRatio, center + newSpan * (1 - leftRatio));
  drawAllCharts();
  scheduleTelemetryRefresh();
}

function setXRange(min, max) {
  const duration = Math.max(CTX.telemetry.duration_s || 1, 1);
  let span = max - min;
  if (span > duration) span = duration;
  if (span <= 0.01) span = 0.01;
  if (min < 0) {
    min = 0;
    max = span;
  }
  if (max > duration) {
    max = duration;
    min = duration - span;
  }
  CTX.xMin = clamp(min, 0, duration);
  CTX.xMax = clamp(max, CTX.xMin + 0.01, duration);
}

function updateReadout() {
  if (CTX.hoverT === null) return;
  const values = CTX.telemetry.signals
    .filter((signal) => CTX.visibleSignals.has(signal.key))
    .map((signal) => {
      const point = nearestPoint(signal.points, CTX.hoverT);
      return `${signal.label}: ${point?.y ?? "missing"} ${signal.unit}`;
    })
    .join(" · ");
  qs("#cursorReadout").textContent = `${CTX.hoverT.toFixed(3)}s · ${values}`;
}

function nearestPoint(points, t) {
  let best = null;
  let bestDistance = Infinity;
  for (const point of points) {
    if (point.y === null) continue;
    const distance = Math.abs(point.t - t);
    if (distance < bestDistance) {
      best = point;
      bestDistance = distance;
    }
  }
  return best;
}

function fillSelect(select, values, selected) {
  select.innerHTML = values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
  select.value = selected || values[values.length - 1];
}

function fillSessionLabels() {
  const taxonomy = CTX.detail.taxonomy;
  const labels = CTX.detail.annotation.session_labels;
  fillSelect(qs("#captureTypeSelect"), taxonomy.capture_type, labels.capture_type);
  fillSelect(qs("#startThermalSelect"), taxonomy.start_thermal_state, labels.start_thermal_state);
  qs("#sessionNotes").value = labels.notes || "";
  qs("#routeType").value = labels.route_type || "";
  qs("#routeNotes").value = labels.route_notes || "";
  qs("#trafficConditions").value = labels.known_traffic_conditions || "";
}

function bindSessionControls() {
  bindMacroControls();
  qs("#reviewKind").addEventListener("change", () => { CTX.suggestionPage = 0; CTX.selectedSuggestions.clear(); renderSuggestions(); });
  qs("#resetZoomBtn").addEventListener("click", () => {
    CTX.xMin = 0;
    CTX.xMax = Math.max(CTX.telemetry.duration_s || 1, 1);
    loadTelemetry();
  });
  qs("#undoBtn").addEventListener("click", () => runAction(undoLast));
  qs("#saveSessionLabelsBtn").addEventListener("click", () => runAction(saveSessionLabels));
  qs("#closeDialogBtn").addEventListener("click", closeDialog);
  qs("#cancelSegmentBtn").addEventListener("click", closeDialog);
  qs("#annotationDialog").addEventListener("cancel", (event) => { event.preventDefault(); closeDialog(); });
  qs("#annotationForm").addEventListener("submit", (event) => {
    event.preventDefault();
    runAction(() => saveSegment("reject"), true);
  });
  qs("#replaceSegmentBtn").addEventListener("click", () => runAction(() => saveSegment("replace"), true));
  qs("#deleteSegmentBtn").addEventListener("click", () => runAction(deleteSegment, true));
  qs("#repeatLabelsBtn").addEventListener("click", repeatLabels);
  qs("#autoLabelBtn").addEventListener("click", () => runAction(autoLabel));
  qs("#addEventBtn").addEventListener("click", () => openSegmentDialog({event: "throttle_burst", start_s: CTX.xMin, end_s: Math.min(CTX.xMin + 1, CTX.xMax)}));
  qs("#acceptSuggestionBtn").addEventListener("click", () => runAction(() => reviewOpenSuggestion("accept"), true));
  qs("#rejectSuggestionBtn").addEventListener("click", () => runAction(() => reviewOpenSuggestion("reject"), true));
  qs("#uncertainSuggestionBtn").addEventListener("click", () => runAction(() => reviewOpenSuggestion("uncertain"), true));
  qs("#nextPendingBtn").addEventListener("click", jumpToNextPending);
  qs("#acceptSelectedBtn").addEventListener("click", () => runAction(() => decideSuggestions("accept", [...CTX.selectedSuggestions])));
  qs("#rejectSelectedBtn").addEventListener("click", () => runAction(() => decideSuggestions("reject", [...CTX.selectedSuggestions])));
  qs("#acceptRangeBtn").addEventListener("click", () => runAction(() => decideSuggestions("accept", [], true)));
  qs("#reviewFilter").addEventListener("change", () => { CTX.suggestionPage = 0; renderSuggestions(); });
  qs("#suggestionPrev").addEventListener("click", () => { CTX.suggestionPage--; renderSuggestions(); });
  qs("#suggestionNext").addEventListener("click", () => { CTX.suggestionPage++; renderSuggestions(); });
  qs("#selectPage").addEventListener("change", (e) => {
    qsa("input[data-suggestion-check]:not(:disabled)").forEach((input) => {
      input.checked = e.target.checked;
      if (input.checked) CTX.selectedSuggestions.add(input.dataset.suggestionCheck);
      else CTX.selectedSuggestions.delete(input.dataset.suggestionCheck);
    });
  });
  qs("#reviewerName").addEventListener("change", (e) => localStorage.setItem("ctxReviewer", e.target.value));
  const iconButtons = [["#undoBtn", "undo-2", "Undo"], ["#resetZoomBtn", "scan", "Reset zoom"]];
  iconButtons.forEach(([selector, name, label]) => {
    const button = qs(selector);
    button.className = "icon-button";
    button.title = label; button.setAttribute("aria-label", label);
    button.innerHTML = `<img src="/context-annotation/static/icons/${name}.svg" alt="" />`;
  });
}

async function saveSessionLabels() {
  pushUndo();
  const payload = {
    capture_type: qs("#captureTypeSelect").value,
    start_thermal_state: qs("#startThermalSelect").value,
    notes: qs("#sessionNotes").value,
    route_type: qs("#routeType").value || null,
    route_notes: qs("#routeNotes").value,
    known_traffic_conditions: qs("#trafficConditions").value || null,
  };
  const annotation = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/session-labels`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  CTX.detail.annotation = annotation;
  updateUndoButton();
}

function populateTaxonomySelects(labels = {}) {
  const taxonomy = CTX.detail.taxonomy;
  fillSelect(qs("#engineSelect"), taxonomy.engine, labels.engine || "unknown");
  fillSelect(qs("#throttleSelect"), taxonomy.throttle, labels.throttle || "unknown");
  fillSelect(qs("#dynamicsSelect"), taxonomy.dynamics, labels.dynamics || "unknown");
  fillSelect(qs("#thermalSelect"), taxonomy.thermal, labels.thermal || "unknown");
  fillSelect(qs("#operatingSelect"), taxonomy.operating_context, labels.operating_context || "unknown");
  fillSelect(qs("#thermalPhaseSelect"), taxonomy.thermal_phase, labels.thermal_phase || "unknown");
  fillSelect(qs("#eventSelect"), taxonomy.event, labels.event || "throttle_burst");
}

function openSegmentDialog(segment = {}, isSuggestion = false) {
  CTX.reviewElapsed = 0;
  CTX.reviewClock = isSuggestion && !document.hidden ? performance.now() : null;
  CTX.editSuggestionId = isSuggestion ? segment.id : null;
  if (isSuggestion) CTX.lastInspectedSuggestionId = segment.id;
  CTX.editEventId = !isSuggestion && segment.event ? segment.id || "new" : null;
  CTX.editSegmentId = !isSuggestion && !segment.event ? segment.id || null : null;
  CTX.legacyEdit = Boolean(segment.labels && "engine" in segment.labels);
  qs("#dialogTitle").textContent = isSuggestion ? "Review suggestion" : segment.event ? "Event marker" : CTX.legacyEdit ? "CTX1 legacy segment" : "Operating segment";
  qs("#segmentStart").value = Number(segment.start_s ?? 0).toFixed(6);
  qs("#segmentEnd").value = Number(segment.end_s ?? Math.min((segment.start_s || 0) + 1, CTX.telemetry.duration_s)).toFixed(6);
  populateTaxonomySelects(isSuggestion ? segment : segment.event ? {event: segment.event} : segment.labels || CTX.lastLabels || {});
  qs("#legacySelectors").classList.toggle("hidden", !CTX.legacyEdit);
  qs("#operatingSelectors").classList.toggle("hidden", CTX.legacyEdit || Boolean(segment.event));
  qs("#eventSelectors").classList.toggle("hidden", !segment.event);
  qs("#segmentNotes").value = segment.notes || "";
  qs("#deleteSegmentBtn").classList.toggle("hidden", !CTX.editSegmentId && (!CTX.editEventId || CTX.editEventId === "new"));
  qs("#suggestionEvidence").classList.toggle("hidden", !isSuggestion && !segment.rule_version);
  qs("#suggestionReason").textContent = segment.reason || "";
  qs("#evidenceDetails").textContent = segment.rule_version ? JSON.stringify({rule_version: segment.rule_version, evidence_quality: segment.evidence_quality, evidence: segment.evidence, original_proposal: segment.original_proposal, human_confirmation: segment.human_confirmation, bulk_endorsement: segment.bulk_endorsement}, null, 2) : "";
  const reviewable = ["pending", "uncertain"].includes(segment.review_status);
  qs("#acceptSuggestionBtn").classList.toggle("hidden", !isSuggestion || !reviewable);
  qs("#rejectSuggestionBtn").classList.toggle("hidden", !isSuggestion || !reviewable);
  qs("#uncertainSuggestionBtn").classList.toggle("hidden", !isSuggestion || segment.review_status !== "pending");
  qs("#saveSegmentBtn").classList.toggle("hidden", isSuggestion && !reviewable);
  qs("#saveSegmentBtn").textContent = isSuggestion ? "Save correction" : "Save";
  qs("#repeatLabelsBtn").classList.toggle("hidden", isSuggestion || Boolean(segment.event));
  qs("#dialogError").classList.add("hidden");
  updateOverlapNotice();
  qs("#annotationDialog").showModal();
}

function closeDialog() {
  CTX.reviewClock = null;
  CTX.reviewElapsed = 0;
  qs("#annotationDialog").close();
  CTX.editSegmentId = null;
  CTX.editSuggestionId = null;
  CTX.editEventId = null;
}

function currentSegmentPayload() {
  const payload = {
    start_s: Number(qs("#segmentStart").value),
    end_s: Number(qs("#segmentEnd").value),
    taxonomy_version: CTX.legacyEdit ? "context-taxonomy-v1" : "context-taxonomy-v2",
    labels: CTX.legacyEdit ? {
      engine: qs("#engineSelect").value,
      throttle: qs("#throttleSelect").value,
      dynamics: qs("#dynamicsSelect").value,
      thermal: qs("#thermalSelect").value,
    } : {operating_context: qs("#operatingSelect").value, thermal_phase: qs("#thermalPhaseSelect").value},
    notes: qs("#segmentNotes").value,
  };
  if (CTX.editEventId || (CTX.editSuggestionId && suggestionById(CTX.editSuggestionId)?.event)) {
    return {start_s: payload.start_s, end_s: payload.end_s, event: qs("#eventSelect").value, notes: payload.notes};
  }
  return payload;
}

function overlapsExisting(payload) {
  return CTX.detail.annotation.segments.some((segment) => {
    if (segment.id === CTX.editSegmentId) return false;
    return payload.start_s < segment.end_s && payload.end_s > segment.start_s;
  });
}

function updateOverlapNotice() {
  const overlap = !CTX.editEventId && overlapsExisting(currentSegmentPayload());
  qs("#overlapNotice").classList.toggle("hidden", !overlap);
  qs("#replaceSegmentBtn").classList.toggle("hidden", !overlap || Boolean(CTX.editSuggestionId));
}

["#segmentStart", "#segmentEnd"].forEach((selector) => {
  document.addEventListener("input", (event) => {
    if (event.target.matches(selector)) updateOverlapNotice();
  });
});

async function saveSegment(overlapStrategy) {
  if (CTX.editSuggestionId) {
    await saveSuggestionCorrection();
    closeDialog();
    renderSuggestions(); drawAllCharts();
    return;
  }
  const payload = { ...currentSegmentPayload(), overlap_strategy: overlapStrategy };
  const previous = CTX.editEventId ? (CTX.detail.annotation.events || []).find((e) => e.id === CTX.editEventId) :
    CTX.detail.annotation.segments.find((s) => s.id === CTX.editSegmentId);
  const namedReviewer = qs("#reviewerName").value.trim();
  if (previous?.origin === "auto_suggested" || previous?.human_confirmation || CTX.detail.capture_provenance) payload.reviewer = reviewer();
  else if (namedReviewer) payload.reviewer = namedReviewer;
  if (!(payload.start_s >= 0) || !(payload.end_s > payload.start_s)) {
    alert("Start must be less than end and both times must be inside the session.");
    return;
  }
  pushUndo();
  let annotation;
  if (CTX.editEventId) {
    const path = `/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/events`;
    const eventPayload = currentSegmentPayload();
    if (payload.reviewer) eventPayload.reviewer = payload.reviewer;
    annotation = await api(CTX.editEventId === "new" ? path : `${path}/${encodeURIComponent(CTX.editEventId)}`, {method: CTX.editEventId === "new" ? "POST" : "PUT", body: JSON.stringify(eventPayload)});
  } else if (CTX.editSegmentId) {
    annotation = await api(
      `/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/segments/${encodeURIComponent(CTX.editSegmentId)}`,
      { method: "PUT", body: JSON.stringify(payload) }
    );
  } else {
    annotation = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/segments`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  }
  CTX.detail.annotation = annotation;
  CTX.lastLabels = payload.labels;
  closeDialog();
  renderAnnotationLanes();
  drawAllCharts();
  updateUndoButton();
}

async function deleteSegment() {
  if (!CTX.editSegmentId && !CTX.editEventId) return;
  pushUndo();
  const annotation = await api(
    `/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/${CTX.editEventId ? "events" : "segments"}/${encodeURIComponent(CTX.editEventId || CTX.editSegmentId)}`,
    { method: "DELETE" }
  );
  CTX.detail.annotation = annotation;
  closeDialog();
  renderAnnotationLanes();
  drawAllCharts();
  updateUndoButton();
}

function repeatLabels() {
  if (!CTX.lastLabels) return;
  populateTaxonomySelects(CTX.lastLabels);
}

function pushUndo() {
  CTX.undoStack.push(JSON.parse(JSON.stringify(CTX.detail.annotation)));
  if (CTX.undoStack.length > 20) CTX.undoStack.shift();
  updateUndoButton();
}

function updateUndoButton() {
  qs("#undoBtn").disabled = CTX.undoStack.length === 0;
}

async function undoLast() {
  const previous = CTX.undoStack.pop();
  if (!previous) return;
  const annotation = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/annotation`, {
    method: "PUT",
    body: JSON.stringify(previous),
  });
  CTX.detail.annotation = annotation;
  const fresh = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/suggestions`);
  CTX.suggestions = fresh.suggestions;
  renderSuggestions();
  renderAnnotationLanes();
  drawAllCharts();
  updateUndoButton();
}

function renderAnnotationLanes() {
  if (!CTX.detail || !CTX.telemetry) return;
  const pending = (CTX.suggestions?.segments || []).filter((s) => ["pending", "uncertain"].includes(s.review_status));
  const accepted = CTX.detail.annotation.segments;
  const dimensions = [
    ["Proposed", pending, "operating_context", "proposed", true],
    ["Reviewed", accepted.filter((s) => s.labels.operating_context), "operating_context", "engine", false],
    ["Thermal proposed", pending, "thermal_phase", "thermal proposed", true],
    ["Thermal reviewed", accepted.filter((s) => s.labels.thermal_phase), "thermal_phase", "thermal", false],
  ];
  const eventTypes = [["throttle_burst", "TPS burst"], ["repeated_stop_go", "Stop-go"], ["engine_start", "Engine start"], ["engine_stop", "Engine stop"]];
  eventTypes.forEach(([kind, label]) => {
    if (!qs("#showEventMarkers").checked && !kind.startsWith("engine_")) return;
    const proposed = (CTX.suggestions?.events || []).filter((e) => ["pending", "uncertain"].includes(e.review_status) && e.event === kind);
    const reviewed = (CTX.detail.annotation.events || []).filter((e) => e.event === kind);
    if (proposed.length || reviewed.length) {
      dimensions.push([`${label} proposed`, proposed, "event", "events proposed", true]);
      dimensions.push([`${label} reviewed`, reviewed, "event", "events", false]);
    }
  });
  if (CTX.macro) {
    for (const axis of ["operating", "thermal"]) {
      const items = CTX.macro.episodes.filter((e) => e.axis === axis);
      dimensions.unshift([`${axis} macro accepted`, items.filter((e) => e.review_status === "accepted").map((e) => ({...e, labels: {label: e.label}})), "label", "engine", false]);
      dimensions.unshift([`${axis} macro pending`, items.filter((e) => ["pending", "uncertain"].includes(e.review_status)), "label", "proposed", true]);
    }
  }
  if (qs("#showRapidMarkers").checked) {
    const items = (CTX.suggestions?.segments || []).filter((s, i, all) => i && s.operating_context !== all[i-1].operating_context)
      .map((s) => ({...s, end_s: s.start_s + 0.25, transition: s.operating_context}));
    dimensions.push(["Detailed transitions", items, "transition", "events proposed", true]);
  }
  if (accepted.some((s) => s.labels.engine)) dimensions.push(["CTX1 legacy", accepted.filter((s) => s.labels.engine), "engine", "engine", false]);
  qs("#annotationLanes").innerHTML = dimensions.map(([label, items, key, style, proposed]) => {
    const bands = items.filter((s) => s.end_s > CTX.xMin && s.start_s < CTX.xMax).map((s) => {
      const left = (Math.max(CTX.xMin, s.start_s) - CTX.xMin) / (CTX.xMax - CTX.xMin) * 100;
      const width = (Math.min(CTX.xMax, s.end_s) - Math.max(CTX.xMin, s.start_s)) / (CTX.xMax - CTX.xMin) * 100;
      const value = proposed ? s[key] : key === "event" ? s.event : s.labels[key];
      const title = `${s.start_s.toFixed(3)}-${s.end_s.toFixed(3)}s | ${value} | ${s.review_status || "reviewed"} | ${s.reason || s.notes || s.taxonomy_version}`;
      return `<button class="segment-band ${style} ${s.review_status === "uncertain" ? "uncertain" : ""}" style="left:${left}%;width:${width}%" data-state="${escapeHtml(value)}" data-${s.axis ? "macro" : proposed ? "proposal" : key === "event" ? "event" : "segment"}="${escapeHtml(s.id)}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">${width >= 9 ? escapeHtml(value) : ""}</button>`;
    }).join("");
    return `<div class="lane"><div class="lane-label">${escapeHtml(label)}</div><div class="lane-track"><div class="lane-inner">${bands}</div></div></div>`;
  }).join("");
  qsa(".segment-band").forEach((band) => {
    band.addEventListener("click", () => {
      if (band.dataset.macro) { runAction(() => openMacro(band.dataset.macro)); return; }
      if (band.dataset.proposal) { openSegmentDialog(suggestionById(band.dataset.proposal), true); return; }
      if (band.dataset.event) { openSegmentDialog(CTX.detail.annotation.events.find((e) => e.id === band.dataset.event)); return; }
      const segment = CTX.detail.annotation.segments.find((item) => item.id === band.dataset.segment);
      if (segment) openSegmentDialog(segment);
    });
  });
}

function suggestionById(id) {
  return [...(CTX.suggestions?.segments || []), ...(CTX.suggestions?.events || [])].find((s) => s.id === id);
}

async function runAction(callback, dialog = false) {
  const errorBox = qs(dialog ? "#dialogError" : "#sessionError");
  errorBox.classList.add("hidden");
  try { await callback(); }
  catch (error) { errorBox.textContent = error.message; errorBox.classList.remove("hidden"); }
}

async function autoLabel() {
  const button = qs("#autoLabelBtn");
  button.disabled = true; button.textContent = "Processing...";
  try {
    CTX.suggestions = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/auto-label`, {method: "POST", body: JSON.stringify({})});
    CTX.suggestionPage = 0; CTX.selectedSuggestions.clear();
    await loadSessionDetail();
    renderSuggestions(); drawAllCharts();
  } finally { button.disabled = false; button.textContent = "Auto-label"; }
}

function reviewer() {
  const name = qs("#reviewerName").value.trim();
  if (!name) throw new Error("Reviewer name is required.");
  return name;
}

async function saveSuggestionCorrection() {
  const payload = currentSegmentPayload();
  const changes = {start_s: payload.start_s, end_s: payload.end_s, notes: payload.notes};
  if (payload.event) changes.event = payload.event;
  else Object.assign(changes, payload.labels);
  const item = suggestionById(CTX.editSuggestionId);
  for (const key of Object.keys(changes)) if (changes[key] === item[key]) delete changes[key];
  if (!Object.keys(changes).length) return;
  const name = reviewer();
  const review_time_s = takeReviewTime();
  CTX.suggestions = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/suggestions/${encodeURIComponent(CTX.editSuggestionId)}`, {method: "PATCH", body: JSON.stringify({changes, reviewer: name, review_time_s})});
  CTX.reviewClock = !document.hidden ? performance.now() : null;
}

async function reviewOpenSuggestion(action) {
  const id = CTX.editSuggestionId;
  if (action === "accept" || action === "uncertain") await saveSuggestionCorrection();
  await decideSuggestions(action, [id]);
  const item = suggestionById(id);
  if (action === "accept" && item?.review_status !== "accepted") throw new Error("Suggestion overlaps reviewed context. Correct its range before acceptance.");
  closeDialog();
}

async function decideSuggestions(action, ids, range = false) {
  if (!ids.length && !range) throw new Error("Select at least one pending suggestion.");
  const payload = {suggestion_ids: ids, action, reviewer: reviewer()};
  if (CTX.editSuggestionId && ids.length === 1 && ids[0] === CTX.editSuggestionId) payload.review_time_s = takeReviewTime();
  if (range) {
    Object.assign(payload, {start_s: Number(qs("#reviewStart").value), end_s: Number(qs("#reviewEnd").value)});
    if (qs("#reviewKind").value !== "all") {
      payload.suggestion_ids = reviewItems().filter((s) => s.review_status === "pending" && s.start_s >= payload.start_s && s.end_s <= payload.end_s).map((s) => s.id);
      if (!payload.suggestion_ids.length) throw new Error("No pending suggestions of this kind in the range.");
    }
  }
  if (action === "accept") pushUndo();
  const result = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/suggestion-review`, {method: "POST", body: JSON.stringify(payload)});
  CTX.suggestions = result.suggestions; CTX.detail.annotation = result.annotation;
  CTX.selectedSuggestions.clear();
  qs("#reviewOutcome").textContent = `${result.decided.length} ${{accept: "accepted", reject: "rejected", uncertain: "uncertain"}[action]}, ${result.skipped.length} skipped`;
  renderSuggestions(); drawAllCharts(); updateUndoButton();
}

function reviewItems() {
  const kind = qs("#reviewKind").value;
  return [...(CTX.suggestions?.segments || []), ...(CTX.suggestions?.events || [])]
    .filter((s) => kind === "all" || kind === "operating" && !s.event || kind === "events" && s.event || kind === "throttle_burst" && s.event === "throttle_burst")
    .sort((a,b) => a.start_s - b.start_s || a.id.localeCompare(b.id));
}

function renderSuggestions() {
  const all = reviewItems();
  const filter = qs("#reviewFilter").value;
  const items = all.filter((s) => filter === "all" || s.review_status === filter);
  const pages = Math.max(1, Math.ceil(items.length / 40));
  CTX.suggestionPage = clamp(CTX.suggestionPage, 0, pages - 1);
  const page = items.slice(CTX.suggestionPage * 40, (CTX.suggestionPage + 1) * 40);
  qs("#pendingCount").textContent = `${all.filter((s) => s.review_status === "pending").length} pending, ${all.filter((s) => s.review_status === "uncertain").length} uncertain`;
  qs("#detailedProgress").textContent = [["Intervals", CTX.suggestions?.segments || []], ["Events", CTX.suggestions?.events || []]]
    .map(([label, items]) => `${label}: ${items.filter((s) => s.review_status === "accepted").length}/${items.length} accepted, ${items.filter((s) => s.review_status === "pending").length} pending, ${items.filter((s) => s.review_status === "uncertain").length} uncertain`).join(" | ");
  qs("#nextPendingBtn").disabled = !all.some((s) => s.review_status === "pending");
  qs("#suggestionPage").textContent = `${CTX.suggestionPage + 1} / ${pages} | ${items.length} suggestions`;
  qs("#suggestionPrev").disabled = CTX.suggestionPage === 0;
  qs("#suggestionNext").disabled = CTX.suggestionPage >= pages - 1;
  qs("#selectPage").checked = false;
  qs("#suggestionRows").innerHTML = page.map((s) => `<tr>
    <td><input type="checkbox" data-suggestion-check="${escapeHtml(s.id)}" aria-label="Select ${escapeHtml(s.id)}" ${s.review_status !== "pending" ? "disabled" : ""} ${CTX.selectedSuggestions.has(s.id) ? "checked" : ""} /></td>
    <td>${s.start_s.toFixed(3)} - ${s.end_s.toFixed(3)}</td><td>${escapeHtml(s.event || s.operating_context)}</td><td>${escapeHtml(s.thermal_phase || "")}</td>
    <td>${escapeHtml(s.evidence_quality)}</td><td>${escapeHtml(reviewLabel(s))}${s.conflicts_with_reviewed && s.review_status === "pending" ? ' <span class="conflict">Overlap</span>' : ""}</td>
    <td><button class="icon-button" data-inspect="${escapeHtml(s.id)}" title="Inspect evidence" aria-label="Inspect evidence"><img src="/context-annotation/static/icons/search.svg" alt="" /></button></td></tr>`).join("") || '<tr><td colspan="7" class="muted">No suggestions</td></tr>';
  qsa("[data-suggestion-check]").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) CTX.selectedSuggestions.add(input.dataset.suggestionCheck); else CTX.selectedSuggestions.delete(input.dataset.suggestionCheck);
  }));
  qsa("[data-inspect]").forEach((button) => button.addEventListener("click", () => {
    const item = suggestionById(button.dataset.inspect);
    setXRange(item.start_s - 10, item.end_s + 10); drawAllCharts(); scheduleTelemetryRefresh();
    openSegmentDialog(item, true);
  }));
  const stale = CTX.suggestions?.source_changed || CTX.suggestions?.raw_evidence_changed;
  ["#acceptSelectedBtn", "#rejectSelectedBtn", "#acceptRangeBtn"].forEach((selector) => qs(selector).disabled = !all.length || Boolean(stale));
}

function reviewLabel(item) {
  return item.review_status === "accepted" && item.bulk_endorsement && !item.human_confirmation
    && !item.review_history?.some((h) => h.action === "accept" && h.reviewer)
    ? "Bulk endorsed" : item.review_status || "reviewed";
}

function jumpToNextPending() {
  const all = reviewItems();
  const last = all.findIndex((s) => s.id === CTX.lastInspectedSuggestionId);
  const next = [...all.slice(last + 1), ...all.slice(0, last + 1)].find((s) => s.review_status === "pending");
  if (!next) return;
  qs("#reviewFilter").value = "pending";
  const pending = all.filter((s) => s.review_status === "pending");
  CTX.suggestionPage = Math.floor(pending.findIndex((s) => s.id === next.id) / 40);
  renderSuggestions();
  setXRange(next.start_s - 10, next.end_s + 10);
  drawAllCharts(); scheduleTelemetryRefresh();
  openSegmentDialog(next, true);
}

function bindMacroControls() {
  for (const [selector, axis] of [["#macroOperating", "operating"], ["#macroThermal", "thermal"]]) {
    qs(selector).addEventListener("click", () => { CTX.macroAxis = axis; CTX.macroPage = 0; renderMacros(); });
  }
  qs("#macroFilter").addEventListener("change", () => { CTX.macroPage = 0; renderMacros(); });
  qs("#macroPrev").addEventListener("click", () => { CTX.macroPage--; renderMacros(); });
  qs("#macroNext").addEventListener("click", () => { CTX.macroPage++; renderMacros(); });
  qs("#macroGenerate").addEventListener("click", () => runAction(async () => {
    CTX.macro = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/macro-generate`, {method: "POST"});
    renderMacros(); renderAnnotationLanes();
  }));
  qs("#macroNextPending").addEventListener("click", () => runAction(async () => {
    const all = CTX.macro?.episodes.filter((e) => e.axis === CTX.macroAxis) || [];
    const last = all.findIndex((e) => e.id === CTX.lastMacroId);
    const next = [...all.slice(last + 1), ...all.slice(0, last + 1)].find((e) => e.review_status === "pending");
    if (next) await openMacro(next.id);
  }));
  for (const selector of ["#showEventMarkers", "#showRapidMarkers"]) qs(selector).addEventListener("change", renderAnnotationLanes);
  qs("#macroClose").addEventListener("click", closeMacro);
  qs("#macroDialog").addEventListener("cancel", (e) => { e.preventDefault(); closeMacro(); });
  qs("#macroForm").addEventListener("submit", (e) => { e.preventDefault(); runMacroAction("correct"); });
  for (const action of ["accept", "reject", "uncertain"]) qs(`#macro${action[0].toUpperCase()+action.slice(1)}`).addEventListener("click", () => runMacroAction(action));
}

function renderMacros() {
  const macro = CTX.macro;
  if (!qs("#macroRows")) return;
  const all = macro?.episodes.filter((e) => e.axis === CTX.macroAxis) || [];
  const items = all.filter((e) => qs("#macroFilter").value === "all" || e.review_status === qs("#macroFilter").value);
  const pages = Math.max(1, Math.ceil(items.length / 30));
  CTX.macroPage = clamp(CTX.macroPage, 0, pages - 1);
  qs("#macroPage").textContent = `${CTX.macroPage+1} / ${pages} | ${items.length} episodes`;
  qs("#macroPrev").disabled = CTX.macroPage === 0;
  qs("#macroNext").disabled = CTX.macroPage === pages-1;
  qs("#macroNextPending").disabled = Boolean(macro?.stale) || !all.some((e) => e.review_status === "pending");
  qs("#macroWarning").classList.toggle("hidden", !macro?.stale);
  qs("#macroProgress").textContent = macro ? ["operating", "thermal"].map((axis) => {
    const p = macro.progress[axis];
    return `${axis}: ${p.accepted}/${p.total} accepted, ${p.rejected} rejected, ${p.uncertain} uncertain, ${p.pending} pending`;
  }).join(" | ") : "No macro grouping";
  for (const [selector, axis] of [["#macroOperating", "operating"], ["#macroThermal", "thermal"]]) {
    qs(selector).classList.toggle("active", axis === CTX.macroAxis);
    qs(selector).setAttribute("aria-selected", String(axis === CTX.macroAxis));
  }
  const displayLabels = {moving_episode:"Mixed movement",moving_steady:"Steady movement",stationary_running:"Stationary running",engine_off:"Engine off"};
  qs("#macroRows").innerHTML = items.slice(CTX.macroPage*30, (CTX.macroPage+1)*30).map((e) => `<tr><td>${e.start_s.toFixed(3)} - ${e.end_s.toFixed(3)}</td><td title="${escapeHtml(e.label)}">${escapeHtml(displayLabels[e.label] || e.label)}<small class="macro-mobile-state">${escapeHtml(reviewLabel(e))}</small></td><td>${e.child_ids.length}</td><td>${e.event_ids.length}</td><td>${escapeHtml(reviewLabel(e))}</td><td><button class="icon-button" data-macro-inspect="${escapeHtml(e.id)}" title="Inspect episode" aria-label="Inspect episode" ${macro.stale ? "disabled" : ""}><img src="/context-annotation/static/icons/search.svg" alt="" /></button></td></tr>`).join("") || '<tr><td colspan="6" class="muted">No episodes</td></tr>';
  qsa("[data-macro-inspect]").forEach((b) => b.addEventListener("click", () => runAction(() => openMacro(b.dataset.macroInspect))));
  qs("#retainedMacroReviews").classList.toggle("hidden", !macro?.retained_reviews.length);
  qs("#retainedMacroEvidence").textContent = JSON.stringify(macro?.retained_reviews || [], null, 2);
}

async function openMacro(id) {
  const evidence = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/macro/${encodeURIComponent(id)}`);
  const e = evidence.episode;
  CTX.macroId = id; CTX.lastMacroId = id; CTX.macroElapsed = 0;
  qs("#macroDialogTitle").textContent = `${e.axis === "thermal" ? "Thermal" : "Operating"} episode | ${reviewLabel(e)}`;
  qs("#macroStart").value = e.start_s.toFixed(6); qs("#macroEnd").value = e.end_s.toFixed(6);
  fillSelect(qs("#macroLabel"), CTX.macro.labels[e.axis], e.label);
  qs("#macroNotes").value = e.notes;
  qs("#macroChildCount").textContent = evidence.children.length;
  qs("#macroChildren").innerHTML = evidence.children.map((c) => `<details data-child-id="${escapeHtml(c.id)}"><summary>${c.start_s.toFixed(3)} - ${c.end_s.toFixed(3)}s | ${escapeHtml(c.operating_context)} | ${escapeHtml(c.thermal_phase)}</summary><pre>${escapeHtml(JSON.stringify(c, null, 2))}</pre><button type="button" class="icon-button" data-child-inspect="${escapeHtml(c.id)}" title="Inspect original interval" aria-label="Inspect original interval"><img src="/context-annotation/static/icons/search.svg" alt="" /></button></details>`).join("");
  qsa("[data-child-inspect]").forEach((b) => b.addEventListener("click", () => {
    const child = suggestionById(b.dataset.childInspect);
    closeMacro(); qs("#detailedReview").open = true;
    setXRange(child.start_s - 2, child.end_s + 2); drawAllCharts(); scheduleTelemetryRefresh();
    openSegmentDialog(child, true);
  }));
  qs("#macroMarkers").textContent = JSON.stringify({events: evidence.events, transitions: evidence.transitions}, null, 2);
  qs("#macroEvidence").textContent = JSON.stringify({measured: evidence.measured_evidence, source_sha256: CTX.macro.source_sha256, rule_version: CTX.macro.rule_version, original_proposal: e.original_proposal, review_history: e.review_history, bulk_endorsement: e.bulk_endorsement, signal_provenance: evidence.signal_provenance, timestamp_integrity: evidence.timestamp_integrity}, null, 2);
  const finalized = ["accepted", "rejected"].includes(e.review_status);
  for (const selector of ["#macroAccept", "#macroReject", "#macroUncertain", "#macroCorrect", "#macroStart", "#macroEnd", "#macroLabel", "#macroNotes"]) qs(selector).disabled = finalized;
  qs("#macroError").classList.add("hidden");
  qs("#macroSignalPlots").textContent = "";
  qs("#macroDialog").showModal();
  const data = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/telemetry?max_points=20000&start_s=${e.start_s}&end_s=${e.end_s}`);
  if (CTX.macroId !== id || !qs("#macroDialog").open) return;
  CTX.macroTelemetry = data;
  drawMacroSignals(data);
  CTX.macroClock = !document.hidden && CTX.macroId ? performance.now() : null;
}

function drawMacroSignals(data) {
  const signals = data.signals.filter((s) => ["vehicle_speed_kmh", "rpm", "tps_voltage", "ect_c"].includes(s.key));
  qs("#macroSignalPlots").innerHTML = signals.map((s) => `<div class="macro-signal"><span>${escapeHtml(s.label || s.key)} ${escapeHtml(s.unit || "")}</span><canvas data-macro-signal="${escapeHtml(s.key)}" aria-label="${escapeHtml(s.key)} measured signal"></canvas></div>`).join("");
  signals.forEach((s, index) => {
    const canvas = qs(`[data-macro-signal="${s.key}"]`);
    canvas.width = Math.max(1, Math.floor(canvas.getBoundingClientRect().width * devicePixelRatio));
    canvas.height = 100 * devicePixelRatio;
    const ctx = canvas.getContext("2d"), points = s.points.filter((p) => Number.isFinite(p.y));
    ctx.fillStyle = "#f1f4f5"; ctx.fillRect(0,0,canvas.width,canvas.height);
    if (!points.length) { ctx.fillStyle="#65727f"; ctx.fillText("Unavailable",10,30); return; }
    const values = points.map((p) => p.y), low=Math.min(...values), high=Math.max(...values);
    const e = CTX.macro.episodes.find((e) => e.id === CTX.macroId);
    ctx.strokeStyle = ["#166a8f", "#2f7d58", "#a33a3a", "#7c5c2e"][index]; ctx.lineWidth=1.5*devicePixelRatio;
    ctx.beginPath();
    let previous = null;
    s.points.forEach((p) => {
      if (!Number.isFinite(p.y)) { previous = null; return; }
      const pad=8*devicePixelRatio;
      const x=pad+(p.t-e.start_s)/(e.end_s-e.start_s)*(canvas.width-2*pad);
      const y=pad+(high-p.y)/Math.max(1,high-low)*Math.max(1,canvas.height-32*devicePixelRatio);
      if (previous && p.t-previous.t <= (CTX.suggestions?.timestamp_integrity?.gap_threshold_s || 3)) ctx.lineTo(x,y); else ctx.moveTo(x,y);
      previous = p;
    });
    ctx.stroke(); ctx.fillStyle="#162029"; ctx.font=`${11*devicePixelRatio}px sans-serif`;
    ctx.fillText(`${low.toFixed(1)} - ${high.toFixed(1)}`,10,canvas.height-10);
  });
}

function closeMacro() {
  CTX.macroClock = null; CTX.macroElapsed = 0; CTX.macroId = null;
  qs("#macroDialog").close();
  qs("#macroSignalPlots").textContent = "";
  CTX.macroTelemetry = null;
}

async function runMacroAction(action) {
  if (CTX.macroSaving) return;
  CTX.macroSaving = true;
  const errorBox=qs("#macroError"); errorBox.classList.add("hidden");
  try {
    const e = CTX.macro.episodes.find((e) => e.id === CTX.macroId);
    const changes={start_s:Number(qs("#macroStart").value),end_s:Number(qs("#macroEnd").value),label:qs("#macroLabel").value,notes:qs("#macroNotes").value};
    for (const k of Object.keys(changes)) if (changes[k] === e[k]) delete changes[k];
    if (CTX.macroClock !== null) CTX.macroElapsed += (performance.now()-CTX.macroClock)/1000;
    CTX.macroClock=null;
    CTX.macro = await api(`/context-annotation/api/sessions/${encodeURIComponent(CTX.sourceId)}/macro/${encodeURIComponent(e.id)}/review`, {method:"POST",body:JSON.stringify({action,reviewer:reviewer(),changes,review_time_s:Math.min(3600,CTX.macroElapsed)})});
    closeMacro(); renderMacros(); renderAnnotationLanes();
  } catch(error) { errorBox.textContent=error.message; errorBox.classList.remove("hidden"); CTX.macroClock=!document.hidden ? performance.now() : null; }
  finally { CTX.macroSaving = false; }
}
