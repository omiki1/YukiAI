import {api} from './api';
import {CapturePanel} from './capture';
import {MemoryPanel} from './memory';
import {ContinuityPanel} from './continuity';
import {CharacterPanel} from './characters';
const $=(id:string)=>document.getElementById(id) as HTMLInputElement;

export type StoryEvidence={id:number;position:number;kind:string;speaker:string;text:string;raw_text:string};
export class StoryPanel{
 state:any=null;private busy=false;private generation=0;private importSignature='';private importId='';private timer:number;private capture:CapturePanel;private memory:MemoryPanel;private continuity:ContinuityPanel;private characters:CharacterPanel;
 constructor(private changed:(state:any,boundary:boolean)=>void,private opening:()=>void,historyChanged:(uid:string,deleted:boolean)=>Promise<void>){
  this.capture=new CapturePanel(()=>({epoch:this.epoch,active:this.active}),()=>this.load());
  this.memory=new MemoryPanel(()=>({epoch:this.epoch,active:this.active}),data=>this.apply(data),value=>this.setBusy(value),historyChanged);
  this.continuity=new ContinuityPanel(()=>({epoch:this.epoch,active:this.active}),data=>this.apply(data),value=>this.setBusy(value),()=>this.load(),events=>this.showEvidence(events));
  this.characters=new CharacterPanel(()=>({epoch:this.epoch,active:this.active}),data=>this.apply(data),value=>this.setBusy(value),()=>this.load(),events=>this.showEvidence(events));
  $('story-toggle').onclick=()=>this.open();$('story-close').onclick=()=>this.close();
  $('story-run').onchange=()=>this.action('activate',{run_id:$('story-run').value||null},'已切换共读。');
  $('story-new-toggle').onclick=()=>{const open=$('story-new-form').hidden;this.tab(open?'new':'events');if(open)$('story-game-title').focus();};
  $('story-mode').onchange=()=>this.modeLabels();
  $('story-new-form').onsubmit=event=>{event.preventDefault();const novel=$('story-mode').value==='novel';this.action('runs',{title:$('story-game-title').value,name:$('story-run-name').value,mode:novel?'novel':'galgame'},novel?'已建立小说共读，可以收录读过的段落了。':'已建立共读，可以收录已读文本了。').then(ok=>{if(ok){$('story-new-form').hidden=true;$('story-game-title').value='';$('story-run-name').value='';this.tab('import');}});};
  document.querySelectorAll<HTMLButtonElement>('[data-story-tab]').forEach(button=>button.onclick=()=>this.tab(button.dataset.storyTab));
  $('story-kind').onchange=()=>{$('story-speaker-field').hidden=$('story-kind').value!=='dialogue';$('story-confirm').checked=false;};
  $('capture-manual').onclick=()=>{this.tab('import');$('story-text').focus();this.message(this.active?'粘贴已经读过的文字，核对后收录。':'先点“新共读”，再粘贴已读文字。');};
  $('story-text').oninput=()=>{$('story-confirm').checked=false;};
  $('story-import-form').onsubmit=event=>{event.preventDefault();this.importText();};
  $('story-bookmark-form').onsubmit=event=>{event.preventDefault();this.action('bookmarks',{name:$('story-bookmark-name').value},'书签已留下，读档后可以回到这里。').then(ok=>{if(ok)$('story-bookmark-name').value='';});};
  $('story-fork-form').onsubmit=event=>{event.preventDefault();this.action('fork',{name:$('story-branch-name').value},'已开启新分支，之前的路线仍保留。').then(ok=>{if(ok)$('story-branch-name').value='';});};
  $('story-branch').onchange=()=>this.action('branch',{branch_id:$('story-branch').value},'已切换到这条路线的记录末尾。');
  $('story-next').onclick=()=>this.action('progress',{position:this.state.active.position+1},'已前进一步。');
  $('story-before').onclick=()=>this.earlier();$('story-evidence-back').onclick=()=>this.allEvents();
  document.addEventListener('keydown',event=>{if($('story-panel').hidden)return;
   if(event.key==='Escape'){event.preventDefault();this.close();}
   if(event.key==='Tab'){const elements=Array.from($('story-panel').querySelectorAll<HTMLElement>('button,input,select,textarea')).filter(el=>el.getClientRects().length&&!el.hasAttribute('disabled'));
    const first=elements[0],last=elements[elements.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
  });
  this.timer=window.setInterval(()=>{if(this.state&&!this.busy)this.load().catch(()=>{});},5000);
 }
 get epoch(){return this.state?.epoch??null;}
 get active(){return this.state?.active??null;}
 async load(){const generation=++this.generation;const data=await api('story');if(generation!==this.generation)return;
  const signature=(state:any)=>JSON.stringify([state?.epoch,state?.runs,state?.active?.position,state?.active?.messages,state?.active?.bookmarks,state?.active?.branches,state?.active?.memory,state?.active?.progress,state?.active?.note_revision]);
  if(!this.state||signature(data)!==signature(this.state))this.apply(data);
 }
 open(){this.opening();$('story-panel').hidden=false;$('story-toggle').setAttribute('aria-expanded','true');$('story-close').focus();this.load().catch(e=>this.message(e.message,true));}
 beginReading(newReading=false){this.open();const current=!!this.active&&!newReading;this.tab(current?'import':'new');$(current?'story-text':'story-game-title').focus();}
 close(){if(this.busy||$('story-panel').hidden)return;$('story-panel').hidden=true;$('story-toggle').setAttribute('aria-expanded','false');$('story-toggle').focus();}
 private tab(name:string){document.querySelectorAll<HTMLElement>('[data-story-pane]').forEach(pane=>pane.hidden=pane.dataset.storyPane!==name);document.querySelectorAll<HTMLElement>('[data-story-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.storyTab===name)));$('story-new-toggle').setAttribute('aria-expanded',String(name==='new'));if(name==='capture')this.capture.open();if(name==='manage')this.memory.open();}
 private message(text:string,error=false){$('story-status').textContent=text;$('story-status').title=text;$('story-status').classList.toggle('error',error);}
 private setBusy(value:boolean){this.busy=value;if(value)++this.generation;$('story-panel').querySelectorAll<HTMLInputElement>('button,input,select,textarea').forEach(el=>el.disabled=value);$('settings-toggle').disabled=value;this.capture.setBlocked(value);this.memory.setBlocked(value);this.continuity.setBlocked(value);this.characters.setBlocked(value);}
 private apply(data:any){
  const boundary=this.state!==null&&(this.state.epoch!==data.epoch||this.state.active?.note_revision!==data.active?.note_revision);this.state=data;const active=data.active;
  const options=[{id:'',label:'普通聊天（不使用剧情记录）'},...data.runs.map(run=>({id:run.id,label:run.title+' · '+run.name}))];
  const select=$('story-run') as unknown as HTMLSelectElement;select.replaceChildren();for(const option of options)select.add(new Option(option.label,option.id));select.value=active?.id||'';
  const reading=active?.memory?.reading;const novel=active?.mode==='novel';
  const place=active?((novel?'小说 · ':'')+active.title+' · '+active.branch_name+' · 已读 '+active.position+' 句'+(reading?' · '+(novel?'本段 ':'本场 ')+reading.start+'–'+reading.end+(reading.at_choice&&!novel?' · 停在选项':''):'')):'先建立一篇共读';
  $('story-text').placeholder=novel?'第一章\n他站着喝酒，穿的却是长衫。':'美咲：昨晚我没有离开宿舍。\n旁白：她避开了你的目光。';
  $('story-position').textContent=place;
  $('story-position').title=$('story-position').textContent;
  const progress=active?.progress;$('story-progress').hidden=!progress?.detail;$('story-progress').textContent=progress?.detail||'';
  if(boundary){this.allEvents();$('story-evidence-lines').replaceChildren();$('story-confirm').checked=false;this.message('共读进度或阅读记忆已更新，旧回复已收起。');}
  this.renderEvents();this.renderMemory();this.renderBookmarks();this.capture.contextChanged();this.memory.contextChanged();this.continuity.contextChanged();this.characters.contextChanged();this.changed(data,boundary);
 }
 private modeLabels(){const novel=$('story-mode').value==='novel';$('story-title-label').textContent=novel?'小说名称':'游戏名称';$('story-game-title').placeholder=novel?'正在读的小说':'你正在玩的中文版游戏';$('story-run-name').placeholder=novel?'小说共读':'初见共读';}
 private renderMemory(){const root=$('story-memory-body');root.replaceChildren();const note=document.createElement('p');note.className='story-note';note.textContent=this.active?.mode==='novel'?'按已读章节整理，不另调用模型。人物说的话和叙述分开记，还没读的章节不会出现在这里。':'按当前路线已读原文整理，不另调用模型。说辞和旁白分开记，其他路线不会出现在这里。';root.append(note);
  const memory=this.active?.memory,scenes=memory?.scenes||[],people=memory?.characters||[];
  if(!scenes.length&&!people.length){const empty=document.createElement('p');empty.className='story-empty';empty.textContent=this.active?'收录已读原文后，这里会按场景和人物收拢。':'先建立共读并收录已读原文。';root.append(empty);return;}
  for(const scene of scenes){const block=document.createElement('div');block.className='story-memory-scene'+(scene.current?' current':'');const label=document.createElement('div');label.className='story-memory-label';label.textContent=(scene.current?'正在读 · ':'')+'第 '+scene.start+'–'+scene.end+' 句'+(scene.speakers?.length?' · '+scene.speakers.join('、'):'')+(scene.current&&scene.at_choice?' · 停在选项':'');const text=document.createElement('p');text.textContent=scene.summary;block.append(label,text);root.append(block);}
  for(const person of people){const block=document.createElement('div');block.className='story-memory-person';const label=document.createElement('div');label.className='story-memory-label';label.textContent=person.name+(person.aliases?.length?' · 也叫'+person.aliases.join('、'):'');block.append(label);for(const claim of person.claims||[])block.append(this.memoryLine('说辞',claim));for(const line of person.witnessed||[])block.append(this.memoryLine('旁白',line));root.append(block);}
 }
 private memoryLine(kind:string,line:StoryEvidence){const row=document.createElement('div');row.className='story-memory-line';const text=document.createElement('span');text.textContent=kind+'：'+line.text;const button=document.createElement('button');button.type='button';button.className='story-position-button';button.textContent='看这句';button.onclick=()=>this.showEvidence([line]);row.append(text,button);return row;}
 private card(event:StoryEvidence,navigate=false){
  const card=document.createElement('article');card.className='story-event';card.dataset.eventId=String(event.id);
  const heading=document.createElement('div');heading.className='story-event-label';heading.textContent='第 '+event.position+' 句 · '+(event.kind==='choice'?'选项':event.speaker||'旁白');
  const text=document.createElement('p');text.textContent=event.raw_text;card.append(heading,text);
  if(navigate){const button=document.createElement('button');button.type='button';button.className='story-position-button';button.textContent='读到这里';button.onclick=()=>this.action('progress',{position:event.position},'已校准到第 '+event.position+' 句。');card.append(button);}
  return card;
 }
 private renderEvents(){const root=$('story-events');root.replaceChildren();const active=this.active;
  if(!active||!active.events.length){const p=document.createElement('p');p.className='story-empty';p.textContent=active?'这里还没有原文，去“收录”放入已经读过的片段吧。':'先点“新共读”，为正在玩的游戏留一篇记录。';root.append(p);}
  else for(const event of active.events)root.append(this.card(event,true));
  $('story-before').hidden=!active?.more_before;$('story-next').hidden=!active||active.position>=active.tip;
 }
 private renderBookmarks(){const root=$('story-bookmarks');root.replaceChildren();
  for(const point of this.active?.bookmarks||[]){const row=document.createElement('div');row.className='story-bookmark';const label=document.createElement('span');label.textContent=point.name+' · 第 '+point.position+' 句';const button=document.createElement('button');button.type='button';button.textContent='回到这里';button.onclick=()=>this.action('restore',{bookmark_id:point.id},'已回到书签。继续收录新剧情时会保留原路线。');row.append(label,button);root.append(row);}
  const select=$('story-branch') as unknown as HTMLSelectElement;select.replaceChildren();for(const branch of this.active?.branches||[])select.add(new Option(branch.name+' · '+branch.tip+' 句',branch.id));select.value=this.active?.branch_id||'';
 }
 private async earlier(){if(this.busy||!this.active?.more_before)return;this.setBusy(true);const epoch=this.epoch,run=this.active.id;
  try{const data=await api('story?before='+this.active.more_before);if(data.epoch!==epoch||data.active?.id!==run){this.apply(data);return;}
   this.state.active.events=[...data.active.events,...this.active.events];this.state.active.more_before=data.active.more_before;this.renderEvents();
  }catch(error){this.message(error.message,true);}finally{this.setBusy(false);}
 }
 private async importText(){if(!this.active){this.message('请先建立一篇共读。',true);return;}
  const body={run_id:this.active.id,text:$('story-text').value,kind:$('story-kind').value,speaker:$('story-kind').value==='dialogue'?$('story-speaker').value:'',confirmed_read:$('story-confirm').checked};
  const signature=JSON.stringify(body);if(signature!==this.importSignature){this.importSignature=signature;this.importId=globalThis.crypto?.randomUUID?.()||Date.now().toString(36)+Math.random().toString(36).slice(2);}
  const ok=await this.action('import',{...body,request_id:this.importId},'已收录在本机。现在可以和 Yuki 聊这段剧情了。');
  if(ok){$('story-text').value='';$('story-confirm').checked=false;this.importSignature='';this.tab('events');}
 }
 async action(endpoint:string,body:any,message:string){if(this.busy)return false;this.setBusy(true);++this.generation;this.message('正在保存……');
  try{const data=await api('story/'+endpoint,{...body,epoch:this.epoch});this.apply(data);this.message(message);return true;}
  catch(error){this.message(error.message,true);try{await this.load();}catch{}return false;}
  finally{this.setBusy(false);}
 }
 async newChat(){return this.action('chat/new',{},'开始新一段讨论，已读剧情仍然保留。');}
 showEvidence(events:StoryEvidence[]){this.opening();$('story-panel').hidden=false;$('story-toggle').setAttribute('aria-expanded','true');this.tab('events');$('story-evidence').hidden=false;$('story-all-events').hidden=true;
  const root=$('story-evidence-lines');root.replaceChildren();for(const event of events)root.append(this.card(event));$('story-close').focus();}
 private allEvents(){$('story-evidence').hidden=true;$('story-all-events').hidden=false;}
 dispose(){clearInterval(this.timer);this.capture.dispose();}
}
