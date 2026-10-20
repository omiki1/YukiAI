"""A separate Textractor CLI process and a progress-bound text capture session."""
import asyncio
import ctypes
from ctypes import wintypes
import io
import os
from pathlib import Path
import queue
import re
import sqlite3
import subprocess
import threading
import time
from uuid import uuid4

from .yuki_story import StoryConflict

from .yuki_paths import data_root
ROOT = data_root()
HEADER = re.compile(r'\[([\dA-Fa-f]+):([\dA-Fa-f]+):([\dA-Fa-f]+):([\dA-Fa-f]+):([\dA-Fa-f]+):([^:\r\n]{0,120}):(.{0,480}?)\] ?')


def clean_text(text):
    # Preserve punctuation and spelling; retain the untouched sentence separately.
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff\u200b\u2060]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


# Whole line is an interface label, not a sentence that merely mentions one.
MENU_LINES = {
    '保存', '读取', '读档', '载入', '存档', '设定', '设置', '选项', '返回', '标题', '退出', '系统',
    '自动', '跳过', '快进', '回想', '快捷保存', '快捷读取', 'save', 'load', 'config', 'auto', 'skip',
    'log', 'q.save', 'q.load', 'quicksave', 'quickload', 'start', 'continue',
}


def is_interface_line(text):
    compact = re.sub(r'[\s　]+', '', text or '')
    bare = compact.strip('【】[]（）()「」').lower()
    if bare in MENU_LINES or compact.lower() in MENU_LINES:
        return True
    return bool(re.fullmatch(r'[0-9]{1,4}|[Ff]\d{1,2}|[>＞»]{1,4}', (text or '').strip()))


def game_processes():
    if os.name != 'nt':
        return []
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    found = {}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        size = user.GetWindowTextLengthW(hwnd)
        if not size:
            return True
        title = ctypes.create_unicode_buffer(min(size + 1, 512))
        user.GetWindowTextW(hwnd, title, len(title))
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == os.getpid() or pid.value in found:
            return True
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                name = Path(path.value).name
                if name.lower() not in ('textractorcli.exe', 'textractor.exe', 'explorer.exe', 'dwm.exe', 'codex.exe'):
                    found[pid.value] = {'pid': pid.value, 'title': title.value, 'name': name}
        finally:
            kernel.CloseHandle(handle)
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(visit, 0)
    return sorted(found.values(), key=lambda row: row['title'].casefold())


def process_arch(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ValueError('游戏已关闭或无法访问，请刷新游戏列表。')
    try:
        wow = wintypes.BOOL()
        kernel.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        if not kernel.IsWow64Process(handle, ctypes.byref(wow)):
            raise ValueError('无法识别游戏位数，请重新选择游戏。')
        return 'x86' if wow.value or ctypes.sizeof(ctypes.c_void_p) == 4 else 'x64'
    finally:
        kernel.CloseHandle(handle)


class TextractorRuntime:
    def __init__(self):
        self.process = None
        self.lines = queue.Queue(maxsize=1000)
        self.overflow = False
        self.pid = None
        self.reader = None

    def available(self):
        return os.name == 'nt' and all((ROOT / 'tools/textractor' / arch / name).is_file()
                                      for arch in ('x86', 'x64') for name in ('TextractorCLI.exe', 'texthook.dll'))

    def processes(self):
        return game_processes()

    def start(self, pid):
        if not self.available():
            raise ValueError('取词内核尚未安装，请先按 README 准备官方 Textractor。')
        executable = ROOT / 'tools/textractor' / process_arch(pid) / 'TextractorCLI.exe'
        self.lines = queue.Queue(maxsize=1000)
        self.overflow = False
        try:
            self.process = subprocess.Popen([str(executable)], cwd=executable.parent,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.pid = pid
            self.reader = threading.Thread(target=self._read, args=(self.process,self.lines), daemon=True)
            self.reader.start()
            self.command(f'attach -P{pid}')
        except OSError:
            self.stop()
            raise ValueError('取词内核未能启动，请检查工具文件与游戏访问权限。') from None

    def _read(self, process, lines):
        try:
            with io.TextIOWrapper(process.stdout, encoding='utf-16-le', errors='replace') as stream:
                while True:
                    line = stream.readline(8193)
                    if not line:
                        break
                    if len(line) > 8192:
                        self.overflow = True
                        continue
                    try:
                        lines.put_nowait((time.monotonic(), line.rstrip('\r\n')))
                    except queue.Full:
                        self.overflow = True
        except (OSError, ValueError):
            pass

    def drain(self):
        result = []
        for _ in range(1000):
            try:
                result.append(self.lines.get_nowait())
            except queue.Empty:
                break
        return result

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def target_alive(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000,False,self.pid)
        if not handle:return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value == 259)
        finally:kernel.CloseHandle(handle)

    def command(self, command):
        if not self.alive():
            raise ValueError('取词工具已退出，请重新连接游戏。')
        try:
            self.process.stdin.write((command + '\n').encode('utf-16-le'))
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise ValueError('取词工具连接已断开。') from None

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.stdin.write(f'detach -P{self.pid}\n'.encode('utf-16-le'))
                process.stdin.flush()
                # Give the host time to send the detach message before closing it.
                time.sleep(.15)
                process.stdin.close()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=2)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
        finally:
            if process.stdin and not process.stdin.closed:process.stdin.close()
            if self.reader:self.reader.join(timeout=1)
            self.reader = None


class CaptureService:
    """All state mutations run on the API event loop, never on the pipe reader."""
    def __init__(self, story, runtime=None, on_boundary=None):
        self.story = story
        self.runtime = runtime or TextractorRuntime()
        self.on_boundary = on_boundary or (lambda: None)
        self.task = None
        self.session = None
        self.process = None
        self.channels = {}
        self.selected = None
        self.recording = False
        self.binding = None
        self.kind, self.speaker = 'auto', ''
        self.message = '连接游戏后，先预览并选择正文来源。'
        self.saved = self.duplicates = 0
        self.block = self.pending = self.last = None
        self.replay = None
        self.started = 0

    def status(self):
        self.check_scope()
        return {'available': self.runtime.available(), 'connected': self.process is not None,
                'story_epoch': self.story.epoch(),
                'session_id': self.session, 'process': self.process, 'recording': self.recording,
                'selected_thread': self.selected, 'kind': self.kind, 'speaker': self.speaker,
                'threads': list(self.channels.values()), 'saved': self.saved, 'duplicates': self.duplicates,
                'message': self.message}

    def require_session(self, session):
        if not self.process or session != self.session:
            raise StoryConflict('游戏连接已改变，请刷新取词面板。')

    async def attach(self, pid):
        process = next((row for row in self.runtime.processes() if row['pid'] == pid), None)
        if process is None:
            raise ValueError('没有找到这个游戏窗口，请刷新列表后重新选择。')
        await self.detach()
        try:
            await asyncio.to_thread(self.runtime.start, pid)
        except ValueError:
            self.message = '连接未成功，请重新选择游戏。'
            raise
        self.process, self.session = process, uuid4().hex
        self.channels, self.selected = {}, None
        self.saved = self.duplicates = 0
        self.message = '已请求连接；请推进一句游戏台词，再选择正文来源。'
        self.task = asyncio.create_task(self._pump())
        return self.status()

    async def detach(self):
        self.pause('已断开游戏，已保存的原文仍然保留。')
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        await asyncio.to_thread(self.runtime.stop)
        self.process = self.session = self.selected = self.block = None
        self.channels = {}
        return self.status()

    def select(self, session, channel, kind='auto', speaker=''):
        self.require_session(session)
        if channel not in self.channels:
            raise ValueError('这个来源还没有可用文字，请推进一句游戏台词。')
        self.pause('已选正文来源。确认预览正确后，再开始记录。')
        self.selected, self.kind, self.speaker = channel, kind, speaker.strip()
        return self.status()

    def start_recording(self, session, epoch, run_id):
        self.require_session(session)
        view = self.story.view()
        if view['epoch'] != epoch or not view['active'] or view['active']['id'] != run_id:
            raise StoryConflict('请先选择当前游戏的共读，并刷新阅读位置。')
        if self.selected not in self.channels:
            raise ValueError('请先选择并确认正文来源。')
        if not self.runtime.alive():
            raise ValueError('取词工具已退出，请重新连接游戏。')
        self.block = self.pending = self.last = self.replay = None
        self.started = time.monotonic()
        self.binding = (epoch, run_id)
        self.recording = True
        self.message = '正在记录新出现的台词；读档或调整来源前请先暂停。'
        return self.status()

    def pause(self, message='已暂停记录；接下来只预览，不写入剧情。'):
        self.recording = False
        self.binding = self.pending = self.last = self.replay = None
        self.message = message

    def check_scope(self):
        if self.recording and self.story.epoch() != self.binding[0]:
            self.pause('阅读位置已改变，记录已暂停；确认路线后请重新开始。')

    def hook(self, session, code):
        self.require_session(session)
        if not re.fullmatch(r'[HR][!-~]{1,439}', code):
            raise ValueError('Hook 码需以 H 或 R 开头，不能包含空格或换行，最多440个字符。')
        self.pause('已发送 Hook 码；请推进台词并重新确认正文来源。')
        self.runtime.command(f'{code} -P{self.process["pid"]}')
        self.channels = {}
        self.selected = self.block = None
        return self.status()

    def line(self, text, received):
        matches = list(HEADER.finditer(text))
        if not matches:
            self._append_block(text,received)
            return
        if matches[0].start():
            self._append_block(text[:matches[0].start()],received)
        # Some legacy hooks emit invalid UTF-16; the CLI may then omit a newline.
        # Recover subsequent headers even when a noisy source shares the physical line.
        for index,match in enumerate(matches):
            self._flush_block()
            parts = match.groups()
            if int(parts[1], 16) != self.process['pid']:
                self.block = None
                continue
            channel = ':'.join(part.upper() for part in parts[:5])
            end = matches[index+1].start() if index+1<len(matches) else len(text)
            self.block = {'id': channel, 'name': parts[5], 'code': parts[6],
                          'raw': text[match.end():end], 'received': received}

    def _append_block(self,text,received):
        if self.block:
            self.block['raw'] += '\n' + text
            self.block['received'] = received
            if len(self.block['raw']) > 8000:
                self.block = None
                self.pause('单条取词内容过长，已暂停；请重新确认正文来源。')

    def _flush_block(self):
        block, self.block = self.block, None
        if block:
            self.sentence(block)

    def sentence(self, item):
        clean = clean_text(item['raw'])
        if not clean:
            return
        if item['id'] not in self.channels and len(self.channels) >= 64:
            self.pause('来源过多，记录已暂停；请断开后重新连接。')
            return
        self.channels[item['id']] = {'id': item['id'], 'name': item['name'], 'code': item['code'],
                                      'text': clean[:2000]}
        self.check_scope()
        if is_interface_line(clean):
            if self.recording and item['id'] == self.selected and item['received'] >= self.started:
                if self.pending:
                    self._save_pending()
                self.message = '跳过了一条菜单或界面文字，没有写入正文。'
            return
        if not self.recording or item['id'] != self.selected or item['received'] < self.started:
            return
        if len(clean) > 2000 or len(item['raw']) > 8000:
            self.pause('单条正文超过2000字，已暂停；请确认选中的是正文来源。')
            return
        item = {**item, 'clean': clean}
        if self.pending:
            previous = self.pending
            if clean == previous['clean']:
                return  # Repaints do not delay committing a stable sentence forever.
            if clean.startswith(previous['clean']) and item['received'] - previous['received'] < 1.5:
                self.pending = item  # Typewriter growth: commit the completed line only.
                return
            self._save_pending()
        if self.recording:
            self.pending = item

    def _save_pending(self):
        item, self.pending = self.pending, None
        self.check_scope()
        if not item or not self.recording:
            return
        if self.last and item['clean'] == self.last['clean'] and item['received'] - self.last['received'] < 1.5:
            self.duplicates += 1
            self.last = item
            return
        decision = self.story.screen_line(item['clean'], self.replay)
        if decision['action'] == 'hold':
            self.replay = decision
            self.message = decision['message']
            return
        if decision['action'] == 'rewind':
            self.pause(decision['message'])
            return
        self.replay = None
        epoch, run_id = self.binding
        try:
            self.story.ingest_capture(item['clean'], item['raw'], self.kind, self.speaker,
                'hook-' + uuid4().hex, epoch, run_id, 'textractor:' + item['id'])
        except (ValueError, StoryConflict):
            self.pause('阅读位置已改变或正文无法收录，记录已暂停；请检查当前共读。')
            return
        except sqlite3.Error:
            self.pause('原文未能保存，记录已暂停；请检查本机存储后重新开始。')
            return
        current = self.story.epoch()
        self.binding = (current, run_id)
        if current != epoch:
            self.on_boundary()
        self.saved += 1
        self.last = item

    def tick(self, now=None):
        self.check_scope()
        now = time.monotonic() if now is None else now
        if self.block and now - self.block['received'] >= .15:
            self._flush_block()
        if self.pending and now - self.pending['received'] >= .7:
            self._save_pending()

    async def _pump(self):
        checked = 0
        while True:
            if self.runtime.overflow:
                self.block = None
                self.pause('取词内容过快，记录已暂停；请重新选择正文来源。')
                self.runtime.drain()
                self.runtime.overflow = False
            else:
                for received, line in self.runtime.drain():
                    self.line(line, received)
                self.tick()
            now = time.monotonic()
            target_alive = True
            if now-checked > 1 and hasattr(self.runtime,'target_alive'):
                checked = now
                target_alive = self.runtime.target_alive()
            if not self.runtime.alive() or not target_alive:
                self.pause('游戏或取词工具已退出，请重新连接游戏。')
                await asyncio.to_thread(self.runtime.stop)
                self.process = self.session = self.selected = self.block = None
                self.channels = {}
                return
            await asyncio.sleep(.05)
