/* Isolated correction/uncertainty verification. Never accepts or freezes annotations. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");

const id = "calib-real-run-session-000004";
const artifacts = "data/context_annotation/exports/ctx21_browser_verification";
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
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ctx21-browser-"));
  const annotations = path.join(tmp, "annotations"), suggestions = path.join(tmp, "suggestions");
  fs.mkdirSync(annotations); fs.mkdirSync(suggestions); fs.mkdirSync(artifacts, {recursive: true});
  fs.copyFileSync(`data/context_annotation/suggestions/${id}.suggestions.json`, path.join(suggestions, `${id}.suggestions.json`));
  const files = ["data/context_annotation/curated_corpus.json", "real_data/real_run/session_000004.jsonl",
    `data/telemetry/${id}/samples.jsonl`, "data/context_annotation/annotations/ride-20260920-1504.context.json",
    `data/context_annotation/suggestions/${id}.suggestions.json`];
  const before = Object.fromEntries(files.map((p) => [p, hash(p)]));
  const port = await unusedPort(), base = `http://127.0.0.1:${port}`;
  server = spawn(".venv/bin/python", ["-m", "app.context_annotation.web", "--port", String(port),
    "--corpus-manifest", "data/context_annotation/curated_corpus.json", "--annotations-dir", annotations,
    "--suggestions-dir", suggestions, "--exports-dir", path.join(tmp, "exports")], {stdio: "ignore"});
  let ready = false;
  for (let i = 0; i < 120; i++) {
    if (server.exitCode !== null) throw new Error(`Verification server exited ${server.exitCode}`);
    try { await fetch(`${base}/`); ready = true; break; } catch { await new Promise((r) => setTimeout(r, 500)); }
  }
  assert(ready, "Isolated verification server started");
  browser = await chromium.launch({headless: true, executablePath: process.env.CTX2_CHROMIUM_EXECUTABLE});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [], decisions = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (request) => {
    if (request.url().endsWith("/suggestion-review")) decisions.push(request.postDataJSON().action);
  });
  await page.goto(`${base}/context-annotation/session/${id}`);
  await page.locator("[data-inspect]").first().waitFor({timeout: 120000});
  await page.locator("#reviewerName").fill("CTX2.1 isolated browser test");
  await page.locator("#nextPendingBtn").click();
  await page.locator("#annotationDialog[open]").waitFor();
  const selected = await page.evaluate(() => ({id: CTX.editSuggestionId,
    start: Number(document.querySelector("#segmentStart").value), end: Number(document.querySelector("#segmentEnd").value)}));
  const correctedEnd = Number((selected.end - Math.min(.25, (selected.end - selected.start) / 4)).toFixed(6));
  assert((await page.locator("#evidenceDetails").textContent()).includes("speed_kmh"));
  await page.locator("#segmentEnd").fill(String(correctedEnd));
  await page.locator("#operatingSelect").selectOption("unknown");
  await page.locator("#segmentNotes").fill("Isolated correction and uncertainty test; not campaign review.");
  await page.screenshot({path: path.join(artifacts, "desktop-evidence.png")});
  await page.locator("#uncertainSuggestionBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open);
  await page.reload();
  await page.locator("[data-inspect]").first().waitFor({timeout: 120000});
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
  assert(await page.locator("#acceptSuggestionBtn").isVisible());
  assert(await page.locator("#saveSegmentBtn").isVisible());
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: path.join(artifacts, "mobile-evidence.png")});
  const box = await page.locator("#annotationDialog").boundingBox();
  assert(box.x >= 0 && box.x + box.width <= 391);
  await page.locator("#closeDialogBtn").click();
  await page.locator(".review-heading").scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(artifacts, "mobile-review.png")});
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
  const result = {verified_at: new Date().toISOString(), isolated_test_only: true,
    campaign_annotations_added: 0, proposals_accepted: 0, freezes_created: 0,
    next_pending_skips_uncertain: true, uncertainty_persists: true, correction_persists_after_reload: true,
    selected_evidence_visible: true, existing_annotations_and_proposals_unchanged: true,
    desktop_viewport: [1440, 1100], mobile_viewport: [390, 844], mobile_overflow: overflow,
    canvas_signal_pixels: pixels, browser_errors: errors, before_sha256: before, after_sha256: after};
  fs.writeFileSync("data/context_annotation/exports/ctx21_review_verification.json", JSON.stringify(result, null, 2) + "\n");
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
