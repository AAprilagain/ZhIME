import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

def main():
    p = argparse.ArgumentParser(); p.add_argument('--checkpoint', required=True); p.add_argument('--tokenizer', required=True); p.add_argument('--context-repeats', type=int, default=1); a = p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / 'spool'
        proc = subprocess.Popen([sys.executable, '-m', 'zhime.server', '--checkpoint', a.checkpoint, '--tokenizer', a.tokenizer, '--port', '18767', '--spool', str(folder)], stdout=subprocess.DEVNULL)
        try:
            deadline = time.time()+60
            while not (folder/'ready').exists():
                if proc.poll() is not None: raise RuntimeError('service exited')
                if time.time() > deadline: raise TimeoutError('service startup')
                time.sleep(.05)
            texts = ['我今天准备去' * a.context_repeats] + ['北京','背景','北境','被惊','倍镜','北','背','被','备','倍','杯','悲','辈','贝','狈','碑','褙','卑','悖','焙']
            payload = '\t'.join(['R1']+[s.encode().hex() for s in texts])+'\n'
            durations=[]
            for i in range(20):
                staging=folder/f'{i}.tmp'; req=folder/f'{i}.req'; res=folder/f'{i}.res'
                start=time.perf_counter(); staging.write_text(payload,encoding='ascii'); os.replace(staging,req)
                while not res.exists():
                    if time.perf_counter()-start > 5: raise TimeoutError('request')
                    time.sleep(.001)
                order=[int(x) for x in res.read_text().split(',')]; res.unlink()
                assert sorted(order)==list(range(1,21))
                durations.append((time.perf_counter()-start)*1000)
            durations.sort()
            try:
                import psutil
                parent=psutil.Process(proc.pid)
                rss=sum(p.memory_info().rss for p in [parent] + parent.children(recursive=True))
            except ImportError: rss=None
            print(json.dumps({'platform':sys.platform,'calls':20,'candidate_count':20,'context_characters':len(texts[0]),'p50_ms':durations[10],'p95_ms':durations[19],'max_ms':durations[-1], 'rss_bytes':rss},indent=2))
        finally:
            proc.terminate(); proc.wait(timeout=10)

if __name__ == '__main__': main()
