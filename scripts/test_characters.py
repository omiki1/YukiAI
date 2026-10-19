"""Character correction acceptance with invented passages and offline providers only."""
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
from scripts.test_capture import CaptureFixtureRuntime

TEXT='旁白：美咲和小春在走廊等雨停，美咲被大家叫作班长。\n班长：我没有去过旧校舍。\n小春：我刚才听见了风铃。'


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=StoryStore(self.root/'story.sqlite3')
        self.store.create_run('自编风铃故事','初见',0);self.run=self.store.view()['active']['id'];self.ingest(TEXT)
        self.parent=self.store.view()['active']['branch_id']
    def tearDown(self):self.temp.cleanup()
    def ingest(self,text):self.store.ingest(text,'auto','',uuid4().hex,self.store.epoch(),self.run)
    def args(self,kind='alias',label='班长',target='美咲',rule=None):
        state=self.store.view();return (self.run,state['active']['position'],kind,label,target,state['epoch'],rule['correction_id'] if rule else None,rule['id'] if rule else None,state['active']['note_revision'])
    def save(self,kind='alias',label='班长',target='美咲',rule=None):
        self.store.save_character(*self.args(kind,label,target,rule));return self.store.character_state()['corrections'][0]
    def manager(self):return MemoryService(self.store,HistoryStore(self.root/'history'))
    def edit_backup(self,manager,backup,edit):
        folder=manager.directory/backup['id'];payload=json.loads((folder/'memory.json').read_bytes());edit(payload)
        content=encoded(payload);(folder/'memory.json').write_bytes(content);manifest=json.loads((folder/'manifest.json').read_bytes())
        manifest.update(bytes=len(content),sha256=hashlib.sha256(content).hexdigest());(folder/'manifest.json').write_bytes(encoded(manifest))

    def test_alias_preview_and_lookup_keep_original_speaker_and_text(self):
        before=self.store.view()['active']['events'];preview=self.store.preview_character(*self.args())
        self.assertTrue(preview['evidence']);self.assertEqual(self.store.character_state()['count'],0)
        self.save();self.assertEqual(before,self.store.view()['active']['events'])
        snapshot=self.store.snapshot('美咲之前说过什么',self.store.epoch());person=next(person for person in snapshot.characters if person['name']=='美咲')
        self.assertIn('班长',person['aliases']);self.assertTrue(person['claims'])
        self.assertTrue(any(event['speaker']=='班长' for event in snapshot.events))
        self.assertIn('班长',person['user_aliases'])
        self.assertEqual(self.store.character_state(),StoryStore(self.store.path).character_state())

    def test_pronoun_overrides_current_line_expires_on_advance_and_can_recall_explicit_line(self):
        rule=self.save('pronoun','她','美咲')
        snap=self.store.snapshot('她为什么这么说',self.store.epoch());self.assertEqual((snap.referent,snap.referent_source),('美咲','player'))
        self.ingest('小春：雨停以后我们就回教室吧。')
        snap=self.store.snapshot('她为什么这么说',self.store.epoch());self.assertNotEqual(snap.referent_source,'player')
        snap=self.store.snapshot('第3句这里她指谁',self.store.epoch());self.assertEqual((snap.referent,snap.referent_source),('美咲','player'))
        self.assertFalse(next(item for item in self.store.character_state()['corrections'] if item['correction_id']==rule['correction_id'])['active'])

    def test_plural_other_and_named_question_do_not_use_singular_override(self):
        self.save('pronoun','他','美咲')
        for question in ('其他人怎么想','他们为什么等雨停','小春说过什么'):
            self.assertNotEqual(self.store.snapshot(question,self.store.epoch()).referent_source,'player')
        self.assertEqual(self.store.snapshot('他们为什么等雨停',self.store.epoch()).referent,'')

    def test_multiple_pronouns_keep_separate_targets(self):
        self.save('pronoun','她','美咲');self.save('pronoun','他','小春')
        snap=self.store.snapshot('她和他各自怎么想',self.store.epoch());context=pack_story_context(snap)
        refs={row['label']:row['target'] for row in context['reading']['character_corrections']}
        self.assertEqual(refs,{'她':'美咲','他':'小春'});self.assertEqual(snap.referent,'')

    def test_unknown_future_names_and_aliases_are_rejected(self):
        for label,target in (('校长','美咲'),('班长','尚未出现的人物'),('<script>','美咲')):
            with self.assertRaises(ValueError):self.save('alias',label,target)
        self.ingest('旁白：A路线才出现的人物名叫小雪。');self.store.progress(3,self.store.epoch())
        with self.assertRaises(ValueError):self.save('pronoun','她','小雪')

    def test_alias_chains_cycles_and_duplicate_keys_are_rejected(self):
        self.save()
        with self.assertRaises(ValueError):self.save('alias','美咲','小春')
        with self.assertRaises(ValueError):self.save('alias','小春','班长')
        with self.assertRaises(StoryConflict):self.save()

    def test_stale_progress_revision_and_edit_version_rejected(self):
        stale=self.args();rule=self.save()
        with self.assertRaises(StoryConflict):self.store.save_character(*stale)
        stale=self.args('alias','班长','小春',rule);self.save('alias','班长','小春',rule)
        with self.assertRaises(StoryConflict):self.store.save_character(*stale)
        stale=self.args('pronoun','她','美咲');self.ingest('旁白：又一阵风吹进走廊。')
        with self.assertRaises(StoryConflict):self.store.save_character(*stale)

    def test_later_edit_rewinds_to_earlier_target(self):
        old=self.save();self.ingest('旁白：两人走到窗边。');self.save('alias','班长','小春',old)
        self.assertEqual(self.store.character_state()['corrections'][0]['target'],'小春')
        self.store.progress(3,self.store.epoch());self.assertEqual(self.store.character_state()['corrections'][0]['target'],'美咲')

    def test_fork_freezes_parent_versions_at_same_position(self):
        original=self.save();self.store.fork('B',self.store.epoch());child=self.store.view()['active']['branch_id']
        self.store.select_branch(self.parent,self.store.epoch());self.save('alias','班长','小春',original)
        self.store.select_branch(child,self.store.epoch());self.assertEqual(self.store.character_state()['corrections'][0]['target'],'美咲')
        self.store.fork('B-1',self.store.epoch());grandchild=self.store.view()['active']['branch_id']
        self.store.select_branch(child,self.store.epoch());self.save('alias','班长','小春',original)
        self.store.select_branch(grandchild,self.store.epoch());self.assertEqual(self.store.character_state()['corrections'][0]['target'],'美咲')

    def test_delete_all_versions_keeps_original_and_invalidates_snapshot(self):
        original=self.save();snapshot=self.store.snapshot('班长说什么',self.store.epoch())
        self.ingest('旁白：风铃停了下来。');latest=self.save('alias','班长','小春',original)
        self.store.delete_character(self.run,latest['correction_id'],latest['id'],self.store.epoch())
        self.assertEqual(self.store.character_state()['count'],0);self.assertFalse(self.store.matches(snapshot))
        self.store.progress(3,self.store.epoch());self.assertEqual(self.store.character_state()['count'],0)
        self.assertEqual(len(self.store.view()['active']['events']),3)

    def test_backup_v3_rewrites_references_and_fork_cutoffs(self):
        original=self.save();self.save('pronoun','她','美咲');self.store.fork('B',self.store.epoch());child=self.store.view()['active']['branch_id']
        self.store.select_branch(self.parent,self.store.epoch());self.save('alias','班长','小春',original);self.store.select_branch(child,self.store.epoch())
        manager=self.manager();backup=manager.create_backup(self.store.epoch());self.assertEqual((backup['version'],backup['counts']['corrections']),(3,2))
        manager.execute(manager.plan('restore_backup',backup['id'],self.store.epoch())['token']);data=self.store.character_state()
        self.assertNotEqual(data['run_id'],self.run);alias=next(row for row in data['corrections'] if row['kind']=='alias')
        self.assertEqual(alias['target'],'美咲');self.assertNotEqual(alias['correction_id'],original['correction_id'])
        self.assertNotEqual(alias['anchor_event_id'],original['anchor_event_id'])
        self.assertEqual(self.store.snapshot('她为什么这样说',self.store.epoch()).referent,'美咲')

    def test_v2_backup_recovery_retains_notes_without_character_records(self):
        self.store.save_note(self.run,'班长的话还值得再想想。','question','open',[1],self.store.epoch())
        manager=self.manager();backup=manager.create_backup(self.store.epoch());folder=manager.directory/backup['id']
        payload=json.loads((folder/'memory.json').read_bytes());payload['version']=2;del payload['tables']['character_corrections']
        for branch in payload['tables']['branches']:del branch['character_cutoff']
        content=encoded(payload);(folder/'memory.json').write_bytes(content);manifest=json.loads((folder/'manifest.json').read_bytes())
        manifest.update(version=2,bytes=len(content),sha256=hashlib.sha256(content).hexdigest());del manifest['counts']['corrections']
        (folder/'manifest.json').write_bytes(encoded(manifest));manager.execute(manager.plan('restore_backup',backup['id'],self.store.epoch())['token'])
        self.assertEqual(self.store.character_state()['count'],0);self.assertEqual(self.store.view()['active']['position'],3)
        self.assertEqual(self.store.continuity()['notes'][0]['text'],'班长的话还值得再想想。')

    def test_many_aliases_paginate_without_dropping_or_repeating_records(self):
        labels=[f'称呼{index:02}' for index in range(52)]
        self.ingest('旁白：美咲的称呼包括'+ '、'.join(labels)+'。')
        for label in labels:self.save('alias',label,'美咲')
        first=self.store.character_state();second=self.store.character_state(first['more'])
        self.assertEqual(first['count'],52);self.assertEqual(len(first['corrections']),50)
        self.assertEqual(len(second['corrections']),2);self.assertIsNone(second['more'])
        self.assertEqual({row['label'] for page in (first,second) for row in page['corrections']},set(labels))
        card=next(person for person in self.store.snapshot('称呼00说过什么',self.store.epoch()).characters if person['name']=='美咲')
        self.assertLessEqual(len(card['aliases']),3);self.assertLessEqual(len(card['user_aliases']),3)
        self.assertIn('称呼00',card['user_aliases'])

    def test_expired_pronoun_does_not_change_automatic_character_index(self):
        self.store.create_run('自编双人叙述','初见',self.store.epoch());self.run=self.store.view()['active']['id']
        self.ingest('旁白：美咲与小春并肩站着。')
        self.save('pronoun','她','美咲');self.ingest('旁白：小春望向窗外。')
        self.assertNotIn('美咲',[person['name'] for person in self.store.snapshot('她在做什么',self.store.epoch()).characters])
        self.assertEqual(self.store.snapshot('第1句的她是谁',self.store.epoch()).referent_source,'player')

    def test_corrupt_backup_links_cannot_import_future_source(self):
        self.save();self.ingest('旁白：较晚才读到小雪的名字。');manager=self.manager();backup=manager.create_backup(self.store.epoch())
        self.edit_backup(manager,backup,lambda data:data['tables']['character_corrections'][0].update(evidence_ids=json.dumps([data['tables']['events'][-1]['id']])))
        with self.assertRaises(ValueError):manager.plan('restore_backup',backup['id'],self.store.epoch())
        self.assertEqual(len(self.store.view()['runs']),1)

    def test_clear_chat_retains_corrections_and_delete_run_removes_them(self):
        self.save();manager=self.manager();token=manager.plan('clear_discussion',self.run,self.store.epoch())['token'];manager.execute(token)
        self.assertEqual(self.store.character_state()['count'],1)
        manager.execute(manager.plan('delete_run',self.run,self.store.epoch())['token'])
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM character_corrections').fetchone()[0],0)

    def test_model_payload_does_not_promote_mapping_to_plot_evidence(self):
        self.save();self.save('pronoun','她','美咲');snapshot=self.store.snapshot('她之前说了什么',self.store.epoch());context=pack_story_context(snapshot)
        self.assertEqual(context['reading']['referent_source'],'player')
        loaded={event['id'] for event in snapshot.events}
        for rule in context['reading']['character_corrections']:
            self.assertTrue(set(rule['linked_event_ids']).issubset(loaded));self.assertNotIn('correction_id',rule)
        self.assertEqual(next(rule for rule in context['reading']['character_corrections'] if rule['kind']=='pronoun')['scope'],'line')

    def test_other_reading_cannot_reuse_correction(self):
        original=self.save();self.store.create_run('另一部自编故事','初见',self.store.epoch())
        self.assertEqual(self.store.character_state()['count'],0)
        with self.assertRaises(StoryConflict):self.store.delete_character(self.run,original['correction_id'],original['id'],self.store.epoch())


class CharacterApiTests(unittest.TestCase):
    def setUp(self):
        from scripts.yuki_server import create_app
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.app=create_app(history_directory=root/'history',config=ConfigStore(root/'settings.json',env={}),allow_cloud=False,capture_runtime=CaptureFixtureRuntime())
        self.client=TestClient(self.app);self.store=self.app.state.story;self.store.create_run('自编风铃故事','初见',0)
        self.run=self.store.view()['active']['id'];self.store.ingest(TEXT,'auto','',uuid4().hex,self.store.epoch(),self.run)
    def tearDown(self):self.client.close();self.temp.cleanup()
    def data(self):
        view=self.store.view();return {'epoch':view['epoch'],'run_id':self.run,'position':view['active']['position'],'revision':view['active']['note_revision'],'kind':'pronoun','label':'她','target':'美咲'}

    def test_preview_and_save_require_local_origin_and_confirmation(self):
        data=self.data();self.assertEqual(self.client.post('/api/story/characters/preview',json=data).status_code,200)
        self.assertEqual(self.store.character_state()['count'],0)
        self.assertEqual(self.client.post('/api/story/characters/save',json=data).status_code,422)
        self.assertEqual(self.client.post('/api/story/characters/save',json={**data,'confirmed':True},headers={'Origin':'https://unrelated.invalid'}).status_code,403)
        self.assertEqual(self.client.post('/api/story/characters/save',json={**data,'confirmed':True}).status_code,200)
        self.assertEqual(self.client.get('/api/story/characters').json()['count'],1)

    def test_correction_cancels_pending_reply_while_capture_continues(self):
        async def scenario():
            started=asyncio.Event()
            class Slow:
                async def complete_story(self,text,snapshot):started.set();await asyncio.Event().wait()
            self.app.state.chat=Slow();capture=self.app.state.capture;await capture.attach(4660)
            import time
            stamp=time.monotonic()+10;capture.line('[2:1234:123ABC:0:0:TextOutW:HQ@123ABC] 小春：又听见风铃了。',stamp);capture.tick(stamp+.2)
            capture.select(capture.session,capture.status()['threads'][0]['id']);capture.start_recording(capture.session,self.store.epoch(),self.run)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://testserver') as client:
                task=asyncio.create_task(client.post('/api/chat',json={'text':'她说什么','session_id':'story','story_epoch':self.store.epoch()}));await asyncio.wait_for(started.wait(),3)
                self.assertEqual((await client.post('/api/story/characters/save',json={**self.data(),'confirmed':True})).status_code,200)
                self.assertEqual((await asyncio.wait_for(task,3)).status_code,409);self.assertTrue(capture.recording)
            await capture.detach()
        asyncio.run(scenario())


if __name__=='__main__':unittest.main()
