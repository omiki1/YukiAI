"""Native one-shot Gal reading. Selection is explicit; frames are never saved."""
import json
import threading
from uuid import uuid4
from PyQt5.QtCore import QObject,Qt,QRect,QBuffer,QIODevice,pyqtSignal
from PyQt5.QtGui import QPainter,QColor,QPen,QImage
from PyQt5.QtWidgets import QDialog,QVBoxLayout,QLabel,QComboBox,QDialogButtonBox
from .yuki_screen import WindowCapture,WindowsOcr


class RegionPicker(QDialog):
    def __init__(self,image,parent):
        super().__init__(parent);self.image=image;self.origin=None;self.region=QRect()
        self.setWindowTitle('框选游戏文字区域 · 拖动鼠标，回车确认，Esc取消')
        area=self.screen().availableGeometry();scale=min(1,(area.width()-80)/image.width(),(area.height()-100)/image.height())
        self.resize(int(image.width()*scale),int(image.height()*scale));self.setMouseTracking(True)
    def paintEvent(self,event):
        painter=QPainter(self);painter.drawImage(self.rect(),self.image);painter.fillRect(self.rect(),QColor(0,0,0,65))
        if not self.region.isNull():
            painter.setClipRect(self.region);painter.drawImage(self.rect(),self.image);painter.setClipping(False)
            painter.setPen(QPen(QColor('#ffafcb'),2));painter.drawRect(self.region)
        painter.setPen(QColor('white'));painter.drawText(10,22,'框住姓名与当前台词；回车确认，Esc取消')
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:self.origin=event.pos();self.region=QRect();self.update()
    def mouseMoveEvent(self,event):
        if self.origin is not None:self.region=QRect(self.origin,event.pos()).normalized().intersected(self.rect());self.update()
    def mouseReleaseEvent(self,event):self.origin=None
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key_Return,Qt.Key_Enter) and self.region.width()>=40 and self.region.height()>=15:self.accept()
        elif event.key()==Qt.Key_Escape:self.reject()
    def normalized(self):return [self.region.x()/self.width(),self.region.y()/self.height(),self.region.width()/self.width(),self.region.height()/self.height()]


class GameReader(QObject):
    event=pyqtSignal(str)
    completed=pyqtSignal(object)
    def __init__(self,parent,*,capture=None,ocr=None):
        super().__init__(parent);self.parent_window=parent;self.capture=capture or WindowCapture();self.ocr=ocr or WindowsOcr()
        self.context=None;self.target=None;self.region=None;self.aspect=None;self.generation=0;self.busy=False;self.closed=False
        self.completed.connect(self.deliver)
    def emit(self,kind,**data):self.event.emit(json.dumps({'kind':kind,**(self.context or {}),**data},ensure_ascii=False))
    def set_context(self,value):
        try:
            value=json.loads(value)
            assert value.get('mode') in ('galgame','novel','chat')
            assert all(type(value.get(key)) is int and value[key]>=0 for key in ('epoch','position','note_revision'))
            assert isinstance(value.get('run_id'),str) and len(value['run_id'])<=100
        except (ValueError,AssertionError,TypeError):return
        identity=lambda item:tuple((item or {}).get(key) for key in ('run_id','epoch','mode','note_revision'))
        if identity(value)!=identity(self.context):
            self.generation+=1;self.ocr.cancel()
            if self.context and self.context['run_id']!=value['run_id']:self.target=self.region=None
        self.context=value
        self.status()
    def status(self):self.emit('status',configured=bool(self.target and self.region),title=self.target['title'] if self.target else '',busy=self.busy)
    def launch(self,function,kind):
        if self.busy:self.emit('error',message='上一张画面还在识别，请稍等。');return
        self.busy=True;generation=self.generation;context=dict(self.context or {});self.emit('busy')
        def worker():
            try:result={'kind':kind,'value':function()}
            except Exception as error:result={'kind':'error','message':str(error) if isinstance(error,ValueError) else '本机读屏没有完成，请重新框选后再试。'}
            if not self.closed:
                try:self.completed.emit({**result,'generation':generation,'context':context})
                except RuntimeError:pass
        threading.Thread(target=worker,daemon=True).start()
    def setup(self):
        if not self.context or self.context['mode']!='galgame' or not self.context['run_id']:self.emit('error',message='请先建立或选择 Galgame 共读。');return
        if self.busy:self.emit('error',message='请等当前画面处理完成后再设置。');return
        rows=self.capture.windows()
        if not rows:self.emit('error',message='没有找到游戏窗口，请先打开游戏。');return
        dialog=QDialog(self.parent_window);dialog.setWindowTitle('选择要读取的游戏');layout=QVBoxLayout(dialog)
        layout.addWidget(QLabel('选择游戏，下一步只框选它的文字区域。\n不会截取整个桌面，也不会上传图片。'))
        choices=QComboBox(dialog)
        for row in rows:choices.addItem(row['title']+' · '+row['name'])
        layout.addWidget(choices);buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        accepted=dialog.exec_()==QDialog.Accepted;index=choices.currentIndex();dialog.deleteLater()
        if not accepted:return
        target=rows[index];self.target=None;self.region=None;self.generation+=1
        self.launch(lambda:(target,self.capture.grab(target)),'selection')
    def wake(self,require_foreground=True):
        if not self.context or self.context['mode']!='galgame':return False
        if not self.target or not self.region:self.emit('setup-needed',message='先在“按键读屏”里选择游戏并框选文字区域。');return True
        if require_foreground and not self.capture.foreground(self.target):self.emit('error',message='请切回已选游戏，再按 Ctrl+Alt+Y 读取当前台词。');return True
        target=dict(self.target);region=list(self.region);aspect=self.aspect
        def recognize():
            image=self.capture.grab(target)
            if abs(image.width()/image.height()/aspect-1)>.05:raise ValueError('游戏画面比例改变，请重新框选文字区域。')
            x,y,w,h=region;cropped=image.copy(round(x*image.width()),round(y*image.height()),round(w*image.width()),round(h*image.height()))
            del image
            if max(cropped.width(),cropped.height())>2500:cropped=cropped.scaled(2500,2500,Qt.KeepAspectRatio,Qt.SmoothTransformation)
            output=QBuffer();output.open(QIODevice.WriteOnly);cropped.save(output,'PNG')
            return self.ocr.recognize(bytes(output.data()))
        self.launch(recognize,'preview');return True
    def deliver(self,result):
        self.busy=False
        if self.closed or result['generation']!=self.generation:return
        if result['kind']=='selection':
            target,image=result['value'];picker=RegionPicker(image,self.parent_window)
            if picker.exec_()==QDialog.Accepted and result['generation']==self.generation and not self.closed:
                self.target=target;self.region=picker.normalized();self.aspect=image.width()/image.height();self.status()
            else:self.status()
            picker.image=QImage();picker.deleteLater()
        elif result['kind']=='preview':self.emit('preview',text=result['value'],request_id='screen-'+uuid4().hex)
        else:self.emit('error',message=result['message'])
    def close(self):self.closed=True;self.generation+=1;self.ocr.cancel()
