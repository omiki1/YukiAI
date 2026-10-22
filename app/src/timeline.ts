import {api} from './api';
import type {StoryEvidence} from './story';
const $=(id:string)=>document.getElementById(id) as HTMLInputElement;
const labels={event:'原文片段',statement:'人物说辞',choice:'选项节点',chapter:'章节',note:'玩家手记'};
const noteLabels={question:'疑问',theory:'猜测',feeling:'感受'};

export class TimelinePanel{
 private data:any=null;private signature='';private generation=0;private blocked=false;private editing:any=null;private removing='';
 constructor(private context:()=>any,private apply:(data:any)=>void,private block:(value:boolean)=>void,
  private refresh:()=>Promise<void>,private evidence:(events:StoryEvidence[])=>void,private discuss:(text:string)=>void){
  $('timeline-filter').onsubmit=event=>{event.preventDefault();this.reload();};
  $('timeline-kind').onchange=()=>this.reload();$('timeline-person').onchange=()=>this.reload();
  $('timeline-clear').onclick=()=>{$('timeline-kind').value='all';$('timeline-person').value='';$('timeline-query').value='';this.reload();};
  $('timeline-more').onclick=()=>this.load(this.data?.more_before).catch(error=>this.message(error.message,true));
  $('timeline-note-form').onsubmit=event=>{event.preventDefault();this.save();};
  $('timeline-note-cancel').onclick=()=>this.resetEditor();
 }
 contextChanged(){const ctx=this.context(),signature=JSON.stringify([ctx.epoch,ctx.active?.id,ctx.active?.branch_id,ctx.active?.position,ctx.active?.note_revision]);
  if(signature===this.signature)return;this.signature=signature;++this.generation;this.data=null;this.resetEditor();this.removing='';
  $('timeline-cards').replaceChildren();$('timeline-person').replaceChildren(new Option('所有说话者',''));
  $('timeline-location').textContent=ctx.active?'载入中……':'请先建立共读。';$('timeline-count').textContent='';$('timeline-more').hidden=true;
  this.controls();if(!$('timeline-panel').hidden)this.open();
 }
 open(){if(!this.data)this.load().catch(error=>this.message(error.message,true));}
 setBlocked(value:boolean){this.blocked=value;this.controls();}
 private controls(){document.querySelectorAll<HTMLInputElement>('#timeline-panel button,#timeline-panel input,#timeline-panel select,#timeline-panel textarea').forEach(element=>element.disabled=this.blocked||!this.context().active||element.dataset.unavailable==='true');}
 private message(text:string,error=false){$('timeline-status').textContent=text;$('timeline-status').classList.toggle('error',error);}
 private resetEditor(){this.editing=null;$('timeline-note-form').hidden=true;$('timeline-note-text').value='';}
 private reload(){this.resetEditor();this.data=null;$('timeline-cards').replaceChildren();$('timeline-more').hidden=true;this.load().catch(error=>this.message(error.message,true));}
 private async load(before?:number){const generation=++this.generation,person=$('timeline-person').value;
  const params=new URLSearchParams({kind:$('timeline-kind').value,speaker:person,q:$('timeline-query').value.trim()});if(before)params.set('before',String(before));
  const data=await api('story/timeline?'+params);if(generation!==this.generation)return;
  const ctx=this.context();if(data.epoch!==ctx.epoch||data.run_id!==(ctx.active?.id||null)||data.run_id&&(data.branch_id!==ctx.active.branch_id||data.position!==ctx.active.position||data.note_revision!==ctx.active.note_revision)){await this.refresh();return;}
  if(before&&this.data)data.cards=[...data.cards,...this.data.cards];this.data=data;
  $('timeline-person').replaceChildren(new Option('所有说话者',''),...data.people.map((name:string)=>new Option(name,name)));$('timeline-person').value=data.people.includes(person)?person:'';
  this.render();this.controls();this.message('');
 }
 private button(text:string,action:()=>void,unavailable=false){const button=document.createElement('button');button.type='button';button.textContent=text;button.dataset.unavailable=String(unavailable);button.disabled=this.blocked||unavailable;button.onclick=action;return button;}
 private render(){const root=$('timeline-cards'),data=this.data;root.replaceChildren();
  $('timeline-location').textContent=data.run_id?this.context().active.branch_name+' · 已读到第 '+data.position+' 句':'请先建立共读。';
  $('timeline-count').textContent=data.run_id?'显示 '+data.cards.length+' / '+data.matched+' 张'+(data.matched!==data.total?'（全部 '+data.total+' 张）':''):'';$('timeline-more').hidden=!data.more_before;
  for(const card of data.cards){const article=document.createElement('article');article.className='timeline-card '+card.kind;article.dataset.cardId=card.id;
   const meta=document.createElement('p');meta.className='timeline-meta';meta.textContent=labels[card.kind]+' · '+(card.kind==='note'?'第 '+card.end+' 句时写下':'第 '+card.start+(card.end!==card.start?'–'+card.end:'')+' 句');
   article.append(meta);if(card.kind!=='note'){const title=document.createElement('h3');title.textContent=(card.kind==='event'?'旁白：':card.kind==='choice'?'选项：':'')+card.title;title.title=card.evidence[0]?.raw_text||card.title;article.append(title);}
   if(card.kind==='note'&&card.people.length){const people=document.createElement('p');people.className='timeline-people';people.textContent='关联原文说话者：'+card.people.join('、');article.append(people);}
   if(card.kind==='note'){const badge=document.createElement('p');badge.className='timeline-note-state';badge.textContent=noteLabels[card.note_kind]+' · '+(card.status==='resolved'?'已聊清楚':'继续追踪')+' · 玩家想法';const text=document.createElement('p');text.textContent=card.text;article.append(badge,text);}
   else for(const event of card.excerpts.slice(1)){const line=document.createElement('p');line.className='timeline-excerpt';const label=document.createElement('span');label.textContent=event.kind==='dialogue'?(event.speaker||'人物')+'说：':event.kind==='choice'?'选项：':event.kind==='chapter'?'章节：':'旁白：';line.append(label,document.createTextNode(event.text));article.append(line);}
   const actions=document.createElement('div');actions.className='timeline-actions';
   actions.append(this.button('看原文',()=>this.evidence(card.evidence),!card.evidence.length));
   if(card.kind==='note'){
    actions.append(this.button('修改',()=>this.edit(card)),this.button(card.status==='open'?'聊清楚了':'继续追踪',()=>this.mutate('notes',{note_id:card.note_id,version:card.version,text:card.text,kind:card.note_kind,status:card.status==='open'?'resolved':'open',evidence_ids:card.evidence.map((line:StoryEvidence)=>line.id)})));
    actions.append(this.button('删除',()=>{if(this.removing!==card.id){this.removing=card.id;this.render();this.message('再次点击确认，会删除这条手记在所有路线中的版本。');}else this.mutate('notes/delete',{note_id:card.note_id,version:card.version,confirmed:true});}));
    if(this.removing===card.id){actions.lastElementChild.textContent='确认删除';actions.append(this.button('取消删除',()=>{this.removing='';this.render();}));}
   }else actions.append(this.button('记个想法',()=>this.edit(card)));
   actions.append(this.button('一起聊',()=>{const refs=card.evidence.map((line:StoryEvidence)=>'第'+line.position+'句').join('、');this.discuss(card.kind==='note'?'关于我的'+noteLabels[card.note_kind]+'“'+card.text+'”'+(refs?'，结合'+refs:'')+'，我们继续聊聊；请把猜测和原文分开。':'我们一起梳理'+refs+'，区分旁白、人物说辞和猜测。');}));
   article.append(actions);root.append(article);
  }
  if(!data.cards.length){const empty=document.createElement('p');empty.className='story-empty';empty.textContent=data.total?'没有符合筛选条件的卡片。':'暂无卡片。';root.append(empty);}
 }
 private edit(card:any){this.editing=card;this.removing='';$('timeline-note-form').hidden=false;$('timeline-note-text').value=card.kind==='note'?card.text:'';$('timeline-note-kind').value=card.note_kind||'question';
  $('timeline-note-source').textContent='关联原文：'+(card.evidence.map((line:StoryEvidence)=>'第 '+line.position+' 句').join('、')||'无');$('timeline-note-text').focus();$('timeline-note-form').scrollIntoView({block:'nearest'});this.message('');}
 private async save(){const card=this.editing;if(!card)return;await this.mutate('notes',{text:$('timeline-note-text').value,kind:$('timeline-note-kind').value,status:card.status||'open',evidence_ids:card.evidence.map((line:StoryEvidence)=>line.id),...(card.kind==='note'?{note_id:card.note_id,version:card.version}:{})});}
 private async mutate(endpoint:string,body:any){if(this.blocked||!this.context().active)return;const ctx=this.context();this.block(true);
  try{const data=await api('story/'+endpoint,{...body,epoch:ctx.epoch,run_id:ctx.active.id,reading_position:ctx.active.position,note_revision:ctx.active.note_revision});this.resetEditor();this.apply(data);this.message(endpoint==='notes/delete'?'手记及其版本已删除，原文保留。':'已保存。');}
  catch(error){this.message(error.message,true);await this.refresh().catch(()=>{});}finally{this.block(false);}}
}
