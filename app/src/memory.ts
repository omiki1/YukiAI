import {api} from './api';
const $=(id:string)=>document.getElementById(id) as HTMLInputElement;
const bytes=(n:number)=>n<1024?n+' B':n<1048576?(n/1024).toFixed(1)+' KB':(n/1048576).toFixed(1)+' MB';

export class MemoryPanel{
 private data:any=null;private plan:any=null;private blocked=false;private generation=0;private planEpoch:number|null=null;
 constructor(private context:()=>any,private apply:(state:any)=>void,private block:(busy:boolean)=>void,
  private historyChanged:(uid:string,deleted:boolean)=>Promise<void>){
  $('memory-refresh').onclick=()=>this.work(()=>this.load());
  $('memory-backup').onclick=()=>this.work(async()=>{await api('memory/backup',{epoch:this.context().epoch});await this.load();this.message('已在本机备份全部共读和普通聊天，不包含接口配置、密钥或模型。');});
  $('memory-compact').onclick=()=>this.work(async()=>{const result=await api('memory/compact',{epoch:this.context().epoch});this.apply(result.story);this.data=result.memory;this.render();this.message('已整理数据库空闲空间。取词已暂停，原文和讨论保留。');});
  $('memory-cancel').onclick=()=>this.cancel();
  $('memory-agree').onchange=()=>this.controls();
  $('memory-confirm').onclick=()=>this.confirm();
 }
 async open(){await this.work(()=>this.load());}
 contextChanged(){if(this.plan&&this.planEpoch!==this.context().epoch){this.cancel();this.message('阅读状态已改变，请重新预览操作范围。',true);}}
 setBlocked(value:boolean){this.blocked=value;this.controls();}
 private controls(){
  $('memory-confirm').disabled=this.blocked||!this.plan||!$('memory-agree').checked;
  document.querySelectorAll<HTMLButtonElement>('#memory-panel button[data-unavailable]').forEach(button=>button.disabled=this.blocked||button.dataset.unavailable==='true');
 }
 private message(text:string,error=false){$('memory-status').textContent=text;$('memory-status').classList.toggle('error',error);}
 private cancel(){this.plan=null;this.planEpoch=null;$('memory-preview').hidden=true;$('memory-agree').checked=false;this.controls();}
 private async work(task:()=>Promise<void>){if(this.blocked)return;this.block(true);try{await task();}catch(error){this.cancel();this.message(error.message,true);}finally{this.block(false);}}
 private async load(){const generation=++this.generation;const data=await api('memory');if(generation!==this.generation)return;this.data=data;this.cancel();this.render();this.message('记录只保存在本机。新聊会保留旧讨论；彻底清理请先预览范围。');}
 private button(text:string,action:()=>void,unavailable=false){const button=document.createElement('button');button.type='button';button.textContent=text;button.dataset.unavailable=String(unavailable);button.disabled=this.blocked||unavailable;button.onclick=action;return button;}
 private row(root:HTMLElement,title:string,note:string,buttons:HTMLButtonElement[]){const item=document.createElement('article');item.className='memory-item';const label=document.createElement('h3');label.textContent=title;const description=document.createElement('p');description.textContent=note;const actions=document.createElement('div');actions.className='memory-actions';actions.append(...buttons);item.append(label,description,actions);root.append(item);return item;}
 private render(){
  const data=this.data;if(!data)return;
  $('memory-usage').textContent='共读 '+bytes(data.storage.database_bytes)+' · 普通聊天 '+bytes(data.storage.history_bytes)+' · 备份 '+bytes(data.storage.backup_bytes);
  const runs=$('memory-runs');runs.replaceChildren();
  for(const item of data.runs)this.row(runs,item.title+' · '+item.name,item.events+' 句原文 · '+item.branches+' 条路线 · '+item.bookmarks+' 个书签 · '+item.story_turns+' 轮讨论（含旧讨论）',[
   this.button('清空讨论',()=>this.preview('clear_discussion',item.id)),this.button('删除共读',()=>this.preview('delete_run',item.id))]);
  if(!data.runs.length)runs.textContent='还没有保存共读。';
  const histories=$('memory-histories');histories.replaceChildren();
  for(const item of data.histories)this.row(histories,(item.restored?'恢复的聊天 · ':'普通聊天 · ')+item.id.slice(0,19).replace('_',' '),item.messages+' 条消息 · '+bytes(item.bytes)+(item.readable?'':' · 文件无法读取'),[
   this.button('继续聊',()=>this.openHistory(item.id),!item.readable),this.button('删除',()=>this.preview('delete_history',item.id))]).dataset.memoryId=item.id;
  if(!data.histories.length)histories.textContent='还没有保存普通聊天。';
  const backups=$('memory-backups');backups.replaceChildren();
  for(const item of data.backups){const counts=item.counts;this.row(backups,item.created_at?new Date(item.created_at).toLocaleString():'不完整的备份',bytes(item.bytes)+(counts?' · '+counts.runs+' 篇共读 · '+counts.histories+' 篇聊天':'')+(item.readable?'':' · 需检查'),[
   this.button('恢复副本',()=>this.preview('restore_backup',item.id),!item.readable),this.button('删除备份',()=>this.preview('delete_backup',item.id))]);}
  if(!data.backups.length)backups.textContent='还没有本机备份。';
  this.controls();
 }
 private async preview(action:string,id:string){await this.work(async()=>{this.cancel();this.plan=await api('memory/plan',{action,id,epoch:this.context().epoch});this.planEpoch=this.context().epoch;$('memory-description').textContent=this.plan.description;$('memory-confirm').textContent=action==='restore_backup'?'确认恢复副本':'确认清理';$('memory-preview').hidden=false;$('memory-preview').scrollIntoView({block:'nearest'});$('memory-agree').focus();this.message('请核对操作范围。备份独立保留，可在下方单独删除。');});}
 private async confirm(){if(!this.plan||!$('memory-agree').checked)return;const token=this.plan.token;await this.work(async()=>{const result=await api('memory/confirm',{token,confirmed:true});this.cancel();if(result.result.action==='delete_history')await this.historyChanged(result.result.id,true);this.apply(result.story);await this.load();this.message(result.result.action==='restore_backup'?'已恢复为独立副本，原有记录保留。其他普通聊天副本可在列表里继续聊。':'已按预览范围清理。已有备份仍保留，可单独删除。');});}
 private async openHistory(uid:string){await this.work(async()=>{const result=await api('memory/history/open',{id:uid,epoch:this.context().epoch});await this.historyChanged(uid,false);this.apply(result.story);this.cancel();this.message('已打开这篇普通聊天，关闭剧情记录后可以继续。');});}
}
