"""Player-confirmed names and local pronouns. Original passages stay unchanged."""
import json
import re
from uuid import uuid4


def pronouns(text):
    return set(re.findall(r'[她他](?![们們])', (text or '').replace('其他', '')))


def valid_character_name(value):
    return isinstance(value, str) and 1 <= len(value) <= 30 and all(char.isalnum() or char in '・·.-_ ' for char in value)


def corrected_people(events, corrections):
    from .yuki_story import build_characters
    people = build_characters(events)
    for rule in corrections:
        if rule['kind'] != 'alias':
            target=rule['target']
            if target not in people:
                witnessed=[event for event in events if event['kind']=='narration' and target in event['text']]
                if witnessed:people[target]={'name':target,'aliases':[],'claims':[],'witnessed':witnessed}
            continue
        label, target = rule['label'], rule['target']
        for person in people.values():
            person['aliases'] = [alias for alias in person['aliases'] if alias != label]
        person = people.setdefault(target, {'name': target, 'aliases': [], 'claims': [], 'witnessed': []})
        if label != target and label not in person['aliases']:
            person['aliases'].append(label)
        person.setdefault('user_aliases', []).append(label)
        source = people.pop(label, None) if label != target else None
        for bucket in ('claims', 'witnessed'):
            rows = person[bucket] + (source[bucket] if source else [])
            if bucket == 'witnessed':
                rows += [event for event in events if event['kind'] == 'narration' and (label in event['text'] or target in event['text'])]
            person[bucket] = sorted({row['id']: row for row in rows}.values(), key=lambda row: row['position'])
    return people


class CharacterStoreMixin:
    def _visible_corrections(self, db, run, events=None):
        from .yuki_story import ui_line
        events = self._events(db, run) if events is None else events
        allowed = {event['id']: event for event in events}
        scope, args = self._version_scope(db, run, 'character_cutoff')
        seen, keys, result = set(), set(), []
        for row in db.execute('SELECT * FROM character_corrections WHERE run_id=? AND '+scope+' ORDER BY id DESC', [run['id'], *args]):
            if row['correction_id'] in seen:
                continue
            seen.add(row['correction_id'])
            ids = json.loads(row['evidence_ids'])
            if row['anchor_event_id'] not in allowed or not all(event_id in allowed for event_id in ids):
                continue
            key = (row['kind'], row['label'], row['anchor_event_id'] if row['kind'] == 'pronoun' else None)
            if key in keys:
                continue
            keys.add(key)
            item = dict(row)
            item.update(evidence_ids=ids, evidence=[ui_line(allowed[event_id]) for event_id in ids],
                        anchor_position=allowed[row['anchor_event_id']]['position'],
                        active=row['kind'] == 'alias' or allowed[row['anchor_event_id']]['position'] == run['position'])
            result.append(item)
        return result

    def character_state(self, before=None):
        from .yuki_story import build_characters, dominant_narration_name, segment_scenes
        with self.connection() as db:
            state = self._state(db)
            if not state['active_run_id']:
                return {'epoch': state['epoch'], 'run_id': None, 'characters': [], 'corrections': [], 'count': 0}
            run = self._run(db)
            events = self._events(db, run)
            rules = self._visible_corrections(db, run, events)
            names = list(build_characters(events))
            name, ambiguous = dominant_narration_name(segment_scenes(events))
            if name and not ambiguous and name not in names:
                names.append(name)
            for rule in rules:
                if rule['target'] not in names:
                    names.append(rule['target'])
            page = [rule for rule in rules if before is None or rule['id'] < before][:51]
            return {'epoch': state['epoch'], 'run_id': run['id'], 'branch_id': run['branch_id'], 'position': run['position'],
                    'revision': run['note_generation'], 'characters': names[:200], 'corrections': page[:50],
                    'count': len(rules), 'more': page[49]['id'] if len(page) > 50 else None}

    def _prepare_character(self, db, run_id, position, kind, label, target, expected, correction_id=None, version=None, revision=None):
        from .yuki_story import StoryConflict, ui_line
        self._check(db, expected)
        run = self._run(db)
        if run['id'] != run_id or run['position'] != position:
            raise StoryConflict('阅读位置已改变，请重新核对人物对应。')
        if revision is not None and run['note_generation'] != revision:
            raise StoryConflict('阅读记忆已更新，请重新核对人物对应。')
        label, target = label.strip(), target.strip()
        if kind not in ('alias', 'pronoun') or not valid_character_name(label) or not valid_character_name(target):
            raise ValueError('请填写1～30字的称呼和人物名，不要包含特殊控制符。')
        if target in ('她','他','它','我','你','我们','他们','她们','旁白','叙述','未知','其他'):
            raise ValueError('目标请填写已读内容里出现过的人物名称。')
        if kind == 'pronoun' and label not in ('她', '他'):
            raise ValueError('当前指代只支持“她”或“他”。')
        if kind == 'alias' and (label in ('她','他','它','我','你','他们','她们') or label == target):
            raise ValueError('称呼不能与目标相同；“她/他”请使用当前指代。')
        events = self._events(db, run)
        if not events:
            raise ValueError('请先收录已经读过的原文。')
        rules = self._visible_corrections(db, run, events)
        current = None
        if correction_id:
            current = next((rule for rule in rules if rule['correction_id'] == correction_id), None)
            if not current or current['id'] != version:
                raise StoryConflict('这条纠正已改变或不在当前进度，请刷新后重试。')
            if current['kind'] != kind or current['label'] != label:
                raise ValueError('修改只更换目标人物；要更改称呼或类型，请先删除原对应。')
        aliases = {rule['label']: rule['target'] for rule in rules if rule['kind'] == 'alias' and rule['correction_id'] != correction_id}
        if kind == 'pronoun':
            target = aliases.get(target, target)
        elif target in aliases or label in aliases.values():
            raise ValueError('这些称呼会形成多层或循环对应，请先整理已有称呼。')
        anchor_id = current['anchor_event_id'] if current and kind == 'pronoun' else events[-1]['id']
        for rule in rules:
            if (rule['correction_id'] != correction_id and rule['kind'] == kind and rule['label'] == label
                    and (kind == 'alias' or rule['anchor_event_id'] == anchor_id)):
                raise StoryConflict('这里已经有同一称呼或指代的对应，请修改原记录。')
        def mentions(event, word):
            return word in ((event.get('speaker') or '')+' '+event['text'])
        target_line = next((event for event in reversed(events) if mentions(event, target)), None)
        alias_line = next((event for event in reversed(events) if mentions(event, label)), None) if kind == 'alias' else next(event for event in events if event['id'] == anchor_id)
        if not target_line:
            raise ValueError('目标人物尚未出现在当前路线的已读原文中。')
        if alias_line is None:
            raise ValueError('这个称呼尚未出现在当前路线的已读原文中。')
        evidence = list({event['id']: event for event in (alias_line, target_line)}.values())
        anchor = next(event for event in events if event['id'] == anchor_id)
        description = (f'自第 {position} 句起，在当前路线把“{label}”作为“{target}”的称呼。' if kind == 'alias' else
                       f'仅在讨论第 {anchor["position"]} 句时，把“{label}”理解为“{target}”；推进后不沿用。')
        return run, {'kind':kind, 'label':label, 'target':target, 'anchor_event_id':anchor_id,
                     'evidence_ids':[event['id'] for event in evidence], 'evidence':[ui_line(event) for event in evidence],
                     'description':description, 'position':position}

    def preview_character(self, run_id, position, kind, label, target, expected, correction_id=None, version=None, revision=None):
        with self.connection() as db:
            _, prepared = self._prepare_character(db, run_id, position, kind, label, target, expected, correction_id, version, revision)
            return prepared

    def save_character(self, run_id, position, kind, label, target, expected, correction_id=None, version=None, revision=None):
        from .yuki_story import timestamp
        with self.connection(True) as db:
            run, item = self._prepare_character(db, run_id, position, kind, label, target, expected, correction_id, version, revision)
            db.execute('INSERT INTO character_corrections(correction_id,run_id,branch_id,position,kind,label,target,anchor_event_id,evidence_ids,created_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)', (correction_id or uuid4().hex, run['id'], run['branch_id'], run['position'],
                       item['kind'], item['label'], item['target'], item['anchor_event_id'], json.dumps(item['evidence_ids']), timestamp()))
            db.execute('UPDATE runs SET note_generation=note_generation+1 WHERE id=?', (run['id'],))

    def delete_character(self, run_id, correction_id, version, expected):
        from .yuki_story import StoryConflict
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            if run['id'] != run_id:
                raise StoryConflict('共读已经切换。')
            current = next((rule for rule in self._visible_corrections(db, run) if rule['correction_id'] == correction_id), None)
            if not current or current['id'] != version:
                raise StoryConflict('这条纠正已改变或不在当前进度，请刷新后重试。')
            db.execute('DELETE FROM character_corrections WHERE run_id=? AND correction_id=?', (run_id, correction_id))
            db.execute('DELETE FROM reading_recaps WHERE run_id=?', (run_id,))
            db.execute('UPDATE runs SET note_generation=note_generation+1 WHERE id=?', (run_id,))
