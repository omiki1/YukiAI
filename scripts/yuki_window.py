"""Transparent native Qt window around our local interface."""
from pathlib import Path
import json
import os
import sys

ROOT=Path(__file__).resolve().parents[1]

def open_window(url):
    # The host can inherit unrelated Qt settings from the terminal/Conda session.
    for name in ('QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','PYTHONPATH'):
        os.environ.pop(name,None)
    os.environ['QT_QPA_PLATFORM']=os.environ.get('YUKI_QT_PLATFORM','windows')
    from PyQt5.QtCore import Qt,QUrl,QObject,QPoint,QAbstractNativeEventFilter,pyqtSlot
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QColor,QSurfaceFormat,QPainter,QFont,QLinearGradient,QPen,QPainterPath
    from PyQt5.QtWidgets import QApplication,QWidget,QVBoxLayout,QSizeGrip,QMenu
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    fmt=QSurfaceFormat();fmt.setAlphaBufferSize(8);QSurfaceFormat.setDefaultFormat(fmt)
    from PyQt5.QtWebEngineWidgets import QWebEngineView,QWebEnginePage,QWebEngineProfile,QWebEngineSettings
    from PyQt5.QtWebEngineCore import QWebEngineUrlRequestInterceptor
    from PyQt5.QtWebChannel import QWebChannel

    class LocalRequests(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            target=info.requestUrl()
            if target.scheme() in ('http','https') and target.host() not in ('127.0.0.1','localhost'):
                info.block(True)

    class LocalPage(QWebEnginePage):
        def acceptNavigationRequest(self,target,kind,is_main):
            return target.scheme() in ('qrc','about','data','blob') or target.toString().startswith(url.rstrip('/')+'/')
        def javaScriptConsoleMessage(self,level,message,line,source):
            if level==QWebEnginePage.ErrorMessageLevel:
                print(f'UI error at line {line}: {message[:300]}',flush=True)

    class Host(QObject):
        @pyqtSlot()
        def closeWindow(self):window.close()
        @pyqtSlot()
        def minimize(self):collapse()
        @pyqtSlot()
        def startDrag(self):
            handle=window.windowHandle()
            if handle:handle.startSystemMove()

    class Launcher(QWidget):
        # A small always-on-top button; the assistant window stays hidden until it is clicked.
        W,H,PAD=76,32,5
        def __init__(self,state_file):
            super().__init__()
            self.state_file=state_file;self.press=None;self.moved=False
            self.setWindowFlags(Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint|Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground);self.setFixedSize(self.W+2*self.PAD,self.H+2*self.PAD);self.hover=False;self.setMouseTracking(True)
            self.setWindowTitle('Yuki 按钮');self.setToolTip('点击打开或收起 Yuki（Ctrl+Alt+Y）；可拖动，右键退出')
            self.move(self.saved_position())
        def saved_position(self):
            try:
                data=json.loads(self.state_file.read_text(encoding='utf-8'))
                point=QPoint(int(data['x']),int(data['y']))
                screen=QApplication.screenAt(point)
                if screen is not None:return point
            except (OSError,ValueError,KeyError,TypeError):pass
            area=QApplication.primaryScreen().availableGeometry()
            return QPoint(area.right()-self.width()-16,area.bottom()-self.height()-80)
        def save_position(self):
            try:
                self.state_file.parent.mkdir(parents=True,exist_ok=True)
                self.state_file.write_text(json.dumps({'x':self.x(),'y':self.y()}),encoding='utf-8')
            except OSError:pass
        def enterEvent(self,event):self.hover=True;self.update()
        def leaveEvent(self,event):self.hover=False;self.update()
        def paintEvent(self,event):
            painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
            opened=window.isVisible();lift=1.0 if (opened or self.hover) else 0.0
            body=QRectF(self.PAD,self.PAD,self.W,self.H);radius=self.H/2
            # Soft shadow made of a few translucent rings.
            for grow,alpha in ((3.5,14),(2,22),(1,32)):
                painter.setPen(Qt.NoPen);painter.setBrush(QColor(0,0,0,alpha))
                painter.drawRoundedRect(body.adjusted(-grow,-grow+2,grow,grow+2),radius+grow,radius+grow)
            gradient=QLinearGradient(body.topLeft(),body.bottomLeft())
            gradient.setColorAt(0,QColor(44,38,52,245));gradient.setColorAt(1,QColor(26,24,34,245))
            painter.setBrush(gradient);painter.setPen(QPen(QColor(255,176,196,255 if lift else 190),1.6))
            painter.drawRoundedRect(body,radius,radius)
            # Status dot: pink glow when the window is open, soft grey when tucked away.
            dot=QColor(255,150,178) if opened else QColor(150,146,160)
            cx,cy=body.left()+radius,body.center().y()
            if opened:
                painter.setPen(Qt.NoPen);painter.setBrush(QColor(255,150,178,70));painter.drawEllipse(QRectF(cx-7,cy-7,14,14))
            painter.setPen(Qt.NoPen);painter.setBrush(dot);painter.drawEllipse(QRectF(cx-3.5,cy-3.5,7,7))
            font=QFont('Georgia',11);font.setItalic(True);font.setLetterSpacing(QFont.AbsoluteSpacing,0.8)
            painter.setFont(font);painter.setPen(QColor(255,236,242) if lift else QColor(228,214,222))
            painter.drawText(QRectF(body.left()+radius+7,body.top(),body.width()-radius-18,body.height()),Qt.AlignVCenter|Qt.AlignLeft,'Yuki')
        def mousePressEvent(self,event):
            if event.button()==Qt.LeftButton:
                self.press=event.globalPos()-self.pos();self.moved=False;self.origin=event.globalPos()
        def mouseMoveEvent(self,event):
            if self.press is not None and event.buttons()&Qt.LeftButton:
                if (event.globalPos()-self.origin).manhattanLength()>4:self.moved=True
                if self.moved:self.move(event.globalPos()-self.press)
        def mouseReleaseEvent(self,event):
            if event.button()==Qt.LeftButton and self.press is not None:
                self.press=None
                if self.moved:self.save_position()
                else:toggle()
        def contextMenuEvent(self,event):
            menu=QMenu(self);quit_action=menu.addAction('退出 Yuki')
            if menu.exec_(event.globalPos())==quit_action:app.quit()

    def place_beside():
        # Open the window beside the button (left if there is room, else right), bottom edges aligned.
        screen=QApplication.screenAt(launcher.geometry().center()) or QApplication.primaryScreen()
        area=screen.availableGeometry();gap=6
        x=launcher.x()-window.width()-gap
        if x<area.left():x=launcher.x()+launcher.width()+gap
        x=max(area.left(),min(x,area.right()-window.width()+1))
        y=launcher.y()+launcher.height()-window.height()
        window.move(x,max(area.top(),min(y,area.bottom()-window.height()+1)))
    def expand():
        place_beside();window.show();window.raise_();window.activateWindow();page.setAudioMuted(False);launcher.update()
    def collapse():
        # Hiding only the window keeps the local server, reading records and capture running.
        window.hide();page.setAudioMuted(True);launcher.update()
    def toggle():
        collapse() if window.isVisible() else expand()

    class HotkeyFilter(QAbstractNativeEventFilter):
        def nativeEventFilter(self,event_type,message):
            if event_type==b'windows_generic_MSG':
                import ctypes.wintypes
                msg=ctypes.wintypes.MSG.from_address(int(message))
                if msg.message==0x0312 and msg.wParam==1:toggle();return True,0
            return False,0

    app=QApplication(sys.argv[:1]);app.setApplicationName('Yuki 一起看剧情')
    window=QWidget();window.setWindowTitle('Yuki · 一起看剧情');window.setWindowFlags(Qt.Window|Qt.FramelessWindowHint)
    window.setAttribute(Qt.WA_TranslucentBackground);window.resize(320,460);window.setMinimumSize(280,380)
    view=QWebEngineView(window);view.setAttribute(Qt.WA_TranslucentBackground);view.setStyleSheet('background:transparent')
    profile=QWebEngineProfile('YukiWindow',window)
    profile.setPersistentStoragePath(str(ROOT/'.cache/yuki-window/profile'))
    profile.setCachePath(str(ROOT/'.cache/yuki-window/cache'))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
    profile.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)
    interceptor=LocalRequests(profile);profile.setUrlRequestInterceptor(interceptor)
    page=LocalPage(profile,view);page.setBackgroundColor(QColor(Qt.transparent));view.setPage(page)
    page.setAudioMuted(False)
    page.featurePermissionRequested.connect(lambda origin,feature:page.setFeaturePermission(origin,feature,QWebEnginePage.PermissionDeniedByUser))
    page.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls,False)
    page.settings().setAttribute(QWebEngineSettings.WebGLEnabled,True)
    page.settings().setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture,False)
    view.setContextMenuPolicy(Qt.NoContextMenu)
    channel=QWebChannel(page);host=Host(channel);channel.registerObject('windowHost',host);page.setWebChannel(channel)
    layout=QVBoxLayout(window);layout.setContentsMargins(0,0,0,0);layout.addWidget(view)
    grip=QSizeGrip(window);grip.setFixedSize(15,15);grip.setStyleSheet('background:transparent')
    original_resize=window.resizeEvent
    def resize(event):
        grip.move(window.width()-17,window.height()-17);grip.raise_();original_resize(event)
    window.resizeEvent=resize
    app.setQuitOnLastWindowClosed(False)
    original_close=window.closeEvent
    def close(event):original_close(event);app.quit()
    window.closeEvent=close
    launcher=Launcher(ROOT/'.cache/yuki-window/launcher.json')
    hotkey=None
    if sys.platform=='win32':
        import ctypes
        # Ctrl+Alt+Y toggles the window; failure (key already taken) just leaves the button.
        if ctypes.windll.user32.RegisterHotKey(None,1,0x0002|0x0001|0x4000,0x59):
            hotkey=HotkeyFilter();app.installNativeEventFilter(hotkey)
    view.load(QUrl(url));launcher.show()
    if os.environ.get('YUKI_START_OPEN')=='1':expand()
    else:page.setAudioMuted(True)
    try:return app.exec_()
    finally:
        if hotkey is not None:ctypes.windll.user32.UnregisterHotKey(None,1)
