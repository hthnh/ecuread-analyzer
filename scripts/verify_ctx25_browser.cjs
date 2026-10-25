/* Read-only UI verification with copied sidecars; not human interval review. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");
const campaign = "data/context_annotation/campaigns/ecuread-filtered-raw-18-v1";
const pilots = [2,4,5,7,18].map(n => `session_${String(n).padStart(6,"0")}`);
const artifacts = `${campaign}/exports/ctx25-browser`;
const hash = p => crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
let browser, server, tmp;

(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(),"ctx25-browser-"));
  fs.mkdirSync(artifacts,{recursive:true});
  const files = ["suggestions","macro_suggestions","annotations"].flatMap(d => fs.readdirSync(`${campaign}/${d}`).filter(n=>n.endsWith(".json")).map(n=>`${campaign}/${d}/${n}`));
  files.push(`${campaign}/corpus_manifest.json`, `${campaign}/provenance/operator_context_approval.v1.json`,
    ...fs.readdirSync("real_data/fulldata_afterclean").map(n=>`real_data/fulldata_afterclean/${n}`));
  const before = Object.fromEntries(files.map(p=>[p,hash(p)]));
  for (const directory of ["suggestions","macro_suggestions","annotations"]) {
    fs.mkdirSync(path.join(tmp,directory));
    for (const name of fs.readdirSync(`${campaign}/${directory}`)) fs.copyFileSync(`${campaign}/${directory}/${name}`,path.join(tmp,directory,name));
  }
  const listener = net.createServer();
  await new Promise(r=>listener.listen(0,"127.0.0.1",r));
  const port = listener.address().port;
  await new Promise(r=>listener.close(r));
  const base = `http://127.0.0.1:${port}`;
  server = spawn(".venv/bin/python",["-m","app.context_annotation.web","--port",String(port),"--corpus-manifest",`${campaign}/corpus_manifest.json`,
    "--annotations-dir",path.join(tmp,"annotations"),"--suggestions-dir",path.join(tmp,"suggestions"),"--exports-dir",path.join(tmp,"exports")],{stdio:"ignore"});
  let ready = false;
  for (let i=0;i<120;i++) {
    try { await fetch(base); ready=true; break; } catch { await new Promise(r=>setTimeout(r,500)); }
  }
  assert(ready);
  browser = await chromium.launch({headless:true,executablePath:process.env.CTX2_CHROMIUM_EXECUTABLE});
  const page = await browser.newPage({viewport:{width:1440,height:1100}}), errors=[], sessions=[];
  page.on("pageerror", e=>errors.push(e.message));
  for (const id of pilots) {
    await page.goto(`${base}/context-annotation/session/${id}`);
    await page.locator("[data-macro-inspect]").first().waitFor({timeout:120000});
    await page.waitForFunction(()=>CTX.telemetry && CTX.chartRows.length===4,null,{timeout:120000});
    const detail = await (await page.request.get(`${base}/context-annotation/api/sessions/${id}`)).json();
    assert(detail.macro_review.episodes.every(e=>e.review_status==="accepted"));
    assert.equal(await page.locator("#macroFilter").inputValue(),"accepted");
    assert(await page.locator("#macroNextPending").isDisabled());
    const episode = detail.macro_review.episodes.find(e=>e.axis==="operating" && e.child_ids.length>1);
    await page.locator(`[data-macro-inspect="${episode.id}"]`).click();
    await page.waitForFunction(()=>document.querySelectorAll("[data-macro-signal]").length===4,null,{timeout:120000});
    assert((await page.locator("#macroDialogTitle").textContent()).includes("Bulk endorsed"));
    assert((await page.locator("#macroEvidence").textContent()).includes('"individual_interval_review": false'));
    assert(await page.locator("#macroAccept").isDisabled());
    assert(await page.locator("#macroStart").isDisabled());
    assert.equal(await page.locator("[data-child-id]").count(),episode.child_ids.length);
    await page.locator("[data-child-id] summary").first().click();
    const child = await page.locator("[data-child-id] pre").first().textContent();
    for (const key of ["speed_kmh","rpm","tps","ect","original_proposal"]) assert(child.includes(key));
    for (const key of ["vehicle_speed_kmh","rpm","tps_voltage","ect_c"]) {
      const pixels = await page.locator(`[data-macro-signal="${key}"]`).evaluate(c=>{
        const d=c.getContext("2d").getImageData(0,0,c.width,c.height).data; let n=0;
        for(let i=0;i<d.length;i+=4) if(d[i]<200 && d[i+1]<200 && d[i+2]<200) n++;
        return n;
      });
      assert(pixels>30,`${id} ${key} blank`);
    }
    await page.screenshot({path:`${artifacts}/${id}-endorsed.png`});
    await page.locator("#macroClose").click();
    await page.locator("#macroThermal").click();
    assert((await page.locator("#macroRows").textContent()).includes("Bulk endorsed"));
    await page.reload();
    await page.locator("[data-macro-inspect]").first().waitFor({timeout:120000});
    assert.equal(await page.locator("#macroFilter").inputValue(),"accepted");
    sessions.push({session_id:id,canonical_samples:detail.session.sample_count,original_children_inspected:episode.child_ids.length,
      bulk_provenance_visible:true,accepted_labels_protected:true,reload_persists:true,measured_canvases_nonblank:4});
  }
  await page.locator("[data-macro-inspect]").first().click();
  await page.waitForFunction(()=>document.querySelectorAll("[data-macro-signal]").length===4,null,{timeout:120000});
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.querySelector("[data-macro-signal]").width<250);
  await page.screenshot({path:`${artifacts}/mobile-endorsed-evidence.png`});
  const box=await page.locator("#macroDialog").boundingBox();
  assert(box.x>=0 && box.x+box.width<=391);
  await page.locator("#macroClose").click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator("#macroOperating").scrollIntoViewIfNeeded();
  assert.equal(await page.locator(".macro-section .suggestion-table-wrap").evaluate(e=>e.scrollWidth>e.clientWidth),false);
  await page.screenshot({path:`${artifacts}/mobile-endorsed-queue.png`});
  assert.deepEqual(Object.fromEntries(files.map(p=>[p,hash(p)])),before);
  assert.deepEqual(errors,[]);
  const report={schema_version:"ctx25-browser-verification-v1",verified_at:new Date().toISOString(),
    isolated_temporary_sidecars_only:true,sessions,source_and_live_annotation_sha_unchanged:true,
    desktop_mobile_layout_verified:true,javascript_errors:errors,individual_human_boundary_validation:"not_performed"};
  fs.writeFileSync(`${campaign}/exports/ctx25_browser_verification.json`,JSON.stringify(report,null,2)+"\n");
  console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  if(server) { const done = new Promise(r=>server.once("exit",r)); server.kill("SIGTERM"); await done; }
  if(tmp) fs.rmSync(tmp,{recursive:true,force:true});
});
