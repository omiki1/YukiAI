// Invented passages, isolated storage, and an offline reply provider.
const {chromium}=require('../app/node_modules/playwright-core');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/characters');
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
const directory=fs.mkdtempSync(path.join(root,'.cache','characters-verify-'));
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
story.create_run('自编风铃故事','初见',0);run=story.view()['active']['id']
story.ingest('旁白：美咲和小春在走廊等雨停，美咲被大家叫作班长。\\n班长：我没有去过旧校舍。\\n小春：我刚才听见了风铃。','auto','','fixture-0001',story.epoch(),run)
class Fixture:
 async def complete_story(self,text,snapshot):
  return {'text':'旧回复标记：此处应在纠正后收起。','emotion':'neutral','answer_type':'analysis','evidence_ids':[snapshot.events[-1]['id']]}
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
  const wait=async predicate=>{for(let n=0;n<100;n++){if(await predicate())return;await new Promise(resolve=>setTimeout(resolve,80));}throw new Error('Character condition timed out');};
  await wait(async()=>{try{return (await fetch(origin+'api/status')).ok;}catch{return false;}});
  const api=async(name,body)=>{const response=await fetch(origin+'api/'+name,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();assert.ok(response.ok,JSON.stringify(data));return data;};
  const state=()=>api('story'),characters=()=>api('story/characters');
  const originals=(await state()).active.events;
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:280,height:380}}),errors=[],external=[];
  page.on('pageerror',error=>errors.push(String(error)));await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  const panel=async()=>{await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab="events"]').click();if(!await page.locator('#character-panel').evaluate(element=>element.open))await page.locator('#character-panel>summary').click();await page.waitForFunction(()=>!document.querySelector('#character-new').disabled);};
  const reload=async()=>{await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);await panel();};
  const row=id=>page.locator('#character-list [data-correction-id="'+id+'"]');
  const preview=async()=>{await page.locator('#character-preview-button').click();await page.waitForFunction(()=>!document.querySelector('#character-preview').hidden&&!document.querySelector('#character-preview-button').disabled);};
  const save=async count=>{await page.locator('#character-agree').check();await page.locator('#character-save').click();await wait(async()=>(await characters()).count===count);await page.waitForFunction(()=>document.querySelector('#character-form').hidden&&!document.querySelector('#character-new').disabled);};
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);await panel();
  await page.locator('#character-new').click();await page.locator('#character-label').fill('班长');await page.locator('#character-target').fill('美咲');
  assert.ok(await page.locator('#character-save').isDisabled());await preview();assert.equal((await characters()).count,0);
  assert.match(await page.locator('#character-evidence').innerText(),/美咲被大家叫作班长/);assert.ok(await page.locator('#character-save').isDisabled());
  await page.locator('#character-evidence button').click();assert.match(await page.locator('#story-evidence-lines').innerText(),/美咲被大家叫作班长/);await page.locator('#story-evidence-back').click();
  await page.locator('#character-agree').check();await page.locator('#character-target').fill('小春');assert.ok(await page.locator('#character-save').isDisabled());assert.ok(await page.locator('#character-preview').isHidden());
  await page.locator('#character-target').fill('美咲');await preview();await page.screenshot({path:path.join(out,'preview-280.png'),omitBackground:true});
  assert.ok(await page.locator('#character-panel').evaluate(element=>element.scrollWidth<=element.clientWidth));await save(1);
  let data=await characters();const alias=data.corrections[0];assert.match(await row(alias.correction_id).innerText(),/班长 → 美咲/);
  await page.locator('#character-new').click();await page.locator('#character-kind').selectOption('pronoun');await page.locator('#character-target').fill('美咲');await preview();
  assert.match(await page.locator('#character-description').innerText(),/第 3 句/);await save(2);
  data=await characters();const pronoun=data.corrections.find(item=>item.kind==='pronoun');assert.equal(pronoun.active,true);
  await reload();assert.match(await row(pronoun.correction_id).innerText(),/此处生效/);
  await api('story/import',{epoch:(await state()).epoch,run_id:ready.run,text:'旁白：后来两人走到窗边。',kind:'auto',speaker:'',request_id:'fixture-0002',confirmed_read:true});
  await reload();assert.match(await row(pronoun.correction_id).innerText(),/历史记录/);
  await row(alias.correction_id).getByRole('button',{name:'修改',exact:true}).click();assert.ok(await page.locator('#character-label').isDisabled());assert.ok(await page.locator('#character-kind').isDisabled());
  await page.locator('#character-target').fill('小春');await preview();await save(2);assert.match(await row(alias.correction_id).innerText(),/班长 → 小春/);
  await api('story/progress',{epoch:(await state()).epoch,position:3});await reload();assert.match(await row(alias.correction_id).innerText(),/班长 → 美咲/);assert.match(await row(pronoun.correction_id).innerText(),/此处生效/);
  await api('story/progress',{epoch:(await state()).epoch,position:4});await reload();
  // A response already completed but delayed in transit must still be discarded.
  let deliver,started;const gate=new Promise(resolve=>deliver=resolve),captured=new Promise(resolve=>started=resolve);
  await page.route('**/api/chat',async route=>{const response=await route.fetch();started();await gate;try{await route.fulfill({response});}catch{}});
  await page.locator('#story-close').click();await page.locator('#chat-toggle').click();await page.locator('#message').fill('班长说过什么');await page.locator('#message').press('Enter');await captured;
  await panel();await row(alias.correction_id).getByRole('button',{name:'修改',exact:true}).click();await page.locator('#character-target').fill('美咲');await preview();await save(2);deliver();await page.waitForTimeout(200);
  assert.equal(await page.evaluate(()=>window.yuki.pending),false);assert.doesNotMatch(await page.locator('#reply').textContent(),/旧回复标记/);await page.unroute('**/api/chat');
  await row(alias.correction_id).getByRole('button',{name:'删除',exact:true}).click();assert.equal((await characters()).count,2);
  await row(alias.correction_id).getByRole('button',{name:'确认删除所有版本',exact:true}).click();await wait(async()=>(await characters()).count===1);
  await api('story/progress',{epoch:(await state()).epoch,position:3});await reload();assert.equal(await row(alias.correction_id).count(),0);
  assert.deepEqual((await state()).active.events,originals);
  await page.locator('[data-story-tab="manage"]').click();await page.waitForFunction(()=>!document.querySelector('#memory-backup').disabled);
  await page.locator('#memory-backup').click();await wait(async()=>(await api('memory')).backups.length===1);await page.waitForFunction(()=>!document.querySelector('#memory-backup').disabled);
  assert.equal((await api('memory')).backups[0].counts.corrections,1);
  await page.locator('#memory-backups button').filter({hasText:'恢复副本'}).click();await page.waitForFunction(()=>!document.querySelector('#memory-preview').hidden&&!document.querySelector('#memory-refresh').disabled);
  await page.locator('#memory-agree').check();await page.locator('#memory-confirm').click();await wait(async()=>(await state()).active.id!==ready.run);
  await page.waitForFunction(()=>!document.querySelector('#memory-refresh').disabled);await page.locator('[data-story-tab="events"]').click();
  await page.waitForFunction(()=>document.querySelector('#character-list').textContent.includes('她 → 美咲'));
  assert.equal((await characters()).corrections[0].active,true);assert.deepEqual((await state()).active.events.map(({id,run_id,branch_id,...event})=>event),originals.map(({id,run_id,branch_id,...event})=>event));
  await page.screenshot({path:path.join(out,'restored-280.png'),omitBackground:true});await page.setViewportSize({width:320,height:460});await page.screenshot({path:path.join(out,'restored-320.png'),omitBackground:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={passed:true,small_viewport:true,original_evidence:true,explicit_confirmation:true,preview_invalidation:true,alias_edit:true,pronoun_expires:true,rewind_versions:true,reload:true,
   late_response_discarded:true,delete_all_versions:true,originals_preserved:true,backup_restores_corrections:true,cloud_calls:0,errors,external};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
