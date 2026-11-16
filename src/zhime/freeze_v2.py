"""Freeze identical key scenarios and independent engine / Rime candidate pools."""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import time
from collections import Counter
from pathlib import Path
from .engine import Engine,FUZZY,variants

def scenarios(item,legal):
    yield 'clean_off',0,item['keys'],'none'
    yield 'clean_on',63,item['keys'],'all'
    for i,name in enumerate(FUZZY):
        for pos,s in enumerate(item['syllables']):
            alternatives=sorted(v for v in variants(s,(name,),legal) if v!=s)
            if alternatives:
                syllables=list(item['syllables']);syllables[pos]=alternatives[0];key=''.join(syllables)
                yield 'fuzzy_off',0,key,name
                yield 'fuzzy_on',1<<i,key,name
                break

def tune_penalty(items,lexicon):
    engine=Engine(lexicon); cases=[]
    for item in items:
        eligible=[s for s in scenarios(item,engine.syllables) if s[0]=='fuzzy_on']
        if eligible:cases.append((item,eligible[int(item['id'][:8],16)%len(eligible)]))
    results=[]
    for penalty in (1.,3.,6.):
        engine.fuzzy_penalty=penalty;engine.query.cache_clear();correct=0
        for item,(_,mask,keys,_) in cases:
            enabled=tuple(k for i,k in enumerate(FUZZY) if mask&(1<<i));pool=engine.query(keys,enabled)
            correct+=bool(pool and pool[0].text==item['target'] and pool[0].consumed==len(keys))
        results.append({'penalty':penalty,'correct':correct,'total':len(cases)})
        print('penalty calibration',results[-1],flush=True)
    best=max(results,key=lambda r:(r['correct'],r['penalty']))['penalty']
    return best,results

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='benchmark-v2');p.add_argument('--lexicon',default='artifacts/lexicon/lexicon.sqlite');p.add_argument('--weasel',default=r'C:\Program Files\Rime\weasel-0.17.4');p.add_argument('--rime-exe',default='native/build/Release/rime_pool.exe');a=p.parse_args()
    root=Path(a.root);items=[json.loads(x) for x in (root/'items.jsonl').read_text(encoding='utf-8').splitlines()]
    if not (root/'engine-config.json').exists():
        penalty,calibration=tune_penalty([x for x in items if x['split']=='dev'],a.lexicon)
        (root/'engine-config.json').write_text(json.dumps({'beam':64,'fuzzy_penalty':penalty,'calibration':calibration},indent=2),encoding='utf-8')
    config=json.loads((root/'engine-config.json').read_text());engine=Engine(a.lexicon,beam=config['beam'],fuzzy_penalty=config['fuzzy_penalty'])
    identity=hashlib.sha256(json.dumps(config,sort_keys=True).encode()+Path(a.lexicon).with_name('manifest.json').read_bytes()+Path(__file__).with_name('engine.py').read_bytes()+(Path(a.weasel)/'rime.dll').read_bytes()).hexdigest()
    cache=sqlite3.connect(root/'query-cache.sqlite');cache.execute('CREATE TABLE IF NOT EXISTS pools(arm TEXT, mask INT, keys TEXT, value TEXT, PRIMARY KEY(arm,mask,keys))')
    stderr=(root/'rime-baseline.stderr.log').open('a',encoding='utf-8')
    process=subprocess.Popen([a.rime_exe,a.weasel,str((root/'rime-profile').resolve()),'bench_'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,encoding='utf-8',bufsize=1)
    def query(arm,mask,keys):
        cache_arm=arm+':'+identity
        saved=cache.execute('SELECT value FROM pools WHERE arm=? AND mask=? AND keys=?',(cache_arm,mask,keys)).fetchone()
        if saved:return json.loads(saved[0])
        start=time.perf_counter()
        if arm=='C':
            from dataclasses import asdict
            enabled=tuple(k for i,k in enumerate(FUZZY) if mask&(1<<i));pool=[asdict(c) for c in engine.query(keys,enabled)]
        else:
            process.stdin.write(arm.lower()+'_'+str(mask)+'\t'+keys+'\n');process.stdin.flush()
            line=process.stdout.readline()
            if line=='':raise RuntimeError('Rime helper exited; see rime-baseline.stderr.log')
            fields=line.rstrip('\n').split('\t');pool=[];seen=set()
            if fields!=['']:
                if len(fields)%2:raise ValueError('invalid Rime pool output')
                for i in range(0,len(fields),2):
                    text=fields[i];consumed=int(fields[i+1])
                    if text not in seen:pool.append({'text':text,'consumed':consumed});seen.add(text)
        value={'candidates':pool,'generation_ms':(time.perf_counter()-start)*1000}
        cache.execute('INSERT INTO pools VALUES(?,?,?,?)',(cache_arm,mask,keys,json.dumps(value,ensure_ascii=False)))
        return value
    stats=Counter();temporary=root/'frozen.jsonl.tmp'
    try:
      with temporary.open('w',encoding='utf-8',newline='\n') as f:
       for split in ('dev','test'):
        cases=[]
        for item in items:
            if item['split']!=split:continue
            for condition,mask,keys,rule in scenarios(item,engine.syllables):
                case={**item,'base_id':item['id'],'case_id':hashlib.sha256((item['id']+'|'+condition+'|'+rule).encode()).hexdigest(),'condition':condition,'fuzzy_mask':mask,'keys':keys,'rule':rule}
                cases.append(case)
        # Group by schema to avoid reloading a Rime prism for every key sequence.
        for arm in ('A','B','C'):
          for mask in sorted({c['fuzzy_mask'] for c in cases}):
            keys=sorted({c['keys'] for c in cases if c['fuzzy_mask']==mask})
            for number,key in enumerate(keys):
                query(arm,mask,key)
                if number%500==0:cache.commit();print('pool',split,arm,mask,number,'/',len(keys),flush=True)
            cache.commit()
        splitpath=root/('frozen-'+split+'.jsonl')
        with splitpath.with_suffix('.tmp').open('w',encoding='utf-8',newline='\n') as sub:
          for case in cases:
            case['pools']={arm:query(arm,case['fuzzy_mask'],case['keys']) for arm in ('A','B','C')}
            line=json.dumps(case,ensure_ascii=False)+'\n';f.write(line);sub.write(line);stats[split+'/'+case['condition']+'/'+case['rule']]+=1
        splitpath.with_suffix('.tmp').replace(splitpath);f.flush();print('frozen split ready',split,len(cases),flush=True)
        cache.commit()
      temporary.replace(root/'frozen.jsonl')
      (root/'frozen-manifest.json').write_text(json.dumps({'engine':config,'pool_identity':identity,'counts':dict(stats),'rime_version':'Windows Weasel 0.17.4 / librime 1.13.1','candidate_limit':20,'personal_learning':False,'sha256':hashlib.sha256((root/'frozen.jsonl').read_bytes()).hexdigest()},indent=2),encoding='utf-8')
    finally:
        process.stdin.close();process.wait(timeout=30);stderr.close();cache.close()

if __name__=='__main__':main()
