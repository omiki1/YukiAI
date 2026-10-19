import './style.css';
import {Avatar} from './avatar';
import {api} from './api';
import {SettingsPanel} from './settings';
import {SpeechPlayer} from './speech';
import {StoryPanel,StoryEvidence} from './story';
import {ModelToolbar} from './toolbar';
declare global{interface Window{qt?:any;QWebChannel?:any;yuki:any;}}
type Turn={role:string;content:string;emotion?:string;evidence?:StoryEvidence[];answer_type?:string};
const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const avatar=new Avatar($<HTMLCanvasElement>('avatar'));
const greeting='读到哪儿了？我陪你一起看。';
let host:any=null,session='',turns:Turn[]=[],pending=false,typing:any=null,relax:any=null,fullText='';
let loadTask:Promise<void>|null=null;
let voiceEnabled=false,previewMode=false;
let classicTurns:Turn[]=[],conversationGeneration=0,readingInitialized=false,currentRequest:AbortController|null=null,currentEvidence:StoryEvidence[]=[],replyKind='',progressNote='';
const speech=new SpeechPlayer(busy=>{$('voice-stop').hidden=!busy;$('settings-voice-stop').hidden=!busy;});
const settings=new SettingsPanel(avatar,speech,data=>{voiceEnabled=data.voice.enabled&&!previewMode;if(!voiceEnabled)speech.stop();});
const story=new StoryPanel(syncReading,()=>{if(!$('settings').hidden)settings.close();closeMenu();setComposer(false);$('memories').hidden=true;$('history-toggle').setAttribute('aria-expanded','false');});
const toolbar=new ModelToolbar(avatar);
$('settings-toggle').addEventListener('click',()=>{story.close();closeMenu();setComposer(false);},true);
function closeMenu(){$('dialogue-tools').hidden=true;$('more-toggle').setAttribute('aria-expanded','false');}
function setComposer(open:boolean,focus=false){$('composer').hidden=!open;$('chat-toggle').textContent=open?'收起输入':'聊聊';$('chat-toggle').setAttribute('aria-expanded',String(open));if(open&&focus)$('message').focus();}
function resetBubble(){document.querySelector('.dialogue').classList.remove('expanded');$('line').scrollTop=0;$('reply-expand').setAttribute('aria-expanded','false');$('reply-expand').textContent='展开';$('reply-expand').hidden=true;}
function measureReply(){if(typing||document.querySelector<HTMLElement>('.dialogue').hidden||document.querySelector('.dialogue').classList.contains('expanded'))return;$('reply-expand').hidden=$('reply').scrollHeight<=$('reply').clientHeight+1;}
function expandReply(){if($('reply-expand').hidden)return;const expanded=document.querySelector('.dialogue').classList.toggle('expanded');$('reply-expand').setAttribute('aria-expanded',String(expanded));$('reply-expand').textContent=expanded?'收起长消息':'展开';if(!expanded)$('line').scrollTop=0;}
function setEvidence(events:StoryEvidence[]=[]){currentEvidence=events;$('evidence-toggle').hidden=events.length===0;}
function showLast(){const last=[...turns].reverse().find(turn=>turn.role==='ai');const text=last?.content||(story.active?'读到这里了，有什么想聊的？':greeting);resetBubble();$('reply').textContent=text;fullText=text;setEvidence(last?.evidence);requestAnimationFrame(measureReply);}
function syncReading(state:any,boundary:boolean){
 const active=state.active;$('reading-badge').textContent=active?'共读 · '+active.position:'';$('reading-badge').title=active?active.title+' · '+active.branch_name:'';
 $('new-chat').textContent=active?'新聊':'新篇';$('new-chat').title=active?'开始新的讨论，保留已读剧情':'开始新的对话';
 if(boundary||!readingInitialized){
  if(boundary){conversationGeneration++;currentRequest?.abort();currentRequest=null;speech.stop();stopTyping();clearTimeout(relax);avatar.setExpression('neutral');pending=false;$<HTMLButtonElement>('send').disabled=false;$<HTMLButtonElement>('new-chat').disabled=false;$<HTMLTextAreaElement>('message').value='';setComposer(false);closeMenu();$('dialogue-restore').textContent='◇ Yuki · 聊两句';}
  turns=active?active.messages:classicTurns;showLast();memories();
  hint(readingHint(active));
 }
 else if(!pending&&!typing&&active?.progress?.detail&&active.progress.verdict!=='steady'){progressNote=active.progress.detail;hint(replyHint());}
 readingInitialized=true;
}
function readingHint(active:any){const base=active?'共读《'+active.title+'》 · 已读 '+active.position+' 句':'一起看剧情，慢慢聊 · 默认不剧透';return active?.progress?.detail&&active.progress.verdict!=='steady'?base+' '+active.progress.detail:base;}
function hint(text:string,error=false){$('hint').textContent=text;$('hint').title=text;$('hint').classList.toggle('error',error);$('hint').classList.toggle('busy',pending&&!error);}
function stopTyping(){if(typing)clearTimeout(typing);typing=null;}
function settle(){clearTimeout(relax);relax=setTimeout(()=>avatar.setExpression('neutral'),2800);}
function replyHint(){const novel=story.active?.mode==='novel';const base=replyKind==='uncertain'?'这句我没把握，只按已读到的说。':replyKind==='analysis'?(novel?'按你读到的这段说的。':'按你这条路线里读到的剧情说的。'):'可以继续和我说。';return progressNote?base+' '+progressNote:base;}
function reveal(){stopTyping();$('reply').textContent=fullText;measureReply();hint(replyHint());settle();}
function write(text:string,emotion='neutral',kind=''){
 stopTyping();clearTimeout(relax);resetBubble();replyKind=kind;fullText=text;$('reply').textContent='';avatar.setExpression(emotion);
 const chars=Array.from(text);let i=0;
 const step=()=>{if(i>=chars.length){typing=null;measureReply();hint(replyHint());settle();return;}
  const c=chars[i++];$('reply').textContent+=c;$('line').scrollTop=$('line').scrollHeight;typing=setTimeout(step,/[。！？…]/.test(c)?130:30);};
 if(matchMedia('(prefers-reduced-motion:reduce)').matches)reveal();else step();
}
function memories(){const root=$('history-lines');root.replaceChildren();if(!turns.length){const p=document.createElement('p');p.className='memory-empty';p.textContent='这一篇，还没有写下对话。';root.append(p);}
 for(const t of turns){const div=document.createElement('div');div.className='memory '+(t.role==='human'?'user':'');const name=document.createElement('strong');name.textContent=t.role==='human'?'你':'Yuki';div.append(name,document.createTextNode(t.content));if(t.answer_type==='uncertain'){const note=document.createElement('span');note.className='memory-note';note.textContent='没把握';div.append(note);}if(t.evidence?.length){const button=document.createElement('button');button.className='memory-evidence';button.textContent='查看原文依据';button.onclick=()=>story.showEvidence(t.evidence);div.append(button);}root.append(div);}root.scrollTop=root.scrollHeight;
}
async function newSession(force=false){if(pending&&!force)return;if(story.active){await story.newChat();return;}speech.stop();const data=await api('sessions',{});session=data.session_id;turns=[];classicTurns=turns;localStorage.setItem('yuki.session',session);stopTyping();clearTimeout(relax);avatar.setExpression('neutral');resetBubble();$('reply').textContent=greeting;fullText=greeting;setEvidence();hint('一起看剧情，慢慢聊 · 默认不剧透');memories();requestAnimationFrame(measureReply);}
async function initialize(){
 const status=await api('status');previewMode=status.preview;await settings.load();session=localStorage.getItem('yuki.session')||'';
 if(session){try{const data=await api('sessions/'+encodeURIComponent(session));turns=data.messages;const last=[...turns].reverse().find(x=>x.role==='ai');if(last){$('reply').textContent=last.content;fullText=last.content;}}catch{session='';}}
 if(!session)await newSession(true);classicTurns=turns;await story.load();if(!status.chat_enabled)hint(status.preview?'预览中，外部接口不会调用。':'鼠标移到 Yuki 上方，打开设置接上聊天服务吧。');
}
$('line').addEventListener('click',()=>{if(typing)reveal();else expandReply();});$('line').addEventListener('keydown',e=>{if((e as KeyboardEvent).key==='Enter'){e.preventDefault();if(typing)reveal();else expandReply();}});
$('reply-expand').onclick=()=>expandReply();
$('chat-toggle').onclick=()=>{closeMenu();setComposer($('composer').hidden,true);};
$('more-toggle').onclick=()=>{const open=$('dialogue-tools').hidden;$('dialogue-tools').hidden=!open;$('more-toggle').setAttribute('aria-expanded',String(open));};
$('dialogue-tools').addEventListener('click',event=>{if((event.target as HTMLElement).closest('button'))closeMenu();});
document.addEventListener('pointerdown',event=>{const target=event.target as Node;if(!$('dialogue-tools').contains(target)&&!$('more-toggle').contains(target))closeMenu();});
document.addEventListener('keydown',event=>{if(event.key!=='Escape'||!$('settings').hidden||!$('story-panel').hidden)return;if(!$('memories').hidden){$('history-close').click();}else if(!$('dialogue-tools').hidden){closeMenu();$('more-toggle').focus();}else if(!$('composer').hidden){setComposer(false);$('chat-toggle').focus();}});
window.addEventListener('resize',()=>measureReply());
$('history-toggle').onclick=()=>{const open=$('memories').hidden;$('memories').hidden=!open;$('history-toggle').setAttribute('aria-expanded',String(open));if(open){memories();$('history-close').focus();}};
$('history-close').onclick=()=>{$('memories').hidden=true;$('history-toggle').setAttribute('aria-expanded','false');$('more-toggle').focus();};
$('new-chat').onclick=()=>newSession().catch(e=>hint(e.message,true));
$('voice-stop').onclick=()=>speech.stop();
$('settings-voice-stop').onclick=()=>speech.stop();
$('evidence-toggle').onclick=()=>story.showEvidence(currentEvidence);
$('dialogue-fold').onclick=()=>{document.querySelector<HTMLElement>('.dialogue').hidden=true;$('dialogue-restore').hidden=false;$('memories').hidden=true;$('history-toggle').setAttribute('aria-expanded','false');closeMenu();};
$('dialogue-restore').onclick=()=>{document.querySelector<HTMLElement>('.dialogue').hidden=false;$('dialogue-restore').hidden=true;$('dialogue-restore').textContent='◇ Yuki · 聊两句';setComposer(true,true);measureReply();};
$('composer').addEventListener('submit',async event=>{
 event.preventDefault();if(pending)return;const input=$<HTMLTextAreaElement>('message'),text=input.value.trim();if(!text)return;
 const generation=conversationGeneration;const controller=new AbortController();currentRequest=controller;
 pending=true;setComposer(false);closeMenu();speech.stop();$<HTMLButtonElement>('send').disabled=true;$<HTMLButtonElement>('new-chat').disabled=true;stopTyping();clearTimeout(relax);avatar.setExpression('neutral');hint('Yuki 正在想……');
 try{if(loadTask)await loadTask;if(generation!==conversationGeneration)return;const epoch=story.epoch;
  const result=await api('chat',{text,session_id:session,story_epoch:epoch},controller.signal);
  if(generation!==conversationGeneration||result.story_epoch!==undefined&&result.story_epoch!==story.epoch)return;
  progressNote=(result.progress_verdict==='looking_back'||result.progress_verdict==='rewind')?(result.progress_detail||''):'';
  turns.push({role:'human',content:text},{role:'ai',content:result.text,emotion:result.emotion,evidence:result.evidence,answer_type:result.answer_type});if(input.value.trim()===text){input.value='';setComposer(false);}setEvidence(result.evidence);write(result.text,result.emotion,result.answer_type||'');memories();
  if(!$('dialogue-restore').hidden)$('dialogue-restore').textContent='◇ Yuki · 有新回复';
  if(voiceEnabled){if(result.text.length<=1200)speech.play('speech',{text:result.text,story_epoch:epoch}).catch(e=>{if(generation===conversationGeneration)hint(e.message,true);});else hint('这段回复较长，保留文字阅读。');}
 }catch(e){if(generation===conversationGeneration&&e.name!=='AbortError'){hint(e.message,true);if(!document.querySelector<HTMLElement>('.dialogue').hidden&&$('story-panel').hidden&&$('settings').hidden)setComposer(true);story.load().catch(()=>{});}}
 finally{if(generation===conversationGeneration){pending=false;currentRequest=null;$('hint').classList.remove('busy');$<HTMLButtonElement>('send').disabled=false;$<HTMLButtonElement>('new-chat').disabled=false;}}
});
$('message').addEventListener('keydown',(e:KeyboardEvent)=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').dispatchEvent(new Event('submit',{cancelable:true}));}});
$('minimize').onclick=()=>host?.minimize();$('close').onclick=()=>host?.closeWindow();document.querySelector('.drag-area').addEventListener('pointerdown',(e:PointerEvent)=>{if(e.button===0)host?.startDrag();});
if(window.qt){const script=document.createElement('script');script.src='qrc:///qtwebchannel/qwebchannel.js';script.onload=()=>new window.QWebChannel(window.qt.webChannelTransport,channel=>{host=channel.objects.windowHost;});document.head.append(script);}
window.yuki={avatar,snapshot:()=>avatar.snapshot(),get pending(){return pending;},get typing(){return Boolean(typing);}};
avatar.init().then(()=>{$('model-status').hidden=true;}).catch(e=>{$('model-status').textContent=e.message;toolbar.modelUnavailable();});
loadTask=initialize().catch(e=>hint(e.message,true));
window.addEventListener('pagehide',()=>{currentRequest?.abort();toolbar.dispose();story.dispose();speech.stop();avatar.dispose();});
