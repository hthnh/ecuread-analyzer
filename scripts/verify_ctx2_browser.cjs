/* Run against a Studio using isolated verification annotation/suggestion paths. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");

const baseURL = process.env.CTX2_VERIFY_URL || "http://127.0.0.1:8767";
const artifacts = process.env.CTX2_VERIFY_ARTIFACTS || "data/context_annotation/exports/browser_verification";
const sessionID = "calib-real-run-session-000004";
const apiPath = `/context-annotation/api/sessions/${sessionID}`;
const hash = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const rawFile = "real_data/real_run/session_000004.jsonl";
const sourceFile = `data/telemetry/${sessionID}/samples.jsonl`;
const humanFile = "data/context_annotation/annotations/ride-20260920-1504.context.json";
let activeBrowser;

(async () => {
  assert.equal(process.env.CTX2_ISOLATED_REVIEW, "1", "Start an isolated verification Studio and set CTX2_ISOLATED_REVIEW=1.");
  const before = {raw: hash(rawFile), canonical: hash(sourceFile), existingHumanAnnotation: hash(humanFile)};
  fs.mkdirSync(artifacts, {recursive: true});
  const browser = await chromium.launch({headless: true, executablePath: process.env.CTX2_CHROMIUM_EXECUTABLE});
  activeBrowser = browser;
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => { if (response.status() >= 500 || (response.status() >= 400 && response.url().includes("/static/"))) errors.push(`HTTP ${response.status()} ${response.url()}`); });
  await page.goto(`${baseURL}/context-annotation/session/${sessionID}`);
  await page.locator('.chart-row[data-signal="vehicle_speed_kmh"]').waitFor();
  await page.locator("#autoLabelBtn").click();
  await page.waitForFunction(() => document.querySelector("#pendingCount").textContent.includes("pending") && document.querySelectorAll("[data-inspect]").length > 0);
  await page.locator("#reviewerName").fill("Automated workflow verification (isolated test)");
  const initial = await (await page.request.get(`${baseURL}${apiPath}`)).json();
  assert.equal(initial.coverage, 0);
  const item = initial.suggestions.segments.find((s) => s.operating_context.startsWith("moving_") && s.end_s - s.start_s > 5);
  assert(item, "Real source has an editable motion proposal.");
  await page.locator("#sessionTitle").scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(artifacts, "desktop.png")});
  await page.locator("#chartStack").scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(artifacts, "signals-and-proposals.png")});

  const canvas = page.locator('.chart-row[data-signal="vehicle_speed_kmh"] canvas');
  const pixels = await canvas.evaluate((el) => {
    const data = el.getContext("2d").getImageData(0, 0, el.width, el.height).data;
    let colored = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i] < 100 && data[i + 1] < 180 && data[i + 2] > 70) colored++;
    return colored;
  });
  assert(pixels > 100, "Speed canvas contains a rendered signal.");
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -220);
  await page.waitForTimeout(350);
  const framing = await page.evaluate(() => {
    const canvas = document.querySelector(".chart-canvas").getBoundingClientRect();
    const inner = document.querySelector(".lane-inner").getBoundingClientRect();
    return {canvasPlotLeft: canvas.left + 44, lanePlotLeft: inner.left, canvasPlotRight: canvas.right - 12, lanePlotRight: inner.right};
  });
  assert(Math.abs(framing.canvasPlotLeft - framing.lanePlotLeft) < 2);
  assert(Math.abs(framing.canvasPlotRight - framing.lanePlotRight) < 2);

  const inspect = page.locator(`[data-inspect="${item.id}"]`);
  await inspect.click();
  await page.locator("#segmentEnd").fill(String(item.end_s - 0.25));
  await page.locator("#operatingSelect").selectOption("unknown");
  await page.locator("#segmentNotes").fill("Isolated verification: corrected boundary and context.");
  await page.locator("#saveSegmentBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open);
  const pending = await (await page.request.get(`${baseURL}${apiPath}`)).json();
  assert.equal(pending.coverage, 0);
  assert.equal(pending.suggestions.segments.find((s) => s.id === item.id).operating_context, "unknown");
  await page.locator(`[data-inspect="${item.id}"]`).click();
  await page.screenshot({path: path.join(artifacts, "evidence-dialog.png")});
  await page.locator("#acceptSuggestionBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open);
  await page.reload();
  await page.locator("#pendingCount").waitFor();
  const accepted = await (await page.request.get(`${baseURL}${apiPath}`)).json();
  const confirmed = accepted.annotation.segments.find((s) => s.suggestion_id === item.id);
  assert(confirmed && confirmed.origin === "auto_suggested" && confirmed.review_status === "accepted");
  assert.equal(confirmed.labels.operating_context, "unknown");
  assert.equal(confirmed.end_s, item.end_s - 0.25);
  assert.equal(confirmed.rule_version, "ctx2-rules-v1");
  assert.equal(confirmed.human_confirmation.reviewer, "Automated workflow verification (isolated test)");
  assert.equal(confirmed.original_proposal.operating_context, item.operating_context);
  assert(accepted.coverage > 0);

  await page.locator("#reviewerName").fill("Second automated reviewer (isolated test)");
  await page.locator(`[data-segment="${confirmed.id}"]`).first().click();
  await page.locator("#thermalPhaseSelect").selectOption("unknown");
  await page.locator("#saveSegmentBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open);
  const correctedAccepted = await (await page.request.get(`${baseURL}${apiPath}`)).json();
  const reconfirmed = correctedAccepted.annotation.segments.find((s) => s.suggestion_id === item.id);
  assert.equal(reconfirmed.human_confirmation.reviewer, "Second automated reviewer (isolated test)");
  assert.equal(reconfirmed.confirmation_history[0].reviewer, confirmed.human_confirmation.reviewer);
  assert.equal(reconfirmed.origin, "auto_suggested");
  assert.deepEqual(reconfirmed.original_proposal, confirmed.original_proposal);

  await page.locator("#annotationLanes").scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(artifacts, "reviewed-timeline.png")});
  await page.setViewportSize({width: 390, height: 844});
  await page.locator("#sessionTitle").scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(artifacts, "mobile.png")});
  const mobileOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  assert.equal(mobileOverflow, false, "Mobile page stays within the viewport.");
  await page.locator("#annotationLanes").scrollIntoViewIfNeeded();
  await page.screenshot({path: path.join(artifacts, "mobile-timeline.png")});
  await page.locator("[data-inspect]").first().click();
  const mobileDialog = await page.locator("#annotationDialog").boundingBox();
  assert(mobileDialog.x >= 0 && mobileDialog.x + mobileDialog.width <= 391);
  await page.screenshot({path: path.join(artifacts, "mobile-dialog.png")});
  await page.locator("#closeDialogBtn").click();
  const after = {raw: hash(rawFile), canonical: hash(sourceFile), existingHumanAnnotation: hash(humanFile)};
  assert.deepEqual(after, before);
  assert.deepEqual(errors, []);
  const result = {verified_at: new Date().toISOString(), source_session_id: sessionID,
    loaded: true, auto_segmented: true, visualized: pixels > 100, manually_corrected: true,
    accepted: true, persisted_and_reloaded: true, source_and_existing_annotation_hashes_unchanged: true,
    before_sha256: before, after_sha256: after, canvas_signal_pixels: pixels, timeline_framing: framing,
    desktop_viewport: [1440, 1100], mobile_viewport: [390, 844], mobile_overflow: mobileOverflow,
    isolated_verification_only: true, campaign_review_added: false, browser_errors: errors};
  result.accepted_correction_preserves_confirmation_history = true;
  fs.writeFileSync("data/context_annotation/exports/ctx2_review_verification.json", JSON.stringify(result, null, 2) + "\n");
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
})().catch(async (error) => { console.error(error); if (activeBrowser) await activeBrowser.close(); process.exitCode = 1; });
