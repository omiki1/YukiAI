"""Launch Yuki's own transparent text window, or its loopback preview server."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys

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
    parser.add_argument('--data-dir', type=Path, help='独立保存配置与阅读记录的目录')
    parser.add_argument('--check', action='store_true', help='检查运行条件，不创建记录或调用接口')
    args = parser.parse_args()
    if args.data_dir:
        os.environ['YUKI_DATA_DIR'] = str(args.data_dir.expanduser().resolve())
    if (getattr(sys,'frozen',False) and os.name=='nt' and not args.headless and not args.check
            and not sys.executable.isascii()):
        # Qt5 also reads its module location while loading ICU. Launch the same file
        # through its Windows ASCII alias before initializing the desktop runtime.
        if os.environ.get('YUKI_QT_ASCII_CHILD')=='1':
            raise RuntimeError('当前目录无法加载桌面组件。请把完整程序解压到英文目录后启动。')
        from scripts.yuki_window import qt_runtime_path
        import subprocess
        executable=qt_runtime_path(sys.executable)
        return subprocess.run([str(executable),*sys.argv[1:]],env={**os.environ,
                              'PYINSTALLER_RESET_ENVIRONMENT':'1','YUKI_QT_ASCII_CHILD':'1'}).returncode
    from scripts.yuki_paths import resource_root, data_root
    root = resource_root()
    modules = ['fastapi', 'uvicorn', 'openai', 'httpx']
    if not args.headless:
        modules.extend(['PyQt5', 'PyQt5.QtWebEngineWidgets'])
    missing = []
    for name in modules:
        try:
            if importlib.util.find_spec(name) is None:missing.append(name)
        except ModuleNotFoundError:missing.append(name)
    web_ready = (root/'app/dist/index.html').is_file()
    if args.check:
        supported = sys.version_info >= (3, 12)
        print(json.dumps({'ready':not missing and web_ready and supported, 'missing':missing,
                          'web_ready':web_ready, 'python_supported':supported}, ensure_ascii=False))
        return 0 if not missing and web_ready and supported else 1
    if sys.version_info < (3, 12):
        raise RuntimeError('源码版需要 Python 3.12 或更新版本；免安装测试包已包含运行环境。')
    if missing:
        raise RuntimeError('运行依赖不完整。源码版请运行 setup_yuki.cmd，或使用免安装测试包。')
    if not web_ready:
        raise RuntimeError('界面尚未构建。源码版请运行 rebuild_frontend.cmd，或使用免安装测试包。')
    data_root().mkdir(parents=True, exist_ok=True)
    from scripts.settings import load_local_settings
    load_local_settings()
    import uvicorn
    from scripts.yuki_server import create_app
    app=create_app(allow_cloud=args.mode!='preview')
    print(f'Yuki 小窗：http://127.0.0.1:{args.port}（模式：{args.mode}，接口可在设置中配置）',flush=True)
    if args.headless:
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')
        return 0
    import socket
    import threading
    import time
    listener=socket.socket()
    try:
        listener.bind(('127.0.0.1',args.port));listener.listen(128)
    except OSError:
        listener.close()
        raise RuntimeError('Yuki 的本机端口已被占用。请先退出另一个 Yuki，再启动。') from None
    server=uvicorn.Server(uvicorn.Config(app,log_level='warning'))
    worker=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
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
        listener.close()
    return 0
if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        import traceback
        from scripts.yuki_paths import data_root
        logs=data_root()/'logs';logs.mkdir(parents=True,exist_ok=True)
        (logs/'window-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        print(str(error),file=sys.stderr)
        if os.name=='nt' and '--headless' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(error),'Yuki 启动提示',0x10)
        raise SystemExit(1)
