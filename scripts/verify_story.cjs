// Full local UI + SQLite fixture server. Never configure or contact a cloud provider.
const {chromium}=require('../app/node_modules/playwright-core');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/story');fs.mkdirSync(out,{recursive:true});
const origin=process.env.YUKI_STORY_VERIFY_URL||'http://127.0.0.1:12396/';
process.env.TEMP=process.env.TMP=path.join(root,'.cache');
const common='旁白：放学后，庭院里下起了雨。\n美咲：我们先在走廊等一会儿吧。';
const secret='美咲：只有在A路线才会发现，旧钟楼的钥匙藏在红色信封里。';
const other='小春：在B路线，我们决定去图书室找雨伞。';
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:320,height:460}}),errors=[],external=[],calls=[];
  const touchModel=async()=>{
   const point=await page.locator('canvas').evaluate(c=>{
    const gl=c.getContext('webgl2')||c.getContext('webgl'),p=new Uint8Array(c.width*c.height*4),r=c.getBoundingClientRect();gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,p);let chosen=0,distance=Infinity;
    for(let i=0;i<c.width*c.height;i++)if(p[i*4+3]>230){const x=i%c.width,y=Math.floor(i/c.width),d=(x-c.width/2)**2+(y-c.height/2)**2;if(d<distance){chosen=i;distance=d;}}
    return {x:r.left+(chosen%c.width+.5)/c.width*r.width,y:r.top+(c.height-1-Math.floor(chosen/c.width)+.5)/c.height*r.height};
   });await page.mouse.move(point.x,point.y);await page.waitForFunction(()=>document.querySelector('.window-bar').classList.contains('revealed'));
  };
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',route=>{const url=route.request().url();if(!url.startsWith(origin)){external.push(url);return route.abort();}calls.push(new URL(url).pathname);return route.continue();});
  const state=async()=>(await page.request.get(origin+'api/story')).json();
  const waitSaved=()=>page.waitForFunction(()=>document.querySelector('#story-status').textContent.startsWith('已收录'));
  const tab=async name=>page.locator(`[data-story-tab=${name}]`).click();
  const ingest=async text=>{await tab('import');await page.locator('#story-text').fill(text);await page.locator('#story-confirm').check();await page.locator('#story-import-submit').click();await waitSaved();};
  const ask=async text=>{await page.locator('#chat-toggle').click();await page.locator('#message').fill(text);await page.locator('#message').press('Enter');await page.waitForFunction(()=>window.yuki.typing&&!window.yuki.pending);await page.locator('#line').click();};
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  await touchModel();await page.locator('#story-toggle').click();await page.locator('#story-new-toggle').click();
  await page.locator('#story-game-title').fill('自编校园故事 '+Date.now());await page.locator('#story-run-name').fill('初见测试');
  await page.screenshot({path:path.join(out,'new-reading.png'),omitBackground:true});
  await page.locator('#story-new-form button').click();await page.waitForFunction(()=>document.querySelector('#story-status').textContent.startsWith('已建立'));
  await ingest(common);assert.equal((await state()).active.position,2);
  await tab('bookmarks');await page.locator('#story-bookmark-name').fill('共同段 · 下雨的走廊');await page.locator('#story-bookmark-form button').click();
  await page.waitForFunction(()=>document.querySelector('#story-bookmarks').textContent.includes('下雨的走廊'));
  await ingest(secret);const aBranch=(await state()).active.branch_id;
  await page.locator('#story-close').click();await ask('美咲刚才说了什么？');
  assert.match(await page.locator('#reply').innerText(),/红色信封/);
  await page.locator('#evidence-toggle').click();assert.match(await page.locator('#story-evidence-lines').innerText(),/红色信封/);
  await page.screenshot({path:path.join(out,'answer-evidence.png'),omitBackground:true});
  await tab('bookmarks');await page.locator('#story-bookmarks button').first().click();await page.waitForFunction(()=>document.querySelector('#story-status').textContent.startsWith('已回到书签'));
  assert.equal((await state()).active.position,2);assert.doesNotMatch(await page.locator('#reply').textContent(),/红色信封/);
  await ingest(other);const bState=await state(),bBranch=bState.active.branch_id;
  assert.notEqual(aBranch,bBranch);assert.equal(bState.active.branches.length,2);assert.ok(!JSON.stringify(bState.active.events).includes('红色信封'));
  assert.ok(!JSON.stringify(bState.active.memory).includes('红色信封'));assert.ok(JSON.stringify(bState.active.memory).includes('图书室'));
  await page.locator('#story-memory summary').click();
  await page.waitForFunction(()=>document.querySelector('#story-memory-body').textContent.includes('图书室'));
  assert.match(await page.locator('#story-position').innerText(),/本场/);
  assert.match(await page.locator('#story-memory-body').innerText(),/正在读/);
  assert.doesNotMatch(await page.locator('#story-memory-body').innerText(),/红色信封|旧钟楼/);
  await page.locator('#story-memory-body button').first().click();
  await page.waitForFunction(()=>document.querySelector('#story-evidence-lines').textContent.length>0);
  assert.doesNotMatch(await page.locator('#story-evidence-lines').innerText(),/红色信封/);
  await page.locator('#story-evidence-back').click();
  await page.screenshot({path:path.join(out,'b-route-events.png'),omitBackground:true});
  await page.locator('#story-close').click();await ask('现在一起分析吧。');
  assert.match(await page.locator('#reply').textContent(),/图书室/);assert.doesNotMatch(await page.locator('#reply').textContent(),/红色信封/);
  await page.locator('#more-toggle').click();await page.locator('#history-toggle').click();assert.doesNotMatch(await page.locator('#history-lines').innerText(),/红色信封/);await page.locator('#history-close').click();
  await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  assert.match(await page.locator('#reply').textContent(),/图书室/);assert.equal((await state()).active.branch_id,bBranch);
  await touchModel();await page.locator('#story-toggle').click();await tab('events');await page.locator('#story-events .story-position-button').first().click();
  await page.waitForFunction(()=>document.querySelector('#story-position').textContent.includes('已读 1 句'));
  assert.equal(await page.locator('#story-events article').count(),1);await page.locator('#story-next').click();
  await page.waitForFunction(()=>document.querySelector('#story-position').textContent.includes('已读 2 句'));
  await tab('bookmarks');await page.locator('#story-branch').selectOption(bBranch);
  await page.waitForFunction(()=>document.querySelector('#story-position').textContent.includes('已读 3 句'));
  // Delay a response after the backend finished, then restore a checkpoint before delivery.
  let deliver,started;const gate=new Promise(r=>deliver=r),captured=new Promise(r=>started=r);
  await page.route('**/api/chat',async route=>{const response=await route.fetch();started();await gate;try{await route.fulfill({response});}catch{}});
  await page.locator('#story-close').click();await page.locator('#chat-toggle').click();await page.locator('#message').fill('这是一条会迟到的讨论');await page.locator('#message').press('Enter');await captured;
  await touchModel();await page.locator('#story-toggle').click();await tab('bookmarks');await page.locator('#story-bookmarks button').first().click();
  await page.waitForFunction(()=>document.querySelector('#story-status').textContent.startsWith('已回到书签'));deliver();
  await page.waitForTimeout(250);assert.equal(await page.evaluate(()=>window.yuki.pending),false);assert.doesNotMatch(await page.locator('#reply').textContent(),/图书室/);
  assert.deepEqual((await state()).active.messages,[]);await page.unroute('**/api/chat');
  // Smallest native window: forms remain inside the paper drawer, with scrollable content.
  await page.setViewportSize({width:280,height:380});await tab('import');
  await page.locator('#story-text').fill('旁白：<script>不应当执行</script>');await page.locator('#story-confirm').check();
  await page.screenshot({path:path.join(out,'import-small.png'),omitBackground:true});
  const panel=await page.locator('#story-panel').boundingBox(),status=await page.locator('.story-footer').boundingBox();
  assert.ok(panel.x>=0&&panel.y>=0&&panel.x+panel.width<=280&&panel.y+panel.height<=380);assert.ok(status.y+status.height<=panel.y+panel.height);
  await page.locator('#story-import-submit').click();await waitSaved();assert.equal(await page.locator('#story-events script').count(),0);assert.match(await page.locator('#story-events').innerText(),/<script>/);
  await page.locator('#story-new-toggle').click();await page.screenshot({path:path.join(out,'new-reading-small.png'),omitBackground:true});
  const footer=await page.locator('.story-footer').boundingBox();assert.ok(footer.y+footer.height<=380);
  await page.locator('#story-new-toggle').click();await tab('bookmarks');await page.screenshot({path:path.join(out,'bookmarks-small.png'),omitBackground:true});
  await page.locator('#story-run').selectOption('');await page.waitForFunction(()=>document.querySelector('#story-position').textContent.includes('先建立'));
  await page.locator('#settings-toggle').click();assert.equal(await page.locator('#story-panel').isVisible(),false);assert.equal(await page.locator('#settings-close').evaluate(el=>document.activeElement===el),true);
  await page.locator('#settings-close').click();assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={passed:true,createAndImport:true,checkpointAndAutoFork:true,routeScopedChatAndEvidence:true,reloadRestoresState:true,progressCalibration:true,lateResponseDiscarded:true,smallWindowForms:true,rawTextEscaped:true,cloudApiCalls:0,errors,external,requests:calls.length};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
