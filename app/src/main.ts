import './style.css';
import {Avatar} from './avatar';
declare global{interface Window{qt?:any;QWebChannel?:any;yuki:any;}}
type Turn={role:string;content:string;emotion?:string};
const $=<T extends HTMLElement=HTMLElement>(id:string)=>document.getElementById(id) as T;
const avatar=new Avatar($<HTMLCanvasElement>('avatar'));
const greeting='玩到哪一段了？一起聊聊刚才那段剧情吧。';
let host:any=null,session='',turns:Turn[]=[],pending=false,typing:any=null,relax:any=null,fullText='';
let loadTask:Promise<void>|null=null;
function hint(text:string,error=false){$('hint').textContent=text;$('hint').classList.toggle('error',error);}
function stopTyping(){if(typing)clearTimeout(typing);typing=null;}
function settle(){clearTimeout(relax);relax=setTimeout(()=>avatar.setExpression('neutral'),2800);}
function reveal(){stopTyping();$('reply').textContent=fullText;$('advance').hidden=false;hint('可以继续和我说。');settle();}
function write(text:string,emotion='neutral'){
 stopTyping();clearTimeout(relax);fullText=text;$('reply').textContent='';$('advance').hidden=true;avatar.setExpression(emotion);
 const chars=Array.from(text);let i=0;
 const step=()=>{if(i>=chars.length){typing=null;$('advance').hidden=false;hint('可以继续和我说。');settle();return;}
  const c=chars[i++];$('reply').textContent+=c;$('line').scrollTop=$('line').scrollHeight;typing=setTimeout(step,/[。！？…]/.test(c)?130:30);};
 if(matchMedia('(prefers-reduced-motion:reduce)').matches)reveal();else step();
}
function memories(){const root=$('history-lines');root.replaceChildren();if(!turns.length){const p=document.createElement('p');p.className='memory-empty';p.textContent='这一篇，还没有写下对话。';root.append(p);}
 for(const t of turns){const div=document.createElement('div');div.className='memory '+(t.role==='human'?'user':'');const name=document.createElement('strong');name.textContent=t.role==='human'?'你':'Yuki';div.append(name,document.createTextNode(t.content));root.append(div);}root.scrollTop=root.scrollHeight;
}
async function api(url:string,body?:any){const r=await fetch('/api/'+url,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.detail||'连接没有成功，请稍后再试。');return data;}
async function newSession(){if(pending)return;const data=await api('sessions',{});session=data.session_id;turns=[];localStorage.setItem('yuki.session',session);stopTyping();clearTimeout(relax);avatar.setExpression('neutral');$('reply').textContent=greeting;fullText=greeting;$('advance').hidden=false;hint('一起看剧情，慢慢聊 · 默认不剧透');memories();}
async function initialize(){
 const status=await api('status');session=localStorage.getItem('yuki.session')||'';
 if(session){try{const data=await api('sessions/'+encodeURIComponent(session));turns=data.messages;const last=[...turns].reverse().find(x=>x.role==='ai');if(last){$('reply').textContent=last.content;fullText=last.content;}}catch{session='';}}
 if(!session)await newSession();if(!status.chat_enabled)hint('预览中，可以欣赏 Yuki。');
}
$('line').addEventListener('click',()=>{if(typing)reveal();});$('line').addEventListener('keydown',e=>{if((e as KeyboardEvent).key==='Enter'&&typing)reveal();});
$('history-toggle').onclick=()=>{const open=$('memories').hidden;$('memories').hidden=!open;$('history-toggle').setAttribute('aria-expanded',String(open));if(open)memories();};
$('history-close').onclick=()=>{$('memories').hidden=true;$('history-toggle').setAttribute('aria-expanded','false');};
$('new-chat').onclick=()=>newSession().catch(e=>hint(e.message,true));
$('composer').addEventListener('submit',async event=>{
 event.preventDefault();if(pending)return;const input=$<HTMLTextAreaElement>('message'),text=input.value.trim();if(!text)return;
 pending=true;$<HTMLButtonElement>('send').disabled=true;$<HTMLButtonElement>('new-chat').disabled=true;stopTyping();clearTimeout(relax);avatar.setExpression('neutral');hint('Yuki 正在想怎么回答……');
 try{if(loadTask)await loadTask;const result=await api('chat',{text,session_id:session});turns.push({role:'human',content:text},{role:'ai',content:result.text,emotion:result.emotion});input.value='';write(result.text,result.emotion);memories();}
 catch(e){hint(e.message,true);}finally{pending=false;$<HTMLButtonElement>('send').disabled=false;$<HTMLButtonElement>('new-chat').disabled=false;input.focus();}
});
$('message').addEventListener('keydown',(e:KeyboardEvent)=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').dispatchEvent(new Event('submit',{cancelable:true}));}});
$('minimize').onclick=()=>host?.minimize();$('close').onclick=()=>host?.closeWindow();document.querySelector('.drag-area').addEventListener('pointerdown',(e:PointerEvent)=>{if(e.button===0)host?.startDrag();});
if(window.qt){const script=document.createElement('script');script.src='qrc:///qtwebchannel/qwebchannel.js';script.onload=()=>new window.QWebChannel(window.qt.webChannelTransport,channel=>{host=channel.objects.windowHost;});document.head.append(script);}
window.yuki={avatar,snapshot:()=>avatar.snapshot(),get pending(){return pending;},get typing(){return Boolean(typing);}};
avatar.init().then(()=>{$('model-status').hidden=true;}).catch(e=>{$('model-status').textContent=e.message;});
loadTask=initialize().catch(e=>hint(e.message,true));
window.addEventListener('pagehide',()=>avatar.dispose());
