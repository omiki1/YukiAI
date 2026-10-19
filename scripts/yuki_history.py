"""Small local conversation store. Reads our existing history format, no reference imports."""
from datetime import datetime
import json
from pathlib import Path
import re
import hashlib
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
class HistoryStore:
    def __init__(self,directory=None):
        self.directory=Path(directory) if directory else ROOT/'chat_history/yuki_original_001'
        self.directory.mkdir(parents=True,exist_ok=True)

    def path(self,uid):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',uid):raise ValueError('无效的对话编号。')
        path=self.directory/(uid+'.json')
        if path.is_symlink() or path.resolve().parent!=self.directory.resolve():raise ValueError('无效的对话文件。')
        return path

    def inventory(self):
        items=[]
        for path in sorted(self.directory.glob('*.json'),reverse=True):
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',path.stem):continue
            try:
                path=self.path(path.stem)
                size=path.stat().st_size
                if size>20*1024*1024:raise ValueError('对话文件过大。')
                messages=self.read(path.stem)
                metadata=json.loads(path.read_text(encoding='utf-8'))
                restored=any(row.get('role')=='metadata' and row.get('name')=='恢复副本' for row in metadata if isinstance(row,dict))
                items.append({'id':path.stem,'bytes':size,'messages':len(messages),'readable':True,'restored':restored})
            except (ValueError,OSError):
                # Broken files remain visible and can be explicitly removed.
                if not path.is_symlink() and path.resolve().parent==self.directory.resolve() and path.exists():
                    items.append({'id':path.stem,'bytes':path.stat().st_size,'messages':0,'readable':False})
        return items

    def revision(self,uid):
        return hashlib.sha256(self.path(uid).read_bytes()).hexdigest()

    def delete(self,uid,revision):
        if self.revision(uid)!=revision:raise ValueError('对话有了新内容，请重新预览清理范围。')
        self.path(uid).unlink()

    def read(self,uid):
        path=self.path(uid)
        if path.stat().st_size>20*1024*1024:raise ValueError('对话文件过大。')
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,list):raise ValueError('对话记录格式错误。')
        return [m for m in data if isinstance(m,dict) and m.get('role') in ('human','ai') and isinstance(m.get('content'),str)]

    def create(self):
        uid=datetime.now().strftime('%Y-%m-%d_%H-%M-%S')+'_'+uuid4().hex
        self.write(uid,[{'role':'metadata','timestamp':datetime.now().isoformat(timespec='seconds')}])
        return uid

    def write(self,uid,data):
        path=self.path(uid)
        temporary=self.directory/(uid+'.'+uuid4().hex+'.tmp')
        try:
            with temporary.open('x',encoding='utf-8') as stream:
                stream.write(json.dumps(data,ensure_ascii=False,indent=2))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def append_exchange(self,uid,user,reply,emotion):
        path=self.path(uid)
        data=json.loads(path.read_text(encoding='utf-8'))
        timestamp=datetime.now().isoformat(timespec='seconds')
        data.extend([{'role':'human','timestamp':timestamp,'content':user,'name':'Human'},
                     {'role':'ai','timestamp':timestamp,'content':reply,'name':'Yuki','emotion':emotion}])
        self.write(uid,data)
