import {Avatar} from './avatar';

// A silhouette captured on layout changes keeps transparent canvas space inactive.
// The renderer's slight hair motion does not require reading pixels on every frame.
export class ModelToolbar{
 private bar=document.querySelector<HTMLElement>('.window-bar');
 private alpha:Uint8Array|null=null;private width=0;private height=0;
 private bounds:{left:number;top:number;right:number}|null=null;
 private pointer:{x:number;y:number}|null=null;private keyboard=false;private failed=false;
 private hideTimer:number|undefined;private listeners:(()=>void)[]=[];
 private resize:ResizeObserver;private panels:MutationObserver;
 constructor(private avatar:Avatar){
  avatar.onLayout=()=>this.capture();
  this.resize=new ResizeObserver(()=>this.position());this.resize.observe(this.bar);
  this.panels=new MutationObserver(()=>{this.position();this.refresh();});
  for(const id of ['settings','story-panel','memories','quickstart'])this.panels.observe(document.getElementById(id),{attributes:true,attributeFilter:['hidden']});
  this.listen(document,'pointermove',((event:PointerEvent)=>{this.pointer={x:event.clientX,y:event.clientY};this.refresh();}) as EventListener);
  this.listen(document,'pointerdown',((event:PointerEvent)=>{this.keyboard=false;this.pointer={x:event.clientX,y:event.clientY};this.refresh();}) as EventListener);
  this.listen(document.documentElement,'pointerleave',()=>{this.pointer=null;this.refresh();});
  this.listen(window,'blur',()=>{this.pointer=null;this.keyboard=false;this.hide();});
  this.listen(document,'focusin',()=>this.refresh());this.listen(document,'focusout',()=>this.refresh());
  this.listen(document,'keydown',((event:KeyboardEvent)=>{
   if(event.key==='Tab'){
    this.keyboard=true;
    if(document.activeElement===document.body){event.preventDefault();this.show();this.bar.querySelector<HTMLElement>('.drag-area').focus();}
    else this.refresh();
   }else if(event.key==='Escape'&&this.bar.contains(document.activeElement)&&!this.panelOpen()){
    (document.activeElement as HTMLElement).blur();this.keyboard=false;this.hide();
   }
  }) as EventListener);
 }
 private listen(target:EventTarget,event:string,handler:EventListener){target.addEventListener(event,handler);this.listeners.push(()=>target.removeEventListener(event,handler));}
 private panelOpen(){return ['settings','story-panel','memories','quickstart'].some(id=>!document.getElementById(id).hidden);}
 private capture(){
  const {pixels,width,height}=this.avatar.pixels();this.width=width;this.height=height;
  this.alpha=new Uint8Array(this.width*this.height);
  let left=this.width,right=-1,top=this.height;
  for(let y=0;y<this.height;y++)for(let x=0;x<this.width;x++){
   const index=y*this.width+x,value=pixels[index*4+3];this.alpha[index]=value;
   if(value>32){left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,this.height-y-1);}
  }
  this.bounds=right>=left?{left:left/this.width,top:top/this.height,right:(right+1)/this.width}:null;
  this.position();this.refresh();
 }
 private overModel(x:number,y:number){
  if(!this.alpha||!this.width||!this.height)return false;
  const rect=this.avatar.canvas.getBoundingClientRect();
  if(x<rect.left||x>=rect.right||y<rect.top||y>=rect.bottom)return false;
  const px=Math.floor((x-rect.left)/rect.width*this.width),py=this.height-1-Math.floor((y-rect.top)/rect.height*this.height);
  return this.alpha[py*this.width+px]>32;
 }
 private position(){
  const rect=this.avatar.canvas.getBoundingClientRect(),panel=this.panelOpen();
  const center=panel||!this.bounds?innerWidth/2:rect.left+(this.bounds.left+this.bounds.right)/2*rect.width;
  const left=Math.max(8,Math.min(innerWidth-this.bar.offsetWidth-8,center-this.bar.offsetWidth/2));
  const top=panel||!this.bounds?6:Math.max(6,rect.top+this.bounds.top*rect.height-this.bar.offsetHeight-8);
  this.bar.style.left=left+'px';this.bar.style.top=top+'px';
 }
 private refresh(){
  let hovering=false;
  if(this.pointer){
   const {x,y}=this.pointer,target=document.elementFromPoint(x,y);
   hovering=this.bar.contains(target)||!!target&&!target.closest('.dialogue,#dialogue-restore,.settings,#story-panel,.memories,#quickstart')&&this.overModel(x,y);
  }
  if(hovering||this.failed||this.panelOpen()||this.keyboard&&this.bar.contains(document.activeElement)){this.show();return;}
  // Leave enough time to cross the small gap from the character to its controls.
  if(this.hideTimer===undefined)this.hideTimer=window.setTimeout(()=>this.hide(),550);
 }
 private show(){clearTimeout(this.hideTimer);this.hideTimer=undefined;this.bar.classList.add('revealed');this.bar.setAttribute('aria-hidden','false');}
 private hide(){
  clearTimeout(this.hideTimer);this.hideTimer=undefined;
  if(this.bar.contains(document.activeElement))(document.activeElement as HTMLElement).blur();
  this.bar.classList.remove('revealed');this.bar.setAttribute('aria-hidden','true');
 }
 modelUnavailable(){this.failed=true;this.position();this.show();}
 dispose(){clearTimeout(this.hideTimer);this.resize.disconnect();this.panels.disconnect();this.listeners.forEach(remove=>remove());this.avatar.onLayout=undefined;this.alpha=null;}
}
