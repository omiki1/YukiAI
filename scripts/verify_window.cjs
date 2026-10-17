// Actual independent renderer + text UI. All chat responses here are local fixtures.
const {chromium}=require('../app/node_modules/playwright-core');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'verification/window');fs.mkdirSync(out,{recursive:true});
const origin=process.env.YUKI_VERIFY_URL||'http://127.0.0.1:12395/';
process.env.TEMP=process.env.TMP=path.join(root,'.cache');
(async()=>{
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
 try{
  const page=await browser.newPage({viewport:{width:460,height:640}}),errors=[],external=[],requests=[];
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',route=>{const url=route.request().url();if(!url.startsWith(origin)){external.push(url);return route.abort();}
   requests.push(route.request().method()+' '+new URL(url).pathname);
   if(new URL(url).pathname==='/api/chat')return route.fulfill({json:{text:'见到你，我也有点开心。今天有什么想一起聊的吗？',emotion:'happy'}});
   return route.continue();});
  await page.goto(origin,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.yuki?.avatar.ready&&window.yuki.avatar.time>.5);
  assert.equal(await page.locator('audio,video').count(),0);
  assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
  const alpha=await page.locator('canvas').evaluate(c=>{const gl=c.getContext('webgl2')||c.getContext('webgl'),p=new Uint8Array(c.width*c.height*4);gl.readPixels(0,0,c.width,c.height,gl.RGBA,gl.UNSIGNED_BYTE,p);let transparent=0,opaque=0;for(let i=3;i<p.length;i+=4){if(p[i]===0)transparent++;if(p[i]>200)opaque++;}return {transparent,opaque};});
  assert.ok(alpha.transparent>10000&&alpha.opaque>10000,JSON.stringify(alpha));
  const snap=await page.evaluate(()=>window.yuki.snapshot());assert.equal(snap.drawables,24);
  for(const id of ['ParamAngleX','ParamAngleY','ParamAngleZ','ParamBodyAngleX','ParamEyeBallX'])assert.equal(snap.parameters[id],0);
  await page.screenshot({path:path.join(out,'window-preview.png'),omitBackground:true});
  await page.locator('#message').fill('今天见到你很开心');await page.locator('#message').press('Enter');
  await page.waitForFunction(()=>window.yuki.typing);
  assert.equal(await page.evaluate(()=>window.yuki.snapshot().expression),'happy');
  const partial=await page.locator('#reply').textContent();assert.ok(partial.length<30);
  await page.locator('#line').click();
  await page.waitForFunction(()=>!window.yuki.typing);
  assert.match(await page.locator('#reply').textContent(),/今天有什么/);
  await page.locator('#history-toggle').click();
  assert.match(await page.locator('#history-lines').innerText(),/今天见到你很开心/);
  await page.locator('#history-close').click();
  await page.waitForTimeout(650);await page.screenshot({path:path.join(out,'window-dialogue.png'),omitBackground:true});
  await page.waitForFunction(()=>window.yuki.snapshot().expression==='neutral');
  await page.setViewportSize({width:380,height:540});await page.waitForTimeout(400);
  const bounds=await page.locator('.dialogue').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=380);
  await page.screenshot({path:path.join(out,'window-small.png'),omitBackground:true});
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  const result={passed:true,nativeMoc:true,transparentCanvas:alpha,drawables:snap.drawables,textTypewriter:true,emotionDuringText:true,returnsToNeutral:true,voiceElements:0,cloudApiCalls:0,errors,external,requests};
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({...result,requests:requests.length}));
 }catch(e){throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
