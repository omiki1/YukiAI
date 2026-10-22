import {api} from './api';
const $=(id:string)=>document.getElementById(id) as HTMLButtonElement;
export const exampleText='旁白：雨停了，美咲仍留在走廊。\n美咲：我在等一位还没回来的朋友。\n旁白：她把另一把伞放在窗边，没有带走。';

export class QuickStart{
 private busy=false;
 constructor(private connect:()=>Promise<void>,private manual:()=>void,private example:()=>Promise<void>,private discuss:()=>void,private opening:()=>void){
  $('guide-open').onclick=()=>this.open().catch(error=>this.message(error.message));
  $('guide-close').onclick=()=>this.close();
  $('guide-connect').onclick=()=>{this.close();this.connect().catch(error=>this.message(error.message));};
  $('guide-manual').onclick=()=>{this.close();this.manual();};
  $('guide-example').onclick=()=>this.useExample();
  $('guide-discuss').onclick=()=>{this.close();this.discuss();};
  $('guide-example-text').textContent=exampleText;
  document.addEventListener('keydown',event=>{if($('quickstart').hidden)return;if(event.key==='Escape'){event.preventDefault();this.close();}
   if(event.key==='Tab'){const buttons=Array.from($('quickstart').querySelectorAll<HTMLButtonElement>('button')).filter(button=>!button.disabled);const first=buttons[0],last=buttons[buttons.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
  });
 }
 async open(){this.opening();$('quickstart').hidden=false;$('guide-close').focus();this.message('');await this.refresh();}
 close(){if(this.busy)return;$('quickstart').hidden=true;localStorage.setItem('yuki.guide.dismissed','1');}
 async refresh(){const [status,story]=await Promise.all([api('status'),api('story')]);
  $('guide-connection').textContent=status.chat_enabled?'已配置':'尚未配置';
  $('guide-reading').textContent=story.active?.position?'当前已读 '+story.active.position+' 句':'尚未开始';
  $('guide-discuss').disabled=!status.chat_enabled||!story.active?.position;
 }
 private message(text:string){$('guide-status').textContent=text;$('guide-status').hidden=!text;}
 private async useExample(){if(this.busy)return;this.busy=true;this.controls(true);this.message('保存中……');
  try{await this.example();this.message('三句原文已保存。');await this.refresh();}
  catch(error){this.message(error.message);}finally{this.busy=false;this.controls(false);await this.refresh().catch(()=>{});}
 }
 private controls(value:boolean){$('quickstart').querySelectorAll<HTMLButtonElement>('button').forEach(button=>button.disabled=value);if(!value)$('guide-discuss').disabled=true;}
}
