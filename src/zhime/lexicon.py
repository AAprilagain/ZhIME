"""Compile pinned rime-ice tables into one explicit shared lexicon.

Unannotated words use 8105 character readings at >=5% of the dominant reading,
with a recorded 64-pronunciation cap. The same exported table feeds baseline B.
"""
import argparse
import hashlib
import itertools
import json
import math
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

TABLES = ('8105', 'base', 'ext', 'tencent', 'others')

def records(path):
    active = False
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if line.strip() == '...': active = True; continue
        if active and line and not line.startswith('#'):
            fields = line.split('\t')
            if fields[0] and not fields[0].startswith(' '): yield fields

def build(source, output):
    source, out = Path(source), Path(output); out.mkdir(parents=True, exist_ok=True)
    dbpath = out / 'lexicon.sqlite'
    if dbpath.exists(): raise FileExistsError(dbpath)
    readings = defaultdict(dict)
    for fields in records(source / 'cn_dicts/8105.dict.yaml'):
        if len(fields) >= 2:
            readings[fields[0]][fields[1].strip().replace('ü','v')] = float(fields[2]) if len(fields)>2 else 1
    choices = {c: sorted((p for p,w in ps.items() if w >= max(ps.values())*.05)) for c,ps in readings.items()}
    db=sqlite3.connect(dbpath)
    db.execute('CREATE TABLE entries(pinyin TEXT, text TEXT, weight REAL, PRIMARY KEY(pinyin,text)) WITHOUT ROWID')
    stats=Counter(); hashes={}
    for table in TABLES:
        path=source / f'cn_dicts/{table}.dict.yaml'; hashes[table]=hashlib.sha256(path.read_bytes()).hexdigest()
        batch=[]
        for fields in records(path):
            text=fields[0]
            if not text or any(not '\u3400' <= c <= '\u9fff' for c in text): stats['non_han_skipped']+=1; continue
            if len(fields)>=3:
                pinyin=fields[1].strip().lower().replace('ü','v'); weight=fields[2]
                variants=[pinyin]
            elif len(fields)==2 and any(c.isalpha() for c in fields[1]):
                variants=[fields[1].strip().lower().replace('ü','v')]; weight=1
            else:
                weight=fields[1] if len(fields)>1 else 1
                if any(c not in choices for c in text): stats['unknown_character_skipped']+=1; continue
                n=math.prod(len(choices[c]) for c in text)
                if n>64: stats['pronunciation_capped_words']+=1
                variants=[' '.join(x) for x in itertools.islice(itertools.product(*(choices[c] for c in text)),64)]
                stats['automatic_pronunciation_words']+=1
            try: weight=max(1.0,float(str(weight).rstrip('%')))
            except ValueError: stats['bad_weight']+=1; continue
            for py in variants:
                syllables=py.split()
                if len(syllables)!=len(text) or any(not s.isascii() or not s.isalpha() for s in syllables): stats['invalid_reading_skipped']+=1; continue
                batch.append((' '.join(syllables),text,weight)); stats['input_readings']+=1
            if len(batch)>=10000:
                db.executemany('INSERT OR IGNORE INTO entries VALUES(?,?,?)',batch); batch=[]
        if batch: db.executemany('INSERT OR IGNORE INTO entries VALUES(?,?,?)',batch)
        db.commit()
    stats['entries']=db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
    with (out/'zhime_shared.dict.yaml').open('w',encoding='utf-8',newline='\n') as f:
        f.write('# Generated from pinned rime-ice; retain upstream GPL and source notices.\n---\nname: zhime_shared\nversion: "1"\nsort: by_weight\n...\n')
        for py,text,w in db.execute('SELECT pinyin,text,weight FROM entries ORDER BY pinyin,weight DESC,text'):
            f.write(f'{text}\t{py}\t{w:g}\n')
    db.close()
    rev=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    manifest={'source':'https://github.com/iDvel/rime-ice','commit':rev,'tables':TABLES,'sha256':hashes,'statistics':dict(stats),'automatic_reading_policy':'8105 >= 5% max character weight; max 64 combinations; first duplicate wins'}
    manifest['shared_sha256']=hashlib.sha256((out/'zhime_shared.dict.yaml').read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--source',required=True); p.add_argument('--output',required=True); a=p.parse_args(); build(a.source,a.output)
