// Isolated actual UI/API flow. The native bridge is a fixture; native OCR has a
// separate real Windows acceptance in verify_local_ocr.py. No private data/cloud.
const {chromium}=require('../app/node_modules/playwright-core'),{spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),folder=fs.mkdtempSync(path.join(root,'.cache','game-reading-verify-'));
const source=`
import json,socket,sys
from pathlib import Path
import uvicorn
from scripts.yuki_story import StoryStore
from scripts.yuki_server import create_app
from scripts.yuki_config import ConfigStore
from scripts.test_capture import CaptureFixtureRuntime
folder=Path(sys.argv[1]);story=StoryStore(folder/'story.sqlite3');story.create_run('自编蓝伞故事','游戏共读',0)
story.ingest('旁白：走廊里的灯还没有亮。\\n美咲：我在旧校舍门口等你，记得带那把蓝色的雨伞。','auto','','game-fixture-0001',story.epoch(),story.view()['active']['id'])
app=create_app(history_directory=folder/'history',story=story,config=ConfigStore(folder/'settings.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
`;
(async()=>{
 const server=spawn(process.env.YUKI_TEST_PYTHON||path.join(root,'.venv/Scripts/python.exe'),['-u','-c',source,folder],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,logs='';server.stderr.on('data',data=>logs=(logs+data.toString()).slice(-2500));
 try{
  const ready=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(new Error('Game fixture startup timeout '+logs)),15000);server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exited '+code+' '+logs));});server.stdout.on('data',data=>{output+=data.toString();const line=output.split('\n').find(line=>line.startsWith('{'));if(line){clearTimeout(timeout);resolve(JSON.parse(line));}});});
  const origin='http://127.0.0.1:'+ready.port+'/',api=async(endpoint,body)=>{const response=await fetch(origin+'api/'+endpoint,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});assert.ok(response.ok,endpoint+' '+response.status);return response.json();};
  browser=await chromium.launch({executablePath:process.env.YUKI_TEST_BROWSER||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:280,height:380}}),errors=[],external=[],cloudCalls=[];
  page.on('pageerror',error=>errors.push(String(error)));page.on('request',request=>{if(/\/api\/(chat|speech|settings\/test-)/.test(request.url()))cloudCalls.push(request.url());});
  await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  await page.addInitScript(()=>{
   localStorage.setItem('yuki.guide.dismissed','1');const listeners=[];
   window.fixtureReader={context:null,emit:value=>listeners.forEach(listener=>listener(JSON.stringify(value)))};
   const host={gameReading:{connect:listener=>listeners.push(listener)},setGameReadingContext:raw=>{window.fixtureReader.context=JSON.parse(raw);},
    setupGameReading(){},readGameDialog(){window.fixtureReader.emit({...window.fixtureReader.context,kind:'preview',text:'美咲：我在旧校舍门口等你，记得带那把蓝色的雨伞。',request_id:'screen-browser-fixture-001'});}};
   window.qt={webChannelTransport:{}};window.QWebChannel=function(transport,callback){callback({objects:{windowHost:host}});};
   const original=Element.prototype.append;Element.prototype.append=function(...nodes){const bridge=nodes.find(node=>node?.tagName==='SCRIPT'&&node.src.startsWith('qrc:'));if(bridge){queueMicrotask(()=>bridge.onload());return;}return original.apply(this,nodes);};
  });
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.fixtureReader.context?.mode==='galgame');
  const open=async()=>{await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab=game]').click();};
  await open();await page.locator('#game-read').evaluate(button=>button.click());
  await page.waitForFunction(()=>!document.getElementById('game-confirm').disabled&&document.getElementById('game-match').value==='2');
  assert.match(await page.locator('#game-status').innerText(),/已匹配/);assert.ok(await page.locator('#game-sync').isVisible());
  await page.locator('#game-question').fill('她为什么特地提到蓝色雨伞？');await page.locator('#game-confirm').click();
  await page.waitForFunction(()=>!document.getElementById('composer').hidden&&document.getElementById('story-panel').hidden);
  assert.match(await page.locator('#message').inputValue(),/蓝色雨伞/);assert.equal((await api('story')).active.position,2);assert.equal(cloudCalls.length,0);
  // Background acquisition advances independently; re-matching uses its records.
  let state=await api('story');const run=state.active.id;
  await api('story/import',{run_id:run,epoch:state.epoch,text:'旁白：又过了一会儿，楼道里传来了轻轻的脚步声。',request_id:'game-later-0001',kind:'auto',speaker:'',confirmed_read:true});
  await open();await page.locator('#game-read').evaluate(button=>button.click());
  await page.waitForFunction(()=>!document.getElementById('game-confirm').disabled&&document.getElementById('game-match').value==='2');
  const oldFrame=await page.evaluate(()=>({...window.fixtureReader.context,kind:'preview',text:'这是切换模式前迟到的游戏台词，不应显示。',request_id:'late-game-fixture'}));
  await page.locator('#game-text').fill('美咲：我在旧校舍门口等你，记得带那把蓝色的雨伞！');assert.ok(await page.locator('#game-confirm').isDisabled());
  await page.locator('#game-locate').click();await page.waitForFunction(()=>!document.getElementById('game-confirm').disabled);
  await page.locator('#game-confirm').click();await page.waitForFunction(()=>document.getElementById('story-panel').hidden);assert.equal((await api('story')).active.position,2);
  // A novel uses the import/chapters flow and ignores old game results.
  state=await api('story');await api('story/runs',{epoch:state.epoch,title:'自编小说','name':'第一章',mode:'novel'});
  await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.fixtureReader.context?.mode==='novel');
  await page.evaluate(frame=>window.fixtureReader.emit(frame),oldFrame);assert.ok(await page.locator('#game-preview').isHidden());
  await page.locator('#story-toggle').evaluate(button=>button.click());assert.ok(await page.locator('[data-story-tab=game]').isHidden());
  await page.locator('#story-new-toggle').click();await page.locator('#story-mode').selectOption('novel');await page.locator('#story-game-title').fill('另一篇自编小说');await page.locator('#story-new-form button[type=submit]').click();
  await page.locator('[data-story-pane=import]').waitFor({state:'visible'});assert.equal(await page.locator('#story-text').getAttribute('placeholder'),null);
  const bounds=await page.locator('#story-panel').boundingBox();assert.ok(bounds.x>=0&&bounds.y>=0&&bounds.x+bounds.width<=280&&bounds.y+bounds.height<=380);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.equal(cloudCalls.length,0);
  const result={passed:true,fixture_native_bridge:true,matched_record_reused:true,no_duplicate_import:true,background_progress_match:true,edited_ocr_rematched:true,
   game_novel_separate_entry:true,late_old_mode_frame_ignored:true,small_window:true,cloud_calls:0};
  fs.writeFileSync(path.join(folder,'report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
