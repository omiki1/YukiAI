// Actual independent renderer + text UI. All chat responses here are local fixtures.
const {chromium}=require('../app/node_modules/playwright-core');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/window');fs.mkdirSync(out,{recursive:true});
const origin=process.env.YUKI_VERIFY_URL||'http://127.0.0.1:12395/';
process.env.TEMP=process.env.TMP=path.join(root,'.cache');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:320,height:460}}),errors=[],external=[],requests=[];
  const touchModel=async(targetPage=page)=>{
   const point=await targetPage.locator('canvas').evaluate(c=>{
    const gl=c.getContext('webgl2')||c.getContext('webgl'),pixels=new Uint8Array(c.width*c.height*4),r=c.getBoundingClientRect();gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
    let chosen=0,distance=Infinity,top=c.height;
    for(let i=0;i<c.width*c.height;i++)if(pixels[i*4+3]>230){const x=i%c.width,y=Math.floor(i/c.width),d=(x-c.width/2)**2+(y-c.height/2)**2;top=Math.min(top,c.height-y-1);if(d<distance){chosen=i;distance=d;}}
    return {x:r.left+(chosen%c.width+.5)/c.width*r.width,y:r.top+(c.height-1-Math.floor(chosen/c.width)+.5)/c.height*r.height,top:r.top+top/c.height*r.height};
   });
   await targetPage.mouse.move(point.x,point.y);await targetPage.waitForFunction(()=>document.querySelector('.window-bar').classList.contains('revealed'));await targetPage.waitForTimeout(140);return point;
  };
  let fixtureText='见到你，我也有点开心。今天有什么想一起聊的吗？',fixtureFailure=false,holdReply=null;
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',route=>{const url=route.request().url();if(!url.startsWith(origin)){external.push(url);return route.abort();}
   requests.push(route.request().method()+' '+new URL(url).pathname);
   if(new URL(url).pathname==='/api/chat')return (async()=>{if(holdReply)await holdReply;return fixtureFailure?route.fulfill({status:502,json:{detail:'本地测试：服务暂时不可用。'}}):route.fulfill({json:{text:fixtureText,emotion:'happy'}});})();
   return route.continue();});
  await page.goto(origin,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.yuki?.avatar.ready&&window.yuki.avatar.time>.5);
  assert.equal(await page.locator('audio,video').count(),0);
  assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
  const alpha=await page.locator('canvas').evaluate(c=>{const gl=c.getContext('webgl2')||c.getContext('webgl'),p=new Uint8Array(c.width*c.height*4);gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,p);let transparent=0,opaque=0;for(let i=3;i<p.length;i+=4){if(p[i]===0)transparent++;if(p[i]>200)opaque++;}return {transparent,opaque};});
  assert.ok(alpha.transparent>10000&&alpha.opaque>10000,JSON.stringify(alpha));
  const snap=await page.evaluate(()=>window.yuki.snapshot());assert.equal(snap.drawables,24);
  assert.equal(snap.scale,.85);
  for(const id of ['ParamAngleX','ParamAngleY','ParamAngleZ','ParamBodyAngleX','ParamEyeBallX'])assert.equal(snap.parameters[id],0);
  assert.equal(await page.locator('.window-bar').isVisible(),false);
  await page.screenshot({path:path.join(out,'window-preview.png'),omitBackground:true});
  // Transparent canvas space must not activate the toolbar; only the painted character does.
  await page.mouse.move(4,50);await page.waitForTimeout(700);assert.equal(await page.locator('.window-bar').isVisible(),false);
  const modelPoint=await touchModel(),toolbarBounds=await page.locator('.window-bar').boundingBox();
  assert.ok(toolbarBounds.y+toolbarBounds.height<=modelPoint.top&&toolbarBounds.x>=0&&toolbarBounds.x+toolbarBounds.width<=320);
  await page.mouse.move(toolbarBounds.x+toolbarBounds.width/2,toolbarBounds.y+toolbarBounds.height+4);await page.waitForTimeout(200);assert.equal(await page.locator('.window-bar').isVisible(),true);
  await page.locator('#settings-toggle').hover();await page.waitForTimeout(700);assert.equal(await page.locator('.window-bar').isVisible(),true);
  await page.screenshot({path:path.join(out,'model-hover-toolbar.png'),omitBackground:true});
  await page.mouse.move(4,50);await page.waitForTimeout(750);assert.equal(await page.locator('.window-bar').isVisible(),false);
  await page.keyboard.press('Tab');assert.equal(await page.locator('.drag-area').evaluate(el=>el===document.activeElement),true);await page.keyboard.press('Escape');await page.waitForTimeout(150);assert.equal(await page.locator('.window-bar').isVisible(),false);
  const paper=await page.locator('#line').boundingBox();assert.ok(paper.height<=80);assert.ok(paper.width<=240);
  assert.equal(await page.locator('#composer').isVisible(),false);assert.equal(await page.locator('#dialogue-tools').isVisible(),false);
  const paintedBounds=()=>page.locator('canvas').evaluate(c=>{const gl=c.getContext('webgl2')||c.getContext('webgl'),p=new Uint8Array(c.width*c.height*4);gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,p);let minX=c.width,maxX=0,minY=c.height,maxY=0;for(let y=0;y<c.height;y++)for(let x=0;x<c.width;x++)if(p[(y*c.width+x)*4+3]>100){minX=Math.min(x,minX);maxX=Math.max(x,maxX);minY=Math.min(y,minY);maxY=Math.max(y,maxY);}return {width:maxX-minX,height:maxY-minY};});
  const smaller=await paintedBounds();await page.evaluate(()=>window.yuki.avatar.setScale(1));await page.waitForTimeout(150);const original=await paintedBounds();
  assert.ok(smaller.height<original.height*.88&&smaller.width<original.width*.88);await page.evaluate(()=>window.yuki.avatar.setScale(.85));
  await page.locator('#chat-toggle').click();await page.locator('#message').fill('今天见到你很开心');await page.locator('#message').press('Enter');
  assert.equal(await page.locator('#composer').isVisible(),false);
  await page.waitForFunction(()=>window.yuki.typing);
  assert.equal(await page.evaluate(()=>window.yuki.snapshot().expression),'happy');
  const partial=await page.locator('#reply').textContent();assert.ok(partial.length<30);
  await page.locator('#line').click();
  await page.waitForFunction(()=>!window.yuki.typing);
  assert.match(await page.locator('#reply').textContent(),/今天有什么/);
  await page.locator('#more-toggle').click();await page.locator('#history-toggle').click();
  assert.match(await page.locator('#history-lines').innerText(),/今天见到你很开心/);
  await page.locator('#history-close').click();
  await page.waitForTimeout(650);await page.screenshot({path:path.join(out,'window-dialogue.png'),omitBackground:true});
  await page.waitForFunction(()=>window.yuki.snapshot().expression==='neutral');
  await page.locator('#chat-toggle').click();await page.locator('#message').fill('留给稍后的话');await page.locator('#more-toggle').click();await page.locator('#dialogue-fold').click();
  assert.equal(await page.locator('.dialogue').isVisible(),false);await page.screenshot({path:path.join(out,'window-folded.png'),omitBackground:true});
  await page.locator('#dialogue-restore').click();assert.equal(await page.locator('#message').inputValue(),'留给稍后的话');
  assert.match(await page.locator('#reply').textContent(),/今天有什么/);
  await touchModel();await page.locator('#settings-toggle').click();
  await page.locator('#avatar-scale').fill('78');assert.equal(await page.evaluate(()=>window.yuki.snapshot().scale),.78);
  await page.locator('[data-tab=llm]').click();await page.locator('#llm-url').fill('https://fixture.invalid/v1');await page.locator('#llm-model').fill('fixture-chat');await page.locator('#llm-key').fill('fixture-chat-credential');
  await page.locator('[data-tab=voice]').click();await page.locator('#voice-key').fill('fixture-fish-credential');
  await page.locator('#voice-provider').selectOption('openai');await page.locator('#voice-url').fill('https://fixture.invalid/v1');await page.locator('#voice-model').fill('fixture-tts');await page.locator('#voice-key').fill('fixture-openai-credential');await page.locator('#voice-id').fill('fixture-voice');
  await page.locator('#voice-provider').selectOption('fish');assert.equal(await page.locator('#voice-key').inputValue(),'fixture-fish-credential');
  assert.equal(requests.filter(x=>/test-voice|test-llm|\/api\/speech/.test(x)).length,0);
  await page.locator('#settings-save').click();await page.waitForFunction(()=>document.querySelector('#settings-status').textContent.includes('已保存在本机'));
  assert.equal(await page.locator('#voice-key').inputValue(),'');assert.match(await page.locator('#voice-key').getAttribute('placeholder'),/已保存/);
  await page.locator('#settings-close').click();await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>window.yuki?.avatar.ready);
  assert.equal(await page.evaluate(()=>window.yuki.snapshot().scale),.78);
  await touchModel();await page.locator('#settings-toggle').click();await page.locator('[data-tab=llm]').click();assert.equal(await page.locator('#llm-key').inputValue(),'');assert.equal(await page.locator('#llm-model').inputValue(),'fixture-chat');
  const safeSettings=await page.evaluate(()=>fetch('/api/settings').then(r=>r.text()));assert.ok(!safeSettings.includes('credential'));assert.ok(!safeSettings.includes('api_key'));
  const wav=Buffer.alloc(44+9600);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(9600,40);
  let voiceTests=0;await page.route('**/api/settings/test-voice',route=>{voiceTests++;return route.fulfill({status:200,contentType:'audio/wav',body:wav});});
  await page.locator('[data-tab=voice]').click();await page.locator('#voice-test').click();await page.waitForFunction(()=>document.querySelector('#settings-status').textContent.includes('已开始试听'));
  assert.equal(voiceTests,1);await page.screenshot({path:path.join(out,'window-settings-voice.png'),omitBackground:true});
  await page.locator('[data-tab=appearance]').click();await page.locator('#scale-reset').click();await page.locator('#settings-save').click();await page.waitForFunction(()=>document.querySelector('#settings-status').textContent.includes('已保存在本机'));
  await page.locator('#settings-close').click();
  await page.setViewportSize({width:280,height:380});await page.waitForTimeout(400);
  const bounds=await page.locator('.dialogue').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=280);
  await page.screenshot({path:path.join(out,'window-small.png'),omitBackground:true});
  await touchModel();await page.locator('#settings-toggle').click();await page.locator('[data-tab=llm]').click();await page.screenshot({path:path.join(out,'window-settings-small.png'),omitBackground:true});
  const panel=await page.locator('#settings').boundingBox();assert.ok(panel.x>=0&&panel.y>=0&&panel.x+panel.width<=280&&panel.y+panel.height<=380);
  assert.ok(await page.locator('#settings-save').isVisible());await page.locator('#settings-close').click();
  // Single-message bubbles grow only when explicitly expanded; no text is lost.
  await page.setViewportSize({width:320,height:460});fixtureText='她明明说自己没有离开，却又记得钟楼里的灯。'.repeat(14)+'最后一句也在这里。';
  await page.locator('#chat-toggle').click();await page.locator('#message').fill('这是一条长回复测试');await page.locator('#message').press('Enter');await page.waitForFunction(()=>window.yuki.typing);await page.locator('#line').click();
  assert.equal(await page.locator('#reply').textContent(),fixtureText);assert.equal(await page.locator('#reply-expand').isVisible(),true);
  const collapsed=await page.locator('#line').boundingBox();assert.ok(collapsed.height<=80);
  await page.screenshot({path:path.join(out,'bubble-long-collapsed.png'),omitBackground:true});
  await page.locator('#reply-expand').click();const expanded=await page.locator('#line').boundingBox();assert.ok(expanded.height>collapsed.height&&expanded.height<=176);
  await page.locator('#line').evaluate(el=>el.scrollTop=el.scrollHeight);assert.match(await page.locator('#reply').textContent(),/最后一句也在这里/);
  await page.screenshot({path:path.join(out,'bubble-long-expanded.png'),omitBackground:true});
  await page.locator('#reply-expand').click();assert.equal(await page.locator('#line').evaluate(el=>el.scrollTop),0);
  // Opening a fresh draft while a request is in flight must not lose it or steal focus.
  let release;holdReply=new Promise(resolve=>release=resolve);fixtureText='先聊到这里吧。';
  await page.locator('#chat-toggle').click();await page.locator('#message').fill('先发送的一句');await page.locator('#message').press('Enter');
  await page.waitForFunction(()=>window.yuki.pending);await page.locator('#chat-toggle').click();await page.locator('#message').fill('留给下一句的草稿');await page.locator('#chat-toggle').focus();release();holdReply=null;
  await page.waitForFunction(()=>!window.yuki.pending);assert.equal(await page.locator('#message').inputValue(),'留给下一句的草稿');assert.equal(await page.locator('#composer').isVisible(),true);assert.equal(await page.locator('#chat-toggle').evaluate(el=>el===document.activeElement),true);
  if(await page.evaluate(()=>window.yuki.typing))await page.locator('#line').click();
  // Failed requests restore the input and keep the draft.
  fixtureFailure=true;await page.locator('#message').press('Enter');await page.waitForFunction(()=>document.querySelector('#hint').classList.contains('error'));
  assert.equal(await page.locator('#composer').isVisible(),true);assert.equal(await page.locator('#message').inputValue(),'留给下一句的草稿');assert.equal(await page.locator('#hint').isVisible(),true);
  await page.locator('#message').press('Escape');assert.equal(await page.locator('#composer').isVisible(),false);
  for(const scale of [.6,1.1,.85]){
   await page.evaluate(value=>window.yuki.avatar.setScale(value),scale);await page.waitForTimeout(150);const point=await touchModel(),bar=await page.locator('.window-bar').boundingBox();assert.ok(bar.y>=0&&bar.y+bar.height<=point.top+1&&bar.x>=0&&bar.x+bar.width<=320);
  }
  const retina=await browser.newPage({viewport:{width:280,height:380},deviceScaleFactor:2});retina.on('pageerror',error=>errors.push(String(error)));
  await retina.route('**/*',route=>{if(!route.request().url().startsWith(origin)){external.push(route.request().url());return route.abort();}return route.continue();});
  await retina.goto(origin,{waitUntil:'networkidle'});await retina.waitForFunction(()=>window.yuki?.avatar.ready&&window.yuki.avatar.time>.3);
  await touchModel(retina);const retinaBar=await retina.locator('.window-bar').boundingBox();assert.ok(retinaBar.x>=0&&retinaBar.x+retinaBar.width<=280);await retina.locator('#settings-toggle').click();assert.equal(await retina.locator('#settings').isVisible(),true);await retina.close();
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={passed:true,nativeMoc:true,transparentCanvas:alpha,drawables:snap.drawables,modelScale:{smaller,original},hoverToolbar:{bounds:toolbarBounds,requiresPaintedModel:true,gapCrossing:true,staysOverButtons:true,hidesOnLeave:true,keyboardAccess:true,followsScale:true,highDpi:true},singleChatBubble:paper,collapsedLongMessage:collapsed,expandedLongMessage:expanded,onDemandComposer:true,replyPreservesDraftAndFocus:true,failedRequestKeepsDraft:true,textTypewriter:true,emotionDuringText:true,returnsToNeutral:true,foldPreservesDraft:true,settingsPersistAndRedactKeys:true,voicePlaybackFixture:true,cloudApiCalls:0,errors,external,requests};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({...result,requests:requests.length}));
 }catch(e){throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
