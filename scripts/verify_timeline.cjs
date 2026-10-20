// Invented story, isolated local files, and no external requests.
const {chromium}=require('../app/node_modules/playwright-core');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/timeline');
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
const directory=fs.mkdtempSync(path.join(root,'.cache','timeline-verify-'));
const python=process.env.YUKI_TEST_PYTHON||path.join(root,'.venv/Scripts/python.exe');
const source=`
import json,socket,sys
from pathlib import Path
import uvicorn
from scripts.yuki_server import create_app
from scripts.yuki_config import ConfigStore
from scripts.yuki_story import StoryStore
from scripts.test_capture import CaptureFixtureRuntime
root=Path(sys.argv[1]);story=StoryStore(root/'data'/'story.sqlite3')
story.create_run('自编雨伞故事','初见',0);run=story.view()['active']['id']
story.ingest('美咲：我没有去过钟楼。\\n小春：可我在那里见过你。\\n选项：问问钟楼的事\\n旁白：她把雨伞靠在窗边。','auto','','fixture-0001',story.epoch(),run)
class NeverCall:
 async def complete_story(self,*args):raise RuntimeError('Browsing must never call the model')
app=create_app(NeverCall(),root/'history',story=story,config=ConfigStore(root/'settings.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1],'run':run}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
`;
(async()=>{
 const server=spawn(python,['-u','-c',source,directory],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,logs='';server.stderr.on('data',data=>logs=(logs+data.toString()).slice(-4000));
 try{
  const ready=await new Promise((resolve,reject)=>{let text='';const timeout=setTimeout(()=>reject(new Error('Fixture startup timed out '+logs)),15000);
   server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exited '+code+' '+logs));});
   server.stdout.on('data',data=>{text+=data.toString();const line=text.split('\n').find(line=>line.startsWith('{'));if(line)try{const result=JSON.parse(line);clearTimeout(timeout);resolve(result);}catch{}});});
  const origin='http://127.0.0.1:'+ready.port+'/';
  const wait=async predicate=>{const deadline=Date.now()+12000;while(Date.now()<deadline){if(await predicate())return;await new Promise(resolve=>setTimeout(resolve,80));}throw new Error('Timeline condition timed out');};
  const api=async(name,body)=>{const response=await fetch(origin+'api/'+name,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();assert.ok(response.ok,JSON.stringify(data));return data;};
  await wait(async()=>{try{return (await fetch(origin+'api/status')).ok;}catch{return false;}});
  const state=()=>api('story'),timeline=()=>api('story/timeline'),post=async(name,body)=>api('story/'+name,{...body,epoch:(await state()).epoch});
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:320,height:460}}),errors=[],external=[],modelCalls=[];
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('request',request=>{if(request.url().includes('/api/chat')||request.url().includes('/api/speech'))modelCalls.push(request.url());});
  await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  const panel=async()=>{if(await page.locator('#story-panel').isHidden())await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab="timeline"]').click();await page.waitForFunction(()=>document.querySelector('#timeline-location').textContent.includes('已读到第'));};
  const reload=async()=>{await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);await panel();};
  const filters=async()=>{if(!await page.locator('#timeline-filters').evaluate(element=>element.open))await page.locator('#timeline-filters>summary').click();};
  const cards=()=>page.locator('#timeline-cards .timeline-card');
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);await panel();
  assert.equal(await cards().count(),3);assert.match(await cards().first().innerText(),/人物说辞/);assert.match(await cards().nth(1).innerText(),/不据此推断/);
  await page.screenshot({path:path.join(out,'timeline-320.png'),omitBackground:true});
  await cards().first().getByRole('button',{name:'看原文',exact:true}).click();assert.match(await page.locator('#story-evidence-lines').innerText(),/小春.*|可我在那里见过你/s);await panel();
  await cards().first().getByRole('button',{name:'记个想法',exact:true}).click();
  const thought='<img src=x onerror=alert(1)> 我猜钟楼有另一条入口。';
  await page.locator('#timeline-note-text').fill(thought);await page.locator('#timeline-note-kind').selectOption('theory');
  assert.match(await page.locator('#timeline-note-source').innerText(),/第 1 句、第 2 句/);
  await page.locator('#timeline-note-form button[type=submit]').click();
  await wait(async()=>(await timeline()).cards.some(card=>card.kind==='note'));
  await page.waitForFunction(()=>document.querySelector('#timeline-cards').textContent.includes('另一条入口'));
  assert.equal(await page.locator('#timeline-cards img').count(),0);assert.match(await page.locator('.timeline-card.note').innerText(),/猜测.*玩家想法/s);
  await reload();assert.match(await page.locator('.timeline-card.note').innerText(),/另一条入口/);
  await filters();await page.locator('#timeline-kind').selectOption('choice');await page.waitForFunction(()=>document.querySelectorAll('#timeline-cards .timeline-card').length===1);
  assert.match(await cards().first().innerText(),/选项节点/);await page.locator('#timeline-clear').click();await page.waitForFunction(()=>document.querySelectorAll('#timeline-cards .timeline-card').length===4);
  await page.locator('#timeline-person').selectOption('小春');await page.waitForFunction(()=>document.querySelectorAll('#timeline-cards .timeline-card').length===2);
  assert.match(await cards().last().innerText(),/玩家手记/);await page.locator('#timeline-clear').click();await page.waitForFunction(()=>document.querySelectorAll('#timeline-cards .timeline-card').length===4);
  await page.locator('#timeline-query').fill('不在原文里的词');await page.locator('#timeline-filter button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('#timeline-cards').textContent.includes('没有符合'));
  await page.locator('#timeline-clear').click();await page.waitForFunction(()=>document.querySelectorAll('#timeline-cards .timeline-card').length===4);
  await page.locator('#timeline-filters>summary').click();await page.setViewportSize({width:280,height:380});
  assert.ok(await page.locator('#story-panel').evaluate(element=>element.scrollWidth<=element.clientWidth));
  assert.ok(await page.locator('#timeline-panel').evaluate(element=>element.scrollWidth<=element.clientWidth));
  await page.locator('#timeline-panel').evaluate(element=>element.closest('.story-content').scrollTop=0);
  await page.screenshot({path:path.join(out,'timeline-280.png'),omitBackground:true});
  await page.locator('.timeline-card.note').getByRole('button',{name:'一起聊',exact:true}).click();
  assert.ok(await page.locator('#story-panel').isHidden());assert.match(await page.locator('#message').inputValue(),/关于我的猜测.*第1句、第2句/s);assert.equal(modelCalls.length,0);
  await panel();await page.locator('.timeline-card.note').getByRole('button',{name:'修改',exact:true}).click();await page.locator('#timeline-note-text').fill('仍未证实的钟楼猜测。');
  await page.locator('#timeline-note-form button[type=submit]').click();await page.waitForFunction(()=>document.querySelector('.timeline-card.note')?.textContent.includes('仍未证实'));
  await page.locator('.timeline-card.note').getByRole('button',{name:'聊清楚了',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.timeline-card.note')?.textContent.includes('已聊清楚'));
  await post('progress',{position:2});await reload();assert.equal(await page.locator('.timeline-card.note').count(),0);assert.doesNotMatch(await page.locator('#timeline-cards').innerText(),/雨伞靠在窗边|问问钟楼/);
  await post('progress',{position:4});await reload();assert.match(await page.locator('.timeline-card.note').innerText(),/仍未证实/);
  const parent=(await state()).active.branch_id;
  await post('fork',{name:'纸鹤分支'});const child=(await state()).active.branch_id;
  await post('import',{run_id:ready.run,text:'小春：分支独有纸鹤线索。',kind:'auto',speaker:'',request_id:'fixture-branch',confirmed_read:true});
  await reload();assert.match(await page.locator('#timeline-cards').innerText(),/分支独有纸鹤/);
  await post('branch',{branch_id:parent});await reload();assert.doesNotMatch(await page.locator('#timeline-cards').innerText(),/分支独有纸鹤/);
  // Delayed old-route responses cannot refill a cleared panel after switching routes.
  let deliver,started;const gate=new Promise(resolve=>deliver=resolve),captured=new Promise(resolve=>started=resolve);let held=false;
  await page.route('**/api/story/timeline?*',async route=>{if(held)return route.continue();held=true;const response=await route.fetch();started();await gate;try{await route.fulfill({response});}catch{}});
  await filters();await page.locator('#timeline-clear').click();await captured;
  await post('branch',{branch_id:child});await page.locator('[data-story-tab="bookmarks"]').click();await page.locator('#story-toggle').evaluate(button=>button.click());
  await page.waitForFunction(()=>document.querySelector('#story-position').textContent.includes('纸鹤分支'));
  await panel();await page.waitForFunction(()=>document.querySelector('#timeline-cards').textContent.includes('分支独有纸鹤'));deliver();await page.waitForTimeout(100);
  assert.match(await page.locator('#timeline-cards').innerText(),/分支独有纸鹤/);await page.unroute('**/api/story/timeline?*');
  await page.locator('.timeline-card.note').getByRole('button',{name:'删除',exact:true}).click();assert.equal((await api('story/timeline?kind=note')).matched,1);
  await page.locator('.timeline-card.note').getByRole('button',{name:'确认删除',exact:true}).click();await wait(async()=>(await api('story/timeline?kind=note')).matched===0);
  await post('import',{run_id:ready.run,text:Array.from({length:55},(_,index)=>'选项：自编分页'+index).join('\n'),kind:'auto',speaker:'',request_id:'fixture-page',confirmed_read:true});
  await reload();assert.equal(await cards().count(),24);await page.locator('#timeline-more').click();await page.waitForFunction(()=>document.querySelectorAll('#timeline-cards .timeline-card').length===48);
  const ids=await cards().evaluateAll(elements=>elements.map(element=>element.dataset.cardId));assert.equal(new Set(ids).size,48);
  await page.locator('#story-close').click();assert.ok(await page.locator('#timeline-panel').evaluate(element=>element.closest('#story-panel').hidden));
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(modelCalls,[]);
  const result={passed:true,viewport_280_320:true,source_links:true,filters:true,reading_order_cards:true,notes_persist:true,notes_edit_resolve_delete:true,
   draft_only:true,rewind_hides_future:true,branch_isolation:true,stale_response_discarded:true,pagination:true,no_horizontal_overflow:true,escaped_text:true,cloud_calls:0,errors,external};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
