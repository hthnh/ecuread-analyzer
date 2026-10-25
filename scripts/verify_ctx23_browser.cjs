/* All review actions below use temporary sidecars, never the research campaign. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");
const campaign = "data/context_annotation/campaigns/ecuread-filtered-raw-18-v1";
const pilot = ["session_000002", "session_000004", "session_000005", "session_000007", "session_000018"];
const artifacts = `${campaign}/exports/ctx23-browser`;
const hash = (p) => crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
let browser, server;

(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ctx23-browser-"));
  const annotations = path.join(tmp, "annotations"), suggestions = path.join(tmp, "suggestions");
  fs.mkdirSync(annotations); fs.mkdirSync(suggestions); fs.mkdirSync(artifacts, {recursive: true});
  for (const id of pilot) fs.copyFileSync(`${campaign}/suggestions/${id}.suggestions.json`, path.join(suggestions, `${id}.suggestions.json`));
  const files = [`${campaign}/corpus_manifest.json`, "data/context_annotation/curated_corpus.json",
    "data/context_annotation/annotations/ride-20260920-1504.context.json",
    ...fs.readdirSync(`${campaign}/raw`).map((n) => `${campaign}/raw/${n}`),
    ...fs.readdirSync(`${campaign}/suggestions`).map((n) => `${campaign}/suggestions/${n}`)];
  const before = Object.fromEntries(files.map((p) => [p, hash(p)]));
  const listener = net.createServer();
  await new Promise((r) => listener.listen(0, "127.0.0.1", r));
  const port = listener.address().port;
  await new Promise((r) => listener.close(r));
  const base = `http://127.0.0.1:${port}`;
  server = spawn(".venv/bin/python", ["-m", "app.context_annotation.web", "--port", String(port),
    "--corpus-manifest", `${campaign}/corpus_manifest.json`, "--annotations-dir", annotations,
    "--suggestions-dir", suggestions, "--exports-dir", path.join(tmp, "exports")], {stdio: "ignore"});
  let ready = false;
  for (let i=0; i<120; i++) {
    if (server.exitCode !== null) throw new Error(`Server exited: ${server.exitCode}`);
    try {await fetch(base); ready = true; break;} catch {await new Promise((r) => setTimeout(r, 500));}
  }
  assert(ready);
  browser = await chromium.launch({headless: true, executablePath: process.env.CTX2_CHROMIUM_EXECUTABLE});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [], sessions = [];
  page.on("pageerror", (e) => errors.push(e.message));
  for (const id of pilot) {
    const start = performance.now();
    await page.goto(`${base}/context-annotation/session/${id}`);
    await page.locator("[data-inspect]").first().waitFor({timeout: 120000});
    const detail = await (await page.request.get(`${base}/context-annotation/api/sessions/${id}`)).json();
    assert.equal(detail.capture_provenance.machine_verified, false);
    assert.equal(detail.annotation.segments.length, 0);
    assert(detail.session.research_signal_provenance.available_count > 0);
    await page.locator("#reviewKind").selectOption("operating");
    await page.locator("#nextPendingBtn").click();
    const evidence = await page.locator("#evidenceDetails").textContent();
    for (const signal of ["speed_kmh", "rpm", "tps", "ect"]) assert(evidence.includes(signal));
    assert.equal(await page.evaluate(() => Boolean(suggestionById(CTX.editSuggestionId).event)), false);
    await page.locator("#closeDialogBtn").click();
    sessions.push({session_id: id, sample_count: detail.session.sample_count, initial_view_and_evidence_ms: performance.now()-start});
  }
  const id = pilot.at(-1), endpoint = `${base}/context-annotation/api/sessions/${id}`;
  await page.locator("#reviewerName").fill("CTX2.3 isolated browser verification, not a campaign reviewer");
  await page.locator("#captureProvenance summary").click();
  assert((await page.locator("#captureProvenanceFields").textContent()).includes("out_of_city"));
  await page.screenshot({path: `${artifacts}/desktop-provenance.png`});
  await page.locator("#nextPendingBtn").click();
  const selected = await page.evaluate(() => ({id: CTX.editSuggestionId,
    end: Number(document.querySelector("#segmentEnd").value), start: Number(document.querySelector("#segmentStart").value)}));
  const correctedEnd = Number((selected.end-Math.min(.25,(selected.end-selected.start)/4)).toFixed(6));
  await page.locator("#segmentEnd").fill(String(correctedEnd));
  await page.locator("#segmentNotes").fill("Isolated compatibility verification only.");
  await page.locator("#acceptSuggestionBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open, {timeout: 120000});
  const acceptedPath = path.join(annotations, `${id}.context.json`), acceptedSHA = hash(acceptedPath);
  await page.locator("#nextPendingBtn").click();
  const uncertainId = await page.evaluate(() => CTX.editSuggestionId);
  await page.locator("#uncertainSuggestionBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open, {timeout: 120000});
  await page.locator("#reviewKind").selectOption("throttle_burst");
  await page.locator("#nextPendingBtn").click();
  const rejectedId = await page.evaluate(() => CTX.editSuggestionId);
  assert.equal(await page.evaluate(() => suggestionById(CTX.editSuggestionId).event), "throttle_burst");
  await page.screenshot({path: `${artifacts}/desktop-event-evidence.png`});
  await page.locator("#rejectSuggestionBtn").click();
  await page.waitForFunction(() => !document.querySelector("#annotationDialog").open, {timeout: 120000});
  assert.equal(hash(acceptedPath), acceptedSHA);
  await page.reload();
  await page.locator("[data-inspect]").first().waitFor({timeout: 120000});
  const generated = await page.request.post(endpoint + "/auto-label", {data: {thresholds: {}}});
  assert.equal(generated.status(), 200);
  const detail = await (await page.request.get(endpoint)).json();
  const accepted = detail.annotation.segments.find((s) => s.suggestion_id === selected.id);
  assert.equal(accepted.end_s, correctedEnd);
  assert(accepted.human_confirmation.reviewer.includes("isolated browser"));
  assert.equal(detail.suggestions.segments.find((s) => s.id === uncertainId).review_status, "uncertain");
  assert.equal(detail.suggestions.events.find((s) => s.id === rejectedId).review_status, "rejected");
  assert.equal(hash(acceptedPath), acceptedSHA);
  assert(accepted.original_proposal.end_s !== accepted.end_s);
  const histories = [...detail.suggestions.segments,...detail.suggestions.events].flatMap((s) => s.review_history);
  assert(histories.filter((h) => h.review_time_s !== null).length >= 4);
  await page.locator("#reviewFilter").selectOption("accepted");
  assert.equal(await page.locator("[data-inspect]").count(), 1);
  await page.locator(`[data-inspect="${selected.id}"]`).click();
  assert.equal(await page.locator("#acceptSuggestionBtn").isVisible(), false);
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: `${artifacts}/mobile-evidence.png`});
  const box = await page.locator("#annotationDialog").boundingBox();
  assert(box.x >= 0 && box.x+box.width <= 391);
  await page.locator("#closeDialogBtn").click();
  await page.locator("#captureProvenance summary").click();
  await page.locator("#captureProvenance").scrollIntoViewIfNeeded();
  await page.screenshot({path: `${artifacts}/mobile-provenance.png`});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const pixels = await page.locator('.chart-row[data-signal="vehicle_speed_kmh"] canvas').evaluate((c) => {
    const p = c.getContext("2d").getImageData(0,0,c.width,c.height).data;
    let colored = 0;
    for (let i=0;i<p.length;i+=4) if(p[i]<100 && p[i+1]<180 && p[i+2]>70) colored++;
    return colored;
  });
  assert(pixels>100);
  const after = Object.fromEntries(files.map((p) => [p, hash(p)]));
  assert.deepEqual(after,before); assert.deepEqual(errors,[]);
  assert.equal(fs.readdirSync(`${campaign}/annotations`).length,0);
  const result = {schema_version: "ctx23-browser-verification-v1", verified_at: new Date().toISOString(),
    isolated_temporary_sidecars_only: true, sessions, human_campaign_review_time_s: null,
    accept_correct_reject_uncertain_tested: true, speed_rpm_tps_ect_evidence_visible: true,
    operating_event_filter_and_next_pending_tested: true, throttle_burst_inspected: true,
    accepted_annotations_preserved_after_reload_and_regeneration: true,
    accepted_proposals_not_editable: true, campaign_annotations_added: 0, campaign_suggestions_accepted: 0,
    source_hashes_preserved: true, mobile_overflow: false, canvas_signal_pixels: pixels, errors,
    desktop_viewport: [1440,1100], mobile_viewport: [390,844], before_sha256: before, after_sha256: after};
  fs.writeFileSync(`${campaign}/exports/ctx23_browser_verification.json`,JSON.stringify(result,null,2)+"\n");
  console.log(JSON.stringify({verified: true, sessions, errors, campaign_annotations_added: 0},null,2));
})().catch((e) => {console.error(e);process.exitCode=1;}).finally(async () => {
  if(browser) await browser.close();
  if(server && server.exitCode===null) {
    const exited = new Promise((r) => server.once("exit",r)); server.kill("SIGTERM");
    const timeout=setTimeout(() => server.kill("SIGKILL"),10000);
    await exited;clearTimeout(timeout);
  }
});
