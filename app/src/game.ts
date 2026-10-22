import {api} from './api';
const $=(id:string)=>document.getElementById(id) as HTMLInputElement;
export class GamePanel{
 private host:any=null;private preview:any=null;private saving=false;private lastContext='';
 constructor(private state:()=>any,private updated:(data:any)=>void,private opening:()=>void,private discuss:(question:string)=>void,private sync:()=>void){
  $('game-setup').onclick=()=>this.host?.setupGameReading();
  $('game-read').onclick=()=>this.host?.readGameDialog();
  $('game-discard').onclick=()=>this.discard();
  $('game-confirm').onclick=()=>this.confirm();
  $('game-locate').onclick=()=>this.locate();$('game-sync').onclick=()=>this.sync();
  $('game-text').oninput=()=>{if(this.preview){this.preview.located_text=null;$('game-confirm').disabled=true;}};
  $('game-match').onchange=()=>{$('game-confirm').disabled=this.preview?.match_status==='ambiguous'&&!$('game-match').value;};
 }
 private context(){const s=this.state(),a=s?.active;return {run_id:a?.id||'',epoch:s?.epoch??0,position:a?.position??0,note_revision:a?.note_revision??0,mode:a?.mode||'chat'};}
 bind(host:any){this.host=host;this.lastContext='';if(host?.gameReading)host.gameReading.connect((event:string)=>this.receive(event));this.changed();}
 changed(){
  const current=this.context();
  if(this.preview&&!this.matches(this.preview))this.discard();
  const desktop=!!this.host?.setGameReadingContext,gal=current.mode==='galgame';
  $('game-setup').disabled=!desktop||!gal||this.saving;$('game-read').disabled=!desktop||!gal||this.saving;
  document.querySelector<HTMLElement>('[data-story-tab="game"]').hidden=current.mode==='novel';
  if(!desktop)this.message('读屏请使用 Windows 桌面版。');
  else if(!gal)this.message('请选择游戏共读。');
  const value=JSON.stringify(current);if(this.host?.setGameReadingContext&&this.lastContext!==value){this.lastContext=value;this.host.setGameReadingContext(value);}
 }
 private matches(value:any){const c=this.context();return c.mode==='galgame'&&['run_id','epoch','note_revision'].every(key=>value[key]===c[key]);}
 open(){this.changed();api('capture').then(data=>{$('game-sync-status').textContent=data.recording?'正在记录':data.connected?'已连接 · 暂未记录':'尚未连接';$('game-sync').textContent=data.connected?'查看连接':'连接游戏';}).catch(()=>{});}
 private message(text:string){$('game-status').textContent=text;}
 private receive(raw:string){
  let value:any;try{value=JSON.parse(raw);}catch{return;}
  if(!this.matches(value))return;
  if(value.kind==='status'){
   this.message(value.configured?value.title+' · Ctrl+Alt+Y':'先选择游戏与文字区域。');
   $('game-read').disabled=!value.configured||value.busy||this.saving;$('game-setup').disabled=value.busy||this.saving;
  }else if(value.kind==='busy'){
   this.discard();this.message('读取中……');$('game-read').disabled=true;$('game-setup').disabled=true;
  }else if(value.kind==='preview'){
   if(typeof value.text!=='string'||!value.text.trim()||value.text.length>6000)return;
   this.preview=value;this.opening();$('game-preview').hidden=false;$('game-text').value=value.text;$('game-question').value='';
   this.message('匹配中……');$('game-text').focus();this.locate();
   $('game-read').disabled=false;$('game-setup').disabled=false;
  }else if(value.kind==='error'||value.kind==='setup-needed'){
   this.opening();this.message(value.message||'没有读到当前台词，请重新选择游戏文字区域。');$('game-read').disabled=false;$('game-setup').disabled=false;
  }
 }
 private discard(){this.preview=null;$('game-preview').hidden=true;$('game-text').value='';$('game-question').value='';}
 private async locate(){
  const frame=this.preview;if(!frame||!this.matches(frame))return;
  const text=$('game-text').value.trim();$('game-confirm').disabled=true;
  try{
   const result=await api('story/screen/locate',{run_id:frame.run_id,epoch:frame.epoch,text});
   if(this.preview!==frame||!this.matches(frame)||$('game-text').value.trim()!==text)return;
   frame.located_text=text;frame.match_status=result.status;const select=$('game-match') as unknown as HTMLSelectElement;select.replaceChildren();
   if(result.status==='ambiguous')select.add(new Option('请选择当前的位置',''));
   for(const row of result.candidates)select.add(new Option('第 '+row.position+' 句 · '+(row.speaker?row.speaker+'：':'')+row.text,String(row.position)));
   $('game-match-row').hidden=!result.candidates.length;
   $('game-confirm').textContent=result.candidates.length?'定位并准备聊天':'补记这段并准备聊天';
   $('game-confirm').disabled=result.status==='ambiguous';
   this.message(result.status==='ambiguous'?'请选择匹配位置。':result.candidates.length?'已匹配。':'未找到匹配，可核对后补记。');
  }catch(error){this.message(error.message);}
 }
 private async confirm(){
  const frame=this.preview;if(this.saving||!frame||!this.matches(frame))return;
  const text=$('game-text').value.trim(),question=$('game-question').value.trim()||'结合当前场景、人物和之前的相关记录，陪我聊聊刚才这段。';
  if(frame.located_text!==text){await this.locate();return;}
  if(!text){this.message('请先核对当前台词，或放弃这次读取。');return;}
  this.saving=true;$('game-confirm').disabled=true;$('game-discard').disabled=true;
  try{
   const position=$('game-match-row').hidden?null:Number($('game-match').value);
   if(position===0)return;
   const data=await api('story/screen',{run_id:frame.run_id,epoch:frame.epoch,note_revision:frame.note_revision,
    text,request_id:frame.request_id,confirmed_read:true,match_position:position});
   // Another client may switch the reading range while saving.
   if(!this.matches(frame))return;
   this.discard();this.updated(data);this.discuss(question);
  }catch(error){this.message(error.message);}
  finally{this.saving=false;$('game-confirm').disabled=false;$('game-discard').disabled=false;this.changed();}
 }
}
