"""Local memory inventory, scoped cleanup and versioned, additive recovery.

Only story tables and ordinary conversations enter snapshots. Configuration,
models, tools and credentials are never read here. No cloud clients are used.
"""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

from .yuki_story import StoryStore, StoryConflict, timestamp

LEGACY_TABLES = ('games', 'runs', 'branches', 'events', 'imports', 'bookmarks', 'story_turns', 'progress_checks')
TABLES = (*LEGACY_TABLES, 'reading_notes')
HISTORY_FIELDS = {'role', 'content', 'timestamp', 'name', 'emotion'}
INTEGER_COLUMNS = {
    'runs': {'position', 'chat_generation', 'note_generation'}, 'branches': {'fork_position', 'note_cutoff', 'turn_cutoff'},
    'events': {'id', 'position', 'confirmed_read'}, 'bookmarks': {'position'},
    'story_turns': {'id', 'position', 'chat_generation', 'note_generation'},
    'progress_checks': {'id', 'position', 'focus_position'},
    'reading_notes': {'id', 'position', 'origin_turn_id'},
}
MAX_BACKUP = 256 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


class MemoryService:
    def __init__(self, story, history):
        self.story, self.history = story, history
        self.directory = story.path.parent / 'memory_backups'
        self.plans = {}

    def _folder(self, uid):
        if not re.fullmatch(r'[a-f0-9]{32}', uid):
            raise ValueError('无效的备份编号。')
        folder = self.directory / uid
        if self.directory.is_symlink() or folder.is_symlink() or folder.resolve().parent != self.directory.resolve():
            raise ValueError('无效的备份目录。')
        return folder

    def _file(self, folder, name):
        path = folder / name
        if path.is_symlink() or path.resolve().parent != folder.resolve():
            raise ValueError('无效的备份文件。')
        return path

    def _manifest(self, uid):
        path = self._file(self._folder(uid), 'manifest.json')
        if path.stat().st_size > 8192:
            raise ValueError('备份说明损坏。')
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('version') not in (1, 2) or data.get('id') != uid
                or not isinstance(data.get('created_at'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', str(data.get('sha256', '')))
                or type(data.get('bytes')) is not int or not 0 < data['bytes'] <= MAX_BACKUP
                or not isinstance(data.get('counts'), dict)
                or set(data['counts']) != ({'runs', 'events', 'discussions', 'histories', 'notes'} if data['version'] == 2 else {'runs', 'events', 'discussions', 'histories'})
                or any(type(n) is not int or n < 0 for n in data['counts'].values())):
            raise ValueError('备份说明损坏。')
        return data

    def backups(self):
        items = []
        if self.directory.exists():
            for folder in self.directory.iterdir():
                if not re.fullmatch(r'[a-f0-9]{32}', folder.name) or not folder.is_dir():
                    continue
                try:
                    data = self._manifest(folder.name)
                    path = self._file(folder, 'memory.json')
                    items.append({**data, 'bytes': self._backup_size(folder.name), 'readable': path.stat().st_size == data['bytes']})
                except (ValueError, OSError):
                    try:size = self._backup_size(folder.name)
                    except (ValueError, OSError):size = 0
                    items.append({'id': folder.name, 'created_at': '', 'bytes': size, 'readable': False})
        return sorted(items, key=lambda item: item['created_at'], reverse=True)

    def _backup_size(self, uid):
        folder = self._folder(uid)
        return sum(path.stat().st_size for path in (self._file(folder, name)
            for name in ('memory.json', 'manifest.json', 'manifest.tmp')) if path.exists())

    def inventory(self):
        result = self.story.memory_inventory()
        result['histories'] = self.history.inventory()
        result['backups'] = self.backups()
        result['storage'] = {
            'database_bytes': sum(path.stat().st_size for path in (
                self.story.path, Path(str(self.story.path) + '-wal'), Path(str(self.story.path) + '-shm')) if path.exists()),
            'history_bytes': sum(item['bytes'] for item in result['histories']),
            'backup_bytes': sum(item['bytes'] for item in result['backups']),
        }
        return result

    def create_backup(self, expected):
        # One read transaction captures every route consistently, including WAL writes.
        with self.story.connection() as db:
            state = self.story._check(db, expected)
            tables = {table: [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in TABLES}
            histories = {}
            for item in self.history.inventory():
                if not item['readable']:
                    raise ValueError('存在无法读取的普通聊天，请先检查或清理，再备份。')
                rows = json.loads(self.history.path(item['id']).read_text(encoding='utf-8'))
                histories[item['id']] = [{key: value for key, value in row.items() if key in HISTORY_FIELDS}
                    for row in rows if isinstance(row, dict) and row.get('role') in ('metadata', 'human', 'ai')]
            payload = {'format': 'yuki-memory', 'version': 2, 'active_run_id': state['active_run_id'],
                       'tables': tables, 'histories': histories}
        content = encoded(payload)
        if len(content) > MAX_BACKUP:
            raise ValueError('单次备份超过256MB，请先分批整理记录。')
        uid = uuid4().hex
        manifest = {'id': uid, 'version': 2, 'created_at': timestamp(), 'sha256': digest(content), 'bytes': len(content),
                    'counts': {'runs': len(tables['runs']), 'events': len(tables['events']),
                               'discussions': len(tables['story_turns']), 'histories': len(histories),
                               'notes': len({row['note_id'] for row in tables['reading_notes']})}}
        self.directory.mkdir(parents=True, exist_ok=True)
        folder = self._folder(uid)
        folder.mkdir()
        try:
            self._file(folder, 'memory.json').write_bytes(content)
            # A snapshot becomes visible only after the small manifest is complete.
            temporary = self._file(folder, 'manifest.tmp')
            temporary.write_bytes(encoded(manifest))
            temporary.replace(folder / 'manifest.json')
        except BaseException:
            for name in ('memory.json', 'manifest.tmp', 'manifest.json'):
                self._file(folder, name).unlink(missing_ok=True)
            folder.rmdir()
            raise
        return manifest

    def _validate(self, payload):
        """Build an isolated database: validate schema, foreign keys and route ownership."""
        if (not isinstance(payload, dict) or payload.get('format') != 'yuki-memory' or payload.get('version') not in (1, 2)
                or set(payload) != {'format', 'version', 'active_run_id', 'tables', 'histories'}
                or not isinstance(payload['tables'], dict) or set(payload['tables']) != set(TABLES if payload['version'] == 2 else LEGACY_TABLES)
                or not isinstance(payload['histories'], dict)):
            raise ValueError('备份格式不受支持。')
        if payload['version'] == 1:
            payload['tables']['reading_notes'] = []
            for row in payload['tables']['runs']:
                row.setdefault('note_generation', 0)
            for row in payload['tables']['branches']:
                row.setdefault('note_cutoff', 0)
                row.setdefault('turn_cutoff', 0)
            for row in payload['tables']['story_turns']:
                row.setdefault('note_generation', 0)
        if sum(len(rows) if isinstance(rows, list) else 1000001 for rows in payload['tables'].values()) > 1000000:
            raise ValueError('备份记录过多。')
        for uid, rows in payload['histories'].items():
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', uid) or not isinstance(rows, list):
                raise ValueError('备份中的普通聊天格式错误。')
            for row in rows:
                if (not isinstance(row, dict) or row.get('role') not in ('metadata', 'human', 'ai')
                        or not set(row).issubset(HISTORY_FIELDS) or any(not isinstance(value, str) for value in row.values())):
                    raise ValueError('备份中的普通聊天格式错误。')
                if row['role'] != 'metadata' and not isinstance(row.get('content'), str):
                    raise ValueError('备份中的普通聊天格式错误。')
        with tempfile.TemporaryDirectory(prefix='yuki-memory-check-') as directory:
            store = StoryStore(Path(directory) / 'story.sqlite3')
            with store.connection(True) as db:
                db.execute('PRAGMA defer_foreign_keys=ON')
                for table in TABLES:
                    columns = [row[1] for row in db.execute(f'PRAGMA table_info({table})')]
                    for row in payload['tables'][table]:
                        if not isinstance(row, dict) or set(row) != set(columns):
                            raise ValueError('备份字段不匹配。')
                        for key, value in row.items():
                            if key in INTEGER_COLUMNS.get(table, set()):
                                if table == 'reading_notes' and key == 'origin_turn_id' and value is None:
                                    continue
                                if type(value) is not int or value < 0:
                                    raise ValueError('备份进度格式错误。')
                            elif value is None and table == 'branches' and key == 'parent_id':
                                continue
                            elif not isinstance(value, str) or len(value) > 60000:
                                raise ValueError('备份文字格式错误。')
                        db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', [row[key] for key in columns])
                active = payload['active_run_id']
                if active is not None and (not isinstance(active, str) or not db.execute('SELECT 1 FROM runs WHERE id=?', (active,)).fetchone()):
                    raise ValueError('备份的当前共读无效。')
                if db.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('备份的引用不完整。')
                for run in db.execute('SELECT * FROM runs'):
                    if run['mode'] not in ('galgame', 'novel'):
                        raise ValueError('备份的阅读模式无效。')
                    branch = store._branch(db, run['id'], run['branch_id'])
                    if not branch['fork_position'] <= run['position'] <= store._tip(db, branch):
                        raise ValueError('备份进度超出原文。')
                    for child in db.execute('SELECT * FROM branches WHERE run_id=?', (run['id'],)):
                        store._segments(db, run['id'], child['id'], store._tip(db, child))
                        if child['parent_id']:
                            parent = store._branch(db, run['id'], child['parent_id'])
                            if not parent['fork_position'] <= child['fork_position'] <= store._tip(db, parent):
                                raise ValueError('备份的分支起点无效。')
                        elif child['fork_position'] != 0:
                            raise ValueError('备份的正文起点无效。')
                        count, first, last = db.execute('SELECT COUNT(*),MIN(position),MAX(position) FROM events WHERE branch_id=?', (child['id'],)).fetchone()
                        if count and (first != child['fork_position'] + 1 or last - first + 1 != count):
                            raise ValueError('备份的正文不连续。')
                    for table in ('events', 'bookmarks', 'story_turns', 'progress_checks', 'reading_notes'):
                        for row in db.execute(f'SELECT * FROM {table} WHERE run_id=?', (run['id'],)):
                            store._branch(db, run['id'], row['branch_id'])
                    for turn in db.execute('SELECT * FROM story_turns WHERE run_id=?', (run['id'],)):
                        ids = json.loads(turn['evidence_ids'])
                        scope, args = store._scope(store._segments(db, run['id'], turn['branch_id'], turn['position']))
                        if not isinstance(ids, list) or any(type(event_id) is not int or not db.execute(
                                'SELECT 1 FROM events WHERE id=? AND run_id=? AND '+scope,
                                [event_id, run['id'], *args]).fetchone() for event_id in ids):
                            raise ValueError('备份的原文依据无效。')
                for event in db.execute('SELECT * FROM events'):
                    if event['kind'] not in ('dialogue', 'narration', 'choice', 'chapter') or event['confirmed_read'] != 1 or event['position'] < 1:
                        raise ValueError('备份的正文类型无效。')
                for note in db.execute('SELECT * FROM reading_notes'):
                    if (note['kind'] not in ('question', 'theory', 'feeling') or note['status'] not in ('open', 'resolved')
                            or not note['text'].strip() or len(note['text']) > 800 or not re.fullmatch(r'[a-f0-9]{32}', note['note_id'])):
                        raise ValueError('备份的手记格式无效。')
                    branch = store._branch(db, note['run_id'], note['branch_id'])
                    if not branch['fork_position'] <= note['position'] <= store._tip(db, branch):
                        raise ValueError('备份的手记超出阅读范围。')
                    scope, args = store._scope(store._segments(db, note['run_id'], note['branch_id'], note['position']))
                    ids = json.loads(note['evidence_ids'])
                    if not isinstance(ids, list) or len(ids) > 12 or any(type(event_id) is not int or not db.execute(
                            'SELECT 1 FROM events WHERE id=? AND run_id=? AND '+scope, [event_id, note['run_id'], *args]).fetchone() for event_id in ids):
                        raise ValueError('备份的手记引用无效。')
                    if note['origin_turn_id'] is not None:
                        turn_scope, turn_args = store._version_scope(db, {'id':note['run_id'],'branch_id':note['branch_id'],'position':note['position']}, 'turn_cutoff')
                        if not db.execute('SELECT 1 FROM story_turns WHERE id=? AND run_id=? AND '+turn_scope,
                                [note['origin_turn_id'], note['run_id'], *turn_args]).fetchone():
                            raise ValueError('备份的讨论来源无效。')
                if db.execute('SELECT note_id FROM reading_notes GROUP BY note_id HAVING COUNT(DISTINCT run_id)>1').fetchone():
                    raise ValueError('备份的手记跨越了不同共读。')

    def load_backup(self, uid):
        manifest = self._manifest(uid)
        path = self._file(self._folder(uid), 'memory.json')
        if path.stat().st_size != manifest['bytes'] or path.stat().st_size > MAX_BACKUP:
            raise ValueError('备份大小不匹配。')
        content = path.read_bytes()
        if digest(content) != manifest['sha256']:
            raise ValueError('备份校验失败，未恢复任何记录。')
        try:
            payload = json.loads(content)
            if payload.get('version') != manifest['version']:
                raise ValueError('备份版本不一致。')
            self._validate(payload)
            tables = payload['tables']
            counts = {'runs': len(tables['runs']), 'events': len(tables['events']),
                      'discussions': len(tables['story_turns']), 'histories': len(payload['histories'])}
            if manifest['version'] == 2:
                counts['notes'] = len({row['note_id'] for row in tables['reading_notes']})
            if counts != manifest['counts']:
                raise ValueError('备份数量不匹配。')
        except Exception:
            raise ValueError('备份内容不完整或版本不兼容，未恢复任何记录。') from None
        return payload, manifest

    def _restore(self, uid, expected, revision):
        payload, manifest = self.load_backup(uid)
        if manifest['sha256'] != revision:
            raise StoryConflict('备份有了变化，请重新预览。')
        tables, created_histories = payload['tables'], []
        mappings = {table: {row['id']: uuid4().hex for row in tables[table]} for table in ('games', 'runs', 'branches', 'bookmarks')}
        event_ids, note_versions = {}, {}
        turn_ids, note_ids = {}, {row['note_id']: uuid4().hex for row in tables['reading_notes']}
        try:
            with self.story.connection(True) as db:
                self.story._check(db, expected)
                db.execute('PRAGMA defer_foreign_keys=ON')
                for row in tables['games']:
                    existing = db.execute('SELECT id FROM games WHERE title=?', (row['title'],)).fetchone()
                    if existing:
                        mappings['games'][row['id']] = existing['id']
                    else:
                        db.execute('INSERT INTO games VALUES(?,?)', (mappings['games'][row['id']], row['title']))
                for table in TABLES[1:]:
                    for original in tables[table]:
                        row = dict(original)
                        if table in mappings:
                            row['id'] = mappings[table][row['id']]
                        elif 'id' in row:
                            del row['id']
                        for field, target in (('game_id', 'games'), ('run_id', 'runs'), ('branch_id', 'branches'), ('parent_id', 'branches')):
                            if row.get(field) is not None:
                                row[field] = mappings[target][row[field]]
                        if table == 'runs':
                            row['name'] = row['name'][:48] + '（恢复副本）'
                            row['created_at'] = timestamp()
                        if table in ('story_turns', 'reading_notes'):
                            row['evidence_ids'] = json.dumps([event_ids[event_id] for event_id in json.loads(row['evidence_ids'])])
                        if table == 'reading_notes':
                            row['note_id'] = note_ids[row['note_id']]
                            if row['origin_turn_id'] is not None:
                                row['origin_turn_id'] = turn_ids[row['origin_turn_id']]
                        cursor = db.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', list(row.values()))
                        if table == 'events':
                            event_ids[original['id']] = cursor.lastrowid
                        elif table == 'story_turns':
                            turn_ids[original['id']] = cursor.lastrowid
                        elif table == 'reading_notes':
                            note_versions[original['id']] = cursor.lastrowid
                for branch in tables['branches']:
                    for column, remapped in (('note_cutoff', note_versions), ('turn_cutoff', turn_ids)):
                        cutoff = max((new_id for old_id, new_id in remapped.items() if old_id <= branch[column]), default=0)
                        db.execute(f'UPDATE branches SET {column}=? WHERE id=?', (cutoff, mappings['branches'][branch['id']]))
                for rows in payload['histories'].values():
                    history_id = self.history.create()
                    created_histories.append(history_id)
                    self.history.write(history_id, [{'role':'metadata','timestamp':timestamp(),'name':'恢复副本'}]
                        + [row for row in rows if row['role'] != 'metadata'])
                active = payload['active_run_id']
                if active:
                    db.execute('UPDATE reading_state SET active_run_id=? WHERE singleton=1', (mappings['runs'][active],))
                self.story._boundary(db)
        except BaseException:
            for history_id in created_histories:
                self.history.path(history_id).unlink(missing_ok=True)
            raise
        return {'restored_runs': len(tables['runs']), 'restored_histories': created_histories}

    def plan(self, action, uid, expected):
        with self.story.connection() as db:
            self.story._check(db, expected)
        if action in ('clear_discussion', 'delete_run'):
            item = next((row for row in self.story.memory_inventory()['runs'] if row['id'] == uid), None)
            if not item:
                raise ValueError('没有找到这篇共读。')
            revision = item['revision']
            description = (f'清空《{item["title"]}》·{item["name"]} 的全部 {item["story_turns"]} 轮讨论（含旧讨论和其他路线）。原文、手记、书签和进度保留。'
                if action == 'clear_discussion' else
                f'删除《{item["title"]}》·{item["name"]}：{item["events"]} 句原文、{item["branches"]} 条路线、{item["bookmarks"]} 个书签、{item["story_turns"]} 轮讨论、{item["notes"]} 条手记及阅读回顾。其他共读和已有备份保留。')
        elif action == 'delete_history':
            revision = self.history.revision(uid)
            description = '删除这篇普通聊天的全部内容。共读原文和已有备份保留。'
        elif action == 'restore_backup':
            _, manifest = self.load_backup(uid)
            revision = manifest['sha256']
            counts = manifest['counts']
            description = f'恢复为独立副本：{counts["runs"]} 篇共读、{counts["events"]} 句原文、{counts["histories"]} 篇普通聊天、{counts.get("notes",0)} 条手记。现有记录保留；切换到备份中正在读的副本，取词会暂停。'
        elif action == 'delete_backup':
            folder = self._folder(uid)
            revision = self._backup_revision(folder)
            description = '永久删除这个本机备份。当前共读和普通聊天保留；此备份中的已删原文也会一并移除。'
        else:
            raise ValueError('不支持这项记忆操作。')
        now = time.monotonic()
        self.plans = {key: plan for key, plan in self.plans.items() if plan['expires'] > now}
        if len(self.plans) >= 32:
            self.plans.pop(next(iter(self.plans)))
        token = uuid4().hex
        self.plans[token] = {'action': action, 'id': uid, 'epoch': expected, 'revision': revision, 'expires': now + 300}
        return {'token': token, 'description': description, 'action': action}

    def _backup_revision(self, folder):
        if not folder.is_dir() or any(path.name not in ('manifest.json', 'memory.json', 'manifest.tmp') for path in folder.iterdir()):
            raise ValueError('备份目录不完整，暂不支持清理。')
        parts = []
        for name in ('manifest.json', 'memory.json', 'manifest.tmp'):
            path = self._file(folder, name)
            if path.exists():
                stat = path.stat()
                parts.append((name, stat.st_size, stat.st_mtime_ns))
        return digest(encoded(parts))

    def execute(self, token):
        plan = self.plans.pop(token, None)
        if not plan or plan['expires'] < time.monotonic():
            raise StoryConflict('操作预览已过期，请重新查看影响范围。')
        action, uid, expected, revision = (plan[key] for key in ('action', 'id', 'epoch', 'revision'))
        with self.story.connection() as db:
            self.story._check(db, expected)
        result = {'action': action, 'id': uid}
        if action in ('clear_discussion', 'delete_run'):
            self.story.clean_memory(uid, action, expected, revision)
        elif action == 'delete_history':
            self.history.delete(uid, revision)
            self.story.memory_boundary(expected)
        elif action == 'restore_backup':
            result.update(self._restore(uid, expected, revision))
        elif action == 'delete_backup':
            folder = self._folder(uid)
            if self._backup_revision(folder) != revision:
                raise StoryConflict('备份有了变化，请重新预览。')
            for name in ('manifest.json', 'memory.json', 'manifest.tmp'):
                self._file(folder, name).unlink(missing_ok=True)
            folder.rmdir()
        return result
