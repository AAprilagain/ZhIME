"""Cache identity-bound LM scores, select on dev, evaluate once with frozen config."""
import argparse
import hashlib
import json
import math
import random
import sqlite3
import time
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
import sentencepiece as spm
import torch
from .model import LM
from .joint_score import score_joint,score_separate_strict,order_pool,ScoringFallback
from .rerank import score_candidates

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def outcome(case,pool,order):
    ranks=[j+1 for j,i in enumerate(order) if pool[i]['text']==case['target'] and pool[i]['consumed']==len(case['keys'])]
    rank=min(ranks) if ranks else None
    return {'top1':int(rank==1),'top5':int(rank is not None and rank<=5),'mrr':1/rank if rank else 0.,'recalled':int(rank is not None),'prediction':pool[order[0]]['text'] if order else None}

def bootstrap(rows,left,right):
    bydoc=defaultdict(lambda:[0.,0])
    for row in rows:
        key=row['document_sha256'];bydoc[key][0]+=row['systems'][right]['top1']-row['systems'][left]['top1'];bydoc[key][1]+=1
    values=np.asarray(list(bydoc.values()),dtype=float)
    if not len(values):return None
    rng=np.random.default_rng(20261111); samples=[]
    for _ in range(2000):
        selected=values[rng.integers(0,len(values),size=len(values))];samples.append(selected[:,0].sum()/selected[:,1].sum())
    return [float(x) for x in np.quantile(samples,[.025,.975])]

def summaries(rows):
    groups=defaultdict(list)
    for row in rows:
        for key in [row['condition'],row['condition']+'/rule/'+row['rule'],row['condition']+'/shard/'+row['shard'],row['condition']+'/length/'+row['length_group']]:groups[key].append(row)
    result={}
    for key,group in groups.items():
        systems={}
        for arm in group[0]['systems']:
            total=len(group);hits=sum(x['systems'][arm]['top1'] for x in group);recall=sum(x['systems'][arm]['recalled'] for x in group)
            systems[arm]={'n':total,'top1_correct':hits,'top1':hits/total,'top5':sum(x['systems'][arm]['top5'] for x in group)/total,'mrr':sum(x['systems'][arm]['mrr'] for x in group)/total,'recall':recall/total,'missing':total-recall,'top1_given_recall':hits/recall if recall else None,'fallbacks':dict(Counter(x.get('fallbacks',{}).get(arm) for x in group if x.get('fallbacks',{}).get(arm)))}
        comparisons={}
        if key.count('/')==0:
            for left,right in [('A','B'),('B','C'),('C','D'),('B','E')]:
                fixed=sum(x['systems'][right]['top1'] and not x['systems'][left]['top1'] for x in group);lost=sum(x['systems'][left]['top1'] and not x['systems'][right]['top1'] for x in group);n=fixed+lost
                # Log-space binomial tail avoids huge integer overflow for large comparisons.
                tail=sum(math.exp(math.lgamma(n+1)-math.lgamma(i+1)-math.lgamma(n-i+1)-n*math.log(2)) for i in range(min(fixed,lost)+1)) if n else .5
                comparisons[left+'->'+right]={'corrections':fixed,'regressions':lost,'exploratory_case_mcnemar_p':min(1.,2*tail),'document_bootstrap_difference_ci95':bootstrap(group,left,right)}
        result[key]={'systems':systems,'comparisons':comparisons}
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default='benchmark-v2');p.add_argument('--input',default=None);p.add_argument('--split',choices=['dev','test'],required=True);p.add_argument('--checkpoint',default='artifacts/model.pt');p.add_argument('--tokenizer',default='artifacts/tokenizer.model');p.add_argument('--device',choices=['cpu','cuda'],default='cpu');a=p.parse_args()
    root=Path(a.root); configpath=root/'selected.json'; output=root/(a.split+'-results-v2.json');predpath=root/(a.split+'-predictions.jsonl')
    if output.exists():raise FileExistsError('completed evaluation exists; do not relabel it blind')
    if a.split=='test' and not configpath.exists():raise ValueError('select development settings before test scoring')
    rows=[json.loads(x) for x in Path(a.input or root/'frozen.jsonl').read_text(encoding='utf-8').splitlines() if x]
    cases=[x for x in rows if x['split']==a.split]
    torch.set_num_threads(2);torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
    saved=torch.load(a.checkpoint,map_location='cpu',weights_only=True);model=LM(**saved['config']);model.load_state_dict(saved['model']);model.to(a.device).eval();sp=spm.SentencePieceProcessor(model_file=a.tokenizer)
    identity={'model_sha256':digest(a.checkpoint),'tokenizer_sha256':digest(a.tokenizer),'scorer_version':'joint-common-context-prefix-v2; separate-boundary-strict-v2; context-NFKC-whitespace','frozen_input_sha256':digest(a.input or root/'frozen.jsonl')}
    cache=sqlite3.connect(root/'score-cache.sqlite');cache.execute('CREATE TABLE IF NOT EXISTS scores(key TEXT PRIMARY KEY,value TEXT)')
    donors={x['base_id']:x['context'] for x in cases}; donor_ids=sorted(donors)
    rng=random.Random(20261111); shuffled=donor_ids[:];rng.shuffle(shuffled)
    # A cyclic shift guarantees that every donor comes from a different base document.
    shuffled_context={i:donors[shuffled[(j+1)%len(shuffled)]] for j,i in enumerate(shuffled)}
    def score(case,arm,family,track):
        pool=case['pools'][arm]['candidates']; texts=[x['text'] for x in pool]
        context=case['context'] if track=='real' else '' if track=='empty' else shuffled_context[case['base_id']]
        key=hashlib.sha256(json.dumps([identity['model_sha256'],identity['tokenizer_sha256'],identity['scorer_version'],family,context,texts],ensure_ascii=False).encode()).hexdigest()
        old=cache.execute('SELECT value FROM scores WHERE key=?',(key,)).fetchone()
        if old:return json.loads(old[0])
        fallback=None; values=[]
        try:
            if texts:
                if family=='joint':values,_=score_joint(model,sp,context,texts,a.device)
                else:values,_=score_separate_strict(model,sp,context,texts,a.device)
        except ScoringFallback as e:fallback=str(e)
        result={'scores':values,'fallback':fallback};cache.execute('INSERT INTO scores VALUES(?,?)',(key,json.dumps(result)));return result
    def ranking(case,arm,family,alpha,track):
        result=score(case,arm,family,track);pool=case['pools'][arm]['candidates']
        order=list(range(len(pool))) if result['fallback'] or not pool else order_pool(result['scores'],[c['consumed'] for c in pool],alpha)
        return outcome(case,pool,order),result['fallback']
    started=time.monotonic()
    if a.split=='dev':
        selections={}; search={}
        for arm in ('C','B'):
            choices=[]
            for family in ('joint','legacy'):
                for alpha in (0.,.25,.5,.75,1.):
                    counts=defaultdict(lambda:[0,0])
                    for case in cases:
                        if case['condition'] not in ('clean_off','fuzzy_on'):continue
                        value,_=ranking(case,arm,family,alpha,'real');c=counts[case['condition']];c[0]+=value['top1'];c[1]+=1
                    objective=sum(v[0]/v[1] for v in counts.values())/len(counts)
                    choices.append({'scorer':family,'alpha':alpha,'objective':objective,'counts':dict(counts)})
                cache.commit();print('development calibrated',arm,family,flush=True)
            best=max(choices,key=lambda x:(x['objective'],-x['alpha'],x['scorer']=='joint'))
            selections[arm]={'scorer':best['scorer'],'alpha':best['alpha']};search[arm]=choices
        config={'settings':selections,'search':search,'identity':identity,'selection_objective':'equal mean of dev clean_off Top1 and all eligible fuzzy_on Top1'}
        configpath.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        config=json.loads(configpath.read_text(encoding='utf-8'))
        if any(config['identity'][k]!=identity[k] for k in ('model_sha256','tokenizer_sha256','scorer_version')):raise ValueError('model/scorer identity changed after selection')
    predictions=[];latencies=defaultdict(list)
    with predpath.with_suffix('.tmp').open('w',encoding='utf-8') as f:
      for number,case in enumerate(cases):
        systems={};fallbacks={}
        for arm in ('A','B','C'):
            pool=case['pools'][arm]['candidates'];systems[arm]=outcome(case,pool,list(range(len(pool))));latencies[arm].append(case['pools'][arm]['generation_ms'])
        for arm,label in [('C','D'),('B','E')]:
            selected=config['settings'][arm]
            for track in ('real','empty','shuffled'):
                name=label if track=='real' else label+'_'+track
                systems[name],fallbacks[name]=ranking(case,arm,selected['scorer'],selected['alpha'],track)
            for family in ('joint','legacy'):
                name=label+'_'+family+'_pure';systems[name],fallbacks[name]=ranking(case,arm,family,1.,'real')
        row={k:case[k] for k in ('case_id','base_id','document_sha256','condition','rule','shard','length_group','target','keys')};row.update(systems=systems,fallbacks=fallbacks)
        predictions.append(row);f.write(json.dumps(row,ensure_ascii=False)+'\n')
        if number%500==0:cache.commit();print('scored',a.split,number,'seconds',round(time.monotonic()-started,1),flush=True)
    cache.commit();cache.close();predpath.with_suffix('.tmp').replace(predpath)
    result={'identity':identity,'split':a.split,'cases':len(cases),'base_documents':len(donors),'settings':config['settings'],'seconds':time.monotonic()-started,'metrics':summaries(predictions),'dictionary_generation_ms':{k:{'p50':float(np.quantile(v,.5)),'p95':float(np.quantile(v,.95)),'max':max(v)} for k,v in latencies.items()},'limitations':['Synthetic pinyin, not typing logs','Case-level p-values are exploratory; repeated variants correlated','Local FineWeb2 raw-shard provenance is not independently verified','Near-duplicate screening is limited to first 256 Han characters']}
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'split':a.split,'cases':len(cases),'settings':config['settings'],'seconds':result['seconds']},indent=2))

if __name__=='__main__':main()
