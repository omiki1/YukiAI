"""Launch Yuki's own transparent text window, or its loopback preview server."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
EXPECTED = Path(r'C:\Users\freeing1\anaconda3\envs\myenv\python.exe')
if Path(sys.executable).resolve() not in (EXPECTED.resolve(),EXPECTED.with_name('pythonw.exe').resolve()):
    raise SystemExit('请使用 myenv 的 Python：' + str(EXPECTED))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

def main():
    # pythonw has no console streams; uvicorn's logger still needs a file-like object.
    for stream_name in ('stdout', 'stderr'):
        if getattr(sys, stream_name) is None:
            setattr(sys, stream_name, open(os.devnull, 'w', encoding='utf-8'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['auto', 'preview', 'chat'], default='auto')
    parser.add_argument('--port', type=int, default=12393)
    parser.add_argument('--headless', action='store_true', help='只启动本机预览服务，不打开桌面窗口')
    args = parser.parse_args()
    os.chdir(ROOT)
    from scripts.settings import load_local_settings
    load_local_settings()
    if not (ROOT / 'app/dist/index.html').is_file():
        raise SystemExit('前端尚未构建。请参阅 README.md。')
    import uvicorn
    from scripts.yuki_server import create_app
    app=create_app(allow_cloud=args.mode!='preview')
    print(f'Yuki 小窗：http://127.0.0.1:{args.port}（模式：{args.mode}，接口可在设置中配置）',flush=True)
    if args.headless:
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
        return
    import threading
    import time
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,log_level='warning'))
    worker=threading.Thread(target=server.run,daemon=True)
    worker.start()
    for _ in range(80):
        if server.started:break
        if not worker.is_alive():raise SystemExit('本机服务未能启动；请检查是否已有 Yuki 窗口。')
        time.sleep(.1)
    if not server.started:raise SystemExit('本机服务启动超时。')
    try:
        from scripts.yuki_window import open_window
        open_window(f'http://127.0.0.1:{args.port}/')
    finally:
        server.should_exit=True
        worker.join(timeout=3)
if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        (ROOT/'logs').mkdir(exist_ok=True)
        (ROOT/'logs/window-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        raise
