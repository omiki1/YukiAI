"""Capture our own offscreen fixture window and run real local Windows OCR."""
import json
import os
import sys
import time
import threading
from PyQt5.QtCore import Qt,QBuffer,QIODevice
from PyQt5.QtGui import QPainter,QFont,QColor
from PyQt5.QtWidgets import QApplication,QWidget
from scripts.yuki_screen import WindowCapture,WindowsOcr
from scripts.yuki_game_reader import GameReader


def main():
    if os.name!='nt':raise SystemExit('This acceptance needs Windows.')
    app=QApplication(sys.argv[:1])
    class Sample(QWidget):
        def paintEvent(self,event):
            painter=QPainter(self);painter.fillRect(self.rect(),QColor('white'));painter.setPen(QColor('black'))
            painter.setFont(QFont('Microsoft YaHei UI',26));painter.drawText(25,65,'美咲：我在旧校舍门口等你。')
            painter.drawText(25,135,'窗边放着一把蓝色的雨伞。')
    sample=Sample();sample.setWindowTitle('Yuki 自编本机读屏验收');sample.resize(850,180);sample.move(-2000,-2000);sample.show();app.processEvents()
    try:
        capture=WindowCapture();target={'hwnd':int(sample.winId()),'pid':os.getpid()};image=capture.grab(target)
        assert image.width()>=800 and image.height()>=170
        buffer=QBuffer();buffer.open(QIODevice.WriteOnly);assert image.save(buffer,'PNG')
        text=WindowsOcr().recognize(bytes(buffer.data()))
        assert '旧校舍' in text and '雨伞' in text,text
        reader=GameReader(sample,capture=capture);events=[];reader.event.connect(lambda value:events.append(json.loads(value)))
        context={'run_id':'fixture-game','epoch':1,'position':2,'note_revision':0,'mode':'galgame'}
        reader.set_context(json.dumps(context));reader.target={**target,'title':'自编验收窗口'};reader.region=[0,0,1,1];reader.aspect=image.width()/image.height()
        reader.wake(require_foreground=False)
        # Captured dialogue keeps its identity while background records advance.
        reader.set_context(json.dumps({**context,'position':20}))
        deadline=time.monotonic()+15
        while reader.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        assert any(event['kind']=='preview' and '旧校舍' in event['text'] for event in events),events
        reader.close()
        class DelayedOcr:
            def __init__(self):self.started=threading.Event();self.release=threading.Event()
            def recognize(self,png):self.started.set();self.release.wait(3);return '迟到的旧游戏台词。'
            def cancel(self):self.release.set()
        delayed=DelayedOcr();reader=GameReader(sample,capture=capture,ocr=delayed);events=[];reader.event.connect(lambda value:events.append(json.loads(value)))
        reader.set_context(json.dumps(context));delayed.release.clear();reader.target={**target,'title':'自编验收窗口'};reader.region=[0,0,1,1];reader.aspect=image.width()/image.height()
        reader.wake(require_foreground=False);deadline=time.monotonic()+3
        while not delayed.started.is_set() and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        assert delayed.started.is_set()
        reader.set_context(json.dumps({**context,'run_id':'another-game','epoch':2}))
        deadline=time.monotonic()+3
        while reader.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        assert not any(event['kind']=='preview' for event in events);reader.close()
        print(json.dumps({'passed':True,'actual_window_capture':True,'actual_windows_ocr':True,
                          'background_progress_keeps_frame':True,'old_context_frame_discarded':True,
                          'fixture_only':True,'images_saved':0,'cloud_calls':0},ensure_ascii=False))
    finally:sample.close();app.processEvents()


if __name__=='__main__':main()
