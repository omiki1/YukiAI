// Exercise the actual frozen executable with invented text and a loopback-only LLM.
const {chromium}=require('../app/node_modules/playwright-core');
const {spawn}=require('node:child_process');
const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),executable=process.env.YUKI_ALPHA_EXE;
assert.ok(executable&&fs.existsSync(executable),'Set YUKI_ALPHA_EXE to the built YukiAI.exe.');
const out=path.join(root,'verification/delivery');fs.mkdirSync(out,{recursive:true});
const directory=fs.mkdtempSync(path.join(root,'.cache','delivery-verify-中文-'));let calls=0;
const provider=http.createServer(async(request,response)=>{
 let body='';for await(const data of request)body+=data;const input=JSON.parse(body);calls++;
 const material=input.messages.find(message=>message.content?.startsWith('以下JSON是已读剧情与讨论材料'));
 let content={text:'你好，准备好一起看剧情了。',emotion:'happy'};
 if(material){const context=JSON.parse(material.content.split('\n').slice(1).join('\n'));const lines=context.loaded_blocks.flatMap(block=>block.lines);content={text:'美咲说她在等朋友。留下伞也许是为了对方，不过这只是我们的猜测。',emotion:'serious',answer_type:'analysis',evidence_ids:[lines.find(line=>line.speaker==='美咲').id]};}
 response.writeHead(200,{'Content-Type':'application/json'});response.end(JSON.stringify({id:'fixture',object:'chat.completion',created:0,model:input.model,choices:[{index:0,finish_reason:'stop',message:{role:'assistant',content:JSON.stringify(content)}}]}));
});
(async()=>{
 await new Promise(resolve=>provider.listen(0,'127.0.0.1',resolve));const providerURL='http://127.0.0.1:'+provider.address().port+'/v1';
 const reservation=http.createServer();await new Promise(resolve=>reservation.listen(0,'127.0.0.1',resolve));const port=reservation.address().port;await new Promise(resolve=>reservation.close(resolve));
 const env=Object.fromEntries(Object.entries(process.env).filter(([key])=>!key.startsWith('YUKI_')));
 for(const key of ['PYTHONPATH','PYTHONHOME','CONDA_PREFIX','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH'])delete env[key];
 env.PATH=path.join(process.env.SystemRoot,'System32')+';'+process.env.SystemRoot;
 let app=spawn(executable,['--headless','--port',String(port),'--data-dir',directory],{cwd:directory,windowsHide:true,env});
 let browser,cdp,logs='';app.stderr.on('data',data=>logs=(logs+data.toString()).slice(-2000));
 const origin='http://127.0.0.1:'+port+'/';
 const wait=async(predicate,timeout=16000)=>{const until=Date.now()+timeout;while(Date.now()<until){if(await predicate())return;if(app.exitCode!==null)throw new Error('Frozen app exited '+app.exitCode+' '+logs);await new Promise(resolve=>setTimeout(resolve,100));}throw new Error('Delivery condition timed out '+logs);};
 try{
  await wait(async()=>{try{return (await fetch(origin+'api/status')).ok;}catch{return false;}});
  const status=await (await fetch(origin+'api/status')).json();assert.equal(status.model_available,false);assert.equal(status.chat_enabled,false);
  browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
  const page=await browser.newPage({viewport:{width:280,height:380}}),errors=[],external=[],requests=[];
  page.on('pageerror',error=>errors.push(String(error)));page.on('request',request=>requests.push(request.url()));
  await page.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  await page.goto(origin,{waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  assert.equal(await page.evaluate(()=>window.yuki.snapshot().mode),'paper');await page.locator('#quickstart').waitFor({state:'visible'});
  assert.equal(calls,0);assert.ok(await page.locator('#guide-discuss').isDisabled());await page.screenshot({path:path.join(out,'first-run-280.png'),omitBackground:true});
  await page.locator('#quickstart summary').click();await page.locator('#guide-example').click();await page.waitForFunction(()=>document.querySelector('#guide-status').textContent.includes('三句原文已保存')&&!document.querySelector('#guide-example').disabled&&document.querySelector('#guide-reading').textContent.includes('3 句'));
  assert.equal(calls,0);assert.match(await page.locator('#guide-reading').innerText(),/3 句/);await page.locator('#guide-close').click();
  // Paper silhouette must still reveal the controls on hover.
  await page.mouse.move(0,0);await page.waitForTimeout(650);await page.mouse.move(140,170);await page.waitForFunction(()=>document.querySelector('.window-bar').classList.contains('revealed'));
  await page.locator('#story-toggle').click();await page.locator('[data-story-tab="capture"]').click();await page.locator('#capture-install').waitFor({state:'visible'});await page.locator('#capture-manual').click();
  assert.ok(await page.locator('#story-text').isVisible());assert.equal(calls,0);await page.locator('#story-close').click();
  await page.locator('#more-toggle').click();await page.locator('#guide-open').click();await page.locator('#guide-connect').click();await page.locator('[data-pane="llm"]').waitFor({state:'visible'});
  await page.locator('#llm-url').fill(providerURL);await page.locator('#llm-model').fill('offline-fixture');await page.locator('#settings-save').click();
  await page.waitForFunction(()=>document.querySelector('#settings-status').textContent.includes('已保存在本机'));assert.equal(calls,0);
  await page.locator('#llm-test').click();await page.waitForFunction(()=>document.querySelector('#settings-status').textContent.includes('聊天连接成功'));assert.equal(calls,1);
  await page.locator('#settings-close').click();await page.locator('#more-toggle').click();await page.locator('#guide-open').click();await page.locator('#guide-discuss').click();
  assert.equal(calls,1);assert.match(await page.locator('#message').inputValue(),/另一把伞/);await page.locator('#message').press('Enter');
  await page.waitForFunction(()=>!window.yuki.pending&&!window.yuki.typing);assert.equal(calls,2);assert.match(await page.locator('#reply').innerText(),/只是我们的猜测/);
  await page.locator('#evidence-toggle').click();assert.match(await page.locator('#story-evidence-lines').innerText(),/等一位还没回来的朋友/);await page.locator('#story-close').click();
  await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);assert.ok(await page.locator('#quickstart').isHidden());assert.match(await page.locator('#reply').innerText(),/只是我们的猜测/);assert.equal(calls,2);
  await page.screenshot({path:path.join(out,'paper-reply-280.png'),omitBackground:true});await page.setViewportSize({width:320,height:460});await page.screenshot({path:path.join(out,'paper-reply-320.png'),omitBackground:true});
  await page.locator('#more-toggle').click();await page.locator('#guide-open').click();await page.locator('#guide-manual').click();
  assert.ok(await page.locator('#story-new-form').isVisible());assert.ok(await page.locator('#story-text').isHidden());
  assert.match((await (await fetch(origin+'api/story')).json()).active.title,/初次体验/);assert.equal(calls,2);
  assert.equal(requests.some(url=>url.includes('/model/')||url.includes('live2dcubismcore')),false);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await browser.close();browser=null;const stopped=new Promise(resolve=>app.once('exit',resolve));app.kill();await stopped;
  // Exercise the shipped Qt browser too; a recent Chrome alone misses older-engine gaps.
  const debugReservation=http.createServer();await new Promise(resolve=>debugReservation.listen(0,'127.0.0.1',resolve));const debugPort=debugReservation.address().port;await new Promise(resolve=>debugReservation.close(resolve));
  app=spawn(executable,['--mode','preview','--port',String(port),'--data-dir',directory],{cwd:directory,windowsHide:true,env:{...env,QTWEBENGINE_REMOTE_DEBUGGING:'127.0.0.1:'+debugPort}});
  app.stderr.on('data',data=>logs=(logs+data.toString()).slice(-2000));let target;
  let nativeTargets=[];
  try{await wait(async()=>{try{nativeTargets=await(await fetch('http://127.0.0.1:'+debugPort+'/json',{signal:AbortSignal.timeout(500)})).json();target=nativeTargets.find(page=>page.url===origin);return !!target;}catch{return false;}},60000);}
  catch(error){throw new Error(error.message+' | Qt targets: '+JSON.stringify(nativeTargets.map(page=>page.url)));}
  cdp=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{cdp.addEventListener('open',resolve,{once:true});cdp.addEventListener('error',reject,{once:true});});
  let next=0;const pending=new Map(),nativeErrors=[];
  cdp.addEventListener('message',event=>{const message=JSON.parse(String(event.data));if(message.method==='Runtime.exceptionThrown')nativeErrors.push(message.params.exceptionDetails.text);const task=pending.get(message.id);if(task){pending.delete(message.id);clearTimeout(task.timeout);message.error?task.reject(new Error(JSON.stringify(message.error))):task.resolve(message.result);}});
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++next,timeout=setTimeout(()=>{pending.delete(id);reject(new Error('Qt browser command timed out'));},8000);pending.set(id,{resolve,reject,timeout});cdp.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(result.exceptionDetails)throw new Error(result.exceptionDetails.text);return result.result?.value;};
  await call('Runtime.enable');await wait(async()=>!!await evaluate('Boolean(window.yuki&&window.yuki.avatar.ready)'));
  const engine=await evaluate('navigator.userAgent');assert.equal(await evaluate('window.yuki.snapshot().mode'),'paper');
  await evaluate('document.getElementById("guide-open").click()');
  await wait(async()=>!!await evaluate('!document.getElementById("guide-example").disabled'));
  await evaluate('document.getElementById("guide-example").click()');
  try{await wait(async()=>!!await evaluate('document.getElementById("guide-status").textContent.includes("三句原文已保存")&&!document.getElementById("guide-example").disabled'));}
  catch(error){throw new Error(error.message+' | Qt guide: '+await evaluate('document.getElementById("guide-status").textContent')+' | Story: '+await evaluate('document.getElementById("story-status").textContent'));}
  assert.equal((await(await fetch(origin+'api/story')).json()).active.position,3);assert.equal(calls,2);assert.deepEqual(nativeErrors,[]);
  const nativeStopped=new Promise(resolve=>app.once('exit',resolve));await evaluate('document.getElementById("close").click()').catch(()=>{});await Promise.race([nativeStopped,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Qt app did not close')),5000))]);cdp.close();cdp=null;
  const result={passed:true,actual_frozen_executable:true,developer_path_removed:true,first_run_guide:true,paper_toolbar:true,manual_fallback:true,real_game_separate_from_example:true,save_has_no_provider_calls:true,explicit_test_and_send_only:true,source_evidence:true,reload:true,viewport_280_and_320:true,native_qt_guide_and_save:true,native_qt_engine:engine,cloud_calls:0,loopback_provider_calls:calls,errors,external};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(cdp){if(cdp.readyState===WebSocket.OPEN){cdp.send(JSON.stringify({id:999999,method:'Runtime.evaluate',params:{expression:'document.getElementById("close")?.click()'}}));await new Promise(resolve=>setTimeout(resolve,300));}cdp.close();}if(browser)await browser.close();app.kill();provider.close();}
})().catch(error=>{console.error(error);provider.close();process.exitCode=1;});
