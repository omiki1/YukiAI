export class SpeechPlayer{
 private audio:HTMLAudioElement|null=null;private controller:AbortController|null=null;private objectURL='';private generation=0;
 constructor(private onBusy:(busy:boolean)=>void){}
 stop(){this.generation++;this.controller?.abort();this.controller=null;if(this.audio){this.audio.pause();this.audio.src='';this.audio=null;}if(this.objectURL)URL.revokeObjectURL(this.objectURL);this.objectURL='';this.onBusy(false);}
 async play(endpoint:string,body:unknown={}){
  this.stop();const generation=this.generation;this.onBusy(true);this.controller=new AbortController();
  try{
   const response=await fetch('/api/'+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:this.controller.signal});
   if(!response.ok){const data=await response.json();throw new Error(data.detail||'语音暂时没有连接成功。');}
   const blob=await response.blob();if(generation!==this.generation)return;
   this.objectURL=URL.createObjectURL(blob);this.audio=new Audio(this.objectURL);
   this.audio.onended=()=>{if(generation===this.generation)this.stop();};
   this.audio.onerror=()=>{if(generation===this.generation)this.stop();};
   await this.audio.play();
  }catch(error){if(generation!==this.generation||error.name==='AbortError')return;this.stop();throw error;}
 }
}
