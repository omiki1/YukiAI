"""Transparent native Qt window around our local interface."""
from pathlib import Path
import os
import sys

ROOT=Path(__file__).resolve().parents[1]

def open_window(url):
    # The host can inherit unrelated Qt settings from the terminal/Conda session.
    for name in ('QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','PYTHONPATH'):
        os.environ.pop(name,None)
    os.environ['QT_QPA_PLATFORM']='windows'
    from PyQt5.QtCore import Qt,QUrl,QObject,pyqtSlot
    from PyQt5.QtGui import QColor,QSurfaceFormat
    from PyQt5.QtWidgets import QApplication,QWidget,QVBoxLayout,QSizeGrip
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
        def minimize(self):window.showMinimized()
        @pyqtSlot()
        def startDrag(self):
            handle=window.windowHandle()
            if handle:handle.startSystemMove()

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
    view.load(QUrl(url));window.show();return app.exec_()
