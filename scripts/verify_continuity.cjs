// Own offline server and invented story; never touches production settings or memory.
const {chromium}=require('../app/node_modules/playwright-core');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/continuity');
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
const directory=fs.mkdtempSync(path.join(root,'.cache','continuity-verify-'));
const python=process.env.YUKI_TEST_PYTHON||'C:/Users/freeing1/anaconda3/envs/myenv/python.exe';
const source=`
import json,socket,sys
from pathlib import Path
import uvicorn
from scripts.yuki_server import create_app
from scripts.yuki_config import ConfigStore
from scripts.yuki_story import StoryStore
from scripts.test_capture import CaptureFixtureRuntime
root=Path(sys.argv[1]);story=StoryStore(root/'data'/'story.sqlite3')
story.create_run('自编纸鹤故事','初见',0);run=story.view()['active']['id']
story.ingest('旁白：黄昏的走廊里有一只纸鹤。\\n美咲：我没有去过旧校舍。','auto','','fixture-0001',story.epoch(),run)
snap=story.snapshot('她为什么避开旧校舍？',story.epoch())
story.append_exchange(snap,'她为什么避开旧校舍？',{'text':'这是她自己的说法，先留一个疑问。','emotion':'neutral','answer_type':'analysis','evidence_ids':[snap.events[-1]['id']]})
class Fixture:
 async def complete_story(self,text,snapshot):
  return {'text':'旧回复标记：这句不应在手记修改后出现。','emotion':'neutral','answer_type':'analysis','evidence_ids':[snapshot.events[-1]['id']]}
app=create_app(Fixture(),root/'history',story=story,config=ConfigStore(root/'settings.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
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
  const wait=async predicate=>{for(let n=0;n<100;n++){if(await predicate())return;await new Promise(resolve=>setTimeout(resolve,80));}throw new Error('Continuity condition timed out');};
  await wait(async()=>{try{return (await fetch(origin+'api/status')).ok;}catch{return false;}});
  const api=async(name,body)=>{const response=await fetch(origin+'api/'+name,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();assert.ok(response.ok,JSON.stringify(data));return data;};
  const state=()=>api('story'),continuity=()=>api('story/continuity');
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:280,height:380}}),errors=[],external=[];
  page.on('pageerror',error=>errors.push(String(error)));await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  const panel=async()=>{await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab="events"]').click();await page.waitForFunction(()=>document.querySelector('#continuity-location').textContent.includes('上次读到第'));};
  const details=async id=>{if(!await page.locator('#'+id).evaluate(element=>element.open))await page.locator('#'+id+'>summary').click();};
  const reload=async()=>{await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);await panel();};
  const row=id=>page.locator('#continuity-notes [data-note-id="'+id+'"]');
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);await panel();await details('continuity-recap-box');
  assert.match(await page.locator('#continuity-recap').innerText(),/美咲说过/);assert.match(await page.locator('#continuity-topics').innerText(),/为什么避开旧校舍/);
  await page.screenshot({path:path.join(out,'recap-280.png'),omitBackground:true});
  await page.locator('#continuity-recap button').last().click();assert.match(await page.locator('#story-evidence-lines').innerText(),/我没有去过旧校舍/);await page.locator('#story-evidence-back').click();
  await page.locator('#continuity-topics button').click();await wait(async()=>(await continuity()).note_count===1);await details('continuity-notes-box');
  await page.locator('#continuity-new').click();const original='<img src=x onerror=alert(1)> 我猜纸鹤和小春有关。';
  await page.locator('#continuity-text').fill(original);await page.locator('#continuity-kind').selectOption('theory');await page.locator('#continuity-link').check();
  await page.screenshot({path:path.join(out,'note-form-280.png'),omitBackground:true});
  assert.ok(await page.locator('#continuity-form').evaluate(element=>element.scrollWidth<=element.clientWidth));
  await page.locator('#continuity-save').click();await wait(async()=>(await continuity()).note_count===2);
  let data=await continuity(),manual=data.notes.find(note=>note.kind==='theory');const manualId=manual.note_id;
  await page.waitForFunction(()=>document.querySelector('#continuity-notes').textContent.includes('<img'));
  assert.equal(await page.locator('#continuity-notes img').count(),0);
  await api('story/import',{epoch:(await state()).epoch,run_id:ready.run,text:'旁白：后来一阵风吹开了窗。',kind:'auto',speaker:'',request_id:'fixture-0002',confirmed_read:true});
  await reload();await details('continuity-notes-box');await row(manualId).getByRole('button',{name:'修改',exact:true}).click();
  const corrected='我猜纸鹤是小春折的，仍未证实。';await page.locator('#continuity-text').fill(corrected);await page.locator('#continuity-save').click();
  await page.waitForFunction(()=>document.querySelector('#continuity-notes').textContent.includes('仍未证实'));
  await row(manualId).getByRole('button',{name:'标记聊清楚',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#continuity-notes').textContent.includes('已聊清楚'));
  await api('story/progress',{epoch:(await state()).epoch,position:2});await reload();await details('continuity-notes-box');
  assert.match(await row(manualId).innerText(),/和小春有关/);assert.doesNotMatch(await row(manualId).innerText(),/仍未证实|已聊清楚/);
  await details('continuity-recap-box');assert.doesNotMatch(await page.locator('#continuity-recap').innerText(),/吹开了窗/);
  await api('story/progress',{epoch:(await state()).epoch,position:3});await reload();await details('continuity-notes-box');assert.match(await row(manualId).innerText(),/仍未证实/);
  // A completed response delayed in transit must not replace the bubble after notes change.
  let deliver,started;const gate=new Promise(resolve=>deliver=resolve),captured=new Promise(resolve=>started=resolve);
  await page.route('**/api/chat',async route=>{const response=await route.fetch();started();await gate;try{await route.fulfill({response});}catch{}});
  await page.locator('#story-close').click();await page.locator('#chat-toggle').click();await page.locator('#message').fill('迟到的纸鹤问题');await page.locator('#message').press('Enter');await captured;
  await panel();await details('continuity-notes-box');await row(manualId).getByRole('button',{name:'修改',exact:true}).click();await page.locator('#continuity-text').fill('先把猜测放下，再留意原文。');await page.locator('#continuity-save').click();
  await page.waitForFunction(()=>document.querySelector('#continuity-notes').textContent.includes('先把猜测放下'));deliver();await page.waitForTimeout(200);
  assert.equal(await page.evaluate(()=>window.yuki.pending),false);assert.doesNotMatch(await page.locator('#reply').textContent(),/旧回复标记/);await page.unroute('**/api/chat');
  await row(manualId).getByRole('button',{name:'删除',exact:true}).click();assert.equal((await continuity()).note_count,2);
  await row(manualId).getByRole('button',{name:'确认删除所有版本',exact:true}).click();await wait(async()=>(await continuity()).note_count===1);
  await page.locator('#story-close').click();await page.locator('#more-toggle').click();await page.locator('#new-chat').click();await wait(async()=>(await continuity()).recap.topics.length===0);
  assert.equal((await continuity()).note_count,1);await panel();await page.locator('[data-story-tab="manage"]').click();await page.waitForFunction(()=>!document.querySelector('#memory-backup').disabled);
  await page.locator('#memory-backup').click();await wait(async()=>(await api('memory')).backups.length===1);await page.waitForFunction(()=>!document.querySelector('#memory-backup').disabled);
  await page.locator('#memory-backups button').filter({hasText:'恢复副本'}).click();await page.waitForFunction(()=>!document.querySelector('#memory-preview').hidden&&!document.querySelector('#memory-refresh').disabled);
  await page.locator('#memory-agree').check();await page.locator('#memory-confirm').click();await wait(async()=>(await state()).active.id!==ready.run);
  await page.waitForFunction(()=>!document.querySelector('#memory-refresh').disabled);await page.locator('[data-story-tab="events"]').click();await details('continuity-notes-box');
  await page.waitForFunction(()=>document.querySelector('#continuity-notes').textContent.includes('为什么避开旧校舍'));
  assert.equal((await continuity()).note_count,1);assert.ok(await page.locator('#continuity').evaluate(element=>element.scrollWidth<=element.clientWidth));
  await page.screenshot({path:path.join(out,'notes-restored-280.png'),omitBackground:true});await page.setViewportSize({width:320,height:460});await page.screenshot({path:path.join(out,'notes-restored-320.png'),omitBackground:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={passed:true,small_viewport:true,original_evidence:true,pin_question:true,edit_and_resolve:true,rewind_versions:true,reload:true,
   raw_text_escaped:true,late_response_discarded:true,delete_all_versions:true,new_chat_keeps_notes:true,backup_restores_notes:true,cloud_calls:0,errors,external};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
