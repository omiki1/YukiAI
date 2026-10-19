// Starts its own isolated, offline server. Never uses production data or API settings.
const {chromium}=require('../app/node_modules/playwright-core');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/memory');
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
const directory=fs.mkdtempSync(path.join(root,'.cache','memory-verify-'));
const python=process.env.YUKI_TEST_PYTHON||'C:/Users/freeing1/anaconda3/envs/myenv/python.exe';
const source=`
import json,socket,sys
from pathlib import Path
import uvicorn
from scripts.yuki_server import create_app
from scripts.yuki_config import ConfigStore
from scripts.yuki_history import HistoryStore
from scripts.yuki_story import StoryStore
from scripts.test_capture import CaptureFixtureRuntime
root=Path(sys.argv[1])
story=StoryStore(root/'data'/'story.sqlite3')
story.create_run('自编雨天故事','初见',0)
run=story.view()['active']['id']
story.ingest('旁白：雨落在走廊前。\\n美咲：一起去图书室吧。','auto','','fixture-0001',story.epoch(),run)
story.bookmark('走廊的雨',story.epoch())
for turn in range(2):
 snapshot=story.snapshot('美咲说什么',story.epoch())
 story.append_exchange(snapshot,'美咲说什么',{'text':'她想去图书室。','emotion':'neutral','answer_type':'analysis','evidence_ids':[snapshot.events[-1]['id']]})
 if turn==0:story.new_chat(story.epoch())
history=HistoryStore(root/'history')
uid=history.create()
history.append_exchange(uid,'陪我看看雨','好呀，一起慢慢看雨。','happy')
app=create_app(history_directory=root/'history',story=story,config=ConfigStore(root/'settings.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1],'history':uid}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
`;
(async()=>{
 const server=spawn(python,['-u','-c',source,directory],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,logs='';server.stderr.on('data',data=>logs=(logs+data.toString()).slice(-4000));
 try{
  const ready=await new Promise((resolve,reject)=>{
   let text='';const timeout=setTimeout(()=>reject(new Error('Fixture startup timed out '+logs)),15000);
   server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exited '+code+' '+logs));});
   server.stdout.on('data',data=>{text+=data.toString();const line=text.split('\n').find(line=>line.startsWith('{'));if(line){try{const result=JSON.parse(line);clearTimeout(timeout);resolve(result);}catch{}}});
  });
  const origin='http://127.0.0.1:'+ready.port+'/';
  for(let tries=0;tries<60;tries++){try{if((await fetch(origin+'api/status')).ok)break;}catch{}await new Promise(resolve=>setTimeout(resolve,100));}
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:280,height:380}}),errors=[],external=[];
  page.on('pageerror',error=>errors.push(String(error)));
  await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  const inventory=async()=>await (await fetch(origin+'api/memory')).json();
  const state=async()=>await (await fetch(origin+'api/story')).json();
  const wait=async predicate=>{for(let n=0;n<100;n++){if(await predicate())return;await new Promise(resolve=>setTimeout(resolve,80));}throw new Error('Memory condition timed out');};
  const manage=async()=>{await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab="manage"]').click();await page.waitForFunction(()=>!document.querySelector('#memory-refresh').disabled);};
  const confirm=async()=>{await page.locator('#memory-agree').check();await page.locator('#memory-confirm').click();await page.waitForFunction(()=>document.querySelector('#memory-preview').hidden&&!document.querySelector('#memory-refresh').disabled);};
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  await manage();
  assert.match(await page.locator('#memory-runs').innerText(),/2 轮讨论/);
  const bounds=await page.locator('#story-panel').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=280&&bounds.y+bounds.height<=380);
  assert.ok(await page.locator('#memory-panel').evaluate(element=>element.scrollWidth<=element.clientWidth));
  const tabs=await page.locator('.story-tabs').evaluate(element=>({width:element.clientWidth,scroll:element.scrollWidth}));assert.ok(tabs.scroll<=tabs.width,'Tabs must fit at 280px');
  await page.locator('#memory-backup').click();await wait(async()=>(await inventory()).backups.length===1);await page.waitForFunction(()=>!document.querySelector('#memory-refresh').disabled);
  await page.locator('#memory-runs button').filter({hasText:'清空讨论'}).click();await page.waitForFunction(()=>!document.querySelector('#memory-preview').hidden);
  assert.equal(await page.locator('#memory-confirm').isDisabled(),true);
  assert.match(await page.locator('#memory-description').innerText(),/全部 2 轮讨论/);
  await page.screenshot({path:path.join(out,'cleanup-preview-280.png'),omitBackground:true});
  await page.locator('#memory-cancel').click();assert.equal((await inventory()).runs[0].story_turns,2);
  await page.locator('#memory-runs button').filter({hasText:'清空讨论'}).click();await confirm();
  assert.equal((await inventory()).runs[0].story_turns,0);assert.equal((await inventory()).runs[0].events,2);
  await page.locator('#memory-backups button').filter({hasText:'恢复副本'}).click();await page.waitForFunction(()=>!document.querySelector('#memory-preview').hidden&&!document.querySelector('#memory-refresh').disabled);assert.match(await page.locator('#memory-description').innerText(),/现有记录保留/);await confirm();
  assert.equal((await inventory()).runs.length,2);let active=(await state()).active;assert.match(active.name,/恢复副本/);assert.equal(active.position,2);assert.equal(active.messages.length,2);
  const restored=active.id;
  await page.locator('#memory-runs article').first().locator('button').filter({hasText:'删除共读'}).click();await confirm();
  assert.equal((await inventory()).runs.length,1);assert.equal((await state()).active.id,restored);
  await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);assert.equal((await state()).active.id,restored);
  await manage();await page.locator('details').filter({has:page.locator('#memory-histories')}).locator('summary').click();
  const historyRow=page.locator('#memory-histories article[data-memory-id="'+ready.history+'"]');
  await historyRow.getByRole('button',{name:'继续聊',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#memory-refresh').disabled);
  assert.equal((await state()).active,null);assert.equal(await page.evaluate(()=>localStorage.getItem('yuki.session')),ready.history);
  await page.waitForFunction(()=>document.querySelector('#reply').textContent.includes('一起慢慢看雨'));
  const peer=await browser.newPage({viewport:{width:320,height:460}});
  peer.on('pageerror',error=>errors.push(String(error)));
  await peer.addInitScript(uid=>localStorage.setItem('yuki.session',uid),ready.history);
  await peer.goto(origin,{waitUntil:'networkidle'});
  await peer.waitForFunction(()=>document.querySelector('#reply').textContent.includes('一起慢慢看雨'));
  await historyRow.getByRole('button',{name:'删除',exact:true}).click();await confirm();
  assert.notEqual(await page.evaluate(()=>localStorage.getItem('yuki.session')),ready.history);
  assert.equal((await fetch(origin+'api/sessions/'+ready.history)).status,404);assert.doesNotMatch(await page.locator('#reply').textContent(),/一起慢慢看雨/);
  await peer.waitForFunction(uid=>localStorage.getItem('yuki.session')!==uid,ready.history);
  assert.doesNotMatch(await peer.locator('#reply').textContent(),/一起慢慢看雨/);await peer.close();
  await page.locator('#memory-backups button').filter({hasText:'删除备份'}).click();await confirm();assert.equal((await inventory()).backups.length,0);
  await page.locator('#memory-compact').click();await page.waitForFunction(()=>document.querySelector('#memory-status').textContent.startsWith('已整理'));
  await page.locator('.story-content').evaluate(element=>element.scrollTop=0);
  await page.screenshot({path:path.join(out,'memory-manager-280.png'),omitBackground:true});
  await page.setViewportSize({width:320,height:460});await page.screenshot({path:path.join(out,'memory-manager-320.png'),omitBackground:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={passed:true,small_viewport:true,hidden_discussions_counted:true,confirmation_and_cancel:true,
   clear_preserves_story:true,additive_restore:true,restart:true,scoped_delete:true,history_reopen_and_delete:true,
   backup_deletion:true,compaction:true,other_client_drops_deleted_chat:true,cloud_calls:0,errors,external};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
