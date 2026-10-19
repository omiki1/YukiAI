"""Offline continuity acceptance. All characters and passages below are invented."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from scripts.yuki_config import ConfigStore
from scripts.yuki_history import HistoryStore
from scripts.yuki_memory import MemoryService, encoded
from scripts.yuki_story import StoryStore, StoryConflict, pack_story_context
from scripts.yuki_chat import TextChat, STORY_SYSTEM
from scripts.test_capture import CaptureFixtureRuntime


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=StoryStore(self.root/'story.sqlite3')
        self.store.create_run('自编纸鹤故事','初见',0)
        self.run=self.store.view()['active']['id']
        self.ingest('旁白：黄昏的走廊里有一只纸鹤。\n美咲：我没有去过旧校舍。')
        self.parent=self.store.view()['active']['branch_id']
        self.source=self.store.view()['active']['events'][-1]['id']

    def tearDown(self):self.temp.cleanup()
    def ingest(self,text):self.store.ingest(text,'auto','',uuid4().hex,self.store.epoch(),self.run)
    def note(self,text='她为何避开旧校舍？',kind='question',status='open',note=None,ids=None):
        self.store.save_note(self.run,text,kind,status,[self.source] if ids is None else ids,self.store.epoch(),
            note['note_id'] if note else None,note['id'] if note else None)
        return self.store.continuity()['notes'][0]
    def turn(self,text='她为什么说没去过旧校舍？'):
        snap=self.store.snapshot(text,self.store.epoch())
        self.store.append_exchange(snap,text,{'text':'这是她自己的说法，还不能据此确定。','emotion':'neutral',
            'answer_type':'analysis','evidence_ids':[self.source]})
        return self.store.continuity()['recap']['topics'][0]['turn_id']

    def test_recap_persists_and_keeps_claim_labels_and_original_evidence(self):
        self.turn();self.note();before=self.store.continuity()
        after=StoryStore(self.store.path).continuity()
        self.assertEqual(before,after)
        self.assertEqual(after['recap']['position'],2)
        line=after['recap']['excerpts'][-1]
        self.assertEqual((line['id'],line['kind'],line['speaker']),(self.source,'dialogue','美咲'))
        self.assertIn('自己的说法',self.store.view()['active']['messages'][-1]['content'])
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reading_recaps').fetchone()[0],1)

    def test_rewind_hides_future_notes_topics_and_cached_passages(self):
        early=self.note();self.ingest('旁白：A路线出现蓝色信封。')
        self.note('蓝色信封是谁写的？');self.turn('蓝色信封里写着什么？')
        self.assertIn('蓝色信封',json.dumps(self.store.continuity(),ensure_ascii=False))
        self.store.progress(2,self.store.epoch());recap=self.store.continuity()
        self.assertNotIn('蓝色信封',json.dumps(recap,ensure_ascii=False))
        self.assertEqual(recap['notes'][0]['note_id'],early['note_id'])
        self.assertNotIn('蓝色信封',json.dumps(pack_story_context(self.store.snapshot('之前留了什么疑问',self.store.epoch())),ensure_ascii=False))

    def test_note_edits_and_resolution_have_progress_scoped_versions(self):
        original=self.note('旧校舍还有谁去过？');self.ingest('旁白：纸鹤停在窗台。')
        edited=self.note('想先问小春有没有见过纸鹤。','theory','resolved',original)
        self.assertEqual(edited['note_id'],original['note_id']);self.assertEqual(edited['status'],'resolved')
        self.store.progress(2,self.store.epoch());old=self.store.continuity()['notes'][0]
        self.assertEqual((old['text'],old['status']),('旧校舍还有谁去过？','open'))
        self.store.progress(3,self.store.epoch())
        self.assertEqual(self.store.continuity()['notes'][0]['text'],edited['text'])

    def test_parent_notes_and_discussions_added_after_fork_do_not_enter_child(self):
        original=self.note('共同段留下的问题');self.turn('共同段的讨论')
        self.store.fork('B路线',self.store.epoch());child=self.store.view()['active']['branch_id']
        self.assertEqual(self.store.continuity()['notes'][0]['text'],original['text'])
        self.store.select_branch(self.parent,self.store.epoch())
        self.note('父路线后来才改成的秘密猜测','theory','open',original);self.turn('父路线后来才聊到的秘密')
        self.store.select_branch(child,self.store.epoch());context=json.dumps(self.store.continuity(),ensure_ascii=False)
        self.assertNotIn('后来才',context);self.assertIn('共同段的讨论',context)
        self.assertNotIn('后来才',json.dumps(self.store.view()['active']['messages'],ensure_ascii=False))
        self.assertNotIn('后来才',json.dumps(self.store.snapshot('上次聊了什么',self.store.epoch()).__dict__,ensure_ascii=False))

    def test_nested_fork_keeps_earliest_cutoff_and_child_edit_is_local(self):
        original=self.note('共同段问题');self.store.fork('B',self.store.epoch())
        child=self.store.view()['active']['branch_id'];edited=self.note('B路线自己的想法','theory',note=original)
        self.store.fork('B-1',self.store.epoch());grandchild=self.store.view()['active']['branch_id']
        self.store.select_branch(child,self.store.epoch());self.note('分支之后才有的新说法','theory',note=edited)
        self.store.select_branch(grandchild,self.store.epoch())
        self.assertEqual(self.store.continuity()['notes'][0]['text'],'B路线自己的想法')
        self.store.select_branch(self.parent,self.store.epoch())
        self.assertEqual(self.store.continuity()['notes'][0]['text'],'共同段问题')

    def test_pin_question_is_idempotent_and_new_chat_keeps_pinned_thoughts(self):
        turn=self.turn()
        for _ in range(2):self.store.save_note(self.run,'','question','open',[],self.store.epoch(),turn_id=turn)
        data=self.store.continuity();self.assertEqual(data['note_count'],1)
        self.assertEqual(data['notes'][0]['origin_turn_id'],turn)
        self.store.new_chat(self.store.epoch());data=self.store.continuity()
        self.assertEqual(data['recap']['topics'],[]);self.assertEqual(data['note_count'],1)
        self.assertIn('为什么',data['notes'][0]['text'])

    def test_invalid_citations_other_readings_and_stale_versions_rejected(self):
        note=self.note();self.note('修改后的问题',note=note)
        with self.assertRaises(StoryConflict):self.note('迟到的修改',note=note)
        with self.assertRaises(StoryConflict):self.note(ids=[999999])
        with self.assertRaises(ValueError):self.note(ids=[True])
        self.store.create_run('另一部故事','初见',self.store.epoch())
        with self.assertRaises(StoryConflict):self.note()

    def test_delete_removes_all_versions_and_cache_without_deleting_source(self):
        note=self.note();self.ingest('旁白：风吹过窗边。');note=self.note('第二次写下的想法','feeling',note=note)
        self.store.delete_note(self.run,note['note_id'],note['id'],self.store.epoch())
        self.assertEqual(self.store.continuity()['notes'],[]);self.store.progress(2,self.store.epoch())
        self.assertEqual(self.store.continuity()['notes'],[]);self.assertEqual(len(self.store.view()['active']['events']),2)
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reading_notes').fetchone()[0],0)

    def test_model_receives_labeled_notes_and_only_opened_source_ids(self):
        self.note('我猜她在保护小春，暂时只是一个猜测。','theory','resolved')
        snapshot=self.store.snapshot('上次的手记里我们猜了什么',self.store.epoch());context=pack_story_context(snapshot)
        self.assertEqual(context['player_notes'][0]['kind'],'theory');self.assertEqual(context['player_notes'][0]['status'],'resolved')
        self.assertIn('不是剧情事实',STORY_SYSTEM)
        self.assertTrue(set(context['player_notes'][0]['linked_event_ids']).issubset({event['id'] for event in snapshot.events}))
        chat=object.__new__(TextChat)
        raw=json.dumps({'text':'猜测已经成真。','emotion':'neutral','answer_type':'analysis','evidence_ids':[snapshot.notes[0]['note_id']]})
        with self.assertRaises(ValueError):chat.read_story_reply(raw,snapshot,'')

    def test_note_change_invalidates_reply_without_changing_capture_epoch(self):
        snapshot=self.store.snapshot('她的说法可信吗',self.store.epoch());old_epoch=self.store.epoch();self.note()
        self.assertEqual(self.store.epoch(),old_epoch);self.assertFalse(self.store.matches(snapshot))
        with self.assertRaises(StoryConflict):self.store.append_exchange(snapshot,'迟到的问题',{'text':'迟到回复','emotion':'neutral','evidence_ids':[]})

    def test_old_linked_source_is_opened_for_note_question_with_bounded_context(self):
        self.note('她为何否认去过旧校舍？')
        self.ingest('\n'.join(f'旁白：第{i}段只是看着操场上空的云。' for i in range(3,84)))
        snapshot=self.store.snapshot('我们的手记还留了哪些疑问',self.store.epoch())
        self.assertIn(self.source,{event['id'] for event in snapshot.events})
        self.assertEqual(snapshot.position,83)
        context=pack_story_context(snapshot,'我们的手记还留了哪些疑问')
        self.assertLessEqual(len(context['player_notes']),4)
        self.assertLessEqual(sum(len(note['text']) for note in context['player_notes']),800)

    def test_note_pagination_and_corrupt_recap_rebuild(self):
        for number in range(52):self.note(f'第{number}条留给明天的疑问')
        first=self.store.continuity();second=self.store.continuity(first['more_notes'])
        self.assertEqual((len(first['notes']),len(second['notes'])),(50,2))
        self.assertEqual(first['note_count'],52)
        self.assertFalse({note['note_id'] for note in first['notes']} & {note['note_id'] for note in second['notes']})
        with self.store.connection(True) as db:db.execute("UPDATE reading_recaps SET content='{broken'")
        self.assertEqual(self.store.continuity()['recap']['position'],2)

    def test_cache_reads_do_not_invalidate_a_cleanup_preview(self):
        manager=MemoryService(self.store,HistoryStore(self.root/'history'))
        token=manager.plan('delete_run',self.run,self.store.epoch())['token']
        self.store.continuity()
        manager.execute(token)
        self.assertIsNone(self.store.view()['active'])

    def test_backups_restore_note_versions_sources_and_fork_cutoffs(self):
        self.turn();original=self.note();self.store.fork('B',self.store.epoch());child=self.store.view()['active']['branch_id']
        self.store.select_branch(self.parent,self.store.epoch());self.note('父路线后来的猜测','theory',note=original);self.turn('父路线后来才聊的秘密')
        self.store.select_branch(child,self.store.epoch());manager=MemoryService(self.store,HistoryStore(self.root/'history'))
        backup=manager.create_backup(self.store.epoch());self.assertEqual(backup['version'],2);self.assertEqual(backup['counts']['notes'],1)
        manager.execute(manager.plan('restore_backup',backup['id'],self.store.epoch())['token']);restored=self.store.continuity()
        self.assertNotEqual(restored['run_id'],self.run);self.assertNotIn('后来',json.dumps(restored,ensure_ascii=False))
        self.assertNotEqual(restored['notes'][0]['note_id'],original['note_id']);self.assertNotEqual(restored['notes'][0]['evidence_ids'][0],self.source)

    def test_clear_discussions_preserves_pinned_notes_but_full_deletion_removes_them(self):
        turn=self.turn();self.store.save_note(self.run,'','question','open',[],self.store.epoch(),turn_id=turn)
        manager=MemoryService(self.store,HistoryStore(self.root/'history'))
        manager.execute(manager.plan('clear_discussion',self.run,self.store.epoch())['token']);data=self.store.continuity()
        self.assertEqual(data['note_count'],1);self.assertIsNone(data['notes'][0]['origin_turn_id']);self.assertEqual(data['recap']['topics'],[])
        manager.execute(manager.plan('delete_run',self.run,self.store.epoch())['token'])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reading_notes').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reading_recaps').fetchone()[0],0)

    def test_legacy_schema_and_v1_backup_upgrade_without_losing_text(self):
        with self.store.connection(True) as db:
            db.execute('DROP TABLE reading_recaps');db.execute('DROP TABLE reading_notes')
            db.execute('ALTER TABLE runs DROP COLUMN note_generation')
            db.execute('ALTER TABLE branches DROP COLUMN note_cutoff');db.execute('ALTER TABLE branches DROP COLUMN turn_cutoff')
            db.execute('ALTER TABLE story_turns DROP COLUMN note_generation')
        self.store=StoryStore(self.store.path);self.assertEqual(self.store.view()['active']['events'][-1]['id'],self.source)
        manager=MemoryService(self.store,HistoryStore(self.root/'history'));backup=manager.create_backup(self.store.epoch());folder=manager.directory/backup['id']
        payload=json.loads((folder/'memory.json').read_bytes());payload['version']=1;del payload['tables']['reading_notes']
        for run in payload['tables']['runs']:del run['note_generation']
        for branch in payload['tables']['branches']:del branch['note_cutoff'];del branch['turn_cutoff']
        for turn in payload['tables']['story_turns']:del turn['note_generation']
        content=encoded(payload);(folder/'memory.json').write_bytes(content);manifest=json.loads((folder/'manifest.json').read_bytes())
        manifest.update(version=1,bytes=len(content),sha256=hashlib.sha256(content).hexdigest());del manifest['counts']['notes']
        (folder/'manifest.json').write_bytes(encoded(manifest));manager.execute(manager.plan('restore_backup',backup['id'],self.store.epoch())['token'])
        self.assertEqual(self.store.continuity()['notes'],[])
        self.assertEqual(self.store.view()['active']['events'][-1]['raw_text'],'美咲：我没有去过旧校舍。')

    def test_backup_rejects_note_link_to_a_later_reading_position(self):
        self.note();self.ingest('旁白：较晚读到的红色信封。')
        manager=MemoryService(self.store,HistoryStore(self.root/'history'));backup=manager.create_backup(self.store.epoch())
        folder=manager.directory/backup['id'];payload=json.loads((folder/'memory.json').read_bytes())
        payload['tables']['reading_notes'][0]['evidence_ids']=json.dumps([payload['tables']['events'][-1]['id']])
        content=encoded(payload);(folder/'memory.json').write_bytes(content)
        manifest=json.loads((folder/'manifest.json').read_bytes());manifest.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest())
        (folder/'manifest.json').write_bytes(encoded(manifest))
        with self.assertRaises(ValueError):manager.plan('restore_backup',backup['id'],self.store.epoch())
        self.assertEqual(len(self.store.view()['runs']),1)


class ContinuityApiTests(unittest.TestCase):
    def setUp(self):
        from scripts.yuki_server import create_app
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.app=create_app(history_directory=root/'history',config=ConfigStore(root/'settings.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
        self.client=TestClient(self.app);self.store=self.app.state.story;self.store.create_run('自编样例','初见',0)
        self.run=self.store.view()['active']['id'];self.store.ingest('美咲：黄昏有一只纸鹤。','auto','',uuid4().hex,self.store.epoch(),self.run)
    def tearDown(self):self.client.close();self.temp.cleanup()

    def test_api_confirmation_validation_and_origin(self):
        data={'epoch':self.store.epoch(),'run_id':self.run,'text':'纸鹤是谁折的？','kind':'question'}
        self.assertEqual(self.client.post('/api/story/notes',json=data,headers={'Origin':'https://unrelated.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/story/notes',json={**data,'evidence_ids':[True]}).status_code,422)
        self.assertEqual(self.client.post('/api/story/notes',json=data).status_code,200)
        note=self.client.get('/api/story/continuity').json()['notes'][0];remove={'epoch':self.store.epoch(),'run_id':self.run,'note_id':note['note_id'],'version':note['id']}
        self.assertEqual(self.client.post('/api/story/notes/delete',json=remove).status_code,422)
        self.assertEqual(self.client.post('/api/story/notes/delete',json={**remove,'confirmed':True}).status_code,200)

    def test_note_edits_cancel_late_reply_but_keep_capture_recording(self):
        async def scenario():
            started=asyncio.Event()
            class SlowChat:
                async def complete_story(self,text,snapshot):started.set();await asyncio.Event().wait()
            self.app.state.chat=SlowChat();capture=self.app.state.capture;await capture.attach(4660)
            import time
            stamp=time.monotonic()+10;capture.line('[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 美咲：纸鹤留在窗边。',stamp);capture.tick(stamp+.2)
            capture.select(capture.session,capture.status()['threads'][0]['id']);capture.start_recording(capture.session,self.store.epoch(),self.run)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver') as client:
                task=asyncio.create_task(client.post('/api/chat',json={'text':'一起猜猜纸鹤','session_id':'story','story_epoch':self.store.epoch()}));await asyncio.wait_for(started.wait(),3)
                result=await client.post('/api/story/notes',json={'epoch':self.store.epoch(),'run_id':self.run,'text':'先留一个纸鹤的疑问'})
                self.assertEqual(result.status_code,200);self.assertEqual((await asyncio.wait_for(task,3)).status_code,409);self.assertTrue(capture.recording)
            await capture.detach()
        asyncio.run(scenario())


if __name__=='__main__':unittest.main()
