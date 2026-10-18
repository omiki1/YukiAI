"""Inject only an owned, offscreen GDI fixture; verify the real CLI-to-SQLite path."""
import asyncio
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    user = ctypes.WinDLL('user32', use_last_error=True)
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    user.CreateWindowExW.argtypes = [wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
        ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
    user.CreateWindowExW.restype = wintypes.HWND
    user.GetDC.argtypes=[wintypes.HWND];user.GetDC.restype=wintypes.HDC
    user.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HDC]
    user.DestroyWindow.argtypes=[wintypes.HWND]
    user.DrawTextW.argtypes=[wintypes.HDC,wintypes.LPCWSTR,ctypes.c_int,ctypes.POINTER(wintypes.RECT),wintypes.UINT]
    window=user.CreateWindowExW(0,'STATIC','Yuki Hook fixture · 自编台词',0x10000000|0x00CF0000,
        -2500,-2500,320,160,None,None,None,None)
    if not window:raise ctypes.WinError(ctypes.get_last_error())
    current=['旁白：这是取词验证用的自编故事。',False]
    def read():
        for line in sys.stdin:
            current[0]=json.loads(line)['text']
            current[1]=False
        current[0]=None
    threading.Thread(target=read,daemon=True).start()
    print('READY',flush=True)
    try:
        while current[0] is not None:
            text=current[0]
            if not current[1]:
                dc=user.GetDC(window)
                try:
                    rect=wintypes.RECT(8,8,310,140)
                    user.DrawTextW(dc,text,len(text),ctypes.byref(rect),0)
                finally:user.ReleaseDC(window,dc)
                current[1]=True
            message=wintypes.MSG()
            while user.PeekMessageW(ctypes.byref(message),None,0,0,1):
                user.TranslateMessage(ctypes.byref(message));user.DispatchMessageW(ctypes.byref(message))
            time.sleep(.05)
    finally:user.DestroyWindow(window)


async def verify():
    from scripts.yuki_capture import CaptureService,TextractorRuntime
    from scripts.yuki_story import StoryStore
    output=ROOT/'verification/capture';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as temporary:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--fixture'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,encoding='utf-8',env={**os.environ,'PYTHONIOENCODING':'utf-8'},
            creationflags=subprocess.CREATE_NO_WINDOW)
        capture=None
        try:
            ready=await asyncio.wait_for(asyncio.to_thread(process.stdout.readline),10)
            assert ready.strip()=='READY','Owned GDI fixture did not start'
            store=StoryStore(Path(temporary)/'story.sqlite3')
            store.create_run('自编取词样例','真实内核验证',0)
            capture=CaptureService(store,TextractorRuntime())
            native_lines=[]
            drain=capture.runtime.drain
            def traced_drain():
                lines=drain();native_lines.extend(lines)
                return lines
            capture.runtime.drain=traced_drain
            await capture.attach(process.pid)
            async def show(text):
                process.stdin.write(json.dumps({'text':text},ensure_ascii=False)+'\n');process.stdin.flush()
            async def until(predicate,timeout=12):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    if predicate():return
                    await asyncio.sleep(.1)
                print(json.dumps({'channels':list(capture.channels.values()),'lines':native_lines[-15:]},ensure_ascii=True),flush=True)
                raise AssertionError('Timed out waiting for native capture')
            # Generic GDI hooks are installed asynchronously after injection.
            for index in range(10):
                await show('旁白：确认正文来源。'+str(index))
                await asyncio.sleep(.7)
                if any(row['name']=='DrawTextW' for row in capture.channels.values()):break
            await until(lambda:any(row['name']=='DrawTextW' for row in capture.channels.values()))
            channel=next(row['id'] for row in capture.channels.values() if row['name']=='DrawTextW')
            assert store.view()['active']['position']==0,'Preview wrote to story memory'
            capture.select(capture.session,channel)
            state=store.view()
            capture.start_recording(capture.session,state['epoch'],state['active']['id'])
            await show('美咲：雨停了，我们一起去图书室吧。')
            await until(lambda:store.view()['active']['position']>=1)
            view=store.view();event=view['active']['events'][-1]
            assert event['speaker']=='美咲',event
            assert event['text']=='雨停了，我们一起去图书室吧。',event
            assert event['source'].startswith('textractor:'),event
            capture.pause()
            await show('旁白：暂停后的文字不应保存。')
            await asyncio.sleep(1.6)
            assert store.view()['active']['position']==1
            # Exercise the real manual Hook command as well as automatic engine hooks.
            capture.hook(capture.session,'HQ-24@0:user32.dll:DrawTextW')
            await asyncio.sleep(.5)
            await show('旁白：手动Hook预览来源。')
            await until(lambda:any(row['text']=='旁白：手动Hook预览来源。' for row in capture.channels.values()))
            manual=next(row['id'] for row in capture.channels.values() if row['text']=='旁白：手动Hook预览来源。')
            capture.select(capture.session,manual)
            state=store.view()
            capture.start_recording(capture.session,state['epoch'],state['active']['id'])
            await show('小春：手动Hook也能完整读取中文。')
            await until(lambda:store.view()['active']['position']>=2)
            assert store.view()['active']['events'][-1]['text']=='手动Hook也能完整读取中文。'
            assert store.view()['active']['events'][-1]['speaker']=='小春'
            await capture.detach()
            assert process.poll() is None,'Detaching must not close the game'
            report={'passed':True,'engine':'Official Textractor 5.2.0 x64 CLI',
                    'owned_fixture_only':True,'preview_does_not_save':True,'real_injection':True,
                    'speaker':'美咲','saved_text':event['text'],'source':event['source'],
                    'pause_does_not_save':True,'manual_hook':True,'detach_keeps_game_running':True,'cloud_calls':0}
            (output/'native-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False,indent=2))
        finally:
            if capture:await capture.detach()
            if process.poll() is None:
                process.stdin.close()
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=3)


if __name__=='__main__':
    if '--fixture' in sys.argv:fixture()
    else:
        sys.path.insert(0,str(ROOT))
        asyncio.run(verify())
