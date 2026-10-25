/* Automated UI actions use isolated sidecars, never human campaign annotations. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const {spawn} = require("node:child_process");
const {chromium} = require("playwright");
const campaign = "data/context_annotation/campaigns/ecuread-filtered-raw-18-v1";
const pilot = [2,4,5,7,18].map(n=>`session_${String(n).padStart(6,"0")}`);
const artifacts = `${campaign}/exports/ctx24-browser`;
const hash = p=>crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex");
let browser, server, tmp, debugPage;

(async()=>{
  tmp=fs.mkdtempSync(path.join(os.tmpdir(),"ctx24-browser-"));
  const annotations=path.join(tmp,"annotations"), suggestions=path.join(tmp,"suggestions"), macros=path.join(tmp,"macro_suggestions");
  for(const dir of [annotations,suggestions,macros,artifacts]) fs.mkdirSync(dir,{recursive:true});
  for(const id of pilot) {
    fs.copyFileSync(`${campaign}/suggestions/${id}.suggestions.json`,`${suggestions}/${id}.suggestions.json`);
    fs.copyFileSync(`${campaign}/macro_suggestions/${id}.macro.json`,`${macros}/${id}.macro.json`);
    if(fs.existsSync(`${campaign}/annotations/${id}.context.json`)) fs.copyFileSync(`${campaign}/annotations/${id}.context.json`,`${annotations}/${id}.context.json`);
  }
  const files=[`${campaign}/corpus_manifest.json`,`${campaign}/provenance/operator_attestation.v1.json`,
    ...["suggestions","macro_suggestions","annotations"].flatMap(d=>fs.readdirSync(`${campaign}/${d}`).filter(n=>n.endsWith(".json")).map(n=>`${campaign}/${d}/${n}`)),
    ...fs.readdirSync("real_data/fulldata_afterclean").map(n=>`real_data/fulldata_afterclean/${n}`)];
  const before=Object.fromEntries(files.map(p=>[p,hash(p)]));
  const listener=net.createServer(); await new Promise(r=>listener.listen(0,"127.0.0.1",r));
  const port=listener.address().port; await new Promise(r=>listener.close(r));
  const base=`http://127.0.0.1:${port}`;
  server=spawn(".venv/bin/python",["-m","app.context_annotation.web","--port",String(port),"--corpus-manifest",`${campaign}/corpus_manifest.json`,"--annotations-dir",annotations,"--suggestions-dir",suggestions,"--exports-dir",path.join(tmp,"exports")],{stdio:"ignore"});
  let ready=false;
  for(let i=0;i<120;i++) {try {await fetch(base);ready=true;break;}catch {await new Promise(r=>setTimeout(r,500));}}
  assert(ready);
  browser=await chromium.launch({headless:true,executablePath:process.env.CTX2_CHROMIUM_EXECUTABLE});
  const page=await browser.newPage({viewport:{width:1440,height:1100}}), errors=[], sessions=[];
  debugPage = page;
  page.on("pageerror",e=>errors.push(e.message));
  for(const id of pilot) {
    await page.goto(`${base}/context-annotation/session/${id}`);
    await page.locator("[data-macro-inspect]").first().waitFor({timeout:120000});
    await page.waitForFunction(()=>CTX.telemetry && CTX.chartRows.length===4 && document.querySelector("#reviewEnd").value,null,{timeout:120000});
    assert.equal(await page.locator("#detailedReview").getAttribute("open"),null);
    assert.equal(await page.locator("#macroOperating").getAttribute("aria-selected"),"true");
    const detail=await (await page.request.get(`${base}/context-annotation/api/sessions/${id}`)).json();
    const op=detail.macro_review.episodes.filter(e=>e.axis==="operating"), thermal=detail.macro_review.episodes.filter(e=>e.axis==="thermal");
    assert(op.length<detail.suggestions.segments.length);
    const episode=op.find(e=>e.child_ids.length>1);
    await page.locator(`[data-macro-inspect="${episode.id}"]`).click();
    await page.waitForFunction(()=>document.querySelectorAll("[data-macro-signal]").length===4,null,{timeout:120000});
    assert.equal(await page.locator("[data-child-id]").count(),episode.child_ids.length);
    assert((await page.locator("#macroEvidence").textContent()).includes("boundary_samples"));
    await page.locator("[data-child-id] summary").first().click();
    const child=await page.locator("[data-child-id] pre").first().textContent();
    for(const key of ["speed_kmh","rpm","tps","ect","original_proposal"]) assert(child.includes(key));
    for(const key of ["vehicle_speed_kmh","rpm","tps_voltage","ect_c"]) {
      const pixels=await page.locator(`[data-macro-signal="${key}"]`).evaluate(c=>{
        const data=c.getContext("2d").getImageData(0,0,c.width,c.height).data;let n=0;
        for(let i=0;i<data.length;i+=4) if(data[i]<200 && data[i+1]<200 && data[i+2]<200) n++;
        return n;
      });
      assert(pixels>30,`${id} ${key} blank`);
    }
    await page.screenshot({path:`${artifacts}/${id}-evidence.png`});
    await page.locator("#macroClose").click();
    await page.locator("#macroThermal").click();
    assert((await page.locator("#macroPage").textContent()).includes(`${thermal.length} episodes`));
    await page.locator("#macroOperating").click();
    await page.locator("#showEventMarkers").check();
    assert((await page.locator("#annotationLanes").textContent()).includes("TPS burst"), `${id}: ${await page.locator("#annotationLanes").textContent()}; state=${JSON.stringify(await page.evaluate(()=>({checked:document.querySelector("#showEventMarkers").checked,events:CTX.suggestions?.events?.length,status:CTX.suggestions?.events?.[0]?.review_status})))}, errors=${JSON.stringify(errors)}`);
    await page.locator("#showEventMarkers").uncheck();
    await page.locator("#showRapidMarkers").check();
    assert((await page.locator("#annotationLanes").textContent()).includes("Detailed transitions"));
    await page.locator("#showRapidMarkers").uncheck();
    sessions.push({session_id:id,canonical_samples:detail.session.sample_count,operating_macro_count:op.length,thermal_macro_count:thermal.length,expanded_original_intervals:episode.child_ids.length,signal_canvases_verified:4});
  }
  const id=pilot.at(-1), endpoint=`${base}/context-annotation/api/sessions/${id}`;
  await page.locator("#reviewerName").fill("CTX2.4 isolated automated test, not human campaign review");
  // Seed a genuine detailed acceptance only in temporary storage to prove preservation.
  let detail=await (await page.request.get(endpoint)).json();
  const fine=detail.suggestions.segments[0];
  assert.equal((await page.request.post(endpoint+"/suggestion-review",{data:{suggestion_ids:[fine.id],action:"accept",reviewer:"Isolated automated test"}})).status(),200);
  const acceptedHash=hash(`${annotations}/${id}.context.json`);
  await page.locator("#macroNextPending").click();
  await page.locator("#macroDialog").waitFor({state:"visible",timeout:120000});
  await page.waitForFunction(()=>document.querySelectorAll("[data-macro-signal]").length===4,null,{timeout:120000});
  const selected=await page.evaluate(()=>({id:CTX.macroId,start:Number(document.querySelector("#macroStart").value),end:Number(document.querySelector("#macroEnd").value)}));
  const correctedEnd=Number((selected.end-Math.min(.125,(selected.end-selected.start)/4)).toFixed(6));
  await page.locator("#macroEnd").fill(String(correctedEnd));
  await page.locator("#macroNotes").fill("Isolated test only; not a campaign annotation.");
  const acceptanceResponse = page.waitForResponse(r=>r.url().endsWith("/review") && r.request().method()==="POST");
  await page.locator("#macroAccept").click();
  const acceptedResponse = await acceptanceResponse;
  assert.equal(acceptedResponse.status(),200,await acceptedResponse.text());
  await page.waitForFunction(()=>!document.querySelector("#macroDialog").open,null,{timeout:120000});
  for(const action of ["Uncertain","Reject"]) {
    await page.locator("#macroNextPending").click();
    await page.locator("#macroDialog").waitFor({state:"visible",timeout:120000});
    await page.waitForFunction(()=>document.querySelectorAll("[data-macro-signal]").length===4,null,{timeout:120000});
    await page.locator(`#macro${action}`).click();
    await page.waitForFunction(()=>!document.querySelector("#macroDialog").open,null,{timeout:120000});
  }
  await page.reload();await page.locator("[data-macro-inspect]").first().waitFor({timeout:120000});
  const regeneration=await page.request.post(endpoint+"/auto-label",{data:{thresholds:{}}});
  assert.equal(regeneration.status(),200);
  detail=await (await page.request.get(endpoint)).json();
  const accepted=detail.macro_review.episodes.find(e=>e.id===selected.id);
  assert.equal(accepted.review_status,"accepted");assert.equal(accepted.end_s,correctedEnd);
  assert.equal(detail.macro_review.progress.operating.uncertain,1);assert.equal(detail.macro_review.progress.operating.rejected,1);
  assert.equal(hash(`${annotations}/${id}.context.json`),acceptedHash);
  assert.equal(detail.suggestions.segments.filter(s=>s.review_status==="accepted").length,1);
  await page.reload();await page.locator("[data-macro-inspect]").first().waitFor({timeout:120000});
  await page.locator("#macroFilter").selectOption("accepted");
  await page.locator(`[data-macro-inspect="${selected.id}"]`).click();
  await page.waitForFunction(()=>document.querySelectorAll("[data-macro-signal]").length===4,null,{timeout:120000});
  assert(await page.locator("#macroAccept").isDisabled());assert(await page.locator("#macroStart").isDisabled());
  await page.screenshot({path:`${artifacts}/desktop-accepted.png`});
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.querySelector("[data-macro-signal]").width<250);
  await page.screenshot({path:`${artifacts}/mobile-accepted.png`});
  const box=await page.locator("#macroDialog").boundingBox();assert(box.x>=0 && box.x+box.width<=391);
  await page.locator("#macroClose").click();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator("#macroFilter").selectOption("pending");
  await page.locator("#macroOperating").scrollIntoViewIfNeeded();
  assert.equal(await page.locator(".macro-section .suggestion-table-wrap").evaluate(e=>e.scrollWidth>e.clientWidth),false);
  await page.screenshot({path:`${artifacts}/mobile-queue.png`});
  // Every original interval remains inspectable from the expanded macro.
  await page.locator("#macroNextPending").click();
  await page.locator("#macroDialog").waitFor({state:"visible",timeout:120000});
  await page.locator("[data-child-id] summary").first().click();
  await page.locator("[data-child-inspect]").first().click();
  assert(await page.locator("#annotationDialog").isVisible());
  await page.locator("#closeDialogBtn").click();
  const after=Object.fromEntries(files.map(p=>[p,hash(p)]));assert.deepEqual(after,before);assert.deepEqual(errors,[]);
  const report={schema_version:"ctx24-browser-verification-v1",verified_at:new Date().toISOString(),
    isolated_temporary_sidecars_only:true,sessions,macro_first_queue:true,independent_thermal_review:true,
    optional_event_and_transition_markers:true,macro_micro_expansion:true,correct_accept_reject_uncertain:true,
    corrections_persist_after_reload_and_regeneration:true,accepted_detailed_annotation_sha_unchanged:true,
    macro_acceptance_does_not_accept_details:true,finalized_macro_overwrite_blocked:true,mobile_overflow:false,
    desktop_viewport:[1440,1100],mobile_viewport:[390,844],campaign_files_unchanged:true,
    campaign_acceptances_added:0,actual_human_review_time_s:null,manual_boundary_quality:"pending",errors,
    before_sha256:before,after_sha256:after};
  fs.writeFileSync(`${campaign}/exports/ctx24_browser_verification.json`,JSON.stringify(report,null,2)+"\n");
  console.log(JSON.stringify({...report,before_sha256:undefined,after_sha256:undefined},null,2));
})().catch(async e=>{
  console.error(e);
  if(debugPage) {console.error("UI error:",await debugPage.locator("#macroError").textContent());await debugPage.screenshot({path:`${artifacts}/failure.png`});}
  process.exitCode=1;
}).finally(async()=>{
  if(browser) await browser.close();
  if(server && server.exitCode===null) {
    server.kill("SIGTERM");
    await new Promise(resolve=>{server.once("exit",resolve);setTimeout(()=>{server.kill("SIGKILL");resolve();},10000).unref();});
  }
  if(tmp) fs.rmSync(tmp,{recursive:true,force:true});
});
