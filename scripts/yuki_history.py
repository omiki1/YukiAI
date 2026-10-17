"""Small local conversation store. Reads our existing history format, no reference imports."""
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
class HistoryStore:
    def __init__(self,directory=None):
        self.directory=Path(directory) if directory else ROOT/'chat_history/yuki_original_001'
        self.directory.mkdir(parents=True,exist_ok=True)

    def path(self,uid):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',uid):raise ValueError('无效的对话编号。')
        return self.directory/(uid+'.json')

    def read(self,uid):
        data=json.loads(self.path(uid).read_text(encoding='utf-8'))
        if not isinstance(data,list):raise ValueError('对话记录格式错误。')
        return [m for m in data if isinstance(m,dict) and m.get('role') in ('human','ai') and isinstance(m.get('content'),str)]

    def create(self):
        uid=datetime.now().strftime('%Y-%m-%d_%H-%M-%S')+'_'+uuid4().hex
        self.path(uid).write_text(json.dumps([{'role':'metadata','timestamp':datetime.now().isoformat(timespec='seconds')}]),encoding='utf-8')
        return uid

    def append_exchange(self,uid,user,reply,emotion):
        path=self.path(uid)
        data=json.loads(path.read_text(encoding='utf-8'))
        timestamp=datetime.now().isoformat(timespec='seconds')
        data.extend([{'role':'human','timestamp':timestamp,'content':user,'name':'Human'},
                     {'role':'ai','timestamp':timestamp,'content':reply,'name':'Yuki','emotion':emotion}])
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(path)
