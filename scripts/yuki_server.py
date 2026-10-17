"""Standalone loopback API and assets for Yuki's transparent text window."""
import asyncio
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .yuki_history import HistoryStore

ROOT=Path(__file__).resolve().parents[1]

def create_app(chat=None,history_directory=None):
    app=FastAPI(title='Yuki 小窗',docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    store=HistoryStore(history_directory)
    app.state.chat=chat
    app.state.store=store
    lock=asyncio.Lock()

    @app.middleware('http')
    async def local_only(request:Request,call_next):
        if request.method=='POST':
            origin=request.headers.get('origin')
            if origin and origin!=f'{request.url.scheme}://{request.headers.get("host")}':
                return Response(status_code=403)
            if request.headers.get('content-type','').split(';')[0]!='application/json':
                return Response(status_code=415)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-eval' qrc:; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'none'; object-src 'none'; frame-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        response.headers['Permissions-Policy']='microphone=(), camera=(), display-capture=()'
        if request.url.path=='/' or request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/status')
    async def status():
        return {'app':'yuki-window','chat_enabled':app.state.chat is not None,'voice_enabled':False,
                'model':'/model/yuki_cutout.model3.json','expression_count':7,'independent':True}

    @app.get('/favicon.ico')
    async def favicon():return Response(status_code=204)

    @app.post('/api/sessions')
    async def new_session():return {'session_id':store.create()}

    def get_messages(uid):
        try:return store.read(uid)
        except (ValueError,FileNotFoundError):raise HTTPException(404,'没有找到这篇对话。')

    @app.get('/api/sessions/{uid}')
    async def get_session(uid:str):return {'session_id':uid,'messages':get_messages(uid)}

    class ChatInput(BaseModel):
        text:str=Field(min_length=1,max_length=4000)
        session_id:str=Field(min_length=1,max_length=100)

    @app.post('/api/chat')
    async def complete(data:ChatInput):
        if not app.state.chat:raise HTTPException(503,'现在是模型预览，文字聊天尚未启用。')
        text=data.text.strip()
        if not text:raise HTTPException(422,'写一点想说的话吧。')
        if lock.locked():raise HTTPException(409,'Yuki 还在回答上一句。')
        async with lock:
            previous=get_messages(data.session_id)
            try:
                result=await app.state.chat.complete(text,previous)
                store.append_exchange(data.session_id,text,result['text'],result['emotion'])
                return result
            except Exception as exc:
                # Never return upstream response bodies, request URLs or credentials.
                code=getattr(exc,'status_code',None)
                message='这次没有收到完整回复，你写的话还在，可以再试一次。'
                if code==429:message='文字服务暂时繁忙，请稍后再试。'
                raise HTTPException(502,message) from None

    app.mount('/',StaticFiles(directory=ROOT/'app/dist',html=True))
    return app
