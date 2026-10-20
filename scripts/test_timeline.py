"""Offline story-card checks. All passages and characters are invented."""
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from scripts.yuki_config import ConfigStore
from scripts.yuki_history import HistoryStore
from scripts.yuki_memory import MemoryService
from scripts.yuki_server import create_app
from scripts.yuki_story import StoryStore, pack_story_context
from scripts.test_capture import CaptureFixtureRuntime


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = StoryStore(self.root/'story.sqlite3')
        self.store.create_run('自编雨伞故事', '初见', 0)
        self.run = self.store.view()['active']['id']

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, text):
        self.store.ingest(text, 'auto', '', uuid4().hex, self.store.epoch(), self.run)

    def note(self, text, ids, kind='theory', note=None):
        self.store.save_note(self.run, text, kind, 'open', ids, self.store.epoch(),
                             note['note_id'] if note else None, note['id'] if note else None)
        return self.store.continuity()['notes'][0]

    def test_types_exact_evidence_and_no_claim_or_choice_outcome_inference(self):
        self.ingest('美咲：我从没去过钟楼。\n小春：可我在那里见过你。\n选项：问问钟楼的事\n旁白：她把雨伞靠在窗边。')
        data = self.store.timeline()
        self.assertEqual([card['kind'] for card in data['cards']], ['statement', 'choice', 'event'])
        claims, choice, event = data['cards']
        self.assertEqual(claims['people'], ['美咲', '小春'])
        self.assertEqual(claims['excerpts'][0]['speaker'], '美咲')
        self.assertEqual(choice['evidence'][0]['kind'], 'choice')
        originals = self.store.view()['active']['events']
        self.assertEqual([line['raw_text'] for card in data['cards'] for line in card['evidence']],
                         [line['raw_text'] for line in originals])
        self.assertNotIn('结局', json.dumps(data, ensure_ascii=False))
        self.assertEqual(StoryStore(self.store.path).timeline(), data)

    def test_progress_rebuilds_partial_card_and_never_retains_unread_people(self):
        self.ingest('旁白：走廊上留着一把雨伞。\n美咲：这是我留下的。\n小春：隐藏后文标记。')
        before = self.store.timeline()['cards'][0]
        self.store.progress(1, self.store.epoch())
        data = self.store.timeline()
        self.assertEqual(data['cards'][0]['id'], before['id'])
        self.assertEqual(data['cards'][0]['end'], 1)
        self.assertEqual(data['people'], [])
        self.assertNotIn('隐藏后文标记', json.dumps(data, ensure_ascii=False))
        self.assertEqual(data['cards'][0]['evidence'][0]['position'], 1)

    def test_long_chapters_cover_each_line_once_in_bounded_cards(self):
        self.ingest('第一章 雨天\n'+'\n'.join('旁白：自编段落'+str(index) for index in range(46)))
        data = self.store.timeline()
        self.assertEqual(data['cards'][0]['kind'], 'chapter')
        lines = [line for card in data['cards'] for line in card['evidence']]
        self.assertEqual([line['position'] for line in lines], list(range(1, 48)))
        self.assertTrue(all(len(card['evidence']) <= 6 for card in data['cards']))
        self.assertTrue(all(len(card['excerpts']) <= 3 for card in data['cards']))

    def test_concurrent_memory_change_rejects_card_editor_even_at_same_progress(self):
        self.ingest('美咲：我们等雨停吧。')
        source = self.store.view()['active']['events'][0]['id']
        revision = self.store.timeline()['note_revision']
        self.note('另一页刚刚写下的想法', [source])
        from scripts.yuki_story import StoryConflict
        with self.assertRaises(StoryConflict):
            self.store.save_note(self.run, '过时的卡片编辑', 'question', 'open', [source], self.store.epoch(),
                                 reading_position=1, note_revision=revision)
        self.assertEqual(self.store.timeline(kind='note')['matched'], 1)

    def test_branch_isolation_includes_late_parent_note_edits(self):
        self.ingest('美咲：共同段只说雨伞。')
        parent = self.store.view()['active']['branch_id']
        source = self.store.view()['active']['events'][0]['id']
        original = self.note('共同段猜测', [source])
        self.store.fork('另一条路线', self.store.epoch())
        child = self.store.view()['active']['branch_id']
        self.ingest('小春：分支B独有标记。')
        self.store.select_branch(parent, self.store.epoch())
        self.note('分支后父线才改成的秘密', [source], note=original)
        self.ingest('美咲：分支A独有标记。')
        self.store.select_branch(child, self.store.epoch())
        content = json.dumps(self.store.timeline(), ensure_ascii=False)
        self.assertIn('分支B独有标记', content)
        self.assertIn('共同段猜测', content)
        self.assertNotIn('分支A独有标记', content)
        self.assertNotIn('分支后父线', content)

    def test_filters_search_entire_evidence_and_notes_and_pagination_has_no_gaps(self):
        for index in range(32):
            self.ingest(f'选项：第{index}个自编选择\n美咲：第{index}次等雨。')
        source = self.store.view()['active']['events'][-1]['id']
        self.note('留个纸鹤疑问', [source], 'question')
        all_cards, before = [], None
        while True:
            data = self.store.timeline(before)
            self.assertLessEqual(len(data['cards']), 24)
            all_cards = data['cards'] + all_cards
            before = data['more_before']
            if not before:
                break
        self.assertEqual(len(all_cards), 65)
        self.assertEqual(len({card['id'] for card in all_cards}), 65)
        self.assertEqual([card['order'] for card in all_cards], list(range(1, 66)))
        self.assertEqual(self.store.timeline(kind='choice')['matched'], 32)
        self.assertEqual(self.store.timeline(speaker='美咲')['matched'], 33)
        self.assertEqual(self.store.timeline(query='纸鹤')['cards'][0]['kind'], 'note')
        self.assertEqual(self.store.timeline(query='不存在的线索')['cards'], [])
        self.ingest('旁白：'+'雨'*110+'隐藏在截断后方的原词')
        self.assertEqual(self.store.timeline(query='隐藏在截断后方的原词')['matched'], 1)

    def test_annotation_uses_existing_note_memory_and_rewind_versions(self):
        self.ingest('美咲：等雨停了再走。')
        card = self.store.timeline()['cards'][0]
        ids = [line['id'] for line in card['evidence']]
        original = self.note('我猜她在等人。', ids)
        data = self.store.timeline()
        self.assertEqual([row['kind'] for row in data['cards']], ['statement', 'note'])
        self.assertEqual(data['cards'][-1]['note_kind'], 'theory')
        context = pack_story_context(self.store.snapshot('之前的疑问和手记', self.store.epoch()))
        self.assertEqual(context['player_notes'][0]['kind'], 'theory')
        self.assertEqual(context['player_notes'][0]['linked_event_ids'], ids)
        self.ingest('旁白：窗边出现纸鹤。')
        self.note('仍未证实她是否在等人。', ids, note=original)
        self.store.progress(1, self.store.epoch())
        self.assertEqual(self.store.timeline(kind='note')['cards'][0]['text'], original['text'])
        self.store.progress(2, self.store.epoch())
        self.assertIn('仍未证实', self.store.timeline(kind='note')['cards'][0]['text'])
        latest = self.store.continuity()['notes'][0]
        self.store.delete_note(self.run, latest['note_id'], latest['id'], self.store.epoch())
        self.assertEqual(self.store.timeline(kind='note')['matched'], 0)

    def test_backup_restores_cards_and_notes_without_storing_duplicate_summaries(self):
        self.ingest('旁白：图书室的门开着。\n美咲：把伞放在这里吧。')
        self.note('她为什么要留伞？', [self.store.view()['active']['events'][-1]['id']], 'question')
        before = self.store.timeline()
        service = MemoryService(self.store, HistoryStore(self.root/'history'))
        backup = service.create_backup(self.store.epoch())
        service.execute(service.plan('restore_backup', backup['id'], self.store.epoch())['token'])
        restored = self.store.timeline()
        self.assertEqual([(row['kind'], row['title']) for row in restored['cards']],
                         [(row['kind'], row['title']) for row in before['cards']])
        self.assertNotEqual(restored['run_id'], before['run_id'])
        with self.store.connection() as db:
            self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='timeline_cards'").fetchone())

    def test_empty_reading_and_inactive_scope_never_leak_previous_cards(self):
        self.assertEqual(self.store.timeline()['total'], 0)
        self.ingest('美咲：只属于这篇共读。')
        self.store.activate(None, self.store.epoch())
        self.assertEqual(self.store.timeline()['cards'], [])
        self.assertIsNone(self.store.timeline()['run_id'])
        self.store.create_run('另一个自编故事', '', self.store.epoch())
        self.assertEqual(self.store.timeline()['people'], [])

    def test_api_is_read_only_local_validated_and_rejects_stale_annotation(self):
        self.ingest('美咲：一段用于接口验证的自编对白。')
        class NeverCall:
            async def complete_story(self, *args):
                raise AssertionError('Browsing cards must never call the model')
        app = create_app(NeverCall(), self.root/'history', story=self.store,
                         config=ConfigStore(self.root/'settings.json', env={}), allow_cloud=False,
                         capture_runtime=CaptureFixtureRuntime())
        with TestClient(app) as client:
            epoch = self.store.epoch()
            revision = self.store.memory_inventory()['runs'][0]['revision']
            for url in ('/api/story/timeline', '/api/story/timeline?kind=statement&speaker=美咲&q=自编'):
                self.assertEqual(client.get(url).status_code, 200)
            self.assertEqual(self.store.memory_inventory()['runs'][0]['revision'], revision)
            for suffix in ('?kind=invalid', '?before=0', '?q='+'字'*81, '?speaker='+'字'*31):
                self.assertEqual(client.get('/api/story/timeline'+suffix).status_code, 422)
            card = client.get('/api/story/timeline').json()['cards'][0]
            self.ingest('旁白：进度已改变。')
            response = client.post('/api/story/notes', json={'epoch': epoch, 'run_id': self.run,
                'text': '迟到的卡片手记', 'reading_position': 1, 'note_revision': 0,
                'evidence_ids': [card['evidence'][0]['id']]})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(self.store.timeline(kind='note')['matched'], 0)


if __name__ == '__main__':
    unittest.main()
