// Offline acceptance of the actual TypeScript player: no cloud or sound device.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {transformSync}=require('../app/node_modules/esbuild');
const source=transformSync(fs.readFileSync(path.join(__dirname,'../app/src/speech.ts'),'utf8'),{loader:'ts',format:'cjs',target:'chrome83'}).code;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(blockPlay=false){
 const requests=[],audios=[],urls=new Set(),busy=[];let id=0;
 class Audio{
  constructor(src){this.src=src;audios.push(this);this.playing=false;this.rejectPlay=blockPlay;}
  play(){this.playing=true;return this.rejectPlay?Promise.reject(new Error('fixture playback blocked')):Promise.resolve();}
  pause(){this.playing=false;}
  end(){this.onended?.();}
  fail(){this.onerror?.();}
 }
 const context={module:{exports:{}},AbortController,DOMException,Audio,URL:{
  createObjectURL(){const url='blob:fixture-'+(++id);urls.add(url);return url;},revokeObjectURL(url){urls.delete(url);}
 },fetch:(endpoint,options)=>new Promise((resolve,reject)=>{
  const request={endpoint,body:JSON.parse(options.body),signal:options.signal,
   ok:()=>resolve({ok:true,blob:async()=>({fixture:true})}),fail:()=>reject(new Error('fixture synthesis failure'))};
  // Deliberately allow late responses after abort to test the generation guard.
  requests.push(request);
 })};
 vm.runInNewContext(source,context,{filename:'speech.ts'});
 return {requests,audios,urls,busy,player:new context.module.exports.SpeechPlayer(value=>busy.push(value)),split:context.module.exports.speechPhrases};
}
const text='她在门口留下了一把伞，但我们还不知道是谁的。'+
 '她说自己一直在等朋友，这只是她目前给出的说法，不能直接当作已经确认的事实。'.repeat(7);
const scope={story_epoch:9,story_note_revision:3};
(async()=>{
 const segmentation=fixture();
 for(const input of ['', '你好。',text,'「'+text+'」', 'English 3.14 is a number. '+('A longer English sentence. '.repeat(18)), 'あいうえお。'.repeat(60),'🙂'.repeat(400)]){
  const parts=Array.from(segmentation.split(input));assert.equal(parts.join(''),input.trim());
  assert.ok(parts.every(part=>part.length>0));
  assert.ok(parts.every(part=>!/[\uD800-\uDBFF]$/.test(part)&&!(/^[\uDC00-\uDFFF]/.test(part))));
 }
 const parts=Array.from(segmentation.split(text));assert.ok(parts.length>=3);assert.ok(parts[0].length<=72&&parts[0].length<text.length);
 const f=fixture();const pending=f.player.speak(text,scope);assert.equal(f.requests.length,1);assert.equal(f.audios.length,0);
 f.requests[0].ok();await flush();assert.equal(f.audios.length,1);assert.equal(f.requests.length,2);
 assert.equal(f.audios[0].playing,true);assert.deepEqual(f.requests[1].body,{text:parts[1],...scope});
 f.requests[1].ok();await flush();assert.equal(f.audios.length,1);assert.equal(f.requests.length,2); // bounded lookahead
 for(let i=0;i<parts.length;i++){
  if(i>1){f.requests[i].ok();await flush();}
  assert.equal(f.audios.filter(a=>a.playing).length,1);f.audios[i].end();await flush();
 }
 await pending;assert.equal(f.requests.map(r=>r.body.text).join(''),text);assert.equal(f.urls.size,0);assert.equal(f.busy.at(-1),false);
 // Stop during the initial fetch, then receive a late result.
 const stopped=fixture(),old=stopped.player.speak(text,scope);stopped.player.stop();assert.ok(stopped.requests[0].signal.aborted);
 stopped.requests[0].ok();await old;assert.equal(stopped.audios.length,0);assert.equal(stopped.requests.length,1);
 // A new reply stops playback and the single in-flight prefetch together.
 const replaced=fixture(),first=replaced.player.speak(text,scope);replaced.requests[0].ok();await flush();
 const second=replaced.player.speak('这是另一条新回复。',{story_epoch:10});assert.ok(replaced.requests[1].signal.aborted);
 assert.equal(replaced.audios[0].playing,false);replaced.requests[1].ok();await first;assert.equal(replaced.busy.at(-1),true);
 replaced.requests[2].ok();await flush();assert.equal(replaced.audios.length,2);replaced.audios[1].end();await second;
 assert.equal(replaced.urls.size,0);assert.equal(replaced.requests.length,3);
 // Prefetch failures are observed at the boundary, with no unhandled rejection.
 const failed=fixture(),failure=failed.player.speak(text,scope);const observed=assert.rejects(failure,/fixture synthesis failure/);
 failed.requests[0].ok();await flush();failed.requests[1].fail();await flush();assert.equal(failed.audios[0].playing,true);
 failed.audios[0].end();await observed;assert.equal(failed.requests.length,2);assert.equal(failed.urls.size,0);
 const broken=fixture(),brokenRun=broken.player.speak(text,scope),brokenObserved=assert.rejects(brokenRun,/播放失败/);
 broken.requests[0].ok();await flush();broken.audios[0].fail();await brokenObserved;assert.ok(broken.requests[1].signal.aborted);
 // Manual test retains the existing "started" contract, without segmenting.
 const manual=fixture(),sample=manual.player.play('settings/test-voice');manual.requests[0].ok();await sample;
 assert.equal(manual.requests.length,1);assert.equal(manual.audios[0].playing,true);assert.equal(manual.busy.at(-1),true);
 manual.player.stop();assert.equal(manual.urls.size,0);assert.equal(manual.audios[0].playing,false);
 const blocked=fixture(true);
 // Fail play() before it starts; the settings UI must report a failure.
 const request=blocked.player.play('settings/test-voice'),blockedObserved=assert.rejects(request,/fixture playback blocked/);
 blocked.requests[0].ok();await blockedObserved;assert.equal(blocked.urls.size,0);assert.equal(blocked.busy.at(-1),false);
 console.log(JSON.stringify({passed:true,phrases:parts.length,first_phrase_characters:parts[0].length,total_characters:text.length,
  checks:['first phrase plays before later synthesis','one phrase lookahead','ordered non-overlapping audio','stop and replacement','late result discarded','prefetch and playback failures','manual sample contract'],cloud_calls:0}));
})().catch(error=>{console.error(error);process.exitCode=1;});
