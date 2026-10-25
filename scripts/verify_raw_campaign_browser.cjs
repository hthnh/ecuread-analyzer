/* Review verification uses temporary sidecars and never accepts a campaign label. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");

const campaign = "data/context_annotation/campaigns/ecuread-filtered-raw-18-v1";
const id = "session_000018";
const artifacts = `${campaign}/exports/browser`;
const hash = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
let browser, server;

async function unusedPort() {
  const listener = net.createServer();
  await new Promise((resolve) => listener.listen(0, "127.0.0.1", resolve));
  const port = listener.address().port;
  await new Promise((resolve) => listener.close(resolve));
  return port;
}

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ctx22-browser-"));
  const annotations = path.join(tmp, "annotations"), suggestions = path.join(tmp, "suggestions");
  fs.mkdirSync(annotations); fs.mkdirSync(suggestions); fs.mkdirSync(artifacts, {recursive: true});
  fs.copyFileSync(`${campaign}/suggestions/${id}.suggestions.json`, path.join(suggestions, `${id}.suggestions.json`));
  const files = [`${campaign}/corpus_manifest.json`, `${campaign}/raw/${id}.jsonl`,
    `real_data/fulldata_afterclean/${id}.jsonl`, "data/context_annotation/curated_corpus.json",
    "data/context_annotation/annotations/ride-20260920-1504.context.json",
    `${campaign}/suggestions/${id}.suggestions.json`];
  const before = Object.fromEntries(files.map((p) => [p, hash(p)]));
  const port = await unusedPort(), base = `http://127.0.0.1:${port}`;
  server = spawn(".venv/bin/python", ["-m", "app.context_annotation.web", "--port", String(port),
    "--corpus-manifest", `${campaign}/corpus_manifest.json`, "--annotations-dir", annotations,
    "--suggestions-dir", suggestions, "--exports-dir", path.join(tmp, "exports")], {stdio: "ignore"});
  let ready = false;
  for (let i = 0; i < 120; i++) {
    if (server.exitCode !== null) throw new Error(`Verification server exited ${server.exitCode}`);
    try { await fetch(`${base}/`); ready = true; break; } catch { await new Promise((r) => setTimeout(r, 500)); }
  }
  assert(ready);
  browser = await chromium.launch({headless: true, executablePath: process.env.CTX2_CHROMIUM_EXECUTABLE});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [], decisions = [], timing = {};
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => { if (r.url().endsWith("/suggestion-review")) decisions.push(r.postDataJSON().action); });
  let started = performance.now();
  await page.goto(`${base}/context-annotation/session/${id}`);
  await page.locator("[data-inspect]").first().waitFor({timeout: 120000});
  timing.initial_view_ms = performance.now() - started;
  const telemetry = await (await page.request.get(`${base}/context-annotation/api/sessions/${id}/telemetry`)).json();
  assert.equal(telemetry.sample_count, 17976);
  assert.equal(telemetry.duration_s, 4496.001);
  for (const signal of telemetry.signals) {
    assert.equal(signal.total_count, 17976);
    assert.equal(signal.points.at(-1).t, telemetry.duration_s);
  }
  const speed = telemetry.signals.find((s) => s.key === "vehicle_speed_kmh");
  assert.equal(speed.available_count, 17976);
  await page.locator("#reviewerName").fill("CTX2.2 isolated browser test");
  await page.locator("#nextPendingBtn").click();
  await page.locator("#annotationDialog[open]").waitFor();
  const selected = await page.evaluate(() => ({id: CTX.editSuggestionId,
    start: Number(document.querySelector("#segmentStart").value), end: Number(document.querySelector("#segmentEnd").value)}));
  const correctedEnd = Number((selected.end - Math.min(.25, (selected.end - selected.start) / 4)).toFixed(6));
  assert((await page.locator("#evidenceDetails").textContent()).includes("speed_kmh"));
  await page.locator("#segmentEnd").fill(String(correctedEnd));
  await page.locator("#operatingSelect").selectOption("unknown");
  await page.locator("#segmentNotes").fill("Isolated test only; not campaign review.");
  await page.screenshot({path: `${artifacts}/desktop-evidence.png`});
  await page.locator("#uncertainSuggestionBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open);
  started = performance.now();
  await page.reload();
  await page.locator("[data-inspect]").first().waitFor({timeout: 120000});
  timing.reload_ms = performance.now() - started;
  started = performance.now();
  const regenerated = await page.request.post(`${base}/context-annotation/api/sessions/${id}/auto-label`, {data: {thresholds: {}}});
  assert.equal(regenerated.status(), 200);
  timing.full_session_auto_suggestion_ms = performance.now() - started;
  const detail = await (await page.request.get(`${base}/context-annotation/api/sessions/${id}`)).json();
  const item = detail.suggestions.segments.find((s) => s.id === selected.id);
  assert.equal(item.review_status, "uncertain");
  assert.equal(item.end_s, correctedEnd);
  assert.equal(item.operating_context, "unknown");
  assert.equal(item.human_confirmation, null);
  assert.equal(detail.coverage, 0);
  assert.equal(detail.annotation.segments.length, 0);
  assert.equal(detail.annotation.events.length, 0);
  assert.equal(fs.readdirSync(annotations).length, 0);
  await page.locator("#nextPendingBtn").click();
  assert.notEqual(await page.evaluate(() => CTX.editSuggestionId), selected.id);
  await page.locator("#closeDialogBtn").click();
  await page.locator("#reviewFilter").selectOption("uncertain");
  await page.locator(`[data-inspect="${selected.id}"]`).click();
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: `${artifacts}/mobile-evidence.png`});
  const box = await page.locator("#annotationDialog").boundingBox();
  assert(box.x >= 0 && box.x + box.width <= 391);
  await page.locator("#closeDialogBtn").click();
  await page.locator(".review-heading").scrollIntoViewIfNeeded();
  await page.screenshot({path: `${artifacts}/mobile-review.png`});
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
  assert.equal(overflow, false);
  const pixels = await page.locator('.chart-row[data-signal="vehicle_speed_kmh"] canvas').evaluate((canvas) => {
    const data = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    let colored = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i] < 100 && data[i + 1] < 180 && data[i + 2] > 70) colored++;
    return colored;
  });
  assert(pixels > 100);
  assert.deepEqual(decisions, ["uncertain"]);
  assert.deepEqual(errors, []);
  const after = Object.fromEntries(files.map((p) => [p, hash(p)]));
  assert.deepEqual(after, before);
  const result = {verified_at: new Date().toISOString(), isolated_test_only: true, session_id: id,
    input_frames: 17976, duration_s: 4496.001, timing, full_session_telemetry: true,
    display_points_per_signal: telemetry.signals[0].points.length,
    speed_provenance: telemetry.speed_provenance,
    campaign_annotations_added: 0, proposals_accepted: 0, freezes_created: 0,
    correction_and_uncertainty_persist_after_reload_and_regeneration: true,
    next_pending_skips_uncertain: true, selected_evidence_visible: true,
    desktop_viewport: [1440, 1100], mobile_viewport: [390, 844], mobile_overflow: overflow,
    canvas_signal_pixels: pixels, browser_errors: errors, before_sha256: before, after_sha256: after};
  fs.writeFileSync(`${campaign}/exports/browser_verification.json`, JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result, null, 2));
})().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    const exited = new Promise((resolve) => server.once("exit", resolve));
    server.kill("SIGTERM");
    const timeout = setTimeout(() => server.kill("SIGKILL"), 10000);
    await exited; clearTimeout(timeout);
  }
});
