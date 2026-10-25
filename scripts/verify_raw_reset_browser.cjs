/* Read-only verification of the active RAW campaign after legacy retirement. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {chromium} = require("playwright");
const campaign = "data/context_annotation/campaigns/ecuread-filtered-raw-18-v1";
const base = process.env.CTX_STUDIO_URL || "http://127.0.0.1:8767";
let browser;

(async () => {
  browser = await chromium.launch({headless: true, executablePath: process.env.CTX2_CHROMIUM_EXECUTABLE});
  const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const report = JSON.parse(fs.readFileSync(`${campaign}/exports/raw_reset_autolabel.json`));
  const artifacts = `${campaign}/exports/raw-reset-browser`;
  fs.mkdirSync(artifacts, {recursive: true});
  await page.goto(`${base}/context-annotation`);
  await page.waitForFunction(() => document.querySelectorAll("#sessionTableBody tr").length === 18, null, {timeout: 120000});
  const listing = await (await page.request.get(`${base}/context-annotation/api/sessions`)).json();
  assert.equal(listing.sessions.length,18);
  assert.equal(listing.summary.total_sessions,18);
  assert.equal(listing.summary.parse_failures,0);
  assert.equal(listing.summary.unannotated,18);
  assert(listing.sessions.every((s) => !report.retired_session_ids.includes(s.source_id)));
  assert(listing.sessions.every((s) => s.filename.startsWith("real_data/fulldata_afterclean/")));
  await page.screenshot({path: `${artifacts}/sessions.png`});
  const id = "session_000018";
  await page.goto(`${base}/context-annotation/session/${id}`);
  await page.locator("[data-inspect]").first().waitFor({timeout: 120000});
  const detail = await (await page.request.get(`${base}/context-annotation/api/sessions/${id}`)).json();
  assert.equal(detail.session.sample_count,17976);
  assert.equal(detail.annotation.segments.length,0);
  assert.equal(detail.suggestions.pending_count,846);
  assert.equal(detail.capture_provenance.confirmed_session_metadata.trip_context,"out_of_city");
  const telemetry = await (await page.request.get(`${base}/context-annotation/api/sessions/${id}/telemetry?max_points=20000`)).json();
  assert.equal(telemetry.sample_count,17976);
  assert.equal(telemetry.duration_s,4496.001);
  const speed = telemetry.signals.find((s) => s.key==="vehicle_speed_kmh");
  assert.equal(speed.available_count,17976);
  assert.equal(speed.points.at(-1).t,4496.001);
  await page.locator("#reviewKind").selectOption("operating");
  await page.locator("#nextPendingBtn").click();
  const evidence = await page.locator("#evidenceDetails").textContent();
  for(const key of ["speed_kmh","rpm","tps","ect"]) assert(evidence.includes(key));
  await page.screenshot({path: `${artifacts}/desktop-evidence.png`});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path: `${artifacts}/mobile-evidence.png`});
  const box = await page.locator("#annotationDialog").boundingBox();
  assert(box.x>=0 && box.x+box.width<=391);
  await page.locator("#closeDialogBtn").click();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth>innerWidth),false);
  const canvasPixels = await page.locator('.chart-row[data-signal="vehicle_speed_kmh"] canvas').evaluate((c) => {
    const data=c.getContext("2d").getImageData(0,0,c.width,c.height).data;
    let count=0;
    for(let i=0;i<data.length;i+=4) if(data[i]<100 && data[i+1]<180 && data[i+2]>70) count++;
    return count;
  });
  assert(canvasPixels>100); assert.deepEqual(errors,[]);
  const annotations=fs.readdirSync(`${campaign}/annotations`).filter((p) => p.endsWith(".json"));
  assert.equal(annotations.length,0);
  const result={verified_at:new Date().toISOString(),studio_url:base,visible_sessions:18,
    legacy_sessions_visible:0,all_sources_from_requested_raw_folder:true,long_session_samples:17976,
    long_session_duration_s:4496.001,full_speed_coverage:true,review_evidence_visible:true,
    campaign_labels_accepted:0,nonblank_canvas_pixels:canvasPixels,mobile_overflow:false,browser_errors:errors};
  fs.writeFileSync(`${campaign}/exports/raw_reset_browser_verification.json`,JSON.stringify(result,null,2)+"\n");
  console.log(JSON.stringify(result,null,2));
})().catch((e) => {console.error(e);process.exitCode=1;}).finally(async () => {if(browser) await browser.close();});
