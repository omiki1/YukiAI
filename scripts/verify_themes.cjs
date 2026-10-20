// Isolated, offline theme and reading-card acceptance; no private stories or keys.
const {chromium}=require('../app/node_modules/playwright-core');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/themes');
fs.mkdirSync(out,{recursive:true});fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
const directory=fs.mkdtempSync(path.join(root,'.cache','themes-verify-'));
const python=process.env.YUKI_TEST_PYTHON||path.join(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python');
const source=`
import json,socket,sys
from pathlib import Path
import uvicorn
from scripts.yuki_server import create_app
from scripts.yuki_config import ConfigStore
from scripts.yuki_story import StoryStore
from scripts.test_capture import CaptureFixtureRuntime
folder=Path(sys.argv[1]);story=StoryStore(folder/'data/story.sqlite3')
story.create_run('自编界面验收故事','主题检查',0);run=story.view()['active']['id']
story.ingest('旁白：雨停了，窗边留着一把伞。\\n美咲：我在等一位朋友。\\n小春：那就再坐一会儿吧。','auto','','theme-fixture-0001',story.epoch(),run)
app=create_app(history_directory=folder/'history',story=story,config=ConfigStore(folder/'settings.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
`;
function contrast(color,background){
 const rgb=text=>text.match(/[\d.]+/g).slice(0,3).map(Number);
 const luminance=text=>rgb(text).map(value=>{const v=value/255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((sum,v,index)=>sum+v*[.2126,.7152,.0722][index],0);
 const a=luminance(color),b=luminance(background);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
}
(async()=>{
 const server=spawn(python,['-u','-c',source,directory],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,logs='';server.stderr.on('data',data=>logs=(logs+data.toString()).slice(-3000));
 try{
  const ready=await new Promise((resolve,reject)=>{let text='';const timeout=setTimeout(()=>reject(new Error('Theme fixture startup timed out '+logs)),15000);
   server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Theme fixture exited '+code+' '+logs));});
   server.stdout.on('data',data=>{text+=data.toString();const line=text.split('\n').find(line=>line.startsWith('{'));if(line)try{const result=JSON.parse(line);clearTimeout(timeout);resolve(result);}catch{}});});
  const origin='http://127.0.0.1:'+ready.port+'/';
  browser=await chromium.launch({executablePath:process.env.YUKI_TEST_BROWSER||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:320,height:460}}),errors=[],external=[],providerCalls=[];
  page.on('pageerror',error=>errors.push(String(error)));
  page.on('request',request=>{if(/\/api\/(chat|speech|settings\/test-)/.test(request.url()))providerCalls.push(request.url());});
  await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'pixel');
  const themeRows=[];
  const openSettings=async()=>{await page.locator('#settings-toggle').evaluate(button=>button.click());await page.locator('[data-tab="appearance"]').click();};
  for(const theme of ['pixel','glass','paper','neon','classic']){
   await openSettings();await page.locator('#theme-select').selectOption(theme);
   assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme||'classic'),theme);
   if(theme==='pixel')assert.equal(await page.evaluate(async()=>{const faces=await document.fonts.load('12px YukiPixel');return faces.length===1&&faces[0].status==='loaded';}),true);
   for(const viewport of [{width:320,height:460},{width:280,height:380}]){
    await page.setViewportSize(viewport);await page.waitForTimeout(220);
    const layout=await page.locator('#settings').evaluate(panel=>{const box=panel.getBoundingClientRect(),save=document.getElementById('settings-save').getBoundingClientRect();return {width:panel.clientWidth,scroll:panel.scrollWidth,left:box.left,right:box.right,bottom:box.bottom,saveBottom:save.bottom};});
    assert.ok(layout.width>150&&layout.scroll<=layout.width+1&&layout.left>=0&&layout.right<=viewport.width+1&&layout.bottom<=viewport.height+1&&layout.saveBottom<=viewport.height+1,JSON.stringify({theme,layout}));
   }
   await page.screenshot({path:path.join(out,theme+'-settings-280.png'),omitBackground:true});
   await page.locator('#settings-close').click();await page.locator('#story-toggle').evaluate(button=>button.click());await page.locator('[data-story-tab="timeline"]').click();await page.locator('.timeline-card').first().waitFor();
   const colors=await page.locator('.timeline-card').first().evaluate(card=>{const text=card.querySelector('.timeline-excerpt')||card.querySelector('h3');return {color:getComputedStyle(text).color,background:getComputedStyle(card).backgroundColor,border:getComputedStyle(card.querySelector('button')).borderWidth};});
   // The night theme must retain readable text against its actual opaque card.
   if(theme==='neon')assert.ok(contrast(colors.color,colors.background)>=4.5,JSON.stringify(colors));
   if(theme==='pixel')assert.equal(colors.border,'2px');
   for(const viewport of [{width:320,height:460},{width:280,height:380}]){
    await page.setViewportSize(viewport);await page.waitForTimeout(220);
    const layout=await page.locator('#timeline-panel').evaluate(panel=>({width:panel.clientWidth,scroll:panel.scrollWidth}));
    assert.ok(layout.width>150&&layout.scroll<=layout.width+1,JSON.stringify({theme,viewport,layout}));
   }
   await page.screenshot({path:path.join(out,theme+'-cards-280.png'),omitBackground:true});
   await page.locator('#story-close').click();await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
   assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme||'classic'),theme);
   themeRows.push(theme);
  }
  await page.evaluate(()=>localStorage.setItem('yuki.theme','invalid-saved-theme'));await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'pixel');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);assert.deepEqual(providerCalls,[]);
  const result={passed:true,default_pixel:true,themes:themeRows,preserves_saved_theme:true,invalid_preference_recovers:true,local_pixel_font:true,small_window_settings_and_cards:true,night_card_readable:true,cloud_calls:0,errors,external};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
