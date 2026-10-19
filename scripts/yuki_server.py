"""Standalone loopback API, local settings and optional speech."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI,HTTPException,Request,Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .yuki_history import HistoryStore
from .yuki_config import ConfigStore,Configuration,ready
from .yuki_chat import TextChat,ground_story_answer
from .yuki_speech import SpeechClient
from .yuki_story import StoryStore,StoryConflict
from .yuki_capture import CaptureService

ROOT=Path(__file__).resolve().parents[1]

def create_app(chat=None,history_directory=None,*,config=None,allow_cloud=True,speech=None,story=None,capture_runtime=None):
    config=config or ConfigStore()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await app.state.capture.detach()
        if isinstance(app.state.chat,TextChat):await app.state.chat.client.close()

    app=FastAPI(title='Yuki 小窗',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    store=HistoryStore(history_directory)
    app.state.chat=chat
    app.state.store=store
    app.state.config=config
    app.state.speech=speech
    story=story or StoryStore(Path(history_directory)/'story.sqlite3' if history_directory else None)
    app.state.story=story
    app.state.active_chat_task=None
    app.state.active_speech_task=None
    lock=asyncio.Lock()
    speech_lock=asyncio.Lock()

    def cancel_reading_tasks():
        for task in (app.state.active_chat_task,app.state.active_speech_task):
            if task and not task.done():task.cancel()

    capture=CaptureService(story,capture_runtime,cancel_reading_tasks)
    app.state.capture=capture
    capture_lock=asyncio.Lock()

    def configure_clients():
        data=config.data
        app.state.chat=TextChat(data['llm']) if allow_cloud and ready(data['llm']) else None
        voice=data['voice'];endpoint=voice[voice['provider']]
        app.state.speech=SpeechClient(voice['provider'],endpoint) if allow_cloud and ready(endpoint) else None

    configure_clients()
    if chat is not None:app.state.chat=chat
    if speech is not None:app.state.speech=speech

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request,exc):
        # Validation errors must never echo submitted API keys or provider bodies.
        message='请检查填写内容：地址需使用 HTTPS（本机可用 HTTP），大小为 60%～110%，语速为 0.5～2。'
        if request.url.path.startswith('/api/story'):
            message='请检查填写内容，并确认收录的是已读文本；单次最多60000字、200行。'
        elif request.url.path=='/api/chat':message='请输入1～4000字，并刷新当前阅读进度后再试。'
        elif request.url.path.startswith('/api/capture'):message='请刷新取词面板，选择游戏与正文来源，并确认只记录已读台词。'
        return JSONResponse(status_code=422,content={'detail':message})

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
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-eval' qrc:; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        response.headers['Permissions-Policy']='microphone=(), camera=(), display-capture=()'
        if request.url.path=='/' or request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/status')
    async def status():
        return {'app':'yuki-window','chat_enabled':app.state.chat is not None,
                'voice_enabled':bool(config.data['voice']['enabled'] and app.state.speech),
                'preview':not allow_cloud,
                'model':'/model/yuki_cutout.model3.json','expression_count':7,'independent':True}

    @app.get('/api/settings')
    async def get_settings():return config.public()

    @app.post('/api/settings')
    async def save_settings(data:Configuration):
        if lock.locked() or speech_lock.locked():raise HTTPException(409,'请等当前回复或语音完成后再保存。')
        async with lock:
            previous=app.state.chat
            try:result=config.save(data)
            except ValueError as exc:raise HTTPException(400,str(exc)) from None
            configure_clients()
            if isinstance(previous,TextChat):await previous.client.close()
            return result

    def provider_error(exc,kind):
        code=getattr(exc,'status_code',None)
        if code is None and getattr(exc,'response',None) is not None:code=exc.response.status_code
        if code in (401,403):message=f'{kind}服务未通过验证，请检查密钥和访问权限。'
        elif code==429:message=f'{kind}服务暂时繁忙或额度不足，请稍后再试。'
        else:message=f'{kind}服务没有返回可用结果，请检查接口地址、模型和参数。'
        return HTTPException(502,message)

    @app.post('/api/settings/test-llm')
    async def test_llm():
        if not allow_cloud:raise HTTPException(403,'预览模式不调用外部接口，请正常启动后测试。')
        if not app.state.chat:raise HTTPException(400,'请先保存完整的聊天接口配置。')
        if lock.locked():raise HTTPException(409,'请等当前回复完成。')
        async with lock:
            try:await app.state.chat.complete('请用一句简短的中文打招呼。',[])
            except Exception as exc:raise provider_error(exc,'聊天') from None
        return {'ok':True}

    async def synthesize(text,epoch=None):
        if not allow_cloud:raise HTTPException(403,'预览模式不调用外部接口，请正常启动后试听。')
        if not app.state.speech:raise HTTPException(400,'请先保存完整的语音接口配置。')
        if speech_lock.locked():raise HTTPException(409,'上一段语音还在准备中。')
        async with speech_lock:
            task=asyncio.create_task(app.state.speech.synthesize(text))
            if epoch is not None:app.state.active_speech_task=task
            try:
                audio=await task
                if epoch is not None and story.epoch()!=epoch:raise StoryConflict('阅读进度已改变，这段语音已停止。')
            except asyncio.CancelledError:raise HTTPException(409,'阅读进度已改变，这段语音已停止。') from None
            except StoryConflict as exc:raise HTTPException(409,str(exc)) from None
            except Exception as exc:raise provider_error(exc,'语音') from None
            finally:
                if app.state.active_speech_task is task:app.state.active_speech_task=None
        return Response(audio,media_type='audio/wav')

    @app.post('/api/settings/test-voice')
    async def test_voice():return await synthesize('你好，我是 Yuki。接下来的剧情，我们一起看吧。')

    class SpeechInput(BaseModel):
        text:str=Field(min_length=1,max_length=1200)
        story_epoch:int|None=Field(default=None,ge=0)

    @app.post('/api/speech')
    async def speak(data:SpeechInput):
        if not config.data['voice']['enabled']:raise HTTPException(403,'自动朗读尚未开启。')
        if not data.text.strip():raise HTTPException(422,'没有可以朗读的文字。')
        try:snapshot=story.snapshot('',data.story_epoch)
        except StoryConflict as exc:raise HTTPException(409,str(exc)) from None
        return await synthesize(data.text.strip(),snapshot.epoch)

    class ReadingEdit(BaseModel):
        epoch:int=Field(ge=0)

    class NewReading(ReadingEdit):
        title:str=Field(min_length=1,max_length=100)
        name:str=Field(default='',max_length=60)
        mode:Literal['galgame','novel']='galgame'

    class ActivateReading(ReadingEdit):
        run_id:str|None=Field(default=None,max_length=100)

    class ImportReading(ReadingEdit):
        run_id:str=Field(min_length=1,max_length=100)
        text:str=Field(min_length=1,max_length=60000)
        kind:Literal['auto','dialogue','narration','choice']='auto'
        speaker:str=Field(default='',max_length=30)
        request_id:str=Field(min_length=8,max_length=100)
        confirmed_read:Literal[True]

    class NamedReading(ReadingEdit):
        name:str=Field(default='',max_length=60)

    class RestoreReading(ReadingEdit):
        bookmark_id:str=Field(min_length=1,max_length=100)

    class ProgressReading(ReadingEdit):
        position:int=Field(ge=0)

    class BranchReading(ReadingEdit):
        branch_id:str=Field(min_length=1,max_length=100)

    def edit_story(function,*args):
        old_epoch=story.epoch()
        try:function(*args)
        except StoryConflict as exc:raise HTTPException(409,str(exc)) from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None
        if story.epoch()!=old_epoch:
            cancel_reading_tasks()
            capture.check_scope()
        return story.view()

    class CaptureSession(BaseModel):
        session_id:str=Field(min_length=1,max_length=64)

    class CaptureAttach(BaseModel):
        pid:int=Field(gt=0,le=4294967295)

    class CaptureSelect(CaptureSession):
        thread_id:str=Field(min_length=1,max_length=160)
        kind:Literal['auto','dialogue','narration','choice']='auto'
        speaker:str=Field(default='',max_length=30)

    class CaptureRecord(CaptureSession):
        epoch:int=Field(ge=0)
        run_id:str=Field(min_length=1,max_length=100)
        confirmed_read:Literal[True]

    class CaptureHook(CaptureSession):
        code:str=Field(min_length=2,max_length=440)

    def capture_call(function,*args):
        try:return function(*args)
        except StoryConflict as exc:raise HTTPException(409,str(exc)) from None
        except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @app.get('/api/capture')
    async def capture_status():return capture.status()

    @app.get('/api/capture/processes')
    async def capture_processes():return {'processes':await asyncio.to_thread(capture.runtime.processes)}

    @app.post('/api/capture/attach')
    async def capture_attach(data:CaptureAttach):
        async with capture_lock:
            try:return await capture.attach(data.pid)
            except ValueError as exc:raise HTTPException(400,str(exc)) from None

    @app.post('/api/capture/select')
    async def capture_select(data:CaptureSelect):
        return capture_call(capture.select,data.session_id,data.thread_id,data.kind,data.speaker)

    @app.post('/api/capture/record')
    async def capture_record(data:CaptureRecord):
        return capture_call(capture.start_recording,data.session_id,data.epoch,data.run_id)

    @app.post('/api/capture/pause')
    async def capture_pause(data:CaptureSession):
        capture_call(capture.require_session,data.session_id)
        capture.pause()
        return capture.status()

    @app.post('/api/capture/detach')
    async def capture_detach(data:CaptureSession):
        async with capture_lock:
            capture_call(capture.require_session,data.session_id)
            return await capture.detach()

    @app.post('/api/capture/hook')
    async def capture_hook(data:CaptureHook):return capture_call(capture.hook,data.session_id,data.code)

    @app.get('/api/story')
    async def reading_state(before:int|None=Query(default=None,ge=1)):return story.view(before)

    @app.post('/api/story/runs')
    async def new_reading(data:NewReading):return edit_story(story.create_run,data.title,data.name,data.epoch,data.mode)

    @app.post('/api/story/activate')
    async def activate_reading(data:ActivateReading):return edit_story(story.activate,data.run_id,data.epoch)

    @app.post('/api/story/import')
    async def import_reading(data:ImportReading):
        return edit_story(story.ingest,data.text,data.kind,data.speaker,data.request_id,data.epoch,data.run_id)

    @app.post('/api/story/bookmarks')
    async def bookmark_reading(data:NamedReading):return edit_story(story.bookmark,data.name,data.epoch)

    @app.post('/api/story/restore')
    async def restore_reading(data:RestoreReading):return edit_story(story.restore,data.bookmark_id,data.epoch)

    @app.post('/api/story/progress')
    async def progress_reading(data:ProgressReading):return edit_story(story.progress,data.position,data.epoch)

    @app.post('/api/story/fork')
    async def fork_reading(data:NamedReading):return edit_story(story.fork,data.name,data.epoch)

    @app.post('/api/story/branch')
    async def select_reading_branch(data:BranchReading):return edit_story(story.select_branch,data.branch_id,data.epoch)

    @app.post('/api/story/chat/new')
    async def new_reading_chat(data:ReadingEdit):return edit_story(story.new_chat,data.epoch)

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
        story_epoch:int|None=Field(default=None,ge=0)

    @app.post('/api/chat')
    async def complete(data:ChatInput):
        if not app.state.chat:raise HTTPException(503,'聊天尚未启用，请在模型上方的设置中填写接口；预览模式需正常启动后聊天。')
        text=data.text.strip()
        if not text:raise HTTPException(422,'写一点想说的话吧。')
        if lock.locked():raise HTTPException(409,'Yuki 还在回答上一句。')
        async with lock:
            try:snapshot=story.snapshot(text,data.story_epoch)
            except StoryConflict as exc:raise HTTPException(409,str(exc)) from None
            previous=get_messages(data.session_id) if snapshot.run_id is None else None
            task=asyncio.create_task(app.state.chat.complete_story(text,snapshot) if snapshot.run_id else app.state.chat.complete(text,previous))
            app.state.active_chat_task=task
            try:
                result=await task
                if not story.matches(snapshot):raise StoryConflict('阅读进度已改变，上一条回复已收起，请重新提问。')
                if snapshot.run_id:
                    result=ground_story_answer(result,snapshot,text)
                    result['evidence']=story.append_exchange(snapshot,text,result)
                    result['story_position']=snapshot.position
                else:store.append_exchange(data.session_id,text,result['text'],result['emotion'])
                result['story_epoch']=snapshot.epoch
                return result
            except asyncio.CancelledError:raise HTTPException(409,'阅读进度已改变，上一条回复已停止。') from None
            except StoryConflict as exc:raise HTTPException(409,str(exc)) from None
            except Exception as exc:
                # Never return upstream response bodies, request URLs or credentials.
                raise provider_error(exc,'聊天') from None
            finally:
                if app.state.active_chat_task is task:app.state.active_chat_task=None

    app.mount('/',StaticFiles(directory=ROOT/'app/dist',html=True))
    return app
