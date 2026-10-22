"""Offline capture acceptance: real CLI framing, progress boundaries, no provider calls."""
import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from scripts.yuki_capture import CaptureService, clean_text, is_interface_line
from scripts.yuki_config import ConfigStore
from scripts.yuki_server import create_app
from scripts.yuki_story import StoryStore, StoryConflict


class CaptureFixtureRuntime:
    """A pipe peer for UI/API checks, not a substitute for native injection testing."""
    overflow = False

    def __init__(self):
        self.running = False
        self.commands = []
        self.lines = []

    def available(self):return True
    def processes(self):return [{'pid': 4660, 'title': 'Yuki 自编取词测试', 'name': 'story-fixture.exe'}]
    def start(self,pid):self.running=True;self.commands.append(f'attach -P{pid}')
    def alive(self):return self.running
    def stop(self):self.running=False
    def command(self,text):self.commands.append(text)
    def drain(self):
        result,self.lines=self.lines,[]
        return result
    def emit(self,text,thread='2'):
        self.lines.append((time.monotonic(), f'[{thread}:1234:123ABC:0:0:TextOutW:HQ@123ABC] {text}'))


class RollingCaptureFixtureRuntime(CaptureFixtureRuntime):
    def start(self,pid):
        super().start(pid)
        self.number=0
        self.next=0
    def drain(self):
        if self.running and time.monotonic()>=self.next:
            self.number+=1
            self.emit(f'【美咲】放学后的第{self.number}声钟响，我们一起等雨停。')
            self.emit('<img src=x onerror=alert(1)> 菜单噪声',thread='3')
            self.next=time.monotonic()+1.4
        return super().drain()


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=StoryStore(Path(self.temp.name)/'story.sqlite3')
        self.store.create_run('自编校园故事','测试',0)
        self.runtime=CaptureFixtureRuntime()
        self.boundaries=[]
        self.capture=CaptureService(self.store,self.runtime,lambda:self.boundaries.append(self.store.epoch()))
        await self.capture.attach(4660)
        self.base=time.monotonic()+10
        self.emit('美咲：先在走廊等一下。',0)
        self.channel=self.capture.status()['threads'][0]['id']
        self.capture.select(self.capture.session,self.channel)

    async def asyncTearDown(self):
        await self.capture.detach()
        self.temp.cleanup()

    def start(self):
        view=self.store.view()
        self.capture.start_recording(self.capture.session,view['epoch'],view['active']['id'])

    def emit(self,text,offset,thread='2',commit=True):
        stamp=self.base+offset
        self.capture.line(f'[{thread}:1234:123ABC:0:0:TextOutW:HQ@123ABC] {text}',stamp)
        self.capture.tick(stamp+.2)
        if commit:self.capture.tick(stamp+.8)

    def events(self):return self.store.view()['active']['events']

    async def test_preview_selected_thread_and_raw_provenance(self):
        self.assertEqual(self.events(),[])
        self.start()
        self.emit('设置：音量',1,thread='3')
        self.emit('【美咲】\u200b我们\x00一起等雨停。',2)
        events=self.events()
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['speaker'],'美咲')
        self.assertEqual(events[0]['text'],'我们一起等雨停。')
        self.assertEqual(events[0]['raw_text'],'【美咲】\u200b我们\x00一起等雨停。')
        self.assertTrue(events[0]['source'].startswith('textractor:'))

    async def test_typewriter_prefixes_and_multiline_framing(self):
        self.start()
        self.emit('美咲：我',1,commit=False)
        self.emit('美咲：我们一起',1.3,commit=False)
        self.capture.line('[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 美咲：我们一起',self.base+1.6)
        self.capture.line('去图书室。',self.base+1.61)
        self.capture.tick(self.base+2.5)
        self.assertEqual(len(self.events()),1)
        self.assertEqual(self.events()[0]['text'],'我们一起 去图书室。')
        self.assertIn('\n',self.events()[0]['raw_text'])

    async def test_cli_missing_newline_does_not_mix_sources_or_console(self):
        self.start()
        self.capture.line('[3:1234:10:0:0:ExtTextOutW:HQ10@0] \ufffd\uffff'
            '[0:0:FFFFFFFFFFFFFFFF:FFFFFFFFFFFFFFFF:FFFFFFFFFFFFFFFF:Console:HB0@0] hook message'
            '[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 美咲：这句正文应该完整保留。',self.base+2)
        self.capture.tick(self.base+3)
        self.assertEqual(len(self.events()),1)
        self.assertEqual(self.events()[0]['text'],'这句正文应该完整保留。')
        self.assertNotIn('hook message',self.events()[0]['raw_text'])
        self.assertEqual(len(self.capture.channels),2)

    async def test_unwoken_background_dialogue_can_be_located_from_later_screen(self):
        self.start()
        for number in range(1,61):self.emit(f'旁白：钟声响过第{number}次，走廊里依然没有人。',number)
        target='美咲：我把那把蓝色雨伞放在旧校舍的门口了。'
        self.emit(target,62)
        state=self.store.view();result=self.store.locate_screen(target,state['epoch'],state['active']['id'])
        self.assertEqual(result['status'],'matched');self.assertEqual(result['candidates'][0]['position'],61)
        self.assertEqual(state['active']['position'],61)
        self.assertTrue(all(event['source'].startswith('textractor:') for event in state['active']['events']))

    async def test_hundred_lines_skip_menus_and_confirm_rewind(self):
        self.assertTrue(is_interface_line('【保存】'))
        self.assertTrue(is_interface_line('Q.SAVE'))
        self.assertFalse(is_interface_line('美咲：我们先保存这封信。'))
        self.start()
        story = []
        for number in range(1, 81):
            if number % 17 == 0:
                story.append(f'旁白：走廊里的第{number}盏灯还亮着。')
            else:
                story.append(f'美咲：这是正文第{number}句，我们还在等雨。')
        menus = ['保存', '读取', 'LOAD', '【设定】', 'F5', '12', '跳过', '标题', 'Config', '快进']
        offset = 1
        for index, line in enumerate(story):
            self.emit(line, offset)
            offset += 1
            if index % 8 == 7:
                self.emit(menus[(index // 8) % len(menus)], offset)
                offset += 1
        state = self.store.view()['active']
        self.assertEqual(state['position'], 80)
        texts = [event['text'] for event in state['events']]
        self.assertEqual(len(texts), 50)
        self.assertTrue(any('正文第80句' in text for text in texts))
        self.assertFalse(any(text in ('保存', '读取', '设定', '跳过', '标题', '快进', 'LOAD', 'Config', 'F5', '12') for text in texts))
        self.assertIn('正文第1句', self.store.snapshot('正文第1句', self.store.epoch()).events[0]['text'])
        self.assertIn('跳过了一条菜单', self.capture.message)
        position = self.store.view()['active']['position']
        self.emit(story[0], offset)
        self.emit(story[1], offset + 1)
        self.assertFalse(self.capture.recording)
        self.assertEqual(self.store.view()['active']['position'], position)
        self.assertIn('书签', self.capture.message)

    async def test_duplicate_repaints_and_legitimate_repeated_dialogue(self):
        self.start()
        self.emit('美咲：知道了。',1)
        self.emit('美咲：知道了。',1.5)
        self.emit('美咲：知道了。',2)
        self.assertEqual(len(self.events()),1)
        self.assertEqual(self.capture.duplicates,2)
        self.emit('小春：我们走吧。',2.3)
        self.emit('美咲：知道了。',2.6)
        self.emit('美咲：知道了。',5)
        self.assertEqual(len(self.events()),4)

    async def test_old_pipe_buffer_and_pause_pending_never_commit(self):
        self.start()
        self.capture.line('[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 旧缓冲',self.capture.started-1)
        self.capture.tick(self.base+1)
        self.assertEqual(self.events(),[])
        self.emit('旁白：这句话尚未稳定。',2,commit=False)
        self.capture.pause()
        self.capture.tick(self.base+4)
        self.assertEqual(self.events(),[])

    async def test_restore_discards_pending_and_new_branch_isolated(self):
        self.start()
        self.emit('旁白：共同路口。',1)
        self.store.bookmark('路口',self.store.epoch())
        point=self.store.view()['active']['bookmarks'][0]['id']
        self.emit('旁白：A路线的秘密。',3)
        a=self.store.view()['active']['branch_id']
        self.emit('旁白：不能进入B的迟到台词。',4,commit=False)
        self.store.restore(point,self.store.epoch())
        self.capture.tick(self.base+6)
        self.assertFalse(self.capture.recording)
        self.start()
        self.emit('旁白：B路线去图书室。',8)
        view=self.store.view()
        self.assertNotEqual(view['active']['branch_id'],a)
        self.assertEqual(len(self.boundaries),1)
        self.assertTrue(self.capture.recording)
        self.assertNotIn('秘密',str(view['active']['events']))
        self.assertNotIn('迟到',str(view['active']['events']))
        self.emit('小春：这里有雨伞。',10)
        self.assertEqual(len(self.events()),3)

    async def test_game_switch_stale_session_long_input_and_hook_validation(self):
        session=self.capture.session
        self.start()
        self.store.create_run('另一个游戏','初见',self.store.epoch())
        self.emit('美咲：不能串入另一个游戏。',2)
        self.assertEqual(self.events(),[])
        self.assertFalse(self.capture.recording)
        for invalid in ('attach -P1','HQ@1\ndetach -P2','H X'):
            with self.assertRaises(ValueError):self.capture.hook(session,invalid)
        self.capture.hook(session,'HQ@123ABC')
        self.assertEqual(self.runtime.commands[-1],'HQ@123ABC -P4660')
        await self.capture.attach(4660)
        with self.assertRaises(StoryConflict):self.capture.require_session(session)
        self.emit('正文。',3)
        self.capture.select(self.capture.session,next(iter(self.capture.channels)))
        self.start()
        self.emit('字'*2001,4)
        self.assertFalse(self.capture.recording)
        self.assertEqual(self.events(),[])

    async def test_tool_exit_and_overflow_pause_without_importing_buffer(self):
        self.start()
        self.runtime.emit('不应收录的溢出缓冲')
        self.runtime.overflow=True
        await asyncio.sleep(.12)
        self.assertFalse(self.capture.recording)
        self.assertEqual(self.events(),[])
        self.runtime.running=False
        await asyncio.sleep(.12)
        self.assertIn('退出',self.capture.message)


class CaptureAPITests(unittest.TestCase):
    def test_connect_button_creates_game_reading_and_never_records_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=CaptureFixtureRuntime();app=create_app(history_directory=directory,config=ConfigStore(Path(directory)/'config.json',env={}),allow_cloud=False,capture_runtime=runtime)
            with TestClient(app) as client:
                self.assertFalse(client.get('/api/capture').json()['connected']);self.assertEqual(runtime.commands,[])
                self.assertEqual(client.post('/api/capture/connect',json={'pid':999,'epoch':0,'run_id':None}).status_code,400)
                self.assertEqual(client.get('/api/story').json()['runs'],[])
                result=client.post('/api/capture/connect',json={'pid':4660,'epoch':0,'run_id':None})
                self.assertEqual(result.status_code,200);data=result.json()
                self.assertTrue(data['capture']['connected']);self.assertFalse(data['capture']['recording'])
                self.assertEqual(data['story']['active']['mode'],'galgame');self.assertEqual(data['story']['active']['position'],0)
                self.assertEqual(runtime.commands,['attach -P4660'])
            self.assertFalse(runtime.running)

    def test_stale_reading_and_novel_connect_do_not_inject(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=CaptureFixtureRuntime();app=create_app(history_directory=directory,config=ConfigStore(Path(directory)/'config.json',env={}),allow_cloud=False,capture_runtime=runtime)
            with TestClient(app) as client:
                self.assertEqual(client.post('/api/capture/connect',json={'pid':4660,'epoch':1}).status_code,409)
                state=client.post('/api/story/runs',json={'title':'自编小说','name':'第一章','mode':'novel','epoch':0}).json()
                self.assertEqual(client.post('/api/capture/connect',json={'pid':4660,'epoch':state['epoch'],'run_id':state['active']['id']}).status_code,400)
                self.assertEqual(runtime.commands,[])

    def test_one_begin_button_checks_source_session_and_confirmation_before_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=CaptureFixtureRuntime();app=create_app(history_directory=directory,config=ConfigStore(Path(directory)/'config.json',env={}),allow_cloud=False,capture_runtime=runtime)
            with TestClient(app) as client:
                data=client.post('/api/capture/connect',json={'pid':4660,'epoch':0}).json();capture=app.state.capture
                def preview():
                    stamp=time.monotonic();capture.line('[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 美咲：这只是确认来源的预览。',stamp);capture.tick(stamp+.3)
                client.portal.call(preview)
                thread=client.get('/api/capture').json()['threads'][0]['id']
                body={'session_id':data['capture']['session_id'],'epoch':data['story']['epoch'],'run_id':data['story']['active']['id'],
                      'thread_id':thread,'kind':'auto','speaker':'','confirmed_read':True}
                for changed,status in (({'confirmed_read':False},422),({'session_id':'stale-session'},409),({'thread_id':'missing-source'},400),({'epoch':0},409)):
                    self.assertEqual(client.post('/api/capture/begin',json={**body,**changed}).status_code,status)
                    self.assertFalse(client.get('/api/capture').json()['recording'])
                result=client.post('/api/capture/begin',json=body)
                self.assertEqual(result.status_code,200);self.assertTrue(result.json()['recording']);self.assertEqual(result.json()['selected_thread'],thread)
                self.assertEqual(client.get('/api/story').json()['active']['position'],0)

    def test_install_requires_click_checks_readiness_and_never_injects(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=CaptureFixtureRuntime();ready=[False];runtime.available=lambda:ready[0]
            app=create_app(history_directory=directory,config=ConfigStore(Path(directory)/'config.json',env={}),capture_runtime=runtime)
            with patch('scripts.install_textractor.install',side_effect=lambda:ready.__setitem__(0,True)) as install,TestClient(app) as client:
                self.assertFalse(client.get('/api/capture').json()['available']);install.assert_not_called()
                self.assertEqual(client.post('/api/capture/connect',json={'pid':4660,'epoch':0}).status_code,400);self.assertEqual(runtime.commands,[])
                self.assertTrue(client.post('/api/capture/install',json={}).json()['available']);install.assert_called_once()
                self.assertTrue(client.post('/api/capture/install',json={}).json()['available']);install.assert_called_once()
                self.assertEqual(runtime.commands,[])
                self.assertEqual(client.post('/api/capture/connect',json={'pid':4660,'epoch':0}).status_code,200)
                self.assertEqual(client.post('/api/capture/install',json={}).status_code,409)

    def test_install_failure_is_redacted_and_preview_cannot_download(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=CaptureFixtureRuntime();runtime.available=lambda:False
            app=create_app(history_directory=directory,config=ConfigStore(Path(directory)/'config.json',env={}),capture_runtime=runtime)
            with patch('scripts.install_textractor.install',side_effect=ValueError('fixture-private-path')),TestClient(app) as client:
                response=client.post('/api/capture/install',json={});self.assertEqual(response.status_code,502)
                self.assertNotIn('fixture-private-path',response.text);self.assertFalse(client.get('/api/capture').json()['available'])
            app=create_app(history_directory=directory,config=ConfigStore(Path(directory)/'config.json',env={}),allow_cloud=False,capture_runtime=runtime)
            with patch('scripts.install_textractor.install') as install,TestClient(app) as client:
                self.assertEqual(client.post('/api/capture/install',json={}).status_code,403);install.assert_not_called()

    def test_session_validation_local_origin_and_lifespan_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=CaptureFixtureRuntime()
            app=create_app(history_directory=directory,config=ConfigStore(Path(directory)/'config.json',env={}),
                           allow_cloud=False,capture_runtime=runtime)
            with TestClient(app) as client:
                self.assertFalse(client.get('/api/capture').json()['recording'])
                self.assertEqual(client.post('/api/capture/attach',json={'pid':999}).status_code,400)
                response=client.post('/api/capture/attach',json={'pid':4660})
                self.assertEqual(response.status_code,200)
                session=response.json()['session_id']
                self.assertEqual(client.post('/api/capture/record',json={'session_id':session,'epoch':0,'run_id':'missing','confirmed_read':False}).status_code,422)
                self.assertEqual(client.post('/api/capture/pause',json={'session_id':'old-session'}).status_code,409)
                self.assertEqual(client.post('/api/capture/pause',json={'session_id':session},headers={'Origin':'https://example.com'}).status_code,403)
                self.assertEqual(client.post('/api/capture/hook',json={'session_id':session,'code':'H\nattach'}).status_code,400)
            self.assertFalse(runtime.running)


if __name__=='__main__':unittest.main()
