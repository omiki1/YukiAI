import {api} from './api';
import type {StoryEvidence} from './story';
const $=(id:string)=>document.getElementById(id) as HTMLInputElement;
const kinds={question:'疑问',theory:'猜测',feeling:'感受'};

export class ContinuityPanel{
 private data:any=null;private generation=0;private blocked=false;private editing:any=null;private signature='';private removing='';
 constructor(private context:()=>any,private apply:(data:any)=>void,private block:(value:boolean)=>void,
  private refresh:()=>Promise<void>,private evidence:(events:StoryEvidence[])=>void){
  $('continuity-new').onclick=()=>this.edit();$('continuity-cancel').onclick=()=>this.reset();
  $('continuity-form').onsubmit=event=>{event.preventDefault();this.save();};
  $('continuity-refresh').onclick=()=>this.load().catch(error=>this.message(error.message,true));
  $('continuity-more').onclick=()=>this.load(this.data?.more_notes).catch(error=>this.message(error.message,true));
 }
 contextChanged(){const ctx=this.context();const signature=JSON.stringify([ctx.epoch,ctx.active?.id,ctx.active?.position,ctx.active?.note_revision,ctx.active?.messages]);
  if(signature===this.signature)return;this.signature=signature;this.reset();this.data=null;++this.generation;
  $('continuity-recap').replaceChildren();$('continuity-topics').replaceChildren();$('continuity-notes').replaceChildren();
  $('continuity-location').textContent='载入中……';$('continuity-count').textContent='';$('continuity-more').hidden=true;
  this.load().catch(error=>this.message(error.message,true));
 }
 setBlocked(value:boolean){this.blocked=value;this.controls();}
 private controls(){
  document.querySelectorAll<HTMLInputElement>('#continuity button,#continuity input,#continuity select,#continuity textarea').forEach(element=>element.disabled=this.blocked||!this.context().active||element.dataset.unavailable==='true');
  $('continuity-refresh').disabled=this.blocked;
 }
 private message(text:string,error=false){$('continuity-status').textContent=text;$('continuity-status').classList.toggle('error',error);}
 private async load(before?:number){const generation=++this.generation;const data=await api('story/continuity'+(before?'?before_note='+before:''));if(generation!==this.generation)return;
  const ctx=this.context();if(data.epoch!==ctx.epoch||data.run_id!==(ctx.active?.id||null)||(data.run_id&&data.note_revision!==ctx.active?.note_revision)){await this.refresh();return;}
  if(data.recap&&data.recap.position!==ctx.active?.position){await this.refresh();return;}
  if(before&&this.data)data.notes=[...this.data.notes,...data.notes];this.data=data;this.render();this.controls();
 }
 private button(text:string,action:()=>void,unavailable=false){const button=document.createElement('button');button.type='button';button.textContent=text;button.dataset.unavailable=String(unavailable);button.disabled=this.blocked||unavailable;button.onclick=action;return button;}
 private line(root:HTMLElement,label:string,text:string){const row=document.createElement('div');row.className='continuity-item';const heading=document.createElement('p');heading.className='continuity-label';heading.textContent=label;const body=document.createElement('p');body.textContent=text;row.append(heading,body);root.append(row);return row;}
 private render(){
  const data=this.data,recap=data?.recap,root=$('continuity-recap'),topics=$('continuity-topics'),notes=$('continuity-notes');root.replaceChildren();topics.replaceChildren();notes.replaceChildren();
  $('continuity-location').textContent=recap?(recap.position?'上次读到第 '+recap.position+' 句'+(recap.at_choice?' · 停在选项':''):'暂无原文。'):'请先建立共读。';
  for(const event of recap?.excerpts||[]){const label=event.kind==='dialogue'?(event.speaker||'人物')+'说过':event.kind==='choice'?'当时的选项':'原文摘录';const row=this.line(root,label+' · 第 '+event.position+' 句',event.text);row.append(this.button('看原句',()=>this.evidence([event])));}
  for(const topic of recap?.topics||[]){const row=this.line(topics,'上次聊到 · 第 '+topic.position+' 句',topic.text);const retained=data.notes.some(note=>note.origin_turn_id===topic.turn_id);row.append(this.button(retained?'已留在手记':'留作疑问',()=>this.mutate('notes',{turn_id:topic.turn_id,kind:'question',status:'open'}),retained));}
  if(recap&&!recap.topics.length)topics.textContent='暂无讨论。';
  for(const note of data?.notes||[]){const row=this.line(notes,kinds[note.kind]+' · '+(note.status==='resolved'?'已聊清楚':'留着继续聊')+' · 第 '+note.position+' 句',note.text);row.dataset.noteId=note.note_id;
   const actions=document.createElement('div');actions.className='continuity-actions';actions.append(this.button('修改',()=>this.edit(note)),this.button(note.status==='resolved'?'继续追踪':'标记聊清楚',()=>this.mutate('notes',{note_id:note.note_id,version:note.id,text:note.text,kind:note.kind,status:note.status==='open'?'resolved':'open',evidence_ids:note.evidence_ids})));
   if(note.evidence.length)actions.append(this.button('关联原文',()=>this.evidence(note.evidence)));
   const remove=this.button('删除',()=>{if(this.removing!==note.note_id){this.removing=note.note_id;remove.textContent='确认删除所有版本';this.message('再次点击会删除这条手记在各路线中的所有版本。原文和讨论保留。');return;}this.mutate('notes/delete',{note_id:note.note_id,version:note.id,confirmed:true});});actions.append(remove);row.append(actions);
  }
  if(!data?.notes.length)notes.textContent='暂无手记。';
  $('continuity-count').textContent=data?.note_count?'共 '+data.note_count+' 条手记'+(data.more_notes?'，已显示'+data.notes.length+'条':''):'';$('continuity-more').hidden=!data?.more_notes;
 }
 private reset(){this.editing=null;this.removing='';$('continuity-form').hidden=true;$('continuity-text').value='';$('continuity-link').checked=false;}
 private edit(note:any=null){this.editing=note;this.removing='';$('continuity-form').hidden=false;$('continuity-text').value=note?.text||'';$('continuity-kind').value=note?.kind||'question';$('continuity-state').value=note?.status||'open';
  $('continuity-link-field').hidden=!!note||!this.context().active?.events.length;$('continuity-link').checked=false;$('continuity-text').focus();this.message(note?'修改仅从当前位置起生效。':'最多 800 字。');}
 private async save(){const note=this.editing,active=this.context().active;let ids=note?.evidence_ids||[];if(!note&&$('continuity-link').checked&&active?.events.length)ids=[active.events[active.events.length-1].id];
  await this.mutate('notes',{note_id:note?.note_id,version:note?.id,text:$('continuity-text').value,kind:$('continuity-kind').value,status:$('continuity-state').value,evidence_ids:ids});}
 private async mutate(endpoint:string,body:any){if(this.blocked||!this.context().active)return;const ctx=this.context();this.block(true);try{const data=await api('story/'+endpoint,{...body,epoch:ctx.epoch,run_id:ctx.active.id});this.reset();this.apply(data);this.message(endpoint==='notes/delete'?'这条手记及其版本已删除，原文和讨论保留。':'手记已保存。');}catch(error){this.message(error.message,true);await this.refresh().catch(()=>{});}finally{this.block(false);}}
}
