"""Windows CPU service timings, explicitly separate from GUI typing latency."""
import argparse,json,time,urllib.parse,urllib.request,platform,statistics
from pathlib import Path
import psutil
p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);p.add_argument('--output',default='runtime/performance.json');a=p.parse_args()
health=json.load(urllib.request.urlopen('http://127.0.0.1:18770/health'))
context='我们正在讨论中文输入法的候选排序方法以及系统实验。'
groups=[('short_exact','nihao','',0),('short_fuzzy','nihao',(context*3)[:32],63),('sentence_exact','jintianwomenqubeijing',(context*3)[:32],0),('long_context','jintianwomenqubeijing',(context*5)[:96],0)]
results=[]
for name,keys,ctx,mask in groups:
 for rank in (0,1):
  times=[];statuses={}
  for i in range(22):
   url='http://127.0.0.1:18770/query?'+urllib.parse.urlencode({'keys':keys,'fuzzy':mask,'context':ctx.encode().hex(),'rank':rank,'format':'json','id':i,'session':'performance-test'})
   start=time.perf_counter();value=json.load(urllib.request.urlopen(url,timeout=10));elapsed=(time.perf_counter()-start)*1000
   if i>=2:times.append(elapsed);statuses[value['status']]=statuses.get(value['status'],0)+1
  times.sort();result={'scenario':name,'keys_length':len(keys),'context_characters':len(ctx),'fuzzy_mask':mask,'model_requested':bool(rank),'calls':20,'warmup_excluded':2,'dictionary_cache':'warm repeated keys','p50_ms':statistics.median(times),'p95_ms':times[18],'max_ms':times[-1],'statuses':statuses};results.append(result);print(json.dumps(result),flush=True)
process=psutil.Process(a.pid);memory=process.memory_info()
record={'platform':platform.platform(),'processor':platform.processor(),'logical_cpus':psutil.cpu_count(),'engine_pid':a.pid,'rss_bytes':memory.rss,'peak_working_set_bytes':getattr(memory,'peak_wset',None),'service_configuration':health,'scope':'Loopback HTTP roundtrip on Windows CPU, warm dictionary queries; not TSF keystroke-to-paint latency. Twenty observations per setting do not characterize production tail latency.','results':results}
Path(a.output).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
