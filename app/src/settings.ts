import {Avatar} from './avatar';
import {api} from './api';
import {SpeechPlayer} from './speech';
const $=(id:string)=>document.getElementById(id) as HTMLInputElement;

export class SettingsPanel{
 data:any=null;private draft:any=null;private provider='fish';private busy=false;
 constructor(private avatar:Avatar,private speech:SpeechPlayer,private saved:(data:any)=>void){
  $('settings-toggle').onclick=()=>this.open().catch(e=>this.message(e.message,true));
  $('settings-close').onclick=()=>this.close();
  document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(button=>button.onclick=()=>{
   document.querySelectorAll<HTMLElement>('[data-pane]').forEach(pane=>pane.hidden=pane.dataset.pane!==button.dataset.tab);
   document.querySelectorAll('[data-tab]').forEach(tab=>tab.setAttribute('aria-pressed',String(tab===button)));
  });
  $('avatar-scale').oninput=()=>this.preview();
  $('scale-reset').onclick=()=>{$('avatar-scale').value='85';this.preview();};
  $('voice-provider').onchange=()=>{this.captureVoice();this.provider=$('voice-provider').value;this.fillVoice();};
  $('settings-form').onsubmit=event=>{event.preventDefault();this.save().catch(e=>this.message(e.message,true));};
  $('llm-test').onclick=()=>this.test('llm');$('voice-test').onclick=()=>this.test('voice');
  document.addEventListener('keydown',event=>{if($('settings').hidden)return;if(event.key==='Escape'){event.preventDefault();this.close();}
   if(event.key==='Tab'){const focusable=Array.from($('settings').querySelectorAll<HTMLElement>('button,input,select')).filter(el=>el.getClientRects().length&&!el.hasAttribute('disabled'));
    const first=focusable[0],last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
  });
 }
 async load(){this.data=await api('settings');this.avatar.setScale(this.data.avatar_scale);this.saved(this.data);}
 async open(){
  if(!this.data)await this.load();this.draft=JSON.parse(JSON.stringify(this.data));this.provider=this.draft.voice.provider;
  $('avatar-scale').value=String(Math.round(this.data.avatar_scale*100));this.preview();
  for(const [id,key] of [['llm-url','base_url'],['llm-model','model']])$(id).value=this.draft.llm[key];
  this.fillKey('llm',this.draft.llm);$('llm-json').checked=this.draft.llm.json_mode;$('voice-enabled').checked=this.draft.voice.enabled;
  $('voice-provider').value=this.provider;this.fillVoice();$('settings').hidden=false;$('settings-toggle').setAttribute('aria-expanded','true');this.message('保存配置不会自动调用接口。');$('settings-close').focus();
 }
 close(){if(this.busy)return;this.avatar.setScale(this.data?.avatar_scale??.85);$('llm-key').value='';$('voice-key').value='';this.draft=null;$('settings').hidden=true;$('settings-toggle').setAttribute('aria-expanded','false');$('settings-toggle').focus();}
 private preview(){const value=Number($('avatar-scale').value);$('scale-value').textContent=value+'%';this.avatar.setScale(value/100);}
 private fillKey(prefix:string,data:any){$(prefix+'-key').value='';$(prefix+'-key').placeholder=data.has_key?'已保存 · 留空保留原密钥':'填写密钥';$(prefix+'-clear').checked=false;}
 private fillVoice(){const data=this.draft.voice[this.provider],fish=this.provider==='fish';
  $('voice-url-label').textContent=fish?'完整合成地址':'接口地址（Base URL，通常以 /v1 结尾）';
  $('voice-url').value=data.base_url;$('voice-url').placeholder=fish?'https://api.fish.audio/v1/tts':'https://你的服务地址/v1';
  $('voice-model').hidden=fish;$('fish-model').hidden=!fish;$('voice-model').value=data.model;$('fish-model').value=fish?data.model:'s2.1-pro-free';
  $('voice-id-label').textContent=fish?'音色 ID（留空使用服务默认音色）':'音色名称（留空使用 alloy）';
  $('voice-id').value=data.voice;$('voice-speed').value=String(data.speed);this.fillKey('voice',data);
  if(data.api_key)$('voice-key').value=data.api_key; // Only the unsaved draft entered during this visit.
  $('voice-clear').checked=Boolean(data.clear_key);
 }
 private captureVoice(){if(!this.draft)return;Object.assign(this.draft.voice[this.provider],{base_url:$('voice-url').value,model:$(this.provider==='fish'?'fish-model':'voice-model').value,api_key:$('voice-key').value,clear_key:$('voice-clear').checked,voice:$('voice-id').value,speed:Number($('voice-speed').value)});}
 private payload(){this.captureVoice();const data=JSON.parse(JSON.stringify(this.draft));data.avatar_scale=Number($('avatar-scale').value)/100;
  Object.assign(data.llm,{base_url:$('llm-url').value,model:$('llm-model').value,api_key:$('llm-key').value,clear_key:$('llm-clear').checked,json_mode:$('llm-json').checked});
  data.voice.provider=this.provider;data.voice.enabled=$('voice-enabled').checked;
  for(const endpoint of [data.llm,data.voice.fish,data.voice.openai])delete endpoint.has_key;return data;
 }
 private setBusy(value:boolean){this.busy=value;$('story-toggle').disabled=value;$('settings').querySelectorAll<HTMLInputElement>('input,select,button').forEach(element=>{if(element.id!=='settings-voice-stop')element.disabled=value;});}
 private async persist(){const data=await api('settings',this.payload());this.data=data;this.draft=JSON.parse(JSON.stringify(data));this.fillKey('llm',data.llm);this.fillVoice();this.avatar.setScale(data.avatar_scale);this.saved(data);return data;}
 async save(){if(this.busy)return;this.setBusy(true);this.message('正在保存……');try{await this.persist();this.message('已保存在本机，配置已生效。');}finally{this.setBusy(false);}}
 private async test(kind:'llm'|'voice'){if(this.busy)return;if(!($('settings-form') as unknown as HTMLFormElement).reportValidity())return;
  this.setBusy(true);this.message('正在保存并'+(kind==='llm'?'测试聊天……':'准备试听……'));
  try{await this.persist();if(kind==='llm'){await api('settings/test-llm',{});this.message('聊天连接成功，可以一起聊剧情了。');}
   else{await this.speech.play('settings/test-voice');this.message('已开始试听，可点击“停止试听”停止。');}}
  catch(error){this.message(error.message,true);}finally{this.setBusy(false);}
 }
 private message(text:string,error=false){$('settings-status').textContent=text;$('settings-status').classList.toggle('error',error);}
}
