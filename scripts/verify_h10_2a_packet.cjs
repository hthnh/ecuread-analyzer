/* Rendering checks only. This script never supplies human review decisions. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");
const root = path.resolve("data/evaluation/h10_2a");
const packet = JSON.parse(fs.readFileSync(path.join(root,"review_packet.json"),"utf8"));
let browser;

(async()=>{
  browser = await chromium.launch({headless:true,executablePath:process.env.CTX2_CHROMIUM_EXECUTABLE});
  const page = await browser.newPage({viewport:{width:1280,height:1000}});
  const checked = [], errors = [];
  page.on("pageerror", e=>errors.push(e.message));
  for (const r of packet.regions) {
    assert.equal(r.review.status,"pending");
    assert.equal(r.review.action,null);
    assert.equal(r.review.manual_review_time_s,null);
    const svg = fs.readFileSync(path.join(root,r.plot.path),"utf8");
    await page.setContent(`<style>body{margin:0}svg{display:block;max-width:100%;height:auto}</style>${svg}`);
    const result = await page.evaluate(async svg=>{
      const textBoxes=[...document.querySelectorAll("text")].map(t=>({text:t.textContent,b:t.getBBox()}));
      const outside=textBoxes.filter(({b})=>b.x<0 || b.y<0 || b.x+b.width>1200.1 || b.y+b.height>940.1).map(x=>x.text);
      const counts=await new Promise((resolve,reject)=>{
        const img=new Image();
        img.onerror=reject;
        img.onload=()=>{
          const canvas=document.createElement("canvas"); canvas.width=1200; canvas.height=940;
          const ctx=canvas.getContext("2d"); ctx.drawImage(img,0,0);
          const d=ctx.getImageData(0,0,1200,940).data;
          const colors=[[6,118,71],[37,99,235],[181,71,8],[190,24,93]], n=colors.map(()=>0);
          for(let i=0;i<d.length;i+=4) for(let j=0;j<colors.length;j++) {
            if(colors[j].every((c,k)=>Math.abs(d[i+k]-c)<8)) n[j]++;
          }
          resolve(n);
        };
        img.src="data:image/svg+xml;charset=utf-8,"+encodeURIComponent(svg);
      });
      return {outside,counts,polylines:document.querySelectorAll("polyline").length,
              overflow:document.documentElement.scrollWidth>innerWidth};
    },svg);
    assert.deepEqual(result.outside,[],`${r.region_id}: text outside plot`);
    assert.equal(result.polylines,4);
    assert(result.counts.every(n=>n>25),`${r.region_id}: blank measured signal`);
    assert.equal(result.overflow,false);
    if(r.capture_id==="session_000001" || (r.capture_id==="session_000023" && r.selection.label==="warming")) {
      await page.screenshot({path:path.join(root,`${r.capture_id}-desktop.png`)});
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      await page.screenshot({path:path.join(root,`${r.capture_id}-mobile.png`)});
      await page.setViewportSize({width:1280,height:1000});
    }
    checked.push({region_id:r.region_id,measured_signal_pixel_counts:result.counts,text_within_plot:true});
  }
  assert.deepEqual(errors,[]);
  const out={schema_version:"h10.2a-plot-render-verification-v1",regions_checked:checked.length,
    four_measured_signals_nonblank:true,desktop_mobile_framing_verified:true,javascript_errors:errors,
    human_boundary_quality:"not assessed",human_review_time:"not measured",checks:checked};
  const destination=path.join(root,"plot_render_verification.json");
  fs.writeFileSync(destination,JSON.stringify(out,null,2)+"\n",{flag:"wx"});
  console.log(JSON.stringify({regions_checked:checked.length,nonblank_signals:checked.length*4,errors}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser) await browser.close();});
