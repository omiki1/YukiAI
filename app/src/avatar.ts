import {api} from './api';
import type {Avatar as LiveAvatar} from './live2d-avatar';
declare const __YUKI_PAPER_ONLY__:boolean;

// The reading UI remains usable without private models, a Cubism runtime, or WebGL.
export class Avatar{
 expression='neutral';ready=false;scale=.85;onLayout?:()=>void;
 private live:LiveAvatar|null=null;private paper:CanvasRenderingContext2D|null=null;
 private frame=0;private drawnScale=0;private observer:ResizeObserver;
 constructor(public canvas:HTMLCanvasElement){this.observer=new ResizeObserver(()=>{if(this.paper)this.drawPaper();});this.observer.observe(canvas);}
 get time(){return this.live?.time||0;}
 async init(){
  const status=await api('status');
  if(!__YUKI_PAPER_ONLY__&&status.model_available){
   try{
    await new Promise<void>((resolve,reject)=>{const script=document.createElement('script');script.src='/vendor/live2d-core/live2dcubismcore.js';script.onload=()=>resolve();script.onerror=()=>reject(new Error('模型运行库不可用'));document.head.append(script);});
    const {Avatar:Live}=await import('./live2d-avatar');
    this.live=new Live(this.canvas);this.live.scale=this.scale;this.live.onLayout=()=>this.onLayout?.();await this.live.init();this.ready=true;return;
   }catch{
    try{this.live?.dispose();}catch{}this.live=null;
    // A canvas already bound to WebGL cannot be switched to 2D.
    const next=this.canvas.cloneNode() as HTMLCanvasElement;this.canvas.replaceWith(next);this.canvas=next;this.observer.disconnect();this.observer.observe(next);
   }
  }
  this.paper=this.canvas.getContext('2d');if(!this.paper)throw new Error('界面绘制暂时不可用，请重新打开。');
  this.ready=true;this.drawPaper();
 }
 setExpression(name:string){this.expression=name;this.live?.setExpression(name);if(this.paper)this.drawPaper();}
 setScale(value:number){if(!Number.isFinite(value))return;this.scale=Math.min(1.1,Math.max(.6,value));this.live?.setScale(this.scale);if(this.paper)this.drawPaper();}
 pixels(){
  const width=this.canvas.width,height=this.canvas.height,pixels=new Uint8Array(width*height*4);
  if(this.live){const gl=this.live.gl;gl.readPixels(0,0,width,height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);}
  else if(this.paper){const data=this.paper.getImageData(0,0,width,height).data;for(let y=0;y<height;y++)pixels.set(data.subarray(y*width*4,(y+1)*width*4),(height-1-y)*width*4);}
  return {pixels,width,height};
 }
 private drawPaper(){
  const ctx=this.paper;if(!ctx||!this.ready)return;const dpr=Math.min(devicePixelRatio||1,2),width=Math.max(1,Math.round(this.canvas.clientWidth*dpr)),height=Math.max(1,Math.round(this.canvas.clientHeight*dpr));
  const changed=width!==this.canvas.width||height!==this.canvas.height||this.drawnScale!==this.scale;
  this.canvas.width=width;this.canvas.height=height;ctx.clearRect(0,0,width,height);
  const unit=Math.min(width/280,height/230)*this.scale;ctx.save();ctx.translate(width/2,height*.55);ctx.scale(unit,unit);
  // Original paper bookmark, intentionally independent of character/model assets.
  ctx.beginPath();ctx.moveTo(-47,-61);ctx.quadraticCurveTo(-56,-61,-56,-51);ctx.lineTo(-56,63);ctx.quadraticCurveTo(-56,72,-47,69);ctx.lineTo(0,48);ctx.lineTo(47,69);ctx.quadraticCurveTo(56,72,56,63);ctx.lineTo(56,-51);ctx.quadraticCurveTo(56,-61,47,-61);ctx.closePath();
  ctx.fillStyle='#faf5e9';ctx.fill();ctx.strokeStyle='#b6a68c';ctx.lineWidth=1.5;ctx.stroke();
  ctx.fillStyle='#d8e4d2';ctx.fillRect(-45,-49,90,10);ctx.strokeStyle='#617866';ctx.lineCap='round';ctx.lineWidth=2.5;
  const happy=this.expression==='happy'||this.expression==='smug';
  for(const x of [-15,15]){ctx.beginPath();if(happy)ctx.arc(x,-7,5,Math.PI,2*Math.PI);else{ctx.moveTo(x,-11);ctx.lineTo(x,-5);}ctx.stroke();}
  ctx.beginPath();ctx.moveTo(-6,8);ctx.quadraticCurveTo(0,this.expression==='sad'?3:14,6,8);ctx.stroke();
  ctx.font='italic 19px Georgia';ctx.textAlign='center';ctx.fillStyle='#786b58';ctx.fillText('Yuki',0,36);ctx.restore();
  this.drawnScale=this.scale;if(changed){cancelAnimationFrame(this.frame);this.frame=requestAnimationFrame(()=>this.onLayout?.());}
 }
 snapshot(){return this.live?.snapshot()||{expression:this.expression,scale:this.scale,parameters:{},drawables:1,mode:'paper'};}
 dispose(){this.ready=false;this.observer.disconnect();cancelAnimationFrame(this.frame);this.live?.dispose();}
}
