// Run against a create_app(..., capture_runtime=RollingCaptureFixtureRuntime()) fixture.
// All dialogue is invented; never contacts providers or attaches a real user game.
const {chromium}=require('../app/node_modules/playwright-core');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/capture');fs.mkdirSync(out,{recursive:true});
const origin=process.env.YUKI_CAPTURE_VERIFY_URL||'http://127.0.0.1:12397/';
process.env.TEMP=process.env.TMP=path.join(root,'.cache');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:280,height:380}}),errors=[],external=[],calls=[],dialogs=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss();});
  await page.route('**/*',route=>{const url=route.request().url();if(!url.startsWith(origin)){external.push(url);return route.abort();}calls.push(new URL(url).pathname);return route.continue();});
  const get=async endpoint=>(await page.request.get(origin+'api/'+endpoint)).json();
  const post=async(endpoint,body)=>{const response=await page.request.post(origin+'api/'+endpoint,{data:body});assert.ok(response.ok(),await response.text());return response.json();};
  const existing=await get('capture');if(existing.connected)await post('capture/detach',{session_id:existing.session_id});
  const story=await get('story');await post('story/runs',{epoch:story.epoch,title:'自编自动取词 UI '+Date.now(),name:'本机验证'});
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  await page.keyboard.press('Tab');await page.locator('#story-toggle').click();await page.locator('[data-story-tab=capture]').click();
  await page.locator('#capture-process').selectOption('4660');await page.locator('#capture-attach').click();
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('#capture-thread option')).some(el=>el.textContent.includes('美咲')));
  assert.equal((await get('story')).active.position,0);
  const options=await page.locator('#capture-thread option').evaluateAll(elements=>elements.map(el=>({value:el.value,text:el.textContent})));
  const dialogue=options.find(option=>option.text.includes('美咲'));assert.ok(dialogue);
  await page.locator('#capture-thread').selectOption(dialogue.value);
  assert.match(await page.locator('#capture-preview').textContent(),/美咲/);
  assert.equal(await page.locator('#capture-record').isDisabled(),true);
  await page.locator('#capture-select').click();await page.waitForFunction(()=>document.querySelector('#capture-status').textContent.includes('已选正文'));
  await page.locator('#capture-confirm').check();assert.equal(await page.locator('#capture-record').isEnabled(),true);
  await page.screenshot({path:path.join(out,'capture-confirm-280.png'),omitBackground:true});
  await page.locator('#capture-record').click();
  const deadline=Date.now()+12000;while(Date.now()<deadline&&(await get('story')).active.position<2)await page.waitForTimeout(150);
  const recorded=await get('story');assert.ok(recorded.active.position>=2);assert.ok(recorded.active.events.every(event=>event.source.startsWith('textractor:')&&event.speaker==='美咲'));
  assert.doesNotMatch(JSON.stringify(recorded.active.events),/菜单噪声/);
  await page.locator('#capture-pause').click();await page.waitForFunction(()=>document.querySelector('#capture-status').textContent.includes('已暂停'));
  const position=(await get('story')).active.position;await page.waitForTimeout(2200);assert.equal((await get('story')).active.position,position);
  // Untrusted preview is text; picking it never executes its HTML.
  const menu=options.find(option=>option.text.includes('<img'));await page.locator('#capture-thread').selectOption(menu.value);
  assert.equal(await page.locator('#capture-preview img').count(),0);assert.match(await page.locator('#capture-preview').textContent(),/<img/);
  await page.locator('#capture-thread').selectOption(dialogue.value);await page.locator('#capture-select').click();
  await page.waitForFunction(()=>document.querySelector('#capture-status').textContent.includes('已选正文'));
  await page.locator('#capture-confirm').check();await page.locator('#capture-record').click();
  await page.waitForFunction(()=>document.querySelector('#capture-status').textContent.includes('正在记录'));
  const before=await get('story');assert.ok(before.active.tip>=1,JSON.stringify(before));await post('story/progress',{epoch:before.epoch,position:1});
  await page.waitForFunction(()=>document.querySelector('#capture-status').textContent.includes('阅读位置已改变'));
  assert.equal((await get('capture')).recording,false);await page.waitForTimeout(1600);assert.equal((await get('story')).active.position,1);
  assert.equal(await page.locator('#capture-confirm').isChecked(),false);
  await page.screenshot({path:path.join(out,'capture-paused-280.png'),omitBackground:true});
  await page.setViewportSize({width:320,height:460});await page.locator('#capture-process').scrollIntoViewIfNeeded();
  const layout=await page.locator('#story-panel').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,content:el.querySelector('.story-content').clientHeight}));
  assert.ok(layout.scroll<=layout.width+1);assert.ok(layout.content>80);
  await page.screenshot({path:path.join(out,'capture-preview-320.png'),omitBackground:true});
  await page.locator('#capture-detach').click();await page.waitForFunction(()=>document.querySelector('#capture-connected').hidden);
  assert.equal((await get('capture')).connected,false);
  assert.equal(errors.length,0,errors.join('\n'));assert.equal(external.length,0,external.join('\n'));assert.equal(dialogs.length,0);
  assert.ok(!calls.some(url=>['/api/chat','/api/speech','/api/settings/test-llm','/api/settings/test-voice'].includes(url)));
  const report={passed:true,preview_does_not_save:true,selected_body_only:true,automatic_sqlite_records:recorded.active.position,
   speaker_recognized:true,pause_stops_recording:true,progress_change_pauses:true,html_preview_is_text:true,
   minimum_viewport:'280×380',layout,errors,external,cloud_calls:0};
  fs.writeFileSync(path.join(out,'ui-results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
