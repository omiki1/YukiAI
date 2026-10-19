"""Local, branch-scoped reading records. No game access or model calls."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


class StoryConflict(ValueError):
    pass


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@dataclass(frozen=True)
class ReadingSnapshot:
    epoch: int
    run_id: str | None
    branch_id: str | None = None
    position: int = 0
    chat_generation: int = 0
    title: str = ''
    events: tuple = ()
    messages: tuple = ()
    scenes: tuple = ()
    characters: tuple = ()


# Derived only from events already inside the current route prefix. No model call.
SCENE_EVENT_LIMIT = 40
WORKING_SCENE_SHORT = 8
EARLIER_BLOCK_LIMIT = 2
RAW_EVENT_LIMIT = 120
RAW_BUDGET = 12000
MESSAGE_BUDGET = 6000
STOPWORDS = {'什么', '我们', '剧情', '之前', '刚才', '觉得', '可以', '现在', '怎么', '一下', '记得'}
NAME = r'[\u3400-\u9fff]{2,4}'
ALIAS_RE = re.compile(rf'(?:我叫|叫我|名叫|人称(?:她|他)?为|大家都叫(?:她|他)?)({NAME})')
SELF_RE = re.compile(rf'我是({NAME})(?=[，。！？、\s]|$)')
ALIAS_STOP = {'一个', '没有', '不是', '不会', '不能', '这个', '那个', '自己', '什么', '这里', '那里', '现在', '刚才',
              '我们', '你们', '他们', '她们', '因为', '所以', '如果', '可是', '但是', '就是', '还是', '已经', '时候'}
SCENE_BREAK = re.compile(r'^(?:【[^】\n]{1,16}】|场景\s*[:：]|地点\s*[:：]|时间\s*[:：])'
                         r'|^(?:次日|翌日|第二天|几天后|数日后|与此同时|另一边|画面一转)')


def clip(text, limit):
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def question_terms(question):
    terms = set(re.findall(r'[a-z0-9_]{2,}', (question or '').lower()))
    for phrase in re.findall(r'[\u3400-\u9fff]+', question or ''):
        terms.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return terms - STOPWORDS


def event_score(event, terms, names=()):
    blob = (event.get('speaker') or '') + (event.get('text') or '')
    score = sum(term in blob.lower() for term in terms)
    for name in names:
        if name and len(name) >= 2 and name in blob:
            score += 5
    return score


def extract_aliases(text):
    found = []
    for pattern in (ALIAS_RE, SELF_RE):
        for match in pattern.finditer(text or ''):
            name = match.group(1)
            if name in ALIAS_STOP or name.startswith(('没有', '不是', '不会', '不能', '不要', '怎么')) or name in found:
                continue
            found.append(name)
    return found


def starts_scene(event):
    return event['kind'] == 'narration' and bool(SCENE_BREAK.search((event.get('text') or '').strip()))


def segment_scenes(events):
    scenes, bucket = [], []

    def close():
        if bucket:
            scenes.append(tuple(bucket))
            bucket.clear()

    for event in events:
        if bucket and (len(bucket) >= SCENE_EVENT_LIMIT or starts_scene(event)):
            close()
        bucket.append(event)
        if event['kind'] == 'choice':
            close()
    close()
    return scenes


def scene_speakers(events):
    speakers = []
    for event in events:
        if event.get('speaker') and event['speaker'] not in speakers:
            speakers.append(event['speaker'])
    return speakers[:6]


def scene_summary(events):
    narration = next((event for event in events if event['kind'] == 'narration' and event.get('text', '').strip()), None)
    last_narration = next((event for event in reversed(events) if event['kind'] == 'narration' and event.get('text', '').strip()), None)
    dialogue = next((event for event in reversed(events) if event['kind'] == 'dialogue' and event.get('text', '').strip()), None)
    choice = next((event for event in reversed(events) if event['kind'] == 'choice'), None)
    bits = [f'第{events[0]["position"]}–{events[-1]["position"]}句']
    speakers = scene_speakers(events)
    if speakers:
        bits.append('在场：' + '、'.join(speakers))
    if narration:
        bits.append('旁白：' + clip(narration['text'], 72))
    if last_narration and last_narration is not narration:
        bits.append('后来：' + clip(last_narration['text'], 72))
    if dialogue:
        bits.append((dialogue.get('speaker') or '对白') + '说：' + clip(dialogue['text'], 72))
    if choice:
        bits.append('选项：' + clip(choice['text'], 36))
    bits.append(f'共{len(events)}句')
    return '。'.join(bits)


def scene_card(events, loaded=False, role='closed'):
    return {'start': events[0]['position'], 'end': events[-1]['position'], 'speakers': scene_speakers(events),
            'summary': scene_summary(events), 'lines': len(events), 'loaded': loaded, 'role': role,
            'at_choice': events[-1]['kind'] == 'choice'}


def block_score(scene, terms, names=()):
    scores = [event_score(event, terms, names) for event in scene]
    if not scores or max(scores) <= 0:
        return 0
    # One strong line can open a block; repeated mentions still matter.
    return max(scores) * 3 + sum(scores)


def block_chars(scene):
    return sum(len(event.get('raw_text') or '') + 80 for event in scene)


def build_characters(events):
    people = {}

    def ensure(name):
        if name not in people:
            people[name] = {'name': name, 'aliases': [], 'claims': [], 'witnessed': []}
        return people[name]

    for event in events:
        if event['kind'] == 'dialogue' and event.get('speaker'):
            ensure(event['speaker'])
    for event in events:
        aliases = extract_aliases(event.get('text') or '')
        if event['kind'] == 'dialogue' and event.get('speaker'):
            person = ensure(event['speaker'])
            person['claims'].append(event)
            for alias in aliases:
                if alias != event['speaker'] and alias not in person['aliases']:
                    person['aliases'].append(alias)
            continue
        if event['kind'] != 'narration':
            continue
        known = []
        for name, person in people.items():
            if len(name) >= 2 and name in event['text'] and name not in known:
                known.append(name)
            for alias in person['aliases']:
                if len(alias) >= 2 and alias in event['text'] and name not in known:
                    known.append(name)
        for alias in aliases:
            already = any(alias == name or alias in person['aliases'] for name, person in people.items())
            if already:
                continue
            if known:
                host = people[known[-1]]
                if alias not in host['aliases']:
                    host['aliases'].append(alias)
            else:
                ensure(alias)
                known.append(alias)
        for name in known:
            people[name]['witnessed'].append(event)
    return people


def mention_row(event, provided_ids, limit=90):
    row = {'position': event['position'], 'text': clip(event.get('text') or '', limit)}
    if provided_ids is None or event['id'] in provided_ids:
        row['id'] = event['id']
    return row


def trim_mentions(events, terms, names, limit, provided_ids):
    if not events:
        return []
    ranked = sorted(events, key=lambda event: (event_score(event, terms, names), event['position']), reverse=True)
    picked = [event for event in ranked if event_score(event, terms, names)][:limit]
    if len(picked) < min(limit, len(events)):
        for event in reversed(events):
            if event not in picked:
                picked.append(event)
            if len(picked) >= limit:
                break
    picked.sort(key=lambda event: event['position'])
    return [mention_row(event, provided_ids) for event in picked]


def ui_line(event):
    return {'id': event['id'], 'position': event['position'], 'kind': event['kind'], 'speaker': event.get('speaker') or '',
            'text': clip(event.get('text') or '', 80), 'raw_text': event.get('raw_text') or event.get('text') or ''}


def present_memory(events):
    """Short, route-local memory for the reading panel. Claims stay separate from narration."""
    characters = []
    for person in build_characters(events).values():
        if not person['claims'] and not person['witnessed']:
            continue
        characters.append({'name': person['name'], 'aliases': person['aliases'][:3],
                           'claims': [ui_line(event) for event in person['claims'][-2:]],
                           'witnessed': [ui_line(event) for event in person['witnessed'][-2:]]})
        if len(characters) == 12:
            break
    scenes = segment_scenes(events)
    shown = []
    for scene in scenes[-4:]:
        card = scene_card(scene)
        card['current'] = scene is scenes[-1]
        shown.append(card)
    reading = None
    if scenes:
        current = scenes[-1]
        reading = {'start': current[0]['position'], 'end': current[-1]['position'],
                   'at_choice': current[-1]['kind'] == 'choice', 'speakers': scene_speakers(current)}
    return {'scenes': shown, 'characters': characters, 'reading': reading}


def choose_characters(people, question, working):
    selected = []

    def add(name):
        if name in people and name not in selected:
            selected.append(name)

    for name, person in people.items():
        if name and name in question:
            add(name)
        if any(alias and alias in question for alias in person['aliases']):
            add(name)
    for event in working:
        if event.get('speaker'):
            add(event['speaker'])
    if len(selected) <= 4:
        return [people[name] for name in selected]
    preferred = [name for name in selected if name in question or any(alias in question for alias in people[name]['aliases'])]
    recent = []
    for event in reversed(working):
        if event.get('speaker') and event['speaker'] not in recent:
            recent.append(event['speaker'])
    ordered = []
    for name in preferred + recent:
        if name in people and name not in ordered:
            ordered.append(name)
    return [people[name] for name in ordered[:4]]


def choose_blocks(scenes, terms, names, explicit):
    """Open whole plot blocks. Scattered lines lose the exchange around them."""
    if not scenes:
        return []
    current = scenes[-1]
    previous = scenes[-2] if len(scenes) > 1 and len(current) < WORKING_SCENE_SHORT else None
    loaded = []

    def add(scene):
        if scene is not None and scene not in loaded:
            loaded.append(scene)

    for scene in scenes:
        if any(str(event['position']) in explicit for event in scene):
            add(scene)
    add(previous)
    add(current)
    opened = 0
    ranked = sorted((scene for scene in scenes if scene not in loaded),
                    key=lambda scene: (block_score(scene, terms, names), scene[-1]['position']), reverse=True)
    for scene in ranked:
        if opened >= EARLIER_BLOCK_LIMIT or block_score(scene, terms, names) <= 0:
            break
        add(scene)
        opened += 1
    return loaded


def take_block(scene, chosen, used, budget, partial=False):
    pending = [event for event in scene if event['id'] not in used]
    if not pending:
        return budget, True
    if not partial:
        if len(chosen) + len(pending) > RAW_EVENT_LIMIT or block_chars(pending) > budget:
            return budget, False
        for event in pending:
            budget = accept_event(event, chosen, used, budget)
        return budget, True
    for event in reversed(pending):
        budget = accept_event(event, chosen, used, budget)
    return budget, True


def accept_event(event, chosen, used, budget):
    if event['id'] in used or len(chosen) >= RAW_EVENT_LIMIT:
        return budget
    size = len(event.get('raw_text') or '') + 80
    if size > budget:
        return budget
    chosen.append(event)
    used.add(event['id'])
    return budget - size


class StoryStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else ROOT / 'data' / 'story.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        initial = sqlite3.connect(self.path)
        try:
            initial.execute('PRAGMA journal_mode=WAL')
        finally:
            initial.close()
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS games(id TEXT PRIMARY KEY,title TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY,game_id TEXT NOT NULL REFERENCES games(id),name TEXT NOT NULL,
                    branch_id TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,
                    chat_generation INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS branches(
                    id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),name TEXT NOT NULL,
                    parent_id TEXT REFERENCES branches(id),fork_position INTEGER NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL REFERENCES runs(id),
                    branch_id TEXT NOT NULL REFERENCES branches(id),position INTEGER NOT NULL,
                    kind TEXT NOT NULL,speaker TEXT NOT NULL,text TEXT NOT NULL,raw_text TEXT NOT NULL,
                    source TEXT NOT NULL,confirmed_read INTEGER NOT NULL,created_at TEXT NOT NULL,
                    UNIQUE(branch_id,position));
                CREATE INDEX IF NOT EXISTS event_scope ON events(run_id,branch_id,position);
                CREATE TABLE IF NOT EXISTS imports(
                    run_id TEXT NOT NULL REFERENCES runs(id),request_id TEXT NOT NULL,digest TEXT NOT NULL,
                    PRIMARY KEY(run_id,request_id));
                CREATE TABLE IF NOT EXISTS bookmarks(
                    id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),branch_id TEXT NOT NULL REFERENCES branches(id),
                    position INTEGER NOT NULL,name TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS story_turns(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL REFERENCES runs(id),
                    branch_id TEXT NOT NULL REFERENCES branches(id),position INTEGER NOT NULL,
                    chat_generation INTEGER NOT NULL,user_text TEXT NOT NULL,reply TEXT NOT NULL,
                    emotion TEXT NOT NULL,answer_type TEXT NOT NULL,evidence_ids TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS turn_scope ON story_turns(run_id,branch_id,position);
                CREATE TABLE IF NOT EXISTS reading_state(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),active_run_id TEXT REFERENCES runs(id),epoch INTEGER NOT NULL);
                INSERT OR IGNORE INTO reading_state VALUES(1,NULL,0);
            ''')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _state(self, db):
        return db.execute('SELECT * FROM reading_state WHERE singleton=1').fetchone()

    def epoch(self):
        with self.connection() as db:
            return self._state(db)['epoch']

    def _check(self, db, expected):
        state = self._state(db)
        if expected is not None and state['epoch'] != expected:
            raise StoryConflict('阅读进度已改变，请刷新后再试。')
        return state

    def _run(self, db):
        state = self._state(db)
        if not state['active_run_id']:
            raise ValueError('请先建立或选择一篇共读。')
        return db.execute('SELECT r.*,g.title FROM runs r JOIN games g ON g.id=r.game_id WHERE r.id=?',
                          (state['active_run_id'],)).fetchone()

    def _boundary(self, db):
        db.execute('UPDATE reading_state SET epoch=epoch+1 WHERE singleton=1')

    def create_run(self, title, name, expected):
        title, name = title.strip(), name.strip() or '初见共读'
        if not title:
            raise ValueError('请填写游戏名称。')
        with self.connection(True) as db:
            self._check(db, expected)
            game = db.execute('SELECT id FROM games WHERE title=?', (title,)).fetchone()
            game_id = game['id'] if game else uuid4().hex
            if not game:
                db.execute('INSERT INTO games VALUES(?,?)', (game_id, title))
            run_id, branch_id = uuid4().hex, uuid4().hex
            db.execute('INSERT INTO runs(id,game_id,name,branch_id,created_at) VALUES(?,?,?,?,?)',
                       (run_id, game_id, name, branch_id, timestamp()))
            db.execute('INSERT INTO branches VALUES(?,?,?,?,?,?)',
                       (branch_id, run_id, '共同线', None, 0, timestamp()))
            db.execute('UPDATE reading_state SET active_run_id=?,epoch=epoch+1 WHERE singleton=1', (run_id,))

    def activate(self, run_id, expected):
        with self.connection(True) as db:
            state = self._check(db, expected)
            if run_id and not db.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone():
                raise ValueError('没有找到这篇共读。')
            if state['active_run_id'] != run_id:
                db.execute('UPDATE reading_state SET active_run_id=?,epoch=epoch+1 WHERE singleton=1', (run_id,))

    def _branch(self, db, run_id, branch_id):
        row = db.execute('SELECT * FROM branches WHERE id=? AND run_id=?', (branch_id, run_id)).fetchone()
        if row is None:
            raise ValueError('没有找到当前路线。')
        return row

    def _tip(self, db, branch):
        return db.execute('SELECT COALESCE(MAX(position),?) FROM events WHERE branch_id=?',
                          (branch['fork_position'], branch['id'])).fetchone()[0]

    def _segments(self, db, run_id, branch_id, position):
        segments, seen = [], set()
        while branch_id:
            if branch_id in seen:
                raise ValueError('路线记录无法读取。')
            seen.add(branch_id)
            branch = self._branch(db, run_id, branch_id)
            segments.append((branch_id, position))
            position = min(position, branch['fork_position'])
            branch_id = branch['parent_id']
        return segments

    def _scope(self, segments):
        if not segments:
            return '0', []
        return '(' + ' OR '.join('(branch_id=? AND position<=?)' for _ in segments) + ')', [x for pair in segments for x in pair]

    def _events(self, db, run, before=None, limit=None):
        scope, args = self._scope(self._segments(db, run['id'], run['branch_id'], run['position']))
        query = 'SELECT * FROM events WHERE run_id=? AND confirmed_read=1 AND ' + scope
        args = [run['id'], *args]
        if before is not None:
            query += ' AND position<?'
            args.append(before)
        query += ' ORDER BY position DESC'
        if limit is not None:
            query += ' LIMIT ?'
            args.append(limit)
        return list(reversed([dict(row) for row in db.execute(query, args)]))

    def _messages(self, db, run):
        scope, args = self._scope(self._segments(db, run['id'], run['branch_id'], run['position']))
        rows = list(db.execute('SELECT * FROM story_turns WHERE run_id=? AND chat_generation=? AND ' + scope +
                               ' ORDER BY id DESC LIMIT 12',
                               [run['id'], run['chat_generation'], *args]))
        messages = []
        allowed = None
        for row in reversed(rows):
            ids = json.loads(row['evidence_ids'])
            if ids:
                if allowed is None:
                    allowed = {event['id']: event for event in self._events(db, run)}
                if not all(event_id in allowed for event_id in ids):
                    continue
            evidence = [allowed[event_id] for event_id in ids] if ids else []
            messages.extend([{'role': 'human', 'content': row['user_text']},
                             {'role': 'ai', 'content': row['reply'], 'emotion': row['emotion'],
                              'evidence': evidence, 'answer_type': row['answer_type']}])
        return messages

    @staticmethod
    def parse(text, kind='auto', speaker=''):
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or len(lines) > 200:
            raise ValueError('每次请收录1～200行已读文本，空行会跳过。')
        events = []
        for line in lines:
            if len(line) > 2000:
                raise ValueError('单行文本最多2000字，请按段落拆分。')
            event = {'kind': kind, 'speaker': speaker.strip(), 'text': line, 'raw_text': line}
            if kind == 'auto':
                event['kind'] = 'narration'
                if re.match(r'^(?:选项\s*[A-H一二三四五六]?|[A-H][.、．)）])', line):
                    event['kind'] = 'choice'
                else:
                    match = re.match(r'^([^：:\n]{1,30})[：:]\s*(.+)$', line) or re.match(r'^【([^】\n]{1,30})】\s*(.+)$', line)
                    if match:
                        name, content = match.groups()
                        event.update(speaker='' if name in ('旁白', '叙述') else name, text=content,
                                     kind='narration' if name in ('旁白', '叙述') else 'dialogue')
            events.append(event)
        return events

    def _fork(self, db, run, name):
        branch_id = uuid4().hex
        count = db.execute('SELECT COUNT(*) FROM branches WHERE run_id=?', (run['id'],)).fetchone()[0]
        name = name.strip() or f'分支 {count}'
        db.execute('INSERT INTO branches VALUES(?,?,?,?,?,?)',
                   (branch_id, run['id'], name, run['branch_id'], run['position'], timestamp()))
        db.execute('UPDATE runs SET branch_id=? WHERE id=?', (branch_id, run['id']))
        self._boundary(db)
        return branch_id

    def ingest(self, text, kind, speaker, request_id, expected, run_id):
        events = self.parse(text, kind, speaker)
        return self._ingest(events, request_id, expected, run_id, 'manual')

    def ingest_capture(self, text, raw_text, kind, speaker, request_id, expected, run_id, source):
        if not text or len(text) > 2000 or len(raw_text) > 8000:
            raise ValueError('取词正文长度不正确。')
        if kind == 'auto':
            match = re.match(r'^【([^】]{1,30})】\s*(.+)$', text)
            if match:
                text = match[1] + '：' + match[2]
        events = self.parse(text, kind, speaker)
        if len(events) != 1:
            raise ValueError('取词正文应为一段完整台词。')
        events[0]['raw_text'] = raw_text
        return self._ingest(events, request_id, expected, run_id, source)

    def _ingest(self, events, request_id, expected, run_id, source):
        digest = hashlib.sha256(json.dumps(events, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.connection(True) as db:
            run = self._run(db)
            if run['id'] != run_id:
                raise StoryConflict('这段文本属于另一篇共读，请重新选择后收录。')
            existing = db.execute('SELECT digest FROM imports WHERE run_id=? AND request_id=?', (run_id, request_id)).fetchone()
            if existing:
                if existing['digest'] != digest:
                    raise StoryConflict('这次收录的内容已经改变，请重新提交。')
                return False
            self._check(db, expected)
            branch = self._branch(db, run_id, run['branch_id'])
            branch_id = run['branch_id']
            if run['position'] < self._tip(db, branch):
                branch_id = self._fork(db, run, '')
            created_at = timestamp()
            for offset, event in enumerate(events, 1):
                db.execute('INSERT INTO events(run_id,branch_id,position,kind,speaker,text,raw_text,source,confirmed_read,created_at) '
                           'VALUES(?,?,?,?,?,?,?,?,1,?)', (run_id, branch_id, run['position'] + offset, event['kind'],
                           event['speaker'], event['text'], event['raw_text'], source, created_at))
            db.execute('UPDATE runs SET position=? WHERE id=?', (run['position'] + len(events), run_id))
            db.execute('INSERT INTO imports VALUES(?,?,?)', (run_id, request_id, digest))
        return True

    def bookmark(self, name, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            name = name.strip() or f'读到第 {run["position"]} 句'
            db.execute('INSERT INTO bookmarks VALUES(?,?,?,?,?,?)',
                       (uuid4().hex, run['id'], run['branch_id'], run['position'], name, timestamp()))

    def restore(self, bookmark_id, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            point = db.execute('SELECT * FROM bookmarks WHERE id=? AND run_id=?', (bookmark_id, run['id'])).fetchone()
            if point is None:
                raise ValueError('没有找到这个书签。')
            db.execute('UPDATE runs SET branch_id=?,position=? WHERE id=?',
                       (point['branch_id'], point['position'], run['id']))
            self._boundary(db)

    def progress(self, position, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            branch = self._branch(db, run['id'], run['branch_id'])
            if not 0 <= position <= self._tip(db, branch):
                raise ValueError('阅读位置不在已收录的范围内。')
            while position < branch['fork_position']:
                branch = self._branch(db, run['id'], branch['parent_id'])
            db.execute('UPDATE runs SET branch_id=?,position=? WHERE id=?', (branch['id'], position, run['id']))
            # Explicit navigation also invalidates a pending reply, even when moving forward.
            self._boundary(db)

    def fork(self, name, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            self._fork(db, self._run(db), name)

    def select_branch(self, branch_id, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            branch = self._branch(db, run['id'], branch_id)
            db.execute('UPDATE runs SET branch_id=?,position=? WHERE id=?', (branch_id, self._tip(db, branch), run['id']))
            self._boundary(db)

    def new_chat(self, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            db.execute('UPDATE runs SET chat_generation=chat_generation+1 WHERE id=?', (run['id'],))
            self._boundary(db)

    def view(self, before=None):
        with self.connection() as db:
            state = self._state(db)
            result = {'epoch': state['epoch'], 'active': None, 'runs': [dict(row) for row in db.execute(
                'SELECT r.id,r.name,g.title FROM runs r JOIN games g ON g.id=r.game_id ORDER BY r.created_at,r.rowid')]}
            if not state['active_run_id']:
                return result
            run = self._run(db)
            branch = self._branch(db, run['id'], run['branch_id'])
            events = self._events(db, run, before=before, limit=50)
            result['active'] = {'id': run['id'], 'title': run['title'], 'name': run['name'],
                'branch_id': run['branch_id'], 'branch_name': branch['name'], 'position': run['position'],
                'tip': self._tip(db, branch), 'events': events,
                'more_before': events[0]['position'] if events and events[0]['position'] > 1 else None,
                'messages': self._messages(db, run),
                'bookmarks': [dict(row) for row in db.execute('SELECT id,name,position,branch_id FROM bookmarks WHERE run_id=? ORDER BY rowid DESC', (run['id'],))],
                'branches': [{'id': row['id'], 'name': row['name'], 'tip': self._tip(db, row)} for row in db.execute('SELECT * FROM branches WHERE run_id=? ORDER BY rowid', (run['id'],))],
                'memory': present_memory(self._events(db, run))}
            return result

    def snapshot(self, question, expected=None):
        with self.connection() as db:
            state = self._check(db, expected)
            if not state['active_run_id']:
                return ReadingSnapshot(state['epoch'], None)
            if expected is None:
                raise StoryConflict('请刷新阅读进度后再提问。')
            run = self._run(db)
            events = self._events(db, run)
            scenes = segment_scenes(events)
            people = build_characters(events)
            terms = question_terms(question)
            current = scenes[-1] if scenes else ()
            nearby = scenes[-2] if len(scenes) > 1 and current and len(current) < WORKING_SCENE_SHORT else ()
            # Names in the question point at a block; the block, not one matching line, is what gets opened.
            selected_people = choose_characters(people, question, tuple(nearby) + tuple(current))
            focus_names = tuple(person['name'] for person in selected_people
                                if person['name'] in question or any(alias in question for alias in person['aliases']))
            explicit = set(re.findall(r'第\s*(\d+)\s*[句条段]', question or ''))
            chosen_blocks = choose_blocks(scenes, terms, focus_names, explicit)
            # The block being read is kept even when an older block is long. Older blocks are all or nothing.
            ordered_blocks = [scene for scene in chosen_blocks if scene is current] + [scene for scene in chosen_blocks if scene is not current]
            chosen, used, budget = [], set(), RAW_BUDGET
            opened = []
            for scene in ordered_blocks:
                budget, ok = take_block(scene, chosen, used, budget, partial=scene is current)
                if ok:
                    opened.append(scene)
            provided = {event['id'] for event in chosen}
            # A block that did not fit stays closed. Do not leave a torn middle.
            opened_ids = {id(scene) for scene in opened}
            index = list(opened)
            for scene in scenes:
                if id(scene) not in opened_ids:
                    index.append(scene)
            closed_kept = [scene for scene in index if id(scene) not in opened_ids][-4:]
            visible = opened + [scene for scene in closed_kept if scene not in opened]
            cards = []
            for scene in sorted(visible, key=lambda item: item[0]['position']):
                role = 'opened'
                if scene is current:
                    role = 'current'
                elif current and len(current) < WORKING_SCENE_SHORT and scenes and scene is scenes[-2]:
                    role = 'previous'
                loaded = id(scene) in opened_ids
                if not loaded:
                    role = 'closed'
                cards.append(scene_card(scene, loaded, role))
            characters = []
            for person in selected_people:
                names = (person['name'], *person['aliases'])
                card = {'name': person['name'], 'aliases': person['aliases'][:3],
                        'claims': trim_mentions(person['claims'], terms, names, 4, provided),
                        'witnessed': trim_mentions(person['witnessed'], terms, names, 3, provided)}
                if card['claims'] or card['witnessed']:
                    characters.append(card)
            messages, budget = [], MESSAGE_BUDGET
            for message in reversed(self._messages(db, run)):
                size = len(message['content'])
                if size > budget:
                    break
                messages.append({'role': message['role'], 'content': message['content']})
                budget -= size
            return ReadingSnapshot(state['epoch'], run['id'], run['branch_id'], run['position'], run['chat_generation'],
                                   run['title'], tuple(sorted(chosen, key=lambda event: event['position'])), tuple(reversed(messages)),
                                   tuple(cards), tuple(characters))

    def matches(self, snapshot):
        with self.connection() as db:
            state = self._state(db)
            return state['epoch'] == snapshot.epoch and state['active_run_id'] == snapshot.run_id

    def append_exchange(self, snapshot, user, result):
        ids = result.get('evidence_ids', [])
        if not isinstance(ids, list) or not all(type(event_id) is int for event_id in ids):
            raise ValueError('回答的原文引用格式不正确。')
        provided = {event['id'] for event in snapshot.events}
        if not set(ids).issubset(provided):
            raise ValueError('回答引用了当前未提供的原文。')
        if result.get('answer_type') == 'analysis' and not ids:
            raise ValueError('剧情判断需要已有原文作为依据。')
        with self.connection(True) as db:
            state = self._check(db, snapshot.epoch)
            if state['active_run_id'] != snapshot.run_id:
                raise StoryConflict('共读记录已经切换。')
            db.execute('INSERT INTO story_turns(run_id,branch_id,position,chat_generation,user_text,reply,emotion,answer_type,evidence_ids,created_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)', (snapshot.run_id, snapshot.branch_id, snapshot.position,
                       snapshot.chat_generation, user, result['text'], result['emotion'], result.get('answer_type', 'uncertain'),
                       json.dumps(list(dict.fromkeys(ids))), timestamp()))
        return [event for event in snapshot.events if event['id'] in ids]
