"""Match a visible dialogue to recorded route text, without model inference."""
from difflib import SequenceMatcher
import unicodedata


def normalized(text):
    return ''.join(char.lower() for char in unicodedata.normalize('NFKC',text or '') if char.isalnum())


def locate_dialogue(text,events):
    query=normalized(text)[-512:]
    if len(query)<8:return {'status':'too_short','candidates':[]}
    grams={query[i:i+2] for i in range(len(query)-1)};found={}
    # OCR may wrap a single sentence or merge a name and several text rows.
    for end in range(len(events)):
        for width in range(1,min(4,end+1)+1):
            rows=events[end-width+1:end+1]
            if width>1:
                tail=query[-10:];last=normalized(rows[-1].get('text',''))
                if SequenceMatcher(None,tail,last,autojunk=False).find_longest_match().size<max(4,len(tail)//2):continue
            variants=[''.join(row.get('text','') for row in rows),
                      ''.join((row.get('speaker') or '')+row.get('text','') for row in rows)]
            score=0
            for raw in variants:
                candidate=normalized(raw)[-640:]
                if not candidate:continue
                if query==candidate:score=max(score,1.0);continue
                if len(query)>=12 and query in candidate:score=max(score,.97);continue
                if not .55<=len(candidate)/len(query)<=1.8:continue
                other={candidate[i:i+2] for i in range(len(candidate)-1)}
                if len(grams&other)/len(grams)<.4:continue
                score=max(score,SequenceMatcher(None,query,candidate,autojunk=False).ratio())
            if score<.8:continue
            row=events[end];position=row['position'];existing=found.get(position)
            if existing and existing['score']>=score:continue
            found[position]={'position':position,'score':round(score,3),'event_ids':[r['id'] for r in rows],
                             'speaker':row.get('speaker',''),'text':row['text'][:180]}
    candidates=sorted(found.values(),key=lambda row:(-row['score'],-row['position']))[:3]
    ambiguous=len(candidates)>1 and candidates[0]['score']-candidates[1]['score']<.025
    status='ambiguous' if ambiguous else 'matched' if candidates else 'not_found'
    return {'status':status,'candidates':candidates}
