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
from .yuki_characters import CharacterStoreMixin, corrected_people, pronouns

from .yuki_paths import data_root
ROOT = data_root()


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
    mode: str = 'galgame'
    focus_position: int = 0
    referent: str = ''
    referent_ambiguous: bool = False
    notes: tuple = ()
    note_revision: int = 0
    character_corrections: tuple = ()
    referent_source: str = ''


# Derived only from events already inside the current route prefix. No model call.
SCENE_EVENT_LIMIT = 40
WORKING_SCENE_SHORT = 8
EARLIER_BLOCK_LIMIT = 2
CURRENT_KEEP = 12
TAIL_LINES = 8
SPAN_RADIUS = 2
PROMPT_LINE = 160
OPEN_BUDGET = 2400
CATALOG_LIMIT = 8
DISCUSSION_TURNS = 6
DISCUSSION_CLIP = 180
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


QUESTION_CUES = (
    (('长什么样', '长相', '模样', '外貌', '长得', '什么样', '什麼樣', '長相'),
     ('脸', '眼', '眉', '头', '身材', '身量', '疤', '臉', '頭', '身量', '鼻子')),
)


def question_terms(question):
    terms = set(re.findall(r'[a-z0-9_]{2,}', (question or '').lower()))
    for phrase in re.findall(r'[\u3400-\u9fff]+', question or ''):
        for size in (2, 3, 4):
            if len(phrase) >= size:
                terms.update(phrase[index:index + size] for index in range(len(phrase) - size + 1))
    for keys, words in QUESTION_CUES:
        if any(key in (question or '') for key in keys):
            terms.update(words)
    return terms - STOPWORDS


def term_weight(term):
    length = len(term)
    if length >= 4:
        return 4
    if length == 3:
        return 3
    return 1


def event_score(event, terms, names=()):
    blob = ((event.get('speaker') or '') + (event.get('text') or '')).lower()
    score = sum(term_weight(term) for term in terms if term in blob)
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


CHAPTER_RE = re.compile(r'^第[0-9０-９一二三四五六七八九十百千零〇两]+[章节回]')
SAID_RE = re.compile(r'^([\u3400-\u9fff]{1,8}?)(?:说道|问道|答道|笑道|叫道|说|道)[：:，,]?\s*[“「](.+)[”」]\s*$')
SAID_SKIP = {'他', '她', '我', '你', '它', '他们', '她们', '我们'}


def progress_key(text):
    return ''.join((text or '').split())[:80]


def line_keys(event):
    found = {progress_key(event.get('text')), progress_key(event.get('raw_text'))}
    speaker = event.get('speaker') or ''
    if speaker:
        found.add(progress_key(speaker + '：' + (event.get('text') or '')))
    return {item for item in found if len(item) >= 12}


def locate_sequence(events, keys, tip):
    """Find an earlier stretch that repeats these lines. Short lines never count."""
    keys = [progress_key(key) for key in keys]
    if not keys or len(keys[0]) < 12:
        return None
    grouped = {}
    for event in events:
        grouped.setdefault(event['branch_id'], []).append(event)
    for branch_id, rows in grouped.items():
        rows.sort(key=lambda item: item['position'])
        stored = [line_keys(item) for item in rows]
        width = len(keys)
        for index, row in enumerate(rows):
            if row['position'] > tip - 2 or index + width > len(rows):
                continue
            if all(keys[offset] in stored[index + offset] for offset in range(width)):
                return {'position': row['position'], 'branch_id': branch_id, 'count': width}
    return None


def starts_scene(event):
    if event.get('kind') == 'chapter':
        return True
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


def catalog_line(events):
    speakers = scene_speakers(events)
    narration = next((event for event in events if event['kind'] == 'narration' and event.get('text', '').strip()), None)
    bits = [f'第{events[0]["position"]}–{events[-1]["position"]}句']
    if speakers:
        bits.append('、'.join(speakers))
    if narration:
        bits.append(clip(narration['text'], 36))
    return ' · '.join(bits)


def clipped_cost(event):
    return min(len(event.get('text') or ''), PROMPT_LINE) + 48


def focus_text(text, terms, limit=PROMPT_LINE):
    """Keep the part of a long line that the question actually names."""
    text = ' '.join((text or '').split())
    if len(text) <= limit:
        return text
    lower = text.lower()
    hits = [lower.find(term) for term in terms if term and len(term) >= 2 and term in lower]
    anchor = min((pos for pos in hits if pos >= 0), default=0)
    start = max(0, anchor - limit // 5)
    body = text[start:start + limit]
    if start:
        body = '…' + body[1:]
    if start + limit < len(text):
        body = body[:-1] + '…'
    return body


def hit_indexes(scene, terms, names, explicit):
    ranked = sorted(range(len(scene)), key=lambda index: (
        event_score(scene[index], terms, names) + (8 if str(scene[index]['position']) in explicit else 0),
        scene[index]['position']), reverse=True)
    return [index for index in ranked
            if event_score(scene[index], terms, names) > 0 or str(scene[index]['position']) in explicit][:2]


def span_indexes(scene, terms, names, explicit, role):
    count = len(scene)
    if not count:
        return []
    if role == 'anchor':
        return list(range(max(0, count - 4), count))
    if role in ('current', 'previous') and count <= CURRENT_KEEP:
        return list(range(count))
    if role == 'current':
        indexes = set(range(max(0, count - TAIL_LINES), count))
        for index, event in enumerate(scene):
            if event['kind'] == 'narration' and (event.get('text') or '').strip():
                indexes.add(index)
                break
        # A long scene still has to open the line the question names, not only its ending.
        for hit in hit_indexes(scene, terms, names, explicit):
            indexes.update(range(max(0, hit - SPAN_RADIUS), min(count, hit + SPAN_RADIUS + 1)))
        return sorted(indexes)
    hits = hit_indexes(scene, terms, names, explicit)
    if not hits and role == 'previous':
        hits = [count - 1]
    indexes = set()
    for hit in hits:
        indexes.update(range(max(0, hit - SPAN_RADIUS), min(count, hit + SPAN_RADIUS + 1)))
    if hits:
        for index, event in enumerate(scene):
            if event['kind'] == 'narration' and (event.get('text') or '').strip():
                indexes.add(index)
                break
    return sorted(indexes)


def catalog_pick(scenes, opened_ids, terms, names):
    closed = [scene for scene in scenes if id(scene) not in opened_ids]
    picked = []
    for scene in sorted(closed, key=lambda item: (block_score(item, terms, names), item[-1]['position']), reverse=True):
        if len(picked) >= 2 or block_score(scene, terms, names) <= 0:
            break
        picked.append(scene)
    for scene in reversed(closed):
        if scene in picked:
            continue
        picked.append(scene)
        if len(picked) >= CATALOG_LIMIT:
            break
    return picked


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


def present_memory(events, corrections=()):
    """Short, route-local memory for the reading panel. Claims stay separate from narration."""
    characters = []
    people = [person for person in corrected_people(events, [rule for rule in corrections if rule['kind']=='alias' or rule['active']]).values() if person['claims'] or person['witnessed']]
    people.sort(key=lambda person: max(line['position'] for line in person['claims'] + person['witnessed']), reverse=True)
    for person in people:
        characters.append({'name': person['name'], 'aliases': person['aliases'][:3],
                           'user_aliases': [alias for alias in person['aliases'][:3] if alias in person.get('user_aliases', [])],
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


def has_pronoun(question):
    return bool(pronouns(question))


NARRATION_STOP = {
    '自己', '他们', '她们', '我们', '你们', '没有', '可是', '可以', '什么', '时候', '知道', '到了',
    '一个', '这个', '那个', '因为', '所以', '但是', '如果', '已经', '还是', '就是', '不是', '只是',
    '于是', '然后', '现在', '今天', '心里', '觉得', '仿佛', '似乎', '这样', '那么', '这么', '怎么',
    '那里', '这里', '出来', '进去', '起来', '不能', '他们', '她們', '他們', '沒有', '可是', '可以',
    '什麼', '時候', '知道', '已經', '還是', '就是', '不是', '只是', '於是', '然後', '現在', '今天',
    '心裡', '覺得', '不能', '車夫', '洋車', '他的', '的車', '輛車',
}


def dominant_narration_name(scenes):
    """Novels often never mark a speaker. A name repeated far more than other words can fill 他/她."""
    def tally(text):
        counts = {}
        for index in range(len(text) - 1):
            token = text[index:index + 2]
            if token in NARRATION_STOP or not re.fullmatch(r'[\u3400-\u9fff]{2}', token):
                continue
            if token[0] in '的了是在着著与與把被就也都还還不' or token[1] in '的了是在着著':
                continue
            counts[token] = counts.get(token, 0) + 1
        return counts

    blobs = [''.join((event.get('text') or '') for event in scene) for scene in scenes[-2:]]
    counts = tally(''.join(blobs))
    if not counts or max(counts.values()) < 6:
        counts = tally(''.join(''.join((event.get('text') or '') for event in scene) for scene in scenes))
    if not counts:
        return '', False
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    best, best_count = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    if best_count < 6:
        return '', False
    return best, bool(second and best_count < second + 3)


def resolve_pronoun(question, scenes, people):
    """她/他 points at the latest clear speaker, or the repeated name in narration."""
    if any(name and name in (question or '') for name in people):
        return '', False
    if any(alias and alias in (question or '') for person in people.values() for alias in person['aliases']):
        return '', False
    if not has_pronoun(question):
        return '', False
    for scene in reversed(scenes):
        speakers = []
        last = ''
        for event in scene:
            if event.get('speaker') and event['speaker'] not in speakers:
                speakers.append(event['speaker'])
            if event.get('speaker'):
                last = event['speaker']
        if not speakers:
            continue
        if len(speakers) == 1:
            return speakers[0], False
        return last, True
    return dominant_narration_name(scenes)


def choose_characters(people, question, working, referent=''):
    selected = []

    def add(name):
        if name in people and name not in selected:
            selected.append(name)

    if referent:
        add(referent)
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


def choose_blocks(scenes, terms, names, explicit, referent=''):
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
    if referent:
        for scene in reversed(scenes):
            if not scene or scene is current or scene in loaded:
                continue
            if any(event.get('speaker') == referent for event in scene):
                add(scene)
                opened += 1
                break
    ranked = sorted((scene for scene in scenes if scene not in loaded),
                    key=lambda scene: (block_score(scene, terms, names), scene[-1]['position']), reverse=True)
    best = block_score(ranked[0], terms, names) if ranked else 0
    for scene in ranked:
        score = block_score(scene, terms, names)
        if opened >= EARLIER_BLOCK_LIMIT or score <= 0:
            break
        # A shared two-character fragment should not open a whole other passage.
        if best >= 8 and score * 4 < best:
            continue
        add(scene)
        opened += 1
    return loaded


def take_span(scene, role, terms, names, explicit, chosen, used, budget):
    """Open the matching neighborhood, not every line stored in the block."""
    pending = [scene[index] for index in span_indexes(scene, terms, names, explicit, role) if scene[index]['id'] not in used]
    if not pending:
        return budget, False
    if sum(clipped_cost(event) for event in pending) > budget and len(pending) > 1:
        scored = [event for event in pending if event_score(event, terms, names) > 0 or str(event['position']) in explicit]
        setting = next((event for event in pending if event['kind'] == 'narration'), None)
        tail = pending[-min(4, len(pending)):] if role == 'current' else []
        narrowed = [event for event in pending if event in scored or event in tail or event is setting]
        pending = narrowed or pending[:1]
    kept = False
    for event in pending:
        cost = clipped_cost(event)
        if cost > budget and chosen:
            continue
        chosen.append(event)
        used.add(event['id'])
        budget -= cost
        kept = True
    return budget, kept


def pack_story_context(snapshot, question=''):
    """Prompt view of a snapshot. Full lines stay in the snapshot for evidence."""
    terms = question_terms(question)
    loaded_ids = set()
    blocks = []
    for scene in snapshot.scenes:
        if not scene.get('loaded'):
            continue
        lines = []
        for event in snapshot.events:
            if scene['start'] <= event['position'] <= scene['end']:
                loaded_ids.add(event['id'])
                lines.append({'id': event['id'], 'position': event['position'], 'kind': event['kind'],
                              'speaker': event['speaker'], 'text': focus_text(event.get('text') or '', terms)})
        blocks.append({'start': scene['start'], 'end': scene['end'], 'role': scene.get('role'),
                       'speakers': scene.get('speakers') or [], 'at_choice': bool(scene.get('at_choice')),
                       'scene_lines': scene.get('lines'), 'shown': len(lines), 'lines': lines})
    catalog = [{'start': scene['start'], 'end': scene['end'], 'summary': clip(scene.get('summary') or '', 80)}
               for scene in snapshot.scenes if not scene.get('loaded')]
    characters = []
    for person in snapshot.characters:
        def slim(item):
            row = {'position': item.get('position')}
            if item.get('id'):
                row['id'] = item['id']
            if item.get('id') not in loaded_ids:
                row['text'] = focus_text(item.get('text') or '', terms, 80)
            return row
        characters.append({'name': person.get('name'), 'aliases': list(person.get('aliases') or []),
                           'user_aliases': list(person.get('user_aliases') or []),
                           'claims': [slim(item) for item in person.get('claims') or []],
                           'witnessed': [slim(item) for item in person.get('witnessed') or []]})
    current = next((scene for scene in snapshot.scenes if scene.get('role') == 'current'), None)
    return {'mode': getattr(snapshot, 'mode', None) or 'galgame', 'game': snapshot.title, 'reading_position': snapshot.position,
            'reading': {'block': f'第{current["start"]}–{current["end"]}句' if current else '',
                        'at_choice': bool(current and current.get('at_choice')),
                        'present_speakers': list(current.get('speakers') or []) if current else [],
                        'confirmed_position': snapshot.position,
                        'asked_about': getattr(snapshot, 'focus_position', None) or snapshot.position,
                        'referent': getattr(snapshot, 'referent', '') or '',
                        'referent_source': getattr(snapshot, 'referent_source', '') or '',
                        'character_corrections': [{'kind':rule['kind'],'label':rule['label'],'target':rule['target'],
                            'scope':'line' if rule['kind']=='pronoun' else 'route','anchor_position':rule['anchor_position'],
                            'linked_event_ids':[event_id for event_id in rule['evidence_ids'] if event_id in loaded_ids]}
                            for rule in getattr(snapshot, 'character_corrections', ())],
                        'referent_ambiguous': bool(getattr(snapshot, 'referent_ambiguous', False))},
            'loaded_blocks': blocks, 'scenes': catalog, 'characters': characters,
            'player_notes': [{'kind': note['kind'], 'status': note['status'], 'text': focus_text(note['text'], terms, 200),
                              'linked_event_ids': [event_id for event_id in note['evidence_ids'] if event_id in loaded_ids]}
                             for note in getattr(snapshot, 'notes', ())],
            'route_discussion': [{'speaker': 'player' if item['role'] == 'human' else 'Yuki',
                                  'text': focus_text(item.get('content') or '', terms, DISCUSSION_CLIP)}
                                 for item in snapshot.messages]}


class StoryStore(CharacterStoreMixin):
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
                    chat_generation INTEGER NOT NULL DEFAULT 0,mode TEXT NOT NULL DEFAULT 'galgame',created_at TEXT NOT NULL);
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
                CREATE TABLE IF NOT EXISTS reading_notes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,note_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(id),branch_id TEXT NOT NULL REFERENCES branches(id),
                    position INTEGER NOT NULL,kind TEXT NOT NULL,text TEXT NOT NULL,status TEXT NOT NULL,
                    evidence_ids TEXT NOT NULL,origin_turn_id INTEGER REFERENCES story_turns(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS note_scope ON reading_notes(run_id,branch_id,position);
                CREATE TABLE IF NOT EXISTS character_corrections(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,correction_id TEXT NOT NULL,
                    run_id TEXT NOT NULL REFERENCES runs(id),branch_id TEXT NOT NULL REFERENCES branches(id),
                    position INTEGER NOT NULL,kind TEXT NOT NULL,label TEXT NOT NULL,target TEXT NOT NULL,
                    anchor_event_id INTEGER NOT NULL REFERENCES events(id),evidence_ids TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS character_scope ON character_corrections(run_id,branch_id,position);
                CREATE TABLE IF NOT EXISTS reading_recaps(
                    run_id TEXT NOT NULL REFERENCES runs(id),branch_id TEXT NOT NULL REFERENCES branches(id),
                    fingerprint TEXT NOT NULL,content TEXT NOT NULL,PRIMARY KEY(run_id,branch_id));
                CREATE TABLE IF NOT EXISTS progress_checks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL REFERENCES runs(id),
                    branch_id TEXT NOT NULL,position INTEGER NOT NULL,kind TEXT NOT NULL,
                    focus_position INTEGER NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reading_state(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),active_run_id TEXT REFERENCES runs(id),epoch INTEGER NOT NULL);
                INSERT OR IGNORE INTO reading_state VALUES(1,NULL,0);
            ''')
        with self.connection(True) as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info(runs)')}
            if 'mode' not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN mode TEXT NOT NULL DEFAULT 'galgame'")
            if 'note_generation' not in columns:
                db.execute('ALTER TABLE runs ADD COLUMN note_generation INTEGER NOT NULL DEFAULT 0')
            branch_columns = {row[1] for row in db.execute('PRAGMA table_info(branches)')}
            for column in ('note_cutoff', 'turn_cutoff', 'character_cutoff'):
                if column not in branch_columns:
                    db.execute(f'ALTER TABLE branches ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0')
            turn_columns = {row[1] for row in db.execute('PRAGMA table_info(story_turns)')}
            if 'note_generation' not in turn_columns:
                db.execute('ALTER TABLE story_turns ADD COLUMN note_generation INTEGER NOT NULL DEFAULT 0')

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

    def create_run(self, title, name, expected, mode='galgame'):
        if mode not in ('galgame', 'novel'):
            raise ValueError('请选择 Galgame 或小说。')
        title = title.strip()
        name = name.strip() or ('小说共读' if mode == 'novel' else '初见共读')
        if not title:
            raise ValueError('请填写作品名称。')
        with self.connection(True) as db:
            self._check(db, expected)
            game = db.execute('SELECT id FROM games WHERE title=?', (title,)).fetchone()
            game_id = game['id'] if game else uuid4().hex
            if not game:
                db.execute('INSERT INTO games VALUES(?,?)', (game_id, title))
            run_id, branch_id = uuid4().hex, uuid4().hex
            db.execute('INSERT INTO runs(id,game_id,name,branch_id,mode,created_at) VALUES(?,?,?,?,?,?)',
                       (run_id, game_id, name, branch_id, mode, timestamp()))
            db.execute('INSERT INTO branches(id,run_id,name,parent_id,fork_position,created_at) VALUES(?,?,?,?,?,?)',
                       (branch_id, run_id, '正文' if mode == 'novel' else '共同线', None, 0, timestamp()))
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

    def _version_scope(self, db, run, cutoff_column):
        """Parent edits/discussions created after a fork cannot enter its child, even at the same position."""
        branch_id, position, ceiling, seen = run['branch_id'], run['position'], None, set()
        pieces, args = [], []
        while branch_id:
            if branch_id in seen:
                raise ValueError('路线记录无法读取。')
            seen.add(branch_id)
            branch = self._branch(db, run['id'], branch_id)
            pieces.append('(branch_id=? AND position<=?' + (' AND id<=?' if ceiling is not None else '') + ')')
            args.extend([branch_id, position] + ([ceiling] if ceiling is not None else []))
            position = min(position, branch['fork_position'])
            ceiling = branch[cutoff_column] if ceiling is None else min(ceiling, branch[cutoff_column])
            branch_id = branch['parent_id']
        return '(' + ' OR '.join(pieces) + ')', args

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
        scope, args = self._version_scope(db, run, 'turn_cutoff')
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
            messages.extend([{'role': 'human', 'content': row['user_text'], 'note_revision': row['note_generation']},
                             {'role': 'ai', 'content': row['reply'], 'emotion': row['emotion'],
                              'evidence': evidence, 'answer_type': row['answer_type'], 'note_revision': row['note_generation']}])
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
                    if CHAPTER_RE.match(line):
                        event['kind'] = 'chapter'
                    else:
                        match = re.match(r'^([\u3400-\u9fff·]{1,8})[：:]\s*(.+)$', line) or re.match(r'^【([^】\n]{1,8})】\s*(.+)$', line)
                        said = SAID_RE.match(line)
                        if match:
                            name, content = match.groups()
                            event.update(speaker='' if name in ('旁白', '叙述') else name, text=content,
                                         kind='narration' if name in ('旁白', '叙述') else 'dialogue')
                        elif said and said.group(1) not in SAID_SKIP:
                            event.update(speaker=said.group(1), text=said.group(2), kind='dialogue')
            events.append(event)
        return events

    def _fork(self, db, run, name):
        branch_id = uuid4().hex
        count = db.execute('SELECT COUNT(*) FROM branches WHERE run_id=?', (run['id'],)).fetchone()[0]
        name = name.strip() or f'分支 {count}'
        note_cutoff = db.execute('SELECT COALESCE(MAX(id),0) FROM reading_notes').fetchone()[0]
        turn_cutoff = db.execute('SELECT COALESCE(MAX(id),0) FROM story_turns').fetchone()[0]
        character_cutoff = db.execute('SELECT COALESCE(MAX(id),0) FROM character_corrections').fetchone()[0]
        db.execute('INSERT INTO branches(id,run_id,name,parent_id,fork_position,created_at,note_cutoff,turn_cutoff,character_cutoff) VALUES(?,?,?,?,?,?,?,?,?)',
                   (branch_id, run['id'], name, run['branch_id'], run['position'], timestamp(), note_cutoff, turn_cutoff, character_cutoff))
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
            earlier = [dict(row) for row in db.execute('SELECT * FROM events WHERE run_id=? AND confirmed_read=1', (run_id,))]
            replay = locate_sequence(earlier, [progress_key(event['text']) for event in events], run['position'])
            if replay and replay['count'] >= 2:
                self._note(db, run, 'rewind', replay['position'], f'收录内容和第 {replay["position"]} 句起的已读原文重合')
                raise ValueError(f'这几句从第 {replay["position"]} 句起已经读过。请回到书签，不要写成新进度。')
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
            self._note(db, run, 'forward', run['position'] + len(events), f'向前收录了 {len(events)} 句')
        return True

    def _note(self, db, run, kind, focus, detail):
        db.execute('INSERT INTO progress_checks(run_id,branch_id,position,kind,focus_position,detail,created_at) VALUES(?,?,?,?,?,?,?)',
                   (run['id'], run['branch_id'], run['position'], kind, int(focus or 0), detail[:180], timestamp()))

    def screen_line(self, text, hold=None):
        """One captured line. A repeated line is only a rewind after the next line agrees."""
        key = progress_key(text)
        with self.connection(True) as db:
            run = self._run(db)
            if hold:
                row = db.execute('SELECT * FROM events WHERE run_id=? AND branch_id=? AND position>? ORDER BY position LIMIT 1',
                                 (run['id'], hold.get('branch_id'), hold.get('position'))).fetchone()
                if row and len(key) >= 12 and key in line_keys(dict(row)):
                    self._note(db, run, 'rewind', hold['position'], f'连续两句与第 {hold["position"]} 句附近的已读台词相同')
                    return {'action': 'rewind', 'position': hold['position'],
                            'message': f'连续两句都是已读台词，像是回到第 {hold["position"]} 句。记录已暂停，请先回到书签。'}
            earlier = [dict(row) for row in db.execute('SELECT * FROM events WHERE run_id=? AND confirmed_read=1', (run['id'],))]
            found = locate_sequence(earlier, [key], run['position'])
            if found:
                self._note(db, run, 'hold', found['position'], f'这句与第 {found["position"]} 句相同，等下一句确认')
                return {'action': 'hold', 'position': found['position'], 'branch_id': found['branch_id'],
                        'message': f'这句和第 {found["position"]} 句相同。下一句若也对上，会暂停记录。'}
            return {'action': 'save'}

    def note_question(self, snapshot):
        if not snapshot or not snapshot.run_id:
            return
        with self.connection(True) as db:
            state = self._state(db)
            if state['active_run_id'] != snapshot.run_id or state['epoch'] != snapshot.epoch:
                return
            run = self._run(db)
            focus = snapshot.focus_position or run['position']
            if run['note_generation'] != snapshot.note_revision:
                return
            detail = f'这一问对上第 {focus} 句' if focus != run['position'] else '这一问就在当前进度'
            self._note(db, run, 'question', focus, detail)

    def _progress(self, db, run):
        rows = [dict(row) for row in db.execute(
            'SELECT * FROM progress_checks WHERE run_id=? ORDER BY id DESC LIMIT 6', (run['id'],))]
        rows.reverse()
        questions = [row for row in rows if row['kind'] == 'question']
        latest = rows[-1] if rows else None
        detail = ''
        verdict = 'steady'
        if latest and latest['kind'] == 'rewind':
            verdict = 'rewind'
            detail = latest['detail']
        elif len(questions) >= 2 and abs(questions[-1]['focus_position'] - questions[-2]['focus_position']) <= 4:
            focus = questions[-1]['focus_position']
            if latest and latest['kind'] == 'question' and run['position'] - focus >= 12:
                verdict = 'looking_back'
                detail = f'最近两问都在看第 {focus} 句附近，确认进度仍是第 {run["position"]} 句。'
        return {'verdict': verdict, 'detail': detail,
                'focus': questions[-1]['focus_position'] if questions else run['position']}

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

    def _visible_notes(self, db, run, events=None):
        events = self._events(db, run) if events is None else events
        allowed = {event['id']: event for event in events}
        scope, args = self._version_scope(db, run, 'note_cutoff')
        rows = db.execute('SELECT * FROM reading_notes WHERE run_id=? AND ' + scope + ' ORDER BY id DESC', [run['id'], *args])
        seen, notes = set(), []
        for row in rows:
            if row['note_id'] in seen:
                continue
            seen.add(row['note_id'])
            ids = json.loads(row['evidence_ids'])
            if not all(event_id in allowed for event_id in ids):
                continue
            item = dict(row)
            item['evidence_ids'] = ids
            item['evidence'] = [ui_line(allowed[event_id]) for event_id in ids]
            notes.append(item)
        return notes

    def save_note(self, run_id, text, kind, status, ids, expected, note_id=None, version=None, turn_id=None):
        text = text.strip()
        if kind not in ('question', 'theory', 'feeling') or status not in ('open', 'resolved'):
            raise ValueError('请选择疑问、猜测或感受，以及是否已聊清楚。')
        if not isinstance(ids, list) or len(ids) > 12 or any(type(event_id) is not int for event_id in ids):
            raise ValueError('关联原文格式不正确。')
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            if run['id'] != run_id:
                raise StoryConflict('共读已经切换，请重新打开手记。')
            events = self._events(db, run)
            allowed = {event['id'] for event in events}
            if note_id:
                current = next((note for note in self._visible_notes(db, run, events) if note['note_id'] == note_id), None)
                if not current or current['id'] != version:
                    raise StoryConflict('这条手记已改变或不在当前进度，请刷新后重试。')
                turn_id = current['origin_turn_id']
            elif turn_id is not None:
                scope, args = self._version_scope(db, run, 'turn_cutoff')
                turn = db.execute('SELECT * FROM story_turns WHERE id=? AND run_id=? AND chat_generation=? AND ' + scope,
                                  [turn_id, run_id, run['chat_generation'], *args]).fetchone()
                if not turn:
                    raise StoryConflict('这段讨论不在当前进度，请刷新后重试。')
                if any(note['origin_turn_id'] == turn_id for note in self._visible_notes(db, run, events)):
                    return
                text, ids = turn['user_text'][:800], json.loads(turn['evidence_ids'])
            if not text or len(text) > 800:
                raise ValueError('手记请写1～800字。')
            if not set(ids).issubset(allowed):
                raise StoryConflict('手记只能关联当前路线、当前进度内的原文。')
            # Edits are new versions at this reading position. Rewinding reveals the earlier version.
            db.execute('INSERT INTO reading_notes(note_id,run_id,branch_id,position,kind,text,status,evidence_ids,origin_turn_id,created_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)', (note_id or uuid4().hex, run_id, run['branch_id'], run['position'],
                       kind, text, status, json.dumps(list(dict.fromkeys(ids))), turn_id, timestamp()))
            db.execute('UPDATE runs SET note_generation=note_generation+1 WHERE id=?', (run_id,))

    def delete_note(self, run_id, note_id, version, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            run = self._run(db)
            if run['id'] != run_id:
                raise StoryConflict('共读已经切换。')
            current = next((note for note in self._visible_notes(db, run) if note['note_id'] == note_id), None)
            if not current or current['id'] != version:
                raise StoryConflict('这条手记已改变或不在当前进度，请刷新后重试。')
            db.execute('DELETE FROM reading_notes WHERE run_id=? AND note_id=?', (run_id, note_id))
            db.execute('DELETE FROM reading_recaps WHERE run_id=?', (run_id,))
            db.execute('UPDATE runs SET note_generation=note_generation+1 WHERE id=?', (run_id,))

    def continuity(self, before_note=None):
        with self.connection(True) as db:
            state = self._state(db)
            if not state['active_run_id']:
                return {'epoch': state['epoch'], 'run_id': None, 'recap': None, 'notes': []}
            run = self._run(db)
            events = self._events(db, run)
            allowed = {event['id']: event for event in events}
            scope, args = self._version_scope(db, run, 'turn_cutoff')
            turns = []
            for row in db.execute('SELECT * FROM story_turns WHERE run_id=? AND chat_generation=? AND ' + scope +
                                  ' ORDER BY id DESC LIMIT 6', [run['id'], run['chat_generation'], *args]):
                ids = json.loads(row['evidence_ids'])
                if all(event_id in allowed for event_id in ids):
                    turns.append({'turn_id': row['id'], 'position': row['position'], 'text': clip(row['user_text'], 280),
                                  'evidence': [ui_line(allowed[event_id]) for event_id in ids[:4]]})
                if len(turns) == 3:
                    break
            signature = [1, run['id'], run['branch_id'], run['position'], run['chat_generation'], run['note_generation'],
                         [event['id'] for event in events], [turn['turn_id'] for turn in turns]]
            fingerprint = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
            cached = db.execute('SELECT * FROM reading_recaps WHERE run_id=? AND branch_id=?', (run['id'], run['branch_id'])).fetchone()
            recap = None
            if cached and cached['fingerprint'] == fingerprint:
                try:
                    candidate = json.loads(cached['content'])
                    if isinstance(candidate, dict) and candidate.get('position') == run['position']:
                        recap = candidate
                except ValueError:
                    pass
            if recap is None:
                scenes = segment_scenes(events)
                current = scenes[-1] if scenes else []
                selected = current[-2:]
                narration = next((event for event in reversed(current) if event['kind'] in ('narration', 'chapter')), None)
                if narration and narration not in selected:
                    selected = [narration, *selected]
                recap = {'position': run['position'], 'start': current[0]['position'] if current else 0,
                         'at_choice': bool(current and current[-1]['kind'] == 'choice'),
                         'excerpts': [ui_line(event) for event in sorted(selected, key=lambda event: event['position'])],
                         'topics': turns, 'updated_at': timestamp()}
                db.execute('INSERT INTO reading_recaps VALUES(?,?,?,?) ON CONFLICT(run_id,branch_id) DO UPDATE '
                           'SET fingerprint=excluded.fingerprint,content=excluded.content',
                           (run['id'], run['branch_id'], fingerprint, json.dumps(recap, ensure_ascii=False)))
            notes = self._visible_notes(db, run, events)
            page = [note for note in notes if before_note is None or note['id'] < before_note][:51]
            return {'epoch': state['epoch'], 'run_id': run['id'], 'branch_id': run['branch_id'],
                    'note_revision': run['note_generation'], 'recap': recap, 'notes': page[:50], 'note_count': len(notes),
                    'more_notes': page[49]['id'] if len(page) > 50 else None}

    def memory_inventory(self):
        """Counts cover every branch and chat generation, including hidden discussions."""
        with self.connection() as db:
            runs = []
            for row in db.execute('SELECT r.*,g.title FROM runs r JOIN games g ON g.id=r.game_id ORDER BY r.created_at,r.rowid'):
                item = dict(row)
                for table in ('events', 'branches', 'bookmarks', 'story_turns', 'progress_checks', 'imports'):
                    item[table] = db.execute(f'SELECT COUNT(*) FROM {table} WHERE run_id=?', (row['id'],)).fetchone()[0]
                item['text_bytes'] = db.execute('SELECT COALESCE(SUM(length(CAST(raw_text AS BLOB))),0) FROM events WHERE run_id=?', (row['id'],)).fetchone()[0]
                item['notes'] = db.execute('SELECT COUNT(DISTINCT note_id) FROM reading_notes WHERE run_id=?', (row['id'],)).fetchone()[0]
                item['corrections'] = db.execute('SELECT COUNT(DISTINCT correction_id) FROM character_corrections WHERE run_id=?', (row['id'],)).fetchone()[0]
                item['revision'] = self._memory_revision(db, row['id'])
                runs.append(item)
            return {'epoch': self._state(db)['epoch'], 'runs': runs}

    def _memory_revision(self, db, run_id):
        run = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if not run:
            raise ValueError('没有找到这篇共读。')
        values = [dict(run)]
        for table in ('events', 'branches', 'bookmarks', 'story_turns', 'progress_checks', 'imports', 'reading_notes', 'character_corrections'):
            values.append(tuple(db.execute(f'SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM {table} WHERE run_id=?', (run_id,)).fetchone()))
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def clean_memory(self, run_id, action, expected, revision):
        if action not in ('clear_discussion', 'delete_run'):
            raise ValueError('不支持这项清理。')
        with self.connection(True) as db:
            state = self._check(db, expected)
            if self._memory_revision(db, run_id) != revision:
                raise StoryConflict('记录有了新变化，请重新预览清理范围。')
            if action == 'clear_discussion':
                db.execute('DELETE FROM reading_recaps WHERE run_id=?', (run_id,))
                db.execute('DELETE FROM story_turns WHERE run_id=?', (run_id,))
                db.execute('UPDATE runs SET chat_generation=chat_generation+1 WHERE id=?', (run_id,))
            else:
                game_id = db.execute('SELECT game_id FROM runs WHERE id=?', (run_id,)).fetchone()[0]
                if state['active_run_id'] == run_id:
                    db.execute('UPDATE reading_state SET active_run_id=NULL WHERE singleton=1')
                for table in ('reading_recaps', 'character_corrections', 'reading_notes', 'progress_checks', 'story_turns', 'imports', 'bookmarks', 'events', 'branches'):
                    db.execute(f'DELETE FROM {table} WHERE run_id=?', (run_id,))
                db.execute('DELETE FROM runs WHERE id=?', (run_id,))
                db.execute('DELETE FROM games WHERE id=? AND NOT EXISTS(SELECT 1 FROM runs WHERE game_id=?)', (game_id, game_id))
            self._boundary(db)

    def memory_boundary(self, expected):
        with self.connection(True) as db:
            self._check(db, expected)
            self._boundary(db)

    def compact(self):
        """Only reclaim SQLite free pages; this is not a secure-erasure promise."""
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            db.close()

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
            result['active'] = {'id': run['id'], 'title': run['title'], 'name': run['name'], 'mode': run['mode'] or 'galgame',
                'branch_id': run['branch_id'], 'branch_name': branch['name'], 'position': run['position'],
                'note_revision': run['note_generation'],
                'tip': self._tip(db, branch), 'events': events,
                'more_before': events[0]['position'] if events and events[0]['position'] > 1 else None,
                'messages': self._messages(db, run),
                'progress': self._progress(db, run),
                'bookmarks': [dict(row) for row in db.execute('SELECT id,name,position,branch_id FROM bookmarks WHERE run_id=? ORDER BY rowid DESC', (run['id'],))],
                'branches': [{'id': row['id'], 'name': row['name'], 'tip': self._tip(db, row)} for row in db.execute('SELECT * FROM branches WHERE run_id=? ORDER BY rowid', (run['id'],))],
                'memory': present_memory(self._events(db, run), self._visible_corrections(db, run))}
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
            corrections = self._visible_corrections(db, run, events)
            explicit_positions = set(re.findall(r'第\s*(\d+)\s*[句条段]', question or ''))
            reference_position = int(next(iter(explicit_positions))) if len(explicit_positions)==1 else run['position'] if not explicit_positions else None
            manual_refs = [rule for rule in corrections if rule['kind']=='pronoun' and rule['anchor_position']==reference_position and rule['label'] in pronouns(question)]
            people = corrected_people(events, [rule for rule in corrections if rule['kind']=='alias' or rule in manual_refs])
            terms = question_terms(question)
            notes = self._visible_notes(db, run, events)
            def note_score(note):
                return sum(term_weight(term) for term in terms if term in note['text'].lower())
            selected_notes = sorted(notes, key=lambda note: (note_score(note), note['status'] == 'open', note['id']), reverse=True)[:4]
            current = scenes[-1] if scenes else ()
            nearby = scenes[-2] if len(scenes) > 1 and current and len(current) < WORKING_SCENE_SHORT else ()
            # Names in the question point at a block; the block, not one matching line, is what gets opened.
            referent, referent_ambiguous = resolve_pronoun(question, scenes, people)
            referent_source = 'inferred' if referent else ''
            if manual_refs:
                targets = {rule['target'] for rule in manual_refs}
                referent = next(iter(targets)) if len(targets)==1 else ''
                referent_ambiguous = bool(pronouns(question)-{rule['label'] for rule in manual_refs})
                referent_source = 'player'
            selected_people = choose_characters(people, question, tuple(nearby) + tuple(current), referent)
            for rule in manual_refs:
                if rule['target'] in people and people[rule['target']] not in selected_people:
                    selected_people.insert(0, people[rule['target']])
            selected_people = selected_people[:4]
            reference_names = {rule['target'] for rule in manual_refs}
            focused = [person for person in selected_people if person['name'] in question or any(alias in question for alias in person['aliases']) or person['name']==referent or person['name'] in reference_names]
            focus_names = tuple(dict.fromkeys(name for person in focused for name in (person['name'], *person['aliases'])))
            relevant_rules = (manual_refs + [rule for rule in corrections if rule['kind']=='alias' and (rule['label'] in question or rule['target'] in focus_names)])[:6]
            explicit = set(explicit_positions)
            correction_links = {event_id for rule in relevant_rules for event_id in rule['evidence_ids']}
            explicit.update(str(event['position']) for event in events if event['id'] in correction_links)
            note_question = any(word in question for word in ('手记', '上次聊', '上回聊', '之前猜', '待解', '疑问'))
            linked = {event_id for note in selected_notes if note_question or note_score(note) >= 4 for event_id in note['evidence_ids']}
            explicit.update(str(event['position']) for event in events if event['id'] in linked)
            chosen_blocks = choose_blocks(scenes, terms, focus_names, explicit, referent)
            # The passage the question names is loaded first. The confirmed tip stays a short anchor.
            focus_scene = current
            if chosen_blocks:
                focus_scene = max(chosen_blocks, key=lambda scene: (block_score(scene, terms, focus_names), scene[-1]['position'] if scene else 0))
                if current and block_score(focus_scene, terms, focus_names) == 0:
                    focus_scene = current
            ordered_blocks = []
            for scene in (focus_scene, current, *chosen_blocks):
                if scene and scene not in ordered_blocks:
                    ordered_blocks.append(scene)
            chosen, used, budget = [], set(), OPEN_BUDGET
            opened = []
            for scene in ordered_blocks:
                if scene is current and scene is focus_scene:
                    role = 'current'
                elif scene is current:
                    role = 'anchor'
                elif scene is nearby:
                    role = 'previous'
                else:
                    role = 'opened'
                budget, ok = take_span(scene, role, terms, focus_names, explicit, chosen, used, budget)
                if ok:
                    opened.append(scene)
            provided = {event['id'] for event in chosen}
            opened_ids = {id(scene) for scene in opened}
            visible = opened + catalog_pick(scenes, opened_ids, terms, focus_names)
            cards = []
            for scene in sorted(visible, key=lambda item: item[0]['position']):
                role = 'current' if scene is current else 'previous' if scene is nearby else 'opened'
                loaded = id(scene) in opened_ids
                if not loaded:
                    role = 'closed'
                card = scene_card(scene, loaded, role)
                card['shown'] = sum(1 for event in chosen if scene[0]['position'] <= event['position'] <= scene[-1]['position'])
                if not loaded:
                    card['summary'] = catalog_line(scene)
                cards.append(card)
            characters = []
            for person in selected_people:
                names = (person['name'], *person['aliases'])
                aliases = sorted(person['aliases'], key=lambda alias: alias in question, reverse=True)[:3]
                card = {'name': person['name'], 'aliases': aliases,
                        'user_aliases': [alias for alias in aliases if alias in person.get('user_aliases', [])],
                        'claims': trim_mentions(person['claims'], terms, names, 4, provided),
                        'witnessed': trim_mentions(person['witnessed'], terms, names, 3, provided)}
                if card['claims'] or card['witnessed']:
                    characters.append(card)
            newest = [message for message in reversed(self._messages(db, run)) if message['note_revision'] == run['note_generation']][:DISCUSSION_TURNS]
            messages = [{'role': message['role'], 'content': clip(message['content'], DISCUSSION_CLIP)} for message in reversed(newest)]
            explicit_lines = [event for event in chosen if str(event['position']) in explicit]
            if explicit_lines:
                focus_position = explicit_lines[0]['position']
            else:
                ranked_lines = sorted(chosen, key=lambda event: event_score(event, terms, focus_names), reverse=True)
                focus_position = ranked_lines[0]['position'] if ranked_lines and event_score(ranked_lines[0], terms, focus_names) else run['position']
            return ReadingSnapshot(state['epoch'], run['id'], run['branch_id'], run['position'], run['chat_generation'],
                                   run['title'], tuple(sorted(chosen, key=lambda event: event['position'])), tuple(reversed(messages)),
                                   tuple(cards), tuple(characters), run['mode'] or 'galgame', focus_position, referent, referent_ambiguous,
                                   tuple(selected_notes), run['note_generation'], tuple(relevant_rules), referent_source)

    def matches(self, snapshot):
        with self.connection() as db:
            state = self._state(db)
            if state['epoch'] != snapshot.epoch or state['active_run_id'] != snapshot.run_id:
                return False
            return not snapshot.run_id or self._run(db)['note_generation'] == snapshot.note_revision

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
            if self._run(db)['note_generation'] != snapshot.note_revision:
                raise StoryConflict('阅读记忆已经改变，请重新提问。')
            db.execute('INSERT INTO story_turns(run_id,branch_id,position,chat_generation,user_text,reply,emotion,answer_type,evidence_ids,created_at,note_generation) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (snapshot.run_id, snapshot.branch_id, snapshot.position,
                       snapshot.chat_generation, user, result['text'], result['emotion'], result.get('answer_type', 'uncertain'),
                       json.dumps(list(dict.fromkeys(ids))), timestamp(), snapshot.note_revision))
        return [event for event in snapshot.events if event['id'] in ids]
