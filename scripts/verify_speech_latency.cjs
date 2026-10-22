// Real browser + loopback sockets, using invented text and silent fixture WAVs.
// Timings demonstrate pipeline overlap only, not the speed of a cloud provider.
const {chromium}=require('../app/node_modules/playwright-core'),{spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),folder=fs.mkdtempSync(path.join(root,'.cache','speech-acceptance-'));
const python=process.env.YUKI_TEST_PYTHON||path.join(root,'.venv/Scripts/python.exe');
const source=`
import asyncio,io,json,socket,sys,time,wave
from pathlib import Path
import uvicorn
from scripts.yuki_config import ConfigStore
from scripts.yuki_server import create_app
TEXT='她在门口留下了一把伞，但我们还不知道是谁的。'+'她说自己一直在等朋友，这只是她目前给出的说法，不能直接当作已经确认的事实。'*7
events=[]
class Chat:
    async def complete(self,*args):
        await asyncio.sleep(.12)
        return {'text':TEXT,'emotion':'neutral'}
class Speech:
    async def synthesize(self,text):
        event={'characters':len(text),'start':time.monotonic(),'done':False,'cancelled':False};events.append(event)
        try:
            await asyncio.sleep(.18+len(text)*.004)
            result=io.BytesIO()
            with wave.open(result,'wb') as output:
                output.setparams((1,2,24000,0,'NONE','not compressed'));output.writeframes(b'\\0\\0'*19200)
            event['done']=True
            return result.getvalue()
        except asyncio.CancelledError:
            event['cancelled']=True;raise
folder=Path(sys.argv[1]);config=ConfigStore(folder/'settings.json',env={});config.data['voice']['enabled']=True
app=create_app(Chat(),folder/'history',config=config,speech=Speech())
@app.get('/fixture/stats')
async def stats():return {'events':events,'active':bool(app.state.active_speech_task),'text':TEXT}
# The production app ends with a root static mount; keep this fixture ahead of it.
app.router.routes.insert(0,app.router.routes.pop())
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='error')).run(sockets=[sock])
`;
const wait=async(check,message,timeout=7000)=>{const deadline=Date.now()+timeout;while(Date.now()<deadline){if(await check())return;await new Promise(resolve=>setTimeout(resolve,40));}throw new Error(message);};
(async()=>{
 const server=spawn(python,['-u','-c',source,folder],{cwd:root,windowsHide:true,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 let browser,logs='';server.stderr.on('data',data=>logs=(logs+data.toString()).slice(-2500));
 try{
  const ready=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(new Error('Fixture startup timed out '+logs)),15000);
   server.once('exit',code=>{clearTimeout(timeout);reject(new Error('Fixture exited '+code+' '+logs));});
   server.stdout.on('data',data=>{output+=data.toString();const line=output.split('\n').find(line=>line.startsWith('{'));if(line){clearTimeout(timeout);resolve(JSON.parse(line));}});});
  const origin='http://127.0.0.1:'+ready.port+'/',stats=()=>fetch(origin+'fixture/stats').then(r=>r.json());
  const fullText=(await stats()).text;
  // Previous pipeline: wait for a single full-reply audio before playback.
  const before=Date.now(),baseline=await fetch(origin+'api/speech',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:fullText,story_epoch:0})});
  if(!baseline.ok)throw new Error('Fixture baseline '+baseline.status+' '+await baseline.text()+' '+JSON.stringify(await stats())+' '+logs);
  await baseline.arrayBuffer();const baselineMs=Date.now()-before;
  browser=await chromium.launch({executablePath:process.env.YUKI_TEST_BROWSER||'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--autoplay-policy=no-user-gesture-required','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
  const page=await browser.newPage({viewport:{width:320,height:460}}),errors=[],external=[],requests=[];
  page.on('pageerror',error=>errors.push(String(error)));
  await page.addInitScript(()=>{
   localStorage.setItem('yuki.guide.dismissed','1');window.fixtureAudio=[];
   const original=HTMLMediaElement.prototype.play;
   HTMLMediaElement.prototype.play=function(){return original.call(this).then(result=>{window.fixtureAudio.push(Date.now());return result;});};
  });
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/speech')requests.push({at:Date.now(),body:request.postDataJSON()});});
  await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  const send=async()=>{await page.locator('#chat-toggle').evaluate(button=>button.click());await page.locator('#message').fill('一起看这把伞。');await page.locator('#message').press('Enter');};
  await send();await page.waitForFunction(()=>window.fixtureAudio.length>0);
  const firstAudio=await page.evaluate(()=>window.fixtureAudio[0]),firstMs=firstAudio-requests[0].at;
  assert.ok(firstMs<baselineMs*.75,JSON.stringify({firstMs,baselineMs}));
  const firstState=await stats();assert.ok(firstState.events.length>=3);assert.equal(firstState.events[2].done,false);
  await page.waitForFunction(()=>document.querySelector('#voice-stop').hidden,{},{timeout:15000});
  assert.equal(requests.map(r=>r.body.text).join(''),fullText);assert.ok(requests.length>=3);
  assert.equal(await page.evaluate(()=>window.fixtureAudio.length),requests.length);
  const completedPhrases=requests.length;
  // Stop while the next phrase is synthesizing; verify on the real HTTP socket.
  await send();await page.waitForFunction(count=>window.fixtureAudio.length>count,completedPhrases);
  await wait(async()=>(await stats()).active,'Next phrase did not start');
  await page.locator('#voice-stop').evaluate(button=>button.click());
  await wait(async()=>{const s=await stats();return !s.active&&s.events.some(event=>event.cancelled);},'Aborted browser request did not cancel synthesis');
  await page.waitForTimeout(700);assert.equal(await page.evaluate(()=>window.fixtureAudio.length),completedPhrases+1);
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const report={passed:true,fixture_only:true,full_reply_audio_ready_ms:baselineMs,first_phrase_playing_ms:firstMs,
   characters:fullText.length,first_phrase_characters:requests[0].body.text.length,phrases:completedPhrases,
   real_socket_stop_cancels_synthesis:true,cloud_calls:0};
  fs.writeFileSync(path.join(folder,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
