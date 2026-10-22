"""Offline scene matching and wake/import boundaries, using invented stories."""
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from scripts.yuki_config import ConfigStore
from scripts.yuki_locator import locate_dialogue
from scripts.yuki_server import create_app
from scripts.yuki_story import StoryStore
from scripts.test_capture import CaptureFixtureRuntime


LINE='美咲：我在旧校舍门口等你，记得带那把蓝色的雨伞。'


class GameReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.story=StoryStore(root/'story.sqlite3');self.story.create_run('自编雨伞故事','初见',0)
        self.run=self.story.view()['active']['id'];self.add('旁白：走廊里的灯还没有亮。\n'+LINE)
        self.client=TestClient(create_app(history_directory=root/'history',story=self.story,
            config=ConfigStore(root/'config.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime()))
    def tearDown(self):self.client.close();self.temp.cleanup()
    def add(self,text):
        from uuid import uuid4
        self.story.ingest(text,'auto','',uuid4().hex,self.story.epoch(),self.run)
    def locate(self,text=LINE):return self.client.post('/api/story/screen/locate',json={'run_id':self.run,'epoch':self.story.epoch(),'text':text})
    def confirm(self,text=LINE,position=None,**changes):
        data={'run_id':self.run,'epoch':self.story.epoch(),'text':text,'note_revision':0,
              'request_id':'screen-fixture-00001','confirmed_read':True,'match_position':position,**changes}
        return self.client.post('/api/story/screen',json=data)
    def test_exact_ocr_wrapping_and_punctuation_match_without_writes(self):
        before=self.story.view();result=self.locate('美咲\n我在旧校舍门口等你，\n记得带那把蓝色的雨伞！').json()
        self.assertEqual(result['status'],'matched');self.assertEqual(result['candidates'][0]['position'],2)
        self.assertEqual(self.story.view(),before)
        response=self.confirm(position=2);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['active']['position'],2);self.assertEqual(len(response.json()['active']['events']),2)
    def test_minor_ocr_error_and_repeated_lines_need_disambiguation(self):
        self.assertEqual(self.locate(LINE.replace('旧校舍','旧校含')).json()['status'],'matched')
        self.add('旁白：第二天，她又站在窗边。');self.add(LINE)
        result=self.locate().json();self.assertEqual(result['status'],'ambiguous')
        self.assertEqual({r['position'] for r in result['candidates'][:2]},{2,4})
        self.assertEqual(self.confirm().status_code,409)
    def test_background_recording_can_advance_between_wake_and_confirm(self):
        epoch=self.story.epoch();self.add('旁白：雨停以后，走廊里只剩下脚步声。')
        self.assertEqual(self.story.epoch(),epoch)
        result=self.locate().json();self.assertEqual(result['status'],'matched');self.assertEqual(result['candidates'][0]['position'],2)
        response=self.confirm(position=2);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['active']['position'],2)
    def test_long_unwoken_interval_matches_accumulated_record(self):
        self.add('\n'.join('旁白：第'+str(i)+'次钟声响起，走廊的窗户仍然关着。' for i in range(60)))
        self.add('小春：原来你还记得那天留在窗边的蓝色雨伞。')
        result=self.locate('小春：原来你还记得那天留在窗边的蓝色雨伞。').json()
        self.assertEqual(result['status'],'matched');self.assertEqual(result['candidates'][0]['position'],63)
    def test_rewind_and_sibling_route_records_cannot_match(self):
        self.story.fork('B',self.story.epoch());child=self.story.view()['active']['branch_id']
        self.add('旁白：B路线的红色信封里写着与雨伞无关的秘密。')
        self.story.progress(2,self.story.epoch())
        self.assertEqual(self.locate('B路线的红色信封里写着与雨伞无关的秘密。').json()['status'],'not_found')
        self.story.fork('C',self.story.epoch());self.add('旁白：C路线只有一张空白便签。')
        self.assertEqual(self.locate('B路线的红色信封里写着与雨伞无关的秘密。').json()['status'],'not_found')
    def test_unmatched_frame_requires_confirmation_and_has_ocr_source(self):
        text='旁白：风吹开了桌上的信纸，露出一片干枯的树叶。'
        self.assertEqual(self.locate(text).json()['status'],'not_found')
        self.assertEqual(self.confirm(text,confirmed_read=False).status_code,422)
        self.assertEqual(self.story.view()['active']['position'],2)
        response=self.confirm(text);self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['active']['events'][-1]['source'],'screen-ocr')
    def test_stale_epoch_notes_bad_match_and_novel_are_rejected(self):
        self.assertEqual(self.confirm(position=99).status_code,409)
        self.assertEqual(self.confirm(position=2,note_revision=1).status_code,409)
        old=self.story.epoch();self.story.progress(1,old)
        self.assertEqual(self.confirm(position=2,epoch=old).status_code,409)
        self.story.create_run('自编小说','小说',self.story.epoch(),'novel');self.run=self.story.view()['active']['id']
        self.assertEqual(self.locate().status_code,409)
    def test_short_line_never_silently_matches_a_greeting(self):
        self.assertEqual(self.locate('你好。').json()['status'],'too_short')

    def test_multi_line_anchor_ends_at_the_matching_last_line(self):
        self.add('小春：我已经把那封信交给门口的老师了。\n旁白：她转身走向楼梯，脚步声逐渐远去。')
        result=self.locate(LINE+'\n小春：我已经把那封信交给门口的老师了。').json()
        self.assertEqual(result['status'],'matched');self.assertEqual(result['candidates'][0]['position'],3)

    def test_no_active_reading_returns_controlled_error(self):
        self.story.activate(None,self.story.epoch())
        self.assertEqual(self.locate().status_code,400)


if __name__=='__main__':unittest.main()
