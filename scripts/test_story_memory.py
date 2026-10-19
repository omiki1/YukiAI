"""Offline reading-memory acceptance checks using an invented branching story."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from scripts.yuki_chat import TextChat, ground_story_answer
from scripts.yuki_story import pack_story_context
from scripts.yuki_config import ConfigStore
from scripts.yuki_server import create_app
from scripts.yuki_story import StoryStore, StoryConflict


COMMON = '旁白：放学后，庭院里下起了雨。\n美咲：我们先在走廊等一会儿吧。'
SECRET = '美咲：只有在A路线才会发现，旧钟楼的钥匙藏在红色信封里。'
OTHER = '小春：在B路线，我们决定去图书室找雨伞。'


class StoryFixture:
    """Local answer provider; never constructs an API client or sends requests."""
    def __init__(self):
        self.calls = []

    async def complete(self, text, previous):
        return {'text': '今天的故事，我们慢慢聊。', 'emotion': 'neutral'}

    async def complete_story(self, text, snapshot):
        self.calls.append(snapshot)
        if not snapshot.events:
            return {'text': '我们还没有收录原文，先读一小段吧。', 'emotion': 'neutral',
                    'answer_type': 'uncertain', 'evidence_ids': []}
        event = snapshot.events[-1]
        return {'text': '这段原文里写到：“' + event['raw_text'] + '” 我们可以先顺着这条线索聊。',
                'emotion': 'serious', 'answer_type': 'analysis', 'evidence_ids': [event['id']]}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'story.sqlite3'
        self.store = StoryStore(self.path)
        self.store.create_run('测试学园', '初见', 0)

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, text, request_id=None):
        state = self.store.view()
        return self.store.ingest(text, 'auto', '', request_id or str(uuid4()),
                                 state['epoch'], state['active']['id'])

    def answer(self, text='一起看看', reply='这是当前的讨论。'):
        snapshot = self.store.snapshot(text, self.store.epoch())
        result = {'text': reply, 'emotion': 'neutral', 'answer_type': 'chat', 'evidence_ids': []}
        self.store.append_exchange(snapshot, text, result)

    def test_100_lines_request_retry_and_restart_pagination(self):
        text = '\n'.join(['我知道了。'] * 2 + [f'旁白：这是第{i}段校园日常。' for i in range(3, 101)])
        old = self.store.epoch()
        self.ingest(text, 'request-100')
        self.assertFalse(self.store.ingest(text, 'auto', '', 'request-100', old, self.store.view()['active']['id']))
        with self.assertRaises(StoryConflict):
            self.ingest('改变内容', 'request-100')
        reopened = StoryStore(self.path)
        active = reopened.view()['active']
        self.assertEqual((active['position'], len(active['events']), active['more_before']), (100, 50, 51))
        earlier = reopened.view(before=51)['active']['events']
        self.assertEqual([event['raw_text'] for event in earlier[:2]], ['我知道了。'] * 2)
        self.assertEqual(earlier[0]['position'], 1)
        snapshot = reopened.snapshot('第3句讲了什么', reopened.epoch())
        self.assertIn(3, [event['position'] for event in snapshot.events])

    def test_restore_isolates_secret_and_discussion_then_auto_forks(self):
        self.ingest(COMMON)
        self.store.bookmark('雨中的共同段', self.store.epoch())
        point = self.store.view()['active']['bookmarks'][0]['id']
        self.answer(reply='我们都在等雨停。')
        self.ingest(SECRET)
        a_branch = self.store.view()['active']['branch_id']
        self.answer(text='红色信封是什么', reply='A线秘密讨论：红色信封。')
        stale = self.store.snapshot('信封', self.store.epoch())
        self.store.restore(point, self.store.epoch())
        with self.assertRaises(StoryConflict):
            self.store.append_exchange(stale, '旧问题', {'text':'迟到的答案', 'emotion':'neutral', 'evidence_ids':[]})
        self.ingest(OTHER)
        state = self.store.view()
        self.assertNotEqual(a_branch, state['active']['branch_id'])
        self.assertEqual(len(state['active']['branches']), 2)
        snapshot = self.store.snapshot('红色信封在哪里', state['epoch'])
        serialized = json.dumps([snapshot.events, snapshot.messages], ensure_ascii=False)
        self.assertNotIn('红色信封', serialized)
        self.assertIn('等雨停', serialized)
        self.assertIn('图书室', serialized)
        memory = json.dumps({'scenes': snapshot.scenes, 'characters': snapshot.characters}, ensure_ascii=False)
        self.assertNotIn('红色信封', memory)
        self.assertNotIn('旧钟楼', memory)
        self.assertIn('图书室', memory)
        panel = json.dumps(self.store.view()['active']['memory'], ensure_ascii=False)
        self.assertNotIn('红色信封', panel)
        self.assertIn('图书室', panel)
        # Original A data remains available only when explicitly selecting its route.
        self.store.select_branch(a_branch, state['epoch'])
        self.assertIn('红色信封', json.dumps(self.store.view()['active'], ensure_ascii=False))

    def test_nested_branches_use_ancestor_prefix_and_empty_chat(self):
        self.answer(reply='开读前打个招呼。')
        self.ingest(COMMON)
        self.store.fork('第一条路线', self.store.epoch())
        self.ingest('旁白：天色逐渐暗下来。')
        self.store.fork('第二次分叉', self.store.epoch())
        self.ingest(OTHER)
        self.assertEqual([e['position'] for e in self.store.view()['active']['events']], [1, 2, 3, 4])
        self.assertIn('开读前', json.dumps(self.store.view()['active']['messages'], ensure_ascii=False))
        self.store.progress(1, self.store.epoch())
        snapshot = self.store.snapshot('现在在哪里', self.store.epoch())
        self.assertEqual(len(snapshot.events), 1)
        self.assertNotIn('图书室', json.dumps(snapshot.messages, ensure_ascii=False))

    def test_separate_runs_and_stale_mutations(self):
        self.ingest(COMMON)
        self.store.bookmark('旧周目', self.store.epoch())
        old = self.store.view()
        self.store.create_run('测试学园', '第二周目', old['epoch'])
        with self.assertRaises(ValueError):
            self.store.restore(old['active']['bookmarks'][0]['id'], self.store.epoch())
        with self.assertRaises(StoryConflict):
            self.store.fork('迟到的操作', old['epoch'])
        self.assertEqual(self.store.snapshot('美咲', self.store.epoch()).events, ())
        self.store.activate(old['active']['id'], self.store.epoch())
        self.assertEqual(self.store.view()['active']['position'], 2)

    def test_citations_only_accept_supplied_events_and_new_chat_keeps_story(self):
        self.ingest(COMMON)
        snapshot = self.store.snapshot('雨', self.store.epoch())
        result = {'text':'她提议等雨停。', 'emotion':'neutral', 'answer_type':'analysis', 'evidence_ids':[]}
        for invalid in ([], [99999], [True]):
            result['evidence_ids'] = invalid
            with self.assertRaises(ValueError):
                self.store.append_exchange(snapshot, '她怎么说', result)
        result['evidence_ids'] = [snapshot.events[1]['id']]
        evidence = self.store.append_exchange(snapshot, '她怎么说', result)
        self.assertEqual(evidence[0]['speaker'], '美咲')
        self.store.new_chat(self.store.epoch())
        self.assertEqual(self.store.view()['active']['messages'], [])
        self.assertEqual(self.store.view()['active']['position'], 2)

    def test_normal_forward_read_does_not_invalidate_original_question(self):
        self.ingest(COMMON)
        snapshot = self.store.snapshot('雨', self.store.epoch())
        self.ingest(SECRET)
        self.assertTrue(self.store.matches(snapshot))
        self.store.append_exchange(snapshot, '雨', {'text':'先在这里避雨。', 'emotion':'neutral',
                                                  'answer_type':'analysis', 'evidence_ids':[snapshot.events[0]['id']]})
        self.store.progress(2, self.store.epoch())
        self.assertIn('避雨', json.dumps(self.store.view()['active']['messages'], ensure_ascii=False))

    def test_parser_and_limits_preserve_sayings_and_choices(self):
        events = self.store.parse(COMMON + '\nA. 一起去钟楼\n旁白：她声称自己从没去过那里。')
        self.assertEqual([e['kind'] for e in events], ['narration','dialogue','choice','narration'])
        self.assertEqual(events[1]['raw_text'], '美咲：我们先在走廊等一会儿吧。')
        for invalid in ('  ', '\n'.join(['一句话'] * 201), '字' * 2001):
            with self.assertRaises(ValueError):
                self.store.parse(invalid)
        bracket = self.store.parse('【美咲】昨晚我没有离开。')
        self.assertEqual((bracket[0]['speaker'], bracket[0]['kind'], bracket[0]['text']), ('美咲', 'dialogue', '昨晚我没有离开。'))

    def test_plot_blocks_open_whole_scenes_and_leave_unrelated_blocks_closed(self):
        lines = ['旁白：旧图书馆的灯一直亮着。']
        lines += [f'旁白：走廊里只有第{i}盏灯。' for i in range(2, 41)]
        lines.append('旁白：第二天，食堂开始供应茶。')
        lines += [f'旁白：食堂窗口第{i}号。' for i in range(42, 71)]
        lines.append('旁白：与此同时，操场起了风。')
        lines += [f'旁白：操场风声第{i}下。' for i in range(72, 81)]
        self.ingest('\n'.join(lines))
        recent = self.store.snapshot('刚才那段', self.store.epoch())
        positions = [event['position'] for event in recent.events]
        self.assertIn(80, positions)
        self.assertIn(71, positions)
        self.assertNotIn(1, positions)
        self.assertNotIn(50, positions)
        self.assertTrue(all(scene['loaded'] for scene in recent.scenes if scene['role'] == 'current'))
        earlier = self.store.snapshot('旧图书馆的灯', self.store.epoch())
        found = [event['position'] for event in earlier.events]
        self.assertIn(1, found)
        self.assertNotIn(20, found)
        self.assertIn(80, found)
        self.assertNotIn(50, found)
        self.assertLessEqual(len(found), 16)
        library = next(scene for scene in earlier.scenes if scene['loaded'] and scene['role'] != 'current')
        self.assertGreater(library['lines'], library['shown'])
        self.assertLessEqual(library['shown'], 6)
        self.assertNotIn('食堂', json.dumps([event['text'] for event in earlier.events], ensure_ascii=False))
        self.assertTrue(any(scene['loaded'] and '旧图书馆' in scene['summary'] for scene in earlier.scenes))
        self.assertTrue(any(not scene['loaded'] and '食堂' in scene['summary'] for scene in earlier.scenes))

    def test_long_current_scene_opens_the_named_line_and_keeps_the_tail(self):
        lines = ['旁白：天色已经暗下来。']
        lines += [f'美咲：这是第{i}句无关闲聊。' for i in range(2, 16)]
        lines.append('美咲：旧图书馆的灯还亮着，我刚才去看过。')
        lines += [f'美咲：接着闲聊第{i}句。' for i in range(17, 41)]
        self.ingest('\n'.join(lines))
        snapshot = self.store.snapshot('旧图书馆的灯还亮着吗', self.store.epoch())
        positions = [event['position'] for event in snapshot.events]
        self.assertIn(16, positions)
        self.assertIn(40, positions)
        self.assertNotIn(8, positions)
        packed = json.dumps(pack_story_context(snapshot, '旧图书馆的灯还亮着吗'), ensure_ascii=False)
        self.assertIn('旧图书馆', packed)
        self.assertLess(len(packed), 5000)

    def test_novel_chapters_keep_unread_text_out_and_notice_speech(self):
        self.store.create_run('短章', '试读', self.store.epoch(), 'novel')
        self.ingest('第一章\n柜台上温着酒。\n孔乙己说道，“不多了，我已经不多了。”')
        state = self.store.view()
        self.assertEqual(state['active']['mode'], 'novel')
        self.assertEqual(state['active']['branch_name'], '正文')
        person = next(item for item in state['active']['memory']['characters'] if item['name'] == '孔乙己')
        self.assertTrue(any('不多了' in claim['text'] for claim in person['claims']))
        self.store.bookmark('第一章', self.store.epoch())
        point = self.store.view()['active']['bookmarks'][0]['id']
        self.ingest('第二章\n后来他的腿断了，这件事只写在这一章。')
        self.store.restore(point, self.store.epoch())
        snapshot = self.store.snapshot('后来他怎么样', self.store.epoch())
        self.assertEqual(snapshot.mode, 'novel')
        packed = json.dumps(pack_story_context(snapshot, '后来他怎么样'), ensure_ascii=False)
        self.assertNotIn('腿断了', packed)
        self.assertIn('温着酒', packed)
        parsed = self.store.parse('第二章 咸亨')
        self.assertEqual(parsed[0]['kind'], 'chapter')

    def test_choice_is_part_of_the_current_block(self):
        self.ingest('旁白：走廊尽头有两扇门。\nA. 推开左边')
        current = next(scene for scene in self.store.snapshot('怎么选', self.store.epoch()).scenes if scene['role'] == 'current')
        self.assertTrue(current['loaded'])
        self.assertTrue(current['at_choice'])
        self.assertEqual(current['end'], 2)

    def test_long_lines_stay_in_evidence_and_are_clipped_for_the_model(self):
        keyword_at = 900
        line = '甲' * keyword_at + '旧图书馆的灯' + '乙' * 900
        self.ingest('旁白：' + line + '\n旁白：操场起了风。')
        snapshot = self.store.snapshot('旧图书馆的灯', self.store.epoch())
        full = next(event['text'] for event in snapshot.events if '旧图书馆' in event['text'])
        self.assertGreater(len(full), 1600)
        packed = pack_story_context(snapshot, '旧图书馆的灯')
        serialized = json.dumps(packed, ensure_ascii=False)
        self.assertIn('旧图书馆的灯', serialized)
        self.assertNotIn('甲' * 80, serialized)
        self.assertLess(len(serialized), 5000)
        opened = [line for block in packed['loaded_blocks'] for line in block['lines']]
        self.assertTrue(all(len(item['text']) <= 161 for item in opened))

    def test_short_new_scene_still_includes_the_scene_just_finished(self):
        lines = ['旁白：旧图书馆的灯一直亮着。'] + [f'旁白：第{i}页笔记。' for i in range(2, 11)]
        lines += ['A. 合上笔记', '旁白：雨还在下。', '美咲：我们走吧。']
        self.ingest('\n'.join(lines))
        snapshot = self.store.snapshot('刚才那段', self.store.epoch())
        text = json.dumps([event['text'] for event in snapshot.events], ensure_ascii=False)
        self.assertIn('旧图书馆', text)
        self.assertIn('我们走吧', text)
        self.assertGreaterEqual(len(snapshot.scenes), 2)

    def test_character_claims_stay_distinct_from_narration(self):
        self.ingest('美咲：昨晚我没有离开宿舍。\n美咲：我叫小咲。\n旁白：门卫看见美咲午夜出了校门。')
        snapshot = self.store.snapshot('小咲有没有离开', self.store.epoch())
        person = next(item for item in snapshot.characters if item['name'] == '美咲')
        self.assertIn('小咲', person['aliases'])
        self.assertTrue(any('没有离开' in item['text'] for item in person['claims']))
        self.assertTrue(any('午夜' in item['text'] for item in person['witnessed']))
        self.assertTrue(all('没有离开' not in item['text'] for item in person['witnessed']))
        self.assertNotIn('小咲', [item['name'] for item in snapshot.characters])
        visible = self.store.view()['active']['memory']['characters']
        self.assertEqual(visible[0]['name'], '美咲')
        self.assertIn('小咲', visible[0]['aliases'])

    def test_spoiler_claims_are_dropped_unless_the_reading_already_contains_them(self):
        self.ingest(COMMON)
        snapshot = self.store.snapshot('美咲', self.store.epoch())
        evidence = snapshot.events[1]['id']
        mixed = ground_story_answer({
            'text': '美咲提议先在走廊等雨。其实她的真实身份是校长，这是真结局。',
            'emotion': 'serious', 'answer_type': 'analysis', 'evidence_ids': [evidence]}, snapshot, '美咲')
        self.assertIn('走廊', mixed['text'])
        self.assertNotIn('校长', mixed['text'])
        self.assertNotIn('真结局', mixed['text'])
        self.assertEqual(mixed['answer_type'], 'analysis')
        self.assertEqual(mixed['evidence_ids'], [evidence])
        blocked = ground_story_answer({
            'text': '选A会进入钟楼。', 'emotion': 'happy', 'answer_type': 'analysis', 'evidence_ids': [evidence]}, snapshot, '怎么选')
        self.assertEqual(blocked['answer_type'], 'uncertain')
        self.assertEqual(blocked['evidence_ids'], [])
        self.assertNotIn('钟楼', blocked['text'])
        self.ingest('旁白：美咲说这不是真结局。')
        allowed = self.store.snapshot('真结局', self.store.epoch())
        kept = ground_story_answer({
            'text': '她明确说这不是真结局。', 'emotion': 'neutral', 'answer_type': 'analysis',
            'evidence_ids': [allowed.events[-1]['id']]}, allowed, '真结局')
        self.assertIn('真结局', kept['text'])
        self.assertEqual(kept['answer_type'], 'analysis')
        comfort = ground_story_answer({
            'text': '这句话其实是在安慰她。后来会明白的。', 'emotion': 'neutral', 'answer_type': 'chat', 'evidence_ids': []}, snapshot, '这句话')
        self.assertIn('安慰', comfort['text'])
        self.assertIn('后来会明白', comfort['text'])
        self.assertEqual(comfort['answer_type'], 'chat')
        direct = ground_story_answer({
            'text': '其实是校长。', 'emotion': 'surprise', 'answer_type': 'analysis', 'evidence_ids': [evidence]}, snapshot, '她是谁')
        self.assertEqual(direct['answer_type'], 'uncertain')
        self.assertNotIn('校长', direct['text'])


class StoryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = StoryFixture()
        self.config = ConfigStore(Path(self.temp.name) / 'config.json', env={})
        self.app = create_app(self.fixture, self.temp.name, config=self.config)
        self.client = TestClient(self.app)
        self.state = self.client.post('/api/story/runs', json={'title':'测试学园','epoch':0}).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_api_import_chat_evidence_and_restart(self):
        body = {'epoch':self.state['epoch'], 'run_id':self.state['active']['id'], 'text':COMMON,
                'request_id':'api-import-one', 'confirmed_read':True}
        self.assertEqual(self.client.post('/api/story/import', json=body).status_code, 200)
        result = self.client.post('/api/chat', json={'text':'美咲怎么说', 'session_id':'story', 'story_epoch':body['epoch']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['evidence'][0]['raw_text'], COMMON.splitlines()[1])
        reopened = TestClient(create_app(history_directory=self.temp.name, config=self.config, allow_cloud=False))
        try:
            active = reopened.get('/api/story').json()['active']
            self.assertEqual(active['position'], 2)
            self.assertEqual(active['messages'][-1]['evidence'], result.json()['evidence'])
        finally:
            reopened.close()

    def test_requires_confirmed_text_current_epoch_and_same_origin(self):
        body = {'epoch':self.state['epoch'], 'run_id':self.state['active']['id'], 'text':COMMON,
                'request_id':'api-import-two', 'confirmed_read':False}
        self.assertEqual(self.client.post('/api/story/import', json=body).status_code, 422)
        self.assertEqual(self.client.post('/api/chat', json={'text':'你好','session_id':'story'}).status_code, 409)
        self.assertEqual(self.client.post('/api/story/bookmarks', json={'epoch':0}).status_code, 409)
        self.assertEqual(self.client.post('/api/story/bookmarks', json={'epoch':1},
                                         headers={'Origin':'https://other.invalid'}).status_code, 403)

    def test_bad_provider_evidence_is_not_stored(self):
        async def invalid(text, snapshot):
            return {'text':'不应保存', 'emotion':'neutral', 'answer_type':'analysis','evidence_ids':[9999]}
        self.fixture.complete_story = invalid
        result = self.client.post('/api/chat', json={'text':'继续','session_id':'story','story_epoch':self.state['epoch']})
        self.assertEqual(result.status_code, 502)
        self.assertEqual(self.client.get('/api/story').json()['active']['messages'], [])

    def test_api_downgrades_spoiler_before_saving(self):
        body = {'epoch': self.state['epoch'], 'run_id': self.state['active']['id'], 'text': COMMON,
                'request_id': 'api-import-spoiler', 'confirmed_read': True}
        self.assertEqual(self.client.post('/api/story/import', json=body).status_code, 200)

        async def spoil(text, snapshot):
            return {'text': '其实她的真实身份是校长。', 'emotion': 'surprise', 'answer_type': 'analysis',
                    'evidence_ids': [snapshot.events[-1]['id']]}

        self.fixture.complete_story = spoil
        result = self.client.post('/api/chat', json={'text': '美咲是谁', 'session_id': 'story', 'story_epoch': body['epoch']})
        self.assertEqual(result.status_code, 200)
        payload = result.json()
        self.assertEqual(payload['answer_type'], 'uncertain')
        self.assertEqual(payload['evidence'], [])
        self.assertNotIn('校长', payload['text'])
        stored = self.client.get('/api/story').json()['active']
        self.assertNotIn('校长', json.dumps(stored['messages'], ensure_ascii=False))
        self.assertEqual(stored['messages'][-1]['answer_type'], 'uncertain')
        self.assertIn('美咲', json.dumps(stored['memory'], ensure_ascii=False))


class AsyncStoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = StoryStore(Path(self.temp.name) / 'story.sqlite3')
        self.store.create_run('测试学园', '', 0)
        self.store.bookmark('开读前', self.store.epoch())
        state = self.store.view()
        self.point = state['active']['bookmarks'][0]['id']
        self.store.ingest(COMMON, 'auto', '', 'async-import', state['epoch'], state['active']['id'])

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_restore_cancels_old_speech_and_rejects_stale_playback(self):
        entered = asyncio.Event()

        class Speech:
            async def synthesize(self, text):
                entered.set()
                await asyncio.Event().wait()

        config = ConfigStore(Path(self.temp.name) / 'voice.json', env={})
        config.data['voice']['enabled'] = True
        app = create_app(StoryFixture(), self.temp.name, config=config, story=self.store, speech=Speech())
        old = self.store.epoch()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            task = asyncio.create_task(client.post('/api/speech', json={'text':'旧路线的朗读','story_epoch':old}))
            await asyncio.wait_for(entered.wait(), timeout=3)
            await client.post('/api/story/restore', json={'bookmark_id':self.point,'epoch':old})
            self.assertEqual((await asyncio.wait_for(task, 3)).status_code, 409)
            self.assertEqual((await client.post('/api/speech', json={'text':'迟到的朗读','story_epoch':old})).status_code, 409)

    async def test_restore_cancels_inflight_and_late_answer_cannot_write(self):
        for suppress_cancel in (False, True):
            with self.subTest(suppress_cancel=suppress_cancel):
                entered = asyncio.Event()
                fixture = StoryFixture()
                original = fixture.complete_story

                async def delayed(text, snapshot):
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if not suppress_cancel:
                            raise
                        return await original(text, snapshot)

                fixture.complete_story = delayed
                config = ConfigStore(Path(self.temp.name) / 'config.json', env={})
                app = create_app(fixture, self.temp.name, config=config, story=self.store)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                    task = asyncio.create_task(client.post('/api/chat', json={
                        'text':'分析这段', 'session_id':'story', 'story_epoch':self.store.epoch()}))
                    await asyncio.wait_for(entered.wait(), timeout=3)
                    response = await client.post('/api/story/restore', json={
                        'bookmark_id':self.point, 'epoch':self.store.epoch()})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual((await asyncio.wait_for(task, 3)).status_code, 409)
                    self.assertEqual(self.store.view()['active']['messages'], [])
                    fixture.complete_story = original
                    self.assertEqual((await client.post('/api/chat', json={
                        'text':'现在呢', 'session_id':'story', 'story_epoch':self.store.epoch()})).status_code, 200)
                    self.store.new_chat(self.store.epoch())

    async def test_actual_sdk_payload_excludes_sibling_branch_and_validates_evidence(self):
        self.store.bookmark('共同段', self.store.epoch())
        point = self.store.view()['active']['bookmarks'][0]['id']
        state = self.store.view()
        self.store.ingest(SECRET, 'auto', '', 'async-secret', state['epoch'], state['active']['id'])
        snap = self.store.snapshot('钥匙', self.store.epoch())
        self.store.append_exchange(snap, '钥匙', {'text':'A线红色信封的讨论。','emotion':'neutral',
                                                'answer_type':'analysis','evidence_ids':[snap.events[-1]['id']]})
        self.store.restore(point, self.store.epoch())
        state = self.store.view()
        self.store.ingest(OTHER, 'auto', '', 'async-other', state['epoch'], state['active']['id'])
        snapshot = self.store.snapshot('现在的线索', self.store.epoch())
        payloads = []
        evidence_ids = [snapshot.events[-1]['id']]

        def handler(request):
            payloads.append(json.loads(request.content))
            reply = json.dumps({'text':'小春提议去找伞。','emotion':'neutral','answer_type':'analysis',
                                'evidence_ids':evidence_ids}, ensure_ascii=False)
            return httpx.Response(200, json={'id':'fixture','object':'chat.completion','created':0,'model':'fixture',
                'choices':[{'index':0,'message':{'role':'assistant','content':reply},'finish_reason':'stop'}]})

        chat = TextChat({'api_key':'fixture-only','base_url':'https://fixture.invalid/v1','model':'fixture'},
                        transport=httpx.MockTransport(handler))
        try:
            result = await chat.complete_story('现在的线索', snapshot)
            self.assertEqual(result['evidence_ids'], evidence_ids)
            serialized = json.dumps(payloads, ensure_ascii=False)
            self.assertNotIn('红色信封', serialized)
            self.assertIn('图书室', serialized)
            self.assertNotIn('图书室', payloads[0]['messages'][0]['content'])
            material = payloads[0]['messages'][1]['content']
            self.assertIn('"scenes"', material)
            self.assertIn('"loaded_blocks"', material)
            self.assertIn('"claims"', material)
            self.assertNotIn('旧钟楼', material)
            self.assertEqual(payloads[0]['messages'][1]['role'], 'user')
            for invalid in ([], [True], [999999]):
                evidence_ids = invalid
                with self.assertRaises(ValueError):
                    await chat.complete_story('现在的线索', snapshot)
        finally:
            await chat.client.close()


if __name__ == '__main__':
    unittest.main()
