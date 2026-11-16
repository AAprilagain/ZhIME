"""Build unseen-source-shard cases with explicit overlap screening and pairing."""
import argparse
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
import pyarrow.parquet as pq
from pypinyin import lazy_pinyin, Style
from opencc import OpenCC
from .engine import variants, FUZZY

HAN=re.compile(r'[\u4e00-\u9fff]+')
CONVERTER=OpenCC('t2s')
_changed=set()
for _table in CONVERTER.dict_cache.values():
    for _key,_values in _table[2].items():
        _value=_values.split(' ')[0]
        if len(_key)==len(_value):_changed.update(a for a,b in zip(_key,_value) if a!=b)
        elif _key!=_value:_changed.update(_key)
_traditional=re.compile('['+re.escape(''.join(sorted(_changed)))+']')
def simplify(text):
    return CONVERTER.convert(text) if _traditional.search(text) else text
SPLITS={'dev':{'000_00027':1000},'test':{'000_00045':2000,'001_00007':2000,'001_00025':2000}}
def sha(s): return hashlib.sha256(s.encode('utf-8')).hexdigest()
def normalized(s): return ''.join(HAN.findall(s))
def grams(s):
    s=normalized(s)[:256]
    return {s[i:i+5] for i in range(len(s)-4)}
def signature(g):
    # Deterministic 4x minhash LSH; exact Jaccard verifies every proposed match.
    if not g:return ()
    hs=[int.from_bytes(hashlib.blake2b(x.encode(),digest_size=8).digest(),'little') for x in g]
    return tuple(min(((h^(0x9e3779b97f4a7c15*k))&((1<<64)-1)) for h in hs) for k in range(1,5))

def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',required=True); p.add_argument('--train',required=True); p.add_argument('--old',default='benchmark'); p.add_argument('--out',required=True); p.add_argument('--seed',type=int,default=20261111); a=p.parse_args()
    out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    if (out/'items.jsonl').exists():raise FileExistsError('benchmark already exists')
    rng=random.Random(a.seed); root=Path(a.root); candidates=[]; stats=Counter(); sources=[]
    for split,shards in SPLITS.items():
      for shard,quota in shards.items():
        assert shard not in ('000_00063','000_00009')
        paths=sorted((root/'train').glob(f'web-{shard}-*.parquet')); pools=[[],[],[]]; cap=(quota*8+2)//3
        for path in paths:
          sources.append({'path':str(path),'bytes':path.stat().st_size,'source_shard':shard})
          pf=pq.ParquetFile(path); groups=list(range(pf.num_row_groups)); rng.shuffle(groups)
          for group in groups:
            for batch in pf.iter_batches(row_groups=[group],columns=['id','host','text'],batch_size=1024):
              rows=batch.to_pylist(); rng.shuffle(rows)
              for row in rows:
                text=simplify(row['text'] or ''); clauses=[m for m in HAN.finditer(text) if 2<=len(m.group())<=16]
                rng.shuffle(clauses)
                for match in clauses:
                  target=match.group(); length=len(target); bucket=0 if length<=4 else 1 if length<=8 else 2
                  if len(pools[bucket])>=cap:continue
                  context=text[max(0,match.start()-64):match.start()].strip()
                  py=lazy_pinyin(text[max(0,match.start()-16):match.end()],style=Style.NORMAL,errors='default')[-length:]
                  if len(py)!=length or any(not re.fullmatch('[a-z]+',s) for s in py):continue
                  doc=normalized(text); g=grams(text)
                  pools[bucket].append({'split':split,'shard':shard,'source_file':path.name,'source_id':row['id'],'host':row['host'],'document_sha256':sha(doc),'target':target,'context':context,'syllables':py,'keys':''.join(py),'length_group':('2-4','5-8','9-16')[bucket], '_grams':g, '_signature':signature(g)})
                  break
              if min(map(len,pools))>=cap:break
            if min(map(len,pools))>=cap:break
          if min(map(len,pools))>=cap:break
        for pool in pools:candidates.extend(pool)
        print('sampled',split,shard,list(map(len,pools)),flush=True)
    bydoc=defaultdict(list); bysig=defaultdict(set); targets={c['target'] for c in candidates}; excluded=set(); repeated=set()
    for i,c in enumerate(candidates):
        bydoc[c['document_sha256']].append(i)
        for band,value in enumerate(c['_signature']):bysig[(band,value)].add(i)
    old_targets=set(); old_docs=set()
    for path in Path(a.old).glob('*.jsonl'):
        for line in path.open(encoding='utf-8'):
            row=json.loads(line); old_targets.add(row.get('target','')); old_docs.add(row.get('document_key_sha256',''))
    with open(a.train,encoding='utf-8') as f:
      for number,line in enumerate(f):
        line=simplify(line)
        doc=normalized(line); digest=sha(doc)
        for i in bydoc.get(digest,[]):excluded.add(i); stats['train_exact_document_matches']+=1
        for m in HAN.finditer(line):
            if m.group() in targets:repeated.add(m.group())
        if len(doc)>=30:
            g=grams(doc); sig=signature(g); near=set()
            for band,value in enumerate(sig):near.update(bysig.get((band,value),()))
            for i in near:
                if i in excluded:continue
                other=candidates[i]['_grams']; union=len(g|other)
                if union and len(g&other)/union>=.85: excluded.add(i); stats['train_near_document_matches']+=1
        if number and number%200000==0: print('screened training documents',number,flush=True)
    seen_docs=set(); seen_targets=set(old_targets); seen_grams=defaultdict(list); selected=[]
    for split,shards in SPLITS.items():
      for shard,quota in shards.items():
        per=[quota//3+(1 if b<quota%3 else 0) for b in range(3)]; counts=[0,0,0]
        for i,c in enumerate(candidates):
            if c['split']!=split or c['shard']!=shard:continue
            b=('2-4','5-8','9-16').index(c['length_group'])
            if counts[b]>=per[b]:continue
            if i in excluded or c['target'] in repeated or c['target'] in seen_targets or c['document_sha256'] in seen_docs or sha(str(c['source_id'])) in old_docs:
                stats['selection_overlap_rejected']+=1; continue
            near=set()
            for band,value in enumerate(c['_signature']):
                for idx in seen_grams[(band,value)]:near.add(idx)
            if any(len(c['_grams']&selected[j]['_grams'])/max(1,len(c['_grams']|selected[j]['_grams']))>=.85 for j in near):continue
            c['id']=sha(c['document_sha256']+'|'+c['target']); idx=len(selected)
            selected.append(c); seen_docs.add(c['document_sha256']); seen_targets.add(c['target']); counts[b]+=1
            for band,value in enumerate(c['_signature']):seen_grams[(band,value)].append(idx)
        print('selected',split,shard,counts,'wanted',per,flush=True)
        if counts!=per:raise RuntimeError('insufficient uncontaminated samples; increase oversampling without changing source shards')
    with (out/'items.jsonl').open('w',encoding='utf-8',newline='\n') as f:
        for c in selected:f.write(json.dumps({k:v for k,v in c.items() if not k.startswith('_')},ensure_ascii=False)+'\n')
    manifest={'seed':a.seed,'splits':SPLITS,'excluded_source_shards':['000_00063','000_00009'],'source_files':sources,'items':len(selected),'statistics':dict(stats),'normalization':'OpenCC t2s (opencc-python-reimplemented 0.1.7), applied to source and overlap-screening training text','screening':'normalized full-document SHA256; exact Han clause equality against training text; first-256-Han-character 5gram minhash LSH, verified Jaccard >=0.85; old target and source-ID exclusions','source_provenance':'User-supplied cleaned web export. Upstream VimeML zh_filter documents FineWeb2 cmn_Hani; exact raw-shard-to-export provenance not available in this export, not independently verified.','items_sha256':hashlib.sha256((out/'items.jsonl').read_bytes()).hexdigest()}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
