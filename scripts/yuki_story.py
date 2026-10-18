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
                    match = re.match(r'^([^：:\n]{1,30})[：:]\s*(.+)$', line)
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
                'branches': [{'id': row['id'], 'name': row['name'], 'tip': self._tip(db, row)} for row in db.execute('SELECT * FROM branches WHERE run_id=? ORDER BY rowid', (run['id'],))]}
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
            terms = set(re.findall(r'[a-z0-9_]{2,}|[\u3400-\u9fff]{2}', question.lower()))
            # Overlapping bigrams find Chinese names without a new tokenizer/model dependency.
            for phrase in re.findall(r'[\u3400-\u9fff]+', question):
                terms.update(phrase[i:i+2] for i in range(len(phrase)-1))
            terms -= {'什么', '我们', '剧情', '之前', '刚才', '觉得', '可以', '现在', '怎么', '一下', '记得'}
            ranked = sorted(events, key=lambda event: (sum(term in (event['speaker'] + event['text']).lower() for term in terms), event['position']), reverse=True)
            relevant = [event for event in ranked if any(term in (event['speaker']+event['text']).lower() for term in terms)][:8]
            explicit = re.findall(r'第\s*(\d+)\s*[句条段]', question)
            chosen, used, budget = [], set(), 8000
            for event in ([event for event in events if str(event['position']) in explicit] + list(reversed(events[-4:])) + relevant):
                size = len(event['raw_text']) + 80
                if event['id'] not in used and size <= budget:
                    chosen.append(event);used.add(event['id']);budget -= size
            messages, budget = [], 6000
            for message in reversed(self._messages(db, run)):
                size = len(message['content'])
                if size > budget:
                    break
                messages.append({'role': message['role'], 'content': message['content']});budget -= size
            return ReadingSnapshot(state['epoch'], run['id'], run['branch_id'], run['position'], run['chat_generation'],
                                   run['title'], tuple(sorted(chosen, key=lambda event:event['position'])), tuple(reversed(messages)))

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
