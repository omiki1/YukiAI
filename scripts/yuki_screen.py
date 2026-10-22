"""One selected window, local in-memory capture and Windows OCR; no cloud."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
import re
import subprocess
import threading


OCR_SCRIPT=r'''
$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.DataWriter,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
function Await($Operation,[Type]$ResultType){
 $task=$script:asTask.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
 $task.GetAwaiter().GetResult()
}
try {
 $request=[Console]::In.ReadToEnd() | ConvertFrom-Json
 $language=[Windows.Globalization.Language]::new('zh-Hans-CN')
 $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
 if ($null -eq $engine) { throw 'language_unavailable' }
 $stream=[Windows.Storage.Streams.InMemoryRandomAccessStream]::new()
 $writer=[Windows.Storage.Streams.DataWriter]::new($stream)
 $writer.WriteBytes([Convert]::FromBase64String($request.image))
 $null=Await ($writer.StoreAsync()) ([uint32])
 $null=$writer.DetachStream();$writer.Dispose();$stream.Seek(0)
 $decoder=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
 $bitmap=Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
 try {
  $result=Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  @{ok=$true;lines=@($result.Lines | ForEach-Object {$_.Text});language=$engine.RecognizerLanguage.LanguageTag} | ConvertTo-Json -Compress -Depth 4
 } finally { $bitmap.Dispose();$stream.Dispose() }
} catch {
 @{ok=$false;code=$(if ($_.Exception.Message -like '*language_unavailable*') {'language'} else {'ocr'})} | ConvertTo-Json -Compress
 exit 1
}
'''


class WindowsOcr:
    def __init__(self):self.process=None;self.lock=threading.Lock()

    def recognize(self,png):
        if os.name!='nt':raise ValueError('按键读屏需要 Windows 桌面版。')
        if not png or len(png)>20*1024*1024:raise ValueError('文字区域无效或过大，请重新框选。')
        script=base64.b64encode(OCR_SCRIPT.encode('utf-16-le')).decode('ascii')
        process=subprocess.Popen(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand',script],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        with self.lock:self.process=process
        try:
            try:output,_=process.communicate(json.dumps({'image':base64.b64encode(png).decode('ascii')}).encode(),timeout=20)
            except subprocess.TimeoutExpired:
                process.kill();process.communicate();raise ValueError('本机识别超时，请缩小文字区域再试。') from None
            try:result=json.loads(output.decode('utf-8-sig'))
            except (ValueError,UnicodeError):raise ValueError('Windows 文字识别未能启动，请检查系统中文识别语言包。') from None
            if not result.get('ok'):
                raise ValueError('请安装 Windows 中文文字识别语言包后重试。' if result.get('code')=='language' else '本机没有识别成功，请重新框选文字区域。')
            lines=[re.sub(r'(?<=[\u3400-\u9fff]) +(?=[\u3400-\u9fff])','',line).strip() for line in result['lines']]
            text='\n'.join(line for line in lines if line)
            if not text:raise ValueError('区域里没有识别出文字；游戏可能不支持这种截取，请用窗口或无边框模式再试。')
            if len(text)>6000:raise ValueError('识别内容太多，请只框住当前对话框。')
            return text
        finally:
            with self.lock:
                if self.process is process:self.process=None

    def cancel(self):
        with self.lock:
            if self.process and self.process.poll() is None:self.process.kill()


class WindowCapture:
    """PrintWindow targets the chosen client area; never capture the desktop."""
    def __init__(self):
        if os.name!='nt':raise ValueError('按键读屏需要 Windows 桌面版。')
        self.user=ctypes.WinDLL('user32',use_last_error=True);self.gdi=ctypes.WinDLL('gdi32',use_last_error=True)
        u,g=self.user,self.gdi
        u.IsWindow.argtypes=[wintypes.HWND];u.IsWindowVisible.argtypes=[wintypes.HWND];u.IsIconic.argtypes=[wintypes.HWND]
        u.GetForegroundWindow.restype=wintypes.HWND
        u.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        u.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]
        u.GetDC.argtypes=[wintypes.HWND];u.GetDC.restype=wintypes.HDC
        u.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HDC]
        u.PrintWindow.argtypes=[wintypes.HWND,wintypes.HDC,wintypes.UINT]
        g.CreateCompatibleDC.argtypes=[wintypes.HDC];g.CreateCompatibleDC.restype=wintypes.HDC
        g.CreateCompatibleBitmap.argtypes=[wintypes.HDC,ctypes.c_int,ctypes.c_int];g.CreateCompatibleBitmap.restype=wintypes.HBITMAP
        g.SelectObject.argtypes=[wintypes.HDC,wintypes.HGDIOBJ];g.SelectObject.restype=wintypes.HGDIOBJ
        g.DeleteObject.argtypes=[wintypes.HGDIOBJ];g.DeleteDC.argtypes=[wintypes.HDC]
        g.GetDIBits.argtypes=[wintypes.HDC,wintypes.HBITMAP,wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.c_void_p,wintypes.UINT]

    def windows(self):
        from .yuki_capture import game_processes
        allowed={row['pid']:row for row in game_processes()};rows=[]
        callback=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        @callback
        def visit(hwnd,_):
            pid=wintypes.DWORD();self.user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            if pid.value in allowed and self.user.IsWindowVisible(hwnd):
                title=ctypes.create_unicode_buffer(512);self.user.GetWindowTextW(hwnd,title,len(title))
                if title.value:rows.append({**allowed[pid.value],'title':title.value,'hwnd':int(hwnd)})
            return True
        self.user.EnumWindows.argtypes=[callback,wintypes.LPARAM];self.user.EnumWindows(visit,0)
        return rows

    def valid(self,target):
        pid=wintypes.DWORD();self.user.GetWindowThreadProcessId(target['hwnd'],ctypes.byref(pid))
        return bool(self.user.IsWindow(target['hwnd']) and pid.value==target['pid'] and self.user.IsWindowVisible(target['hwnd']) and not self.user.IsIconic(target['hwnd']))

    def foreground(self,target):return int(self.user.GetForegroundWindow() or 0)==target['hwnd']

    def grab(self,target):
        from PyQt5.QtGui import QImage
        if not self.valid(target):raise ValueError('所选游戏已关闭、最小化或窗口已改变，请重新选择。')
        rect=wintypes.RECT();self.user.GetClientRect(target['hwnd'],ctypes.byref(rect));width,height=rect.right,rect.bottom
        if not (100<=width<=6000 and 80<=height<=4000 and width*height<=16000000):raise ValueError('游戏窗口尺寸不支持，请切到普通窗口模式。')
        dc=self.user.GetDC(target['hwnd']);memory=self.gdi.CreateCompatibleDC(dc);bitmap=self.gdi.CreateCompatibleBitmap(dc,width,height)
        if not dc or not memory or not bitmap:
            if bitmap:self.gdi.DeleteObject(bitmap)
            if memory:self.gdi.DeleteDC(memory)
            if dc:self.user.ReleaseDC(target['hwnd'],dc)
            raise ValueError('无法准备游戏画面。')
        previous=self.gdi.SelectObject(memory,bitmap)
        try:
            if not self.user.PrintWindow(target['hwnd'],memory,3):raise ValueError('游戏未提供可读取画面，请尝试窗口或无边框模式。')
            self.gdi.SelectObject(memory,previous);previous=None
            class Header(ctypes.Structure):
                _fields_=[('size',wintypes.DWORD),('width',wintypes.LONG),('height',wintypes.LONG),('planes',wintypes.WORD),('bits',wintypes.WORD),('compression',wintypes.DWORD),('image_size',wintypes.DWORD),('x',wintypes.LONG),('y',wintypes.LONG),('used',wintypes.DWORD),('important',wintypes.DWORD)]
            header=Header(ctypes.sizeof(Header),width,-height,1,32,0,width*height*4,0,0,0,0);pixels=ctypes.create_string_buffer(width*height*4)
            if self.gdi.GetDIBits(memory,bitmap,0,height,pixels,ctypes.byref(header),0)!=height:raise ValueError('没有读到完整游戏画面。')
            return QImage(pixels.raw,width,height,width*4,QImage.Format_RGB32).copy()
        finally:
            if previous:self.gdi.SelectObject(memory,previous)
            self.gdi.DeleteObject(bitmap);self.gdi.DeleteDC(memory);self.user.ReleaseDC(target['hwnd'],dc)
