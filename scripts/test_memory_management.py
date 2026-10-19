"""Offline acceptance for local memory cleanup and additive recovery. Invented text only."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from scripts.yuki_config import ConfigStore
from scripts.yuki_history import HistoryStore
from scripts.yuki_memory import MemoryService, encoded
from scripts.yuki_server import create_app
from scripts.yuki_story import StoryStore, StoryConflict
from scripts.test_capture import CaptureFixtureRuntime


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.story = StoryStore(self.root / 'data' / 'story.sqlite3')
        self.history = HistoryStore(self.root / 'history')
        self.memory = MemoryService(self.story, self.history)
        self.story.create_run('自编雨天故事', '初见', 0)
        self.run = self.story.view()['active']['id']
        self.ingest('旁白：走廊里下起了雨。\n美咲：我们一起去图书室吧。')
        self.story.bookmark('雨天的走廊', self.story.epoch())
        self.point = self.story.view()['active']['bookmarks'][0]['id']
        self.add_turn()
        self.story.new_chat(self.story.epoch())
        self.add_turn()
        self.uid = self.history.create()
        self.history.append_exchange(self.uid, '陪我看看雨吧', '好呀，一起慢慢看。', 'happy')

    def tearDown(self):self.temp.cleanup()

    def ingest(self, text):
        self.story.ingest(text, 'auto', '', 'test-' + uuid4().hex, self.story.epoch(), self.story.view()['active']['id'])

    def add_turn(self):
        snapshot = self.story.snapshot('美咲说什么', self.story.epoch())
        self.story.append_exchange(snapshot, '美咲说什么', {'text':'她想去图书室。', 'emotion':'neutral',
            'answer_type':'analysis', 'evidence_ids':[snapshot.events[-1]['id']]})

    def plan(self, action, uid):return self.memory.plan(action, uid, self.story.epoch())['token']

    def backup(self):return self.memory.create_backup(self.story.epoch())

    def rewrite(self, backup, edit):
        folder = self.memory.directory / backup['id']
        payload = json.loads((folder / 'memory.json').read_bytes())
        edit(payload)
        content = encoded(payload)
        (folder / 'memory.json').write_bytes(content)
        manifest = json.loads((folder / 'manifest.json').read_bytes())
        manifest.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        (folder / 'manifest.json').write_bytes(encoded(manifest))

    def test_inventory_counts_hidden_chats_and_all_branches(self):
        self.ingest('美咲：A路线的信藏在钟楼。')
        self.story.restore(self.point, self.story.epoch())
        self.ingest('小春：B路线的伞放在图书室。')
        item = self.memory.inventory()['runs'][0]
        self.assertEqual((item['events'], item['branches'], item['bookmarks'], item['story_turns']), (4,2,1,2))
        self.assertGreater(item['text_bytes'], 0)
        self.assertEqual(self.memory.inventory()['histories'][0]['messages'], 2)

    def test_clear_all_discussion_generations_retains_source_and_other_run(self):
        self.story.create_run('另一部自编故事', '另一篇', self.story.epoch())
        other = self.story.view()['active']['id']
        self.ingest('旁白：另一篇故事的风吹过花园。')
        self.add_turn()
        before = self.story.view()['active']
        self.memory.execute(self.plan('clear_discussion', self.run))
        self.assertEqual(self.story.view()['active']['events'], before['events'])
        self.assertEqual(self.story.view()['active']['id'], other)
        self.story.activate(self.run, self.story.epoch())
        state = self.story.view()['active']
        self.assertEqual((state['position'],len(state['bookmarks']),state['messages']), (2,1,[]))
        with self.story.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM story_turns WHERE run_id=?',(self.run,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM story_turns WHERE run_id=?',(other,)).fetchone()[0],1)

    def test_delete_whole_run_has_no_orphans_and_invalidates_snapshot(self):
        self.ingest('美咲：A路线有一封红信。')
        self.story.restore(self.point,self.story.epoch())
        self.ingest('小春：B路线在图书室。')
        snapshot = self.story.snapshot('图书室', self.story.epoch())
        self.memory.execute(self.plan('delete_run',self.run))
        self.assertIsNone(self.story.view()['active'])
        self.assertFalse(self.story.matches(snapshot))
        with self.assertRaises(StoryConflict):
            self.story.append_exchange(snapshot,'迟到的讨论',{'text':'旧回复','emotion':'neutral','evidence_ids':[]})
        with self.story.connection() as db:
            for table in ('games','runs','branches','events','imports','bookmarks','story_turns','progress_checks'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(len(self.history.read(self.uid)),2)

    def test_new_text_or_new_discussion_rejects_stale_plan_without_epoch_change(self):
        token = self.plan('delete_run', self.run)
        old = self.story.epoch()
        self.ingest('旁白：雨停后，他们来到另一间教室。')
        self.assertEqual(old,self.story.epoch())
        with self.assertRaises(StoryConflict):self.memory.execute(token)
        token = self.plan('clear_discussion', self.run)
        self.add_turn()
        with self.assertRaises(StoryConflict):self.memory.execute(token)
        self.assertEqual(self.memory.inventory()['runs'][0]['story_turns'],3)

    def test_full_backup_and_additive_recovery_preserve_scopes_citations_and_restart(self):
        (self.root / '.yuki.local.json').write_text('PRIVATE_CONFIG_NOT_IN_BACKUP')
        self.ingest('美咲：A路线的红信留在钟楼。')
        self.story.restore(self.point,self.story.epoch())
        self.ingest('小春：B路线去图书室找伞。')
        self.add_turn()
        original = self.story.view()['active']
        backup = self.backup()
        payload_path = self.memory.directory / backup['id'] / 'memory.json'
        self.assertNotIn('PRIVATE_CONFIG_NOT_IN_BACKUP',payload_path.read_text(encoding='utf-8'))
        self.ingest('旁白：备份以后新读到的句子仍然保留。')
        epoch = self.story.epoch()
        result = self.memory.execute(self.plan('restore_backup',backup['id']))
        restored = self.story.view()['active']
        self.assertNotEqual(restored['id'],self.run)
        self.assertEqual(restored['position'],original['position'])
        self.assertGreater(self.story.epoch(),epoch)
        self.assertEqual(len(restored['branches']),2)
        self.assertNotIn('红信',json.dumps(restored['memory'],ensure_ascii=False))
        self.assertIn('图书室',json.dumps(restored['messages'],ensure_ascii=False))
        self.assertEqual(len(result['restored_histories']),1)
        self.assertEqual(self.history.read(result['restored_histories'][0]),self.history.read(self.uid))
        reopened = StoryStore(self.story.path)
        self.assertEqual(reopened.view()['active']['id'],restored['id'])
        evidence = restored['messages'][-1]['evidence']
        self.assertTrue(evidence)
        self.assertTrue(all(item['run_id']==restored['id'] for item in evidence))
        self.story.restore(restored['bookmarks'][0]['id'],self.story.epoch())
        self.assertNotIn('找伞',json.dumps(self.story.snapshot('小春',self.story.epoch()).__dict__,ensure_ascii=False))
        self.story.activate(self.run,self.story.epoch())
        self.assertIn('备份以后',self.story.view()['active']['events'][-1]['text'])

    def test_corrupted_and_forged_backup_rejected_before_any_restore(self):
        backup = self.backup()
        path = self.memory.directory / backup['id'] / 'memory.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.plan('restore_backup',backup['id'])
        backup = self.backup()
        self.rewrite(backup,lambda data:data['tables']['branches'][0].update(parent_id=data['tables']['branches'][0]['id']))
        with self.assertRaises(ValueError):self.plan('restore_backup',backup['id'])
        backup = self.backup()
        self.rewrite(backup,lambda data:data['tables']['story_turns'][0].update(evidence_ids='[999999]'))
        with self.assertRaises(ValueError):self.plan('restore_backup',backup['id'])
        self.assertEqual(len(self.story.view()['runs']),1)
        self.assertEqual(len(self.history.inventory()),1)

    def test_recovery_failure_rolls_back_database_and_new_histories(self):
        second = self.history.create()
        backup = self.backup()
        original_create = self.history.create
        calls = []
        def fail_second():
            calls.append(1)
            if len(calls)==2:raise OSError('fixture disk full')
            return original_create()
        token = self.plan('restore_backup',backup['id'])
        with patch.object(self.history,'create',side_effect=fail_second):
            with self.assertRaises(OSError):self.memory.execute(token)
        self.assertEqual(len(self.story.view()['runs']),1)
        self.assertEqual({item['id'] for item in self.history.inventory()},{self.uid,second})
        self.assertEqual(self.story.view()['active']['id'],self.run)

    def test_history_and_backup_deletion_are_scoped_and_tokens_single_use(self):
        backup = self.backup()
        token = self.plan('delete_history',self.uid)
        self.history.append_exchange(self.uid,'新增的话','会留下','neutral')
        with self.assertRaises(ValueError):self.memory.execute(token)
        token = self.plan('delete_history',self.uid)
        self.memory.execute(token)
        self.assertFalse(self.history.path(self.uid).exists())
        with self.assertRaises(StoryConflict):self.memory.execute(token)
        self.assertTrue(self.story.view()['active'])
        token = self.plan('delete_backup',backup['id'])
        self.memory.execute(token)
        self.assertFalse((self.memory.directory/backup['id']).exists())
        self.assertEqual(self.memory.backups(),[])

    def test_compaction_keeps_records_and_expired_plan_does_not_delete(self):
        original = self.story.view()
        self.story.compact()
        self.assertEqual(self.story.view(),original)
        token = self.plan('delete_run',self.run)
        self.memory.plans[token]['expires']=0
        with self.assertRaises(StoryConflict):self.memory.execute(token)
        self.assertEqual(self.story.view(),original)

    def test_novel_and_empty_snapshots_can_be_recovered(self):
        self.story.create_run('自编小说','第一遍',self.story.epoch(),'novel')
        self.ingest('第一章\n小春说道：“我们看见清晨的雨了。”')
        backup=self.backup()
        self.memory.execute(self.plan('restore_backup',backup['id']))
        self.assertEqual(self.story.view()['active']['mode'],'novel')
        self.assertEqual(self.story.view()['active']['position'],2)
        empty=StoryStore(self.root/'empty'/'story.sqlite3')
        manager=MemoryService(empty,HistoryStore(self.root/'empty-history'))
        backup=manager.create_backup(empty.epoch())
        token=manager.plan('restore_backup',backup['id'],empty.epoch())['token']
        manager.execute(token)
        self.assertEqual(empty.view()['runs'],[])
        self.assertIsNone(empty.view()['active'])

    def test_shared_work_survives_deleting_one_run(self):
        self.story.create_run('自编雨天故事','第二周目',self.story.epoch())
        second=self.story.view()['active']['id']
        self.ingest('旁白：同一部作品的另一篇共读。')
        self.memory.execute(self.plan('delete_run',self.run))
        with self.story.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM games').fetchone()[0],1)
        self.assertEqual(self.story.view()['active']['id'],second)

    def test_restore_rejects_cross_branch_citation_and_post_preview_corruption(self):
        self.ingest('美咲：A路线有一封信。')
        self.story.restore(self.point,self.story.epoch())
        self.ingest('小春：B路线在图书室。')
        self.add_turn()
        backup=self.backup()
        def cross_branch(data):
            event=next(row for row in data['tables']['events'] if 'A路线' in row['text'])
            data['tables']['story_turns'][-1]['evidence_ids']=json.dumps([event['id']])
        self.rewrite(backup,cross_branch)
        with self.assertRaises(ValueError):self.plan('restore_backup',backup['id'])
        backup=self.backup()
        token=self.plan('restore_backup',backup['id'])
        path=self.memory.directory/backup['id']/'memory.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.memory.execute(token)
        self.assertEqual(len(self.story.view()['runs']),1)

    def test_broken_history_can_be_removed_and_incomplete_backup_counts_disk_space(self):
        self.history.path(self.uid).write_text('{broken',encoding='utf-8')
        self.assertFalse(self.memory.inventory()['histories'][0]['readable'])
        with self.assertRaises(ValueError):self.backup()
        self.memory.execute(self.plan('delete_history',self.uid))
        backup=self.backup()
        folder=self.memory.directory/backup['id']
        (folder/'manifest.json').replace(folder/'manifest.tmp')
        item=self.memory.inventory()['backups'][0]
        self.assertFalse(item['readable'])
        self.assertGreater(item['bytes'],backup['bytes'])
        self.memory.execute(self.plan('delete_backup',backup['id']))
        self.assertFalse(folder.exists())

    def test_failed_history_write_preserves_original_file(self):
        before=self.history.path(self.uid).read_bytes()
        with patch.object(Path,'replace',side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError):self.history.append_exchange(self.uid,'新增的话','会留下','neutral')
        self.assertEqual(self.history.path(self.uid).read_bytes(),before)
        self.assertFalse(self.history.path(self.uid).with_suffix('.tmp').exists())


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.config=ConfigStore(self.root/'settings.json',env={})
        self.app=create_app(history_directory=self.root/'history',config=self.config,allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
        self.client=TestClient(self.app)
        self.story=self.app.state.story
        self.story.create_run('离线测试故事','测试',0)
        self.run=self.story.view()['active']['id']
        self.story.ingest('美咲：今晚一起听雨吧。','auto','','fixture-0001',self.story.epoch(),self.run)

    def tearDown(self):self.client.close();self.temp.cleanup()

    def test_api_requires_preview_explicit_confirmation_and_local_origin(self):
        payload={'action':'delete_run','id':self.run,'epoch':self.story.epoch()}
        self.assertEqual(self.client.post('/api/memory/plan',json=payload,headers={'Origin':'https://unrelated.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/memory/plan',content='{}').status_code,415)
        result=self.client.post('/api/memory/plan',json=payload)
        self.assertEqual(result.status_code,200)
        token=result.json()['token']
        self.assertEqual(self.client.post('/api/memory/confirm',json={'token':token}).status_code,422)
        self.assertEqual(self.client.post('/api/memory/confirm',json={'token':token,'confirmed':False}).status_code,422)
        self.assertTrue(self.story.view()['active'])
        result=self.client.post('/api/memory/confirm',json={'token':token,'confirmed':True})
        self.assertEqual(result.status_code,200)
        self.assertIsNone(result.json()['story']['active'])
        self.assertEqual(self.client.post('/api/memory/confirm',json={'token':token,'confirmed':True}).status_code,409)

    def test_history_open_bumps_epoch_and_rejects_path_escape(self):
        uid=self.client.post('/api/sessions',json={}).json()['session_id']
        epoch=self.story.epoch()
        result=self.client.post('/api/memory/history/open',json={'epoch':epoch,'id':uid})
        self.assertEqual(result.status_code,200)
        self.assertIsNone(result.json()['story']['active'])
        self.assertGreater(self.story.epoch(),epoch)
        epoch=self.story.epoch()
        result=self.client.post('/api/memory/history/open',json={'epoch':epoch,'id':uid})
        self.assertGreater(result.json()['story']['epoch'],epoch)
        for action in ('delete_history','restore_backup','delete_backup'):
            result=self.client.post('/api/memory/plan',json={'epoch':self.story.epoch(),'action':action,'id':'../../settings'})
            self.assertEqual(result.status_code,400)
        self.assertEqual(self.client.get('/api/memory').status_code,200)

    def test_delete_pauses_capture_and_discards_buffer(self):
        async def scenario():
            capture=self.app.state.capture
            await capture.attach(4660)
            import time
            stamp=time.monotonic()+10
            capture.line('[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 美咲：预览中的一句话。',stamp)
            capture.tick(stamp+.2)
            capture.select(capture.session,capture.status()['threads'][0]['id'])
            capture.start_recording(capture.session,self.story.epoch(),self.run)
            capture.line('[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 美咲：等待合并的新一句。',stamp+1)
            token=self.app.state.memory.plan('delete_run',self.run,self.story.epoch())['token']
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver') as client:
                result=await client.post('/api/memory/confirm',json={'token':token,'confirmed':True})
                self.assertEqual(result.status_code,200)
            self.assertFalse(capture.recording)
            self.assertIsNone(capture.pending)
            capture.tick(stamp+3)
            self.assertEqual(self.story.view()['runs'],[])
            await capture.detach()
        asyncio.run(scenario())

    def test_late_provider_reply_cannot_resurrect_deleted_memory(self):
        async def scenario():
            started=asyncio.Event()
            class SlowChat:
                async def complete_story(self,text,snapshot):
                    started.set()
                    await asyncio.Event().wait()
            self.app.state.chat=SlowChat()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver') as client:
                task=asyncio.create_task(client.post('/api/chat',json={'text':'陪我看雨','session_id':'story','story_epoch':self.story.epoch()}))
                await asyncio.wait_for(started.wait(),3)
                token=(await client.post('/api/memory/plan',json={'action':'delete_run','id':self.run,'epoch':self.story.epoch()})).json()['token']
                result=await client.post('/api/memory/confirm',json={'token':token,'confirmed':True})
                self.assertEqual(result.status_code,200)
                self.assertEqual((await asyncio.wait_for(task,3)).status_code,409)
                self.assertEqual(self.story.view()['runs'],[])
        asyncio.run(scenario())

    def test_late_ordinary_chat_cannot_recreate_deleted_file(self):
        async def scenario():
            self.story.activate(None,self.story.epoch())
            uid=self.app.state.store.create()
            started=asyncio.Event()
            class SlowChat:
                async def complete(self,text,messages):
                    started.set()
                    await asyncio.Event().wait()
            self.app.state.chat=SlowChat()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver') as client:
                task=asyncio.create_task(client.post('/api/chat',json={'text':'一起看雨','session_id':uid,'story_epoch':self.story.epoch()}))
                await asyncio.wait_for(started.wait(),3)
                token=(await client.post('/api/memory/plan',json={'action':'delete_history','id':uid,'epoch':self.story.epoch()})).json()['token']
                self.assertEqual((await client.post('/api/memory/confirm',json={'token':token,'confirmed':True})).status_code,200)
                self.assertEqual((await asyncio.wait_for(task,3)).status_code,409)
                self.assertFalse(self.app.state.store.path(uid).exists())
        asyncio.run(scenario())


if __name__=='__main__':unittest.main()
