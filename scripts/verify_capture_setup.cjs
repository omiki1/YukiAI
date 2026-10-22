// Full beginner flow with a local installer/CLI fixture; never injects a user game.
const {chromium}=require('../app/node_modules/playwright-core'),{spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),folder=fs.mkdtempSync(path.join(root,'.cache','capture-setup-'));
const source=`
import json,socket,sys
from pathlib import Path
import uvicorn
from scripts.yuki_server import create_app
from scripts.yuki_config import ConfigStore
from scripts.test_capture import RollingCaptureFixtureRuntime
import scripts.install_textractor as installer
folder=Path(sys.argv[1]);runtime=RollingCaptureFixtureRuntime();ready=[False];attempts=[0]
runtime.available=lambda:ready[0]
def install():
    attempts[0]+=1
    if attempts[0]==1:raise ValueError('fixture-private-download-error')
    ready[0]=True
installer.install=install
app=create_app(history_directory=folder/'history',config=ConfigStore(folder/'settings.json',env={}),capture_runtime=runtime)
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
`;
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
 const server=spawn(process.env.YUKI_TEST_PYTHON||path.join(root,'.venv/Scripts/python.exe'),['-u','-c',source,folder],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,logs='';server.stderr.on('data',data=>logs=(logs+data.toString()).slice(-2500));
 try{
  const ready=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(new Error('Capture setup startup timed out '+logs)),15000);server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exited '+code+' '+logs));});server.stdout.on('data',data=>{output+=data.toString();const line=output.split('\n').find(line=>line.startsWith('{'));if(line){clearTimeout(timeout);resolve(JSON.parse(line));}});});
  const origin='http://127.0.0.1:'+ready.port+'/',get=endpoint=>fetch(origin+'api/'+endpoint).then(r=>r.json());
  browser=await chromium.launch({executablePath:process.env.YUKI_TEST_BROWSER||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:280,height:380}}),errors=[],external=[],requests=[];
  page.on('pageerror',error=>errors.push(String(error)));page.on('request',request=>{if(request.method()==='POST')requests.push(new URL(request.url()).pathname);});
  await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  await page.addInitScript(()=>localStorage.setItem('yuki.guide.dismissed','1'));
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab=game]').click();await page.locator('#game-sync').click();
  await page.locator('#capture-install').waitFor({state:'visible'});assert.ok(await page.locator('#capture-attach').isDisabled());
  assert.ok(!requests.some(path=>/capture\/(install|connect|begin)/.test(path)));assert.equal((await get('story')).runs.length,0);
  await page.locator('#capture-install-button').click();await page.waitForFunction(()=>document.getElementById('capture-status').classList.contains('error'));
  await page.waitForTimeout(1100);assert.match(await page.locator('#capture-status').innerText(),/校验未通过/);assert.doesNotMatch(await page.locator('#capture-status').innerText(),/fixture-private/);
  await page.locator('#capture-install-button').click();await page.locator('#capture-install').waitFor({state:'hidden'});
  assert.equal((await get('capture')).connected,false);assert.equal(requests.filter(path=>path==='/api/capture/install').length,2);
  await page.locator('#capture-process').selectOption('4660');await page.locator('#capture-attach').click();
  await page.waitForFunction(()=>Array.from(document.querySelectorAll('#capture-thread option')).some(option=>option.textContent.includes('美咲')));
  let state=await get('story');assert.equal(state.active.mode,'galgame');assert.equal(state.active.position,0);assert.equal(state.runs.length,1);
  assert.match(await page.locator('#capture-stage').innerText(),/2 \/ 3/);
  const body=await page.locator('#capture-thread option').evaluateAll(options=>options.find(option=>option.textContent.includes('美咲')).value);
  await page.locator('#capture-thread').selectOption(body);assert.ok(await page.locator('#capture-record').isDisabled());
  assert.equal(await page.locator('.capture-advanced').evaluate(details=>details.open),false);
  await page.locator('#capture-confirm').check();assert.ok(await page.locator('#capture-record').isEnabled());await page.locator('#capture-record').click();
  await page.waitForFunction(()=>document.getElementById('capture-stage').textContent.includes('3 / 3'));
  assert.equal(requests.filter(path=>path==='/api/capture/select').length,0);assert.equal(requests.filter(path=>path==='/api/capture/begin').length,1);
  // Closing the setup drawer leaves collection running independently of wakes.
  await page.locator('#story-close').click();const deadline=Date.now()+12000;
  while(Date.now()<deadline&&(await get('story')).active.position<2)await delay(100);
  state=await get('story');assert.ok(state.active.position>=2);assert.ok(state.active.events.every(event=>event.source.startsWith('textractor:')));
  await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab=capture]').click();await page.locator('#capture-pause').click();
  await page.waitForFunction(()=>document.getElementById('capture-status').textContent.includes('已暂停'));
  const position=(await get('story')).active.position;await page.waitForTimeout(1100);assert.equal((await get('story')).active.position,position);
  await page.locator('#capture-detach').click();await page.waitForFunction(()=>document.getElementById('capture-connected').hidden);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.ok(!requests.some(path=>/\/api\/(chat|speech|settings\/test)/.test(path)));
  const result={passed:true,button_installer_fixture:true,install_failure_survives_poll:true,no_automatic_injection:true,connect_creates_game_reading:true,
   source_confirmation_before_save:true,single_begin_button:true,closed_drawer_keeps_recording:true,small_window:true,cloud_calls:0};
  fs.writeFileSync(path.join(folder,'report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  // The established capture regression also uses this isolated pipe peer.
  const regression=spawn(process.execPath,['scripts/verify_capture.cjs'],{cwd:root,windowsHide:true,env:{...process.env,YUKI_CAPTURE_VERIFY_URL:origin},stdio:['ignore','pipe','pipe']});
  let report='',failure='';regression.stdout.on('data',data=>report+=data);regression.stderr.on('data',data=>failure+=data);
  const code=await new Promise(resolve=>regression.once('exit',resolve));assert.equal(code,0,failure+report);console.log(report.trim());
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
