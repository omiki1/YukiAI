import {api} from './api';

const $=(id:string)=>document.getElementById(id) as HTMLInputElement;

export class CapturePanel{
 private state:any=null;private busy=false;private blocked=false;private generation=0;private timer:number;private epoch:number|null=null;private failure='';
 constructor(private context:()=>{epoch:number|null;active:any},private refreshStory:()=>Promise<void>){
  $('capture-refresh').onclick=()=>this.processes();
  $('capture-install-button').onclick=()=>this.action('install',{});
  $('capture-process').onchange=()=>this.controls();
  $('capture-attach').onclick=()=>this.action('connect',{pid:Number($('capture-process').value),epoch:this.context().epoch,run_id:this.context().active?.id||null});
  $('capture-detach').onclick=()=>this.action('detach',this.session());
  $('capture-thread').onchange=()=>{this.preview();$('capture-confirm').checked=false;this.controls();};
  $('capture-kind').onchange=()=>{$('capture-speaker-field').hidden=$('capture-kind').value!=='dialogue';$('capture-confirm').checked=false;this.controls();};
  $('capture-speaker').oninput=()=>{$('capture-confirm').checked=false;this.controls();};
  $('capture-select').onclick=()=>this.action('select',{...this.session(),thread_id:$('capture-thread').value,kind:$('capture-kind').value,speaker:$('capture-kind').value==='dialogue'?$('capture-speaker').value:''});
  $('capture-confirm').onchange=()=>this.controls();
  $('capture-record').onclick=()=>{const context=this.context();if(context.active)this.action('begin',{...this.session(),epoch:context.epoch,run_id:context.active.id,thread_id:$('capture-thread').value,kind:$('capture-kind').value,speaker:$('capture-kind').value==='dialogue'?$('capture-speaker').value:'',confirmed_read:$('capture-confirm').checked});};
  $('capture-pause').onclick=()=>this.action('pause',this.session());
  $('capture-hook-form').onsubmit=event=>{event.preventDefault();this.action('hook',{...this.session(),code:$('capture-code').value.trim()});};
  this.timer=window.setInterval(()=>{if((this.state?.connected||this.visible())&&!this.busy)this.load().catch(()=>{});},800);
 }
 private visible(){return !$('story-panel').hidden&&!document.querySelector<HTMLElement>('[data-story-pane="capture"]').hidden;}
 private session(){return {session_id:this.state?.session_id||''};}
 private message(text:string,error=false){if(error)this.failure=text;const failed=!!this.failure;$('capture-status').textContent=this.failure||text;$('capture-status').classList.toggle('error',failed);}
 async open(){try{await this.load();await this.processes();}catch(error){this.message(error.message,true);}}
 async load(){const generation=++this.generation;const data=await api('capture');if(generation!==this.generation)return;this.render(data);}
 contextChanged(){const {epoch}=this.context();if(this.epoch!==epoch){this.epoch=epoch;$('capture-confirm').checked=false;}this.controls();}
 setBlocked(value:boolean){this.blocked=value;this.controls();}
 private async processes(){if(this.busy||this.blocked)return;this.busy=true;this.failure='';this.controls();
  try{const data=await api('capture/processes'),select=$('capture-process') as unknown as HTMLSelectElement,selected=select.value;select.replaceChildren(new Option('选择正在运行的游戏窗口',''));
   for(const process of data.processes)select.add(new Option(process.title+' · '+process.name,String(process.pid)));if(data.processes.some(p=>String(p.pid)===selected))select.value=selected;
   if(!data.processes.length)this.message('先启动游戏，再刷新游戏列表。');
  }catch(error){this.message(error.message,true);}finally{this.busy=false;this.controls();}
 }
 private render(data:any){
  const changed=this.state?.session_id!==data.session_id,stopped=this.state?.recording&&!data.recording;this.state=data;
  $('capture-install').hidden=data.available;$('capture-connected').hidden=!data.connected;
  $('capture-stage').textContent=this.context().active?.mode==='novel'?'请先切换到游戏共读':!data.available?'先准备取词工具':data.recording?'3 / 3 · 正在记录剧情':data.connected?'2 / 3 · 核对游戏台词':'1 / 3 · 选择并连接游戏';
  $('capture-game').textContent=data.process?data.process.title+' · '+data.process.name:'尚未连接游戏';
  $('capture-count').textContent='本次已记录 '+data.saved+' 句 · 已过滤重复 '+data.duplicates+' 次';
  const select=$('capture-thread') as unknown as HTMLSelectElement,selected=changed?data.selected_thread:select.value;
  select.replaceChildren(new Option('先推进台词，再选择正文来源',''));
  data.threads.forEach((channel:any,index:number)=>select.add(new Option('来源 '+(index+1)+' · '+channel.text.slice(0,54),channel.id)));
  select.value=data.threads.some(t=>t.id===selected)?selected:data.selected_thread||'';
  if(changed){$('capture-kind').value=data.kind;$('capture-speaker').value=data.speaker;$('capture-confirm').checked=false;$('capture-code').value='';}
  if(stopped||data.story_epoch!==this.context().epoch){$('capture-confirm').checked=false;if(!this.busy&&data.story_epoch!==this.context().epoch)this.refreshStory().catch(()=>{});}
  this.preview();this.contextChanged();this.message(data.message);this.controls();
 }
 private preview(){const channel=this.state?.threads.find(t=>t.id===$('capture-thread').value);$('capture-preview').textContent=channel?.text||'等待游戏的新台词……';$('capture-source').textContent=channel?channel.name+' · '+channel.code:'';}
 private selected(){return this.state?.selected_thread===$('capture-thread').value&&this.state?.kind===$('capture-kind').value&&this.state?.speaker===($('capture-kind').value==='dialogue'?$('capture-speaker').value.trim():'');}
 private controls(){const blocked=this.busy||this.blocked,connected=!!this.state?.connected,recording=!!this.state?.recording;
  $('capture-panel').querySelectorAll<HTMLInputElement>('button,input,select').forEach(el=>el.disabled=blocked);
  $('capture-attach').disabled=blocked||recording||!this.state?.available||!$('capture-process').value||this.context().active?.mode==='novel';$('capture-refresh').disabled=blocked||recording;$('capture-process').disabled=blocked||recording;
  $('capture-install-button').disabled=blocked||connected;
  $('capture-install-button').textContent=this.busy?'正在准备，请稍等……':'准备取词工具';
  for(const id of ['capture-thread','capture-kind','capture-speaker'])$(id).disabled=blocked||recording;
  $('capture-select').disabled=blocked||recording||!connected||!$('capture-thread').value;
  $('capture-record').disabled=blocked||!connected||recording||!$('capture-thread').value||this.context().active?.mode!=='galgame'||this.state?.story_epoch!==this.context().epoch||!$('capture-confirm').checked;
  $('capture-pause').disabled=blocked||!recording;$('capture-detach').disabled=blocked||!connected;
  $('capture-code-submit').disabled=blocked||!connected;
  $('capture-target').textContent=this.context().active?.mode==='novel'?'请先切换到游戏共读。':this.context().active?'记录到《'+this.context().active.title+'》 · '+this.context().active.branch_name:'连接后建立共读';
 }
 private async action(endpoint:string,body:any){if(this.busy||this.blocked)return;this.busy=true;this.failure='';++this.generation;this.controls();
  if(endpoint==='install')this.message('正在下载并校验……');
  try{const data=await api('capture/'+endpoint,body);this.render(data.capture||data);if(endpoint==='select'||endpoint==='pause'||endpoint==='hook')$('capture-confirm').checked=false;await this.refreshStory();}
  catch(error){try{await this.load();await this.refreshStory();}catch{}this.message(error.message,true);}
  finally{this.busy=false;this.controls();}
 }
 dispose(){clearInterval(this.timer);}
}
