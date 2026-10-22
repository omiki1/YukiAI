import {api} from './api';
import type {StoryEvidence} from './story';
const $=(id:string)=>document.getElementById(id) as HTMLInputElement;

export class CharacterPanel{
 private data:any=null;private editing:any=null;private preview:any=null;private blocked=false;private generation=0;private signature='';private removing='';
 constructor(private context:()=>any,private apply:(data:any)=>void,private block:(value:boolean)=>void,
  private refresh:()=>Promise<void>,private evidence:(events:StoryEvidence[])=>void){
  $('character-new').onclick=()=>this.edit();$('character-cancel').onclick=()=>this.reset();
  $('character-more').onclick=()=>this.load(this.data?.more).catch(error=>this.message(error.message,true));
  $('character-form').onsubmit=event=>{event.preventDefault();this.save();};
  $('character-preview-button').onclick=()=>this.prepare();
  for(const id of ['character-kind','character-label','character-pronoun','character-target'])$(id).addEventListener('input',()=>{this.clearPreview();this.fields();});
  $('character-agree').onchange=()=>this.controls();
 }
 contextChanged(){const ctx=this.context();const signature=JSON.stringify([ctx.epoch,ctx.active?.id,ctx.active?.position,ctx.active?.note_revision]);if(signature===this.signature)return;this.signature=signature;this.reset();this.data=null;++this.generation;$('character-list').replaceChildren();$('character-count').textContent='';$('character-more').hidden=true;this.load().catch(error=>this.message(error.message,true));}
 setBlocked(value:boolean){this.blocked=value;this.controls();}
 private controls(){const unavailable=this.blocked||!this.context().active?.position;
  document.querySelectorAll<HTMLInputElement>('#character-panel button,#character-panel input,#character-panel select').forEach(element=>element.disabled=unavailable);
  for(const id of ['character-kind','character-label','character-pronoun'])$(id).disabled=unavailable||!!this.editing;
  $('character-agree').disabled=unavailable||!this.preview;$('character-save').disabled=unavailable||!this.preview||!$('character-agree').checked;
 }
 private message(text:string,error=false){$('character-status').textContent=text;$('character-status').classList.toggle('error',error);}
 private clearPreview(){this.preview=null;$('character-preview').hidden=true;$('character-agree').checked=false;$('character-evidence').replaceChildren();this.controls();}
 private reset(){this.editing=null;this.removing='';$('character-form').hidden=true;$('character-label').value='';$('character-target').value='';this.clearPreview();}
 private fields(){const pronoun=$('character-kind').value==='pronoun';$('character-alias-field').hidden=pronoun;$('character-pronoun-field').hidden=!pronoun;$('character-scope').textContent=pronoun?'仅当前句生效。':'当前路线 · 从此处起生效。';}
 private async load(before?:number){const generation=++this.generation;const data=await api('story/characters'+(before?'?before_correction='+before:''));if(generation!==this.generation)return;const ctx=this.context();
  if(data.epoch!==ctx.epoch||data.run_id!==(ctx.active?.id||null)||(data.run_id&&(data.position!==ctx.active.position||data.revision!==ctx.active.note_revision))){await this.refresh();return;}
  if(before&&this.data)data.corrections=[...this.data.corrections,...data.corrections];this.data=data;this.render();this.controls();
 }
 private button(text:string,action:()=>void){const button=document.createElement('button');button.type='button';button.textContent=text;button.onclick=action;button.disabled=this.blocked;return button;}
 private render(){const data=this.data,root=$('character-list');root.replaceChildren();$('character-count').textContent=data?.count?'共 '+data.count+' 条':'';$('character-more').hidden=!data?.more;
  const choices=$('character-targets');choices.replaceChildren();for(const name of data?.characters||[]){const option=document.createElement('option');option.value=name;choices.append(option);}
  for(const item of data?.corrections||[]){const row=document.createElement('article');row.className='character-item';row.dataset.correctionId=item.correction_id;const label=document.createElement('p');label.className='character-label';label.textContent=item.kind==='alias'?'玩家核对的称呼 · 自第 '+item.position+' 句起':'第 '+item.anchor_position+' 句的讨论指代'+(item.active?' · 此处生效':' · 历史记录');const text=document.createElement('p');text.textContent=item.label+' → '+item.target;const actions=document.createElement('div');actions.className='continuity-actions';actions.append(this.button('修改',()=>this.edit(item)),this.button('关联原文',()=>this.evidence(item.evidence)));
   const remove=this.button('删除',()=>{if(this.removing!==item.correction_id){this.removing=item.correction_id;remove.textContent='确认删除所有版本';this.message('再次点击会删除各路线中的这条纠正及其全部版本，原文保留。');return;}this.mutate('delete',{correction_id:item.correction_id,version:item.id,confirmed:true});});actions.append(remove);row.append(label,text,actions);root.append(row);}
  if(!data?.count)root.textContent='暂无纠正。';
 }
 private edit(item:any=null){this.editing=item;this.removing='';$('character-form').hidden=false;$('character-kind').value=item?.kind||'alias';$('character-label').value=item?.kind==='alias'?item.label:'';$('character-pronoun').value=item?.kind==='pronoun'?item.label:'她';$('character-target').value=item?.target||'';this.clearPreview();this.fields();$('character-target').focus();this.message('');}
 private async work(task:()=>Promise<void>){if(this.blocked)return;this.block(true);try{await task();}catch(error){this.clearPreview();this.message(error.message,true);await this.refresh().catch(()=>{});}finally{this.block(false);}}
 private async prepare(){await this.work(async()=>{const ctx=this.context();const kind=$('character-kind').value;const body={epoch:ctx.epoch,run_id:ctx.active.id,position:ctx.active.position,revision:ctx.active.note_revision,kind,label:kind==='pronoun'?$('character-pronoun').value:$('character-label').value,target:$('character-target').value,correction_id:this.editing?.correction_id,version:this.editing?.id};
   const data=await api('story/characters/preview',body);this.preview=body;$('character-description').textContent=data.description;const root=$('character-evidence');root.replaceChildren();for(const event of data.evidence){const paragraph=document.createElement('p');paragraph.textContent='第 '+event.position+' 句 · '+(event.speaker||'旁白')+'：'+event.text;root.append(paragraph);}root.append(this.button('查看完整原文',()=>this.evidence(data.evidence)));$('character-preview').hidden=false;$('character-agree').checked=false;this.message('');});}
 private async save(){if(!this.preview||!$('character-agree').checked)return;await this.mutate('save',{...this.preview,confirmed:true});}
 private async mutate(endpoint:string,body:any){await this.work(async()=>{const ctx=this.context();const result=await api('story/characters/'+endpoint,{epoch:ctx.epoch,run_id:ctx.active.id,...body});this.reset();this.apply(result);this.message(endpoint==='delete'?'纠正及全部版本已删除，原文保留。':'已保存。');});}
}
