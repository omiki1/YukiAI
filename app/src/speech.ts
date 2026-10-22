// Bound requests by phrases rather than sending the entire reply to TTS at once.
// The first phrase is shorter; later phrases batch small sentences to limit calls.
export function speechPhrases(text:string):string[]{
 const chars=Array.from(text.trim()),phrases:string[]=[];let start=0;
 for(let i=0;i<chars.length;i++){
  const first=phrases.length===0,length=i-start+1,min=first?16:48,max=first?72:160;
  const terminal=/[。！？!?；;\n]/.test(chars[i])||(chars[i]==='.'&&(!chars[i+1]||/\s/.test(chars[i+1])));
  const pause=/[，、,:：]/.test(chars[i])&&length>=(first?36:96);
  if((terminal&&length>=min)||pause||length>=max){
   // Keep closing quotes and repeated punctuation attached to the phrase.
   while(i+1<chars.length&&/[。！？!?；;”’」』）)\]…]/.test(chars[i+1]))i++;
   phrases.push(chars.slice(start,i+1).join(''));start=i+1;
  }
 }
 if(start<chars.length)phrases.push(chars.slice(start).join(''));
 return phrases;
}

type AudioResult='ended'|'error'|'aborted';
export class SpeechPlayer{
 private controller:AbortController|null=null;private generation=0;
 constructor(private onBusy:(busy:boolean)=>void){}
 stop(){this.generation++;this.controller?.abort();this.controller=null;this.onBusy(false);}
 private begin(){this.stop();this.controller=new AbortController();this.onBusy(true);return {generation:this.generation,signal:this.controller.signal};}
 private current(run:{generation:number}){return run.generation===this.generation;}
 private async request(endpoint:string,body:unknown,signal:AbortSignal){
  const response=await fetch('/api/'+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal});
  if(!response.ok){const data=await response.json();throw new Error(data.detail||'语音暂时没有连接成功。');}
  return response.blob();
 }
 private output(blob:Blob,signal:AbortSignal){
  if(signal.aborted)throw new DOMException('朗读已停止。','AbortError');
  const url=URL.createObjectURL(blob),audio=new Audio(url);
  let finish:(result:AudioResult)=>void;
  const ended=new Promise<AudioResult>(resolve=>finish=resolve);
  let settled=false;
  const settle=(result:AudioResult)=>{
   if(settled)return;settled=true;
   signal.removeEventListener('abort',abort);audio.onended=null;audio.onerror=null;
   audio.pause();audio.src='';URL.revokeObjectURL(url);finish(result);
  };
  const abort=()=>settle('aborted');signal.addEventListener('abort',abort,{once:true});
  audio.onended=()=>settle('ended');audio.onerror=()=>settle('error');
  const started=audio.play().catch(error=>{settle('error');throw error;});
  return {started,ended};
 }
 // A manual sample resolves as soon as playback starts, as the settings UI expects.
 async play(endpoint:string,body:unknown={}){
  const run=this.begin();
  try{
   const blob=await this.request(endpoint,body,run.signal);if(!this.current(run))return;
   const audio=this.output(blob,run.signal);
   await audio.started;
   audio.ended.then(()=>{if(this.current(run))this.stop();});
  }catch(error){if(!this.current(run)||run.signal.aborted)return;this.stop();throw error;}
 }
 // Only one following phrase is prefetched, while the current phrase plays.
 // Outcomes are captured immediately so a failed prefetch cannot reject unhandled.
 async speak(text:string,context:{story_epoch:number;story_note_revision?:number}){
  const run=this.begin(),phrases=speechPhrases(text);
  const fetchPhrase=(part:string)=>this.request('speech',{text:part,...context},run.signal)
   .then(blob=>({blob,error:null}),error=>({blob:null,error}));
  try{
   let next=phrases.length?fetchPhrase(phrases[0]):null;
   for(let i=0;i<phrases.length;i++){
    const result=await next;if(!this.current(run))return;if(result.error)throw result.error;
    const audio=this.output(result.blob,run.signal);await audio.started;
    if(!this.current(run))return;
    next=i+1<phrases.length?fetchPhrase(phrases[i+1]):null;
    const outcome=await audio.ended;if(!this.current(run))return;
    if(outcome==='error')throw new Error('语音播放失败，文字仍可阅读。');
   }
  }catch(error){if(!this.current(run)||run.signal.aborted)return;throw error;}
  finally{if(this.current(run))this.stop();}
 }
}
