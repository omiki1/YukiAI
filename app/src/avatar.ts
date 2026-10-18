import {CubismFramework,LogLevel} from '../vendor/live2d/Framework/src/live2dcubismframework';
import {CubismUserModel} from '../vendor/live2d/Framework/src/model/cubismusermodel';
import {CubismModelSettingJson} from '../vendor/live2d/Framework/src/cubismmodelsettingjson';
import {CubismEyeBlink} from '../vendor/live2d/Framework/src/effect/cubismeyeblink';
import {CubismMatrix44} from '../vendor/live2d/Framework/src/math/cubismmatrix44';
import {ACubismMotion} from '../vendor/live2d/Framework/src/motion/acubismmotion';

const names=['neutral','happy','smug','serious','sad','shy','surprise'];
const locked=['ParamAngleX','ParamAngleY','ParamAngleZ','ParamBodyAngleX','ParamBodyAngleY','ParamBodyAngleZ','ParamEyeBallX','ParamEyeBallY'];
async function bytes(url:string){const r=await fetch(url);if(!r.ok)throw new Error('模型文件读取失败');return r.arrayBuffer();}

export class Avatar extends CubismUserModel{
  canvas:HTMLCanvasElement;gl:WebGLRenderingContext;expressions=new Map<string,ACubismMotion>();
  expression='neutral';time=0;ready=false;scale=.85;onLayout?:()=>void;private drawnScale=0;private previous=0;private frame=0;private textures:WebGLTexture[]=[];
  constructor(canvas:HTMLCanvasElement){super();this.canvas=canvas;}
  async init(){
    this.gl=(this.canvas.getContext('webgl2',{alpha:true,premultipliedAlpha:true,antialias:true,preserveDrawingBuffer:true})||this.canvas.getContext('webgl',{alpha:true,premultipliedAlpha:true,antialias:true,preserveDrawingBuffer:true})) as WebGLRenderingContext;
    if(!this.gl)throw new Error('暂时无法显示模型，请重新打开小窗。');
    CubismFramework.startUp({logFunction:()=>{},loggingLevel:LogLevel.LogLevel_Off});CubismFramework.initialize();
    const root='/model/',configBytes=await bytes(root+'yuki_cutout.model3.json');
    const setting=new CubismModelSettingJson(configBytes,configBytes.byteLength);
    this.loadModel(await bytes(root+setting.getModelFileName()),true);
    if(!this._model)throw new Error('模型初始化失败');
    this._eyeBlink=CubismEyeBlink.create(setting);
    await Promise.all(names.map(async(name,i)=>{const b=await bytes(root+setting.getExpressionFileName(i));this.expressions.set(name,this.loadExpression(b,b.byteLength,name));}));
    this.createRenderer();this.getRenderer().startUp(this.gl);this.getRenderer().setIsPremultipliedAlpha(true);
    for(let i=0;i<setting.getTextureCount();i++){
      const img=await new Promise<HTMLImageElement>((resolve,reject)=>{const v=new Image();v.onload=()=>resolve(v);v.onerror=()=>reject(new Error('模型纹理读取失败'));v.src=root+setting.getTextureFileName(i);});
      const gl=this.gl,t=gl.createTexture();this.textures.push(t);gl.bindTexture(gl.TEXTURE_2D,t);gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,1);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,img);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR_MIPMAP_LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);gl.generateMipmap(gl.TEXTURE_2D);this.getRenderer().bindTexture(i,t);
    }
    this._model.saveParameters();this.ready=true;this.setExpression('neutral');this.frame=requestAnimationFrame(t=>this.drawFrame(t));
  }
  setExpression(name:string){const exp=this.expressions.get(name);if(!exp)return;this.expression=name;this._expressionManager.startMotionPriority(exp,false,3);}
  setScale(value:number){if(Number.isFinite(value))this.scale=Math.min(1.1,Math.max(.6,value));}
  private set(id:string,value:number){this._model.setParameterValueById(CubismFramework.getIdManager().getId(id),value);}
  private drawFrame(now:number){
    if(!this.ready)return;const dt=this.previous?Math.min((now-this.previous)/1000,.05):0;this.previous=now;this.time+=dt;
    this._model.loadParameters();this._eyeBlink.updateParameters(this._model,dt);this._expressionManager.updateMotion(this._model,dt);
    this.set('ParamHairSide',Math.sin(this.time*.8)*.32);this.set('ParamHairBack',Math.sin(this.time*.67+.4)*.28);
    // Final pose guard. No mouse tracking, breathing sway or speaking/audio driver.
    for(const id of locked)this.set(id,0);
    this._model.update();const gl=this.gl,dpr=Math.min(devicePixelRatio||1,2),w=Math.round(this.canvas.clientWidth*dpr),h=Math.round(this.canvas.clientHeight*dpr);
    const layoutChanged=w!==this.canvas.width||h!==this.canvas.height||this.drawnScale!==this.scale;
    if(w!==this.canvas.width||h!==this.canvas.height){this.canvas.width=w;this.canvas.height=h;}
    gl.viewport(0,0,w,h);gl.bindFramebuffer(gl.FRAMEBUFFER,null);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    const projection=new CubismMatrix44();projection.scale(h/w*.95*this.scale,.95*this.scale);projection.translateY(-.95*(1-this.scale));
    this.getRenderer().setMvpMatrix(projection);this.getRenderer().setRenderState(null,[0,0,w,h]);this.getRenderer().drawModel();
    this.frame=requestAnimationFrame(t=>this.drawFrame(t));
    if(layoutChanged){this.drawnScale=this.scale;this.onLayout?.();}
  }
  snapshot(){const c=this._model?.getModel();return c?{expression:this.expression,scale:this.scale,parameters:Object.fromEntries(c.parameters.ids.map((id,i)=>[id,c.parameters.values[i]])),drawables:c.drawables.ids.length}:null;}
  dispose(){cancelAnimationFrame(this.frame);this.ready=false;for(const t of this.textures)this.gl.deleteTexture(t);this.release();}
}
