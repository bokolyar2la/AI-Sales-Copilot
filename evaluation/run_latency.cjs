// Run with Node and PLAYWRIGHT_MODULE pointing to an installed playwright package.
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='http://127.0.0.1:8877';
const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'latency-fixtures.json'),'utf8'));
const output=path.join(__dirname,'latency-results.json');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const results=[];
 try {
  // Fixed balanced AB/BA order, one request at a time: no experiment concurrency.
  for(let i=0;i<cases.length;i++){
   const c=cases[i]; const modes=i%2?['stream_off','current']:['current','stream_off'];
   for(const mode of modes){
    const setup=await page.request.post(base+'/experiment/setup',{data:{case:c.id,mode}});
    if(!setup.ok())throw Error('Setup failed '+setup.status());
    const seed=await setup.json();
    await page.goto(base);
    await page.getByRole('button',{name:'Открыть список разговоров'}).click();
    await page.locator('.sidebar nav button').filter({hasText:c.id}).first().click();
    await page.getByRole('textbox',{name:'Реплика',exact:true}).fill(seed.text);
    await page.evaluate(()=>{window.__copilotDiagnostics=[]});
    const responsePromise=page.waitForResponse(r=>r.url().endsWith('/turns')&&r.request().method()==='POST',{timeout:125000});
    const clicked=Date.now();
    await page.getByRole('textbox',{name:'Реплика',exact:true}).press('Control+Enter');
    let row={case:c.id,mode,expect:c.expect};
    try{
     const response=await responsePromise;
     const data=await response.json();
     await page.waitForFunction(()=>window.__copilotDiagnostics?.length>0,{},{timeout:10000});
     const timing=await page.evaluate(()=>window.__copilotDiagnostics.at(-1));
     const rec=data.recommendations?.at(-1);
     row={...row,timing,analysis_error:data.analysis_error||null,recommendation:rec||null,http_status:response.status(),turns:data.turns};
    }catch(e){row={...row,error_type:e.name,elapsed_ms:Date.now()-clicked};}
    results.push(row);
    fs.writeFileSync(output,JSON.stringify({method:'16 paired synthetic checkpoints; balanced AB/BA; sequential; same model/prompt/budget; thinking disabled in both; Edge headless 1440x1000',results},null,2));
    console.log(c.id,mode,row.timing?.outcome||row.error_type,Math.round(row.timing?.display_proxy_ms||row.elapsed_ms));
   }
  }
 }finally{await browser.close();}
})();
