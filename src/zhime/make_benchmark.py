"""Build frozen pools with a separate, clean Rime profile and held-out texts."""
import argparse
import hashlib
import json
import random
import re
import subprocess
from pathlib import Path
import pyarrow.parquet as pq
from pypinyin import lazy_pinyin, Style

HAN = re.compile(r'[\u4e00-\u9fff]{8,}')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True); p.add_argument('--output', required=True)
    p.add_argument('--rime-command', nargs='+', required=True)
    p.add_argument('--per-source', type=int, default=300); p.add_argument('--seed', type=int, default=20261110)
    a = p.parse_args(); rng = random.Random(a.seed); result = {'dev': [], 'test': []}
    proc = subprocess.Popen(a.rime_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1)
    try:
        for source in ('web', 'review', 'chat'):
            paths = sorted((Path(a.root) / 'validation').glob(source + '*.parquet'))
            if source == 'chat':
                paths += sorted((Path(a.root) / 'test').glob(source + '*.parquet'))
            rng.shuffle(paths); counts = {'dev': 0, 'test': 0}
            for path in paths:
                pf = pq.ParquetFile(path)
                columns = [c for c in ('text', 'id', 'host', 'dialogue') if c in pf.schema_arrow.names]
                for batch in pf.iter_batches(columns=columns, batch_size=1024):
                    rows = batch.to_pylist(); rng.shuffle(rows)
                    for row in rows:
                        text = row.get('text') or ''
                        # A stable document/dialogue key keeps samples from the same item in one split.
                        key = str(row.get('dialogue') or row.get('id') or text)
                        split = ('test' if path.parent.name == 'test' else 'dev') if source == 'chat' else ('dev' if int(hashlib.sha256(key.encode()).hexdigest(), 16) % 2 == 0 else 'test')
                        if counts[split] >= a.per_source: continue
                        matches = list(HAN.finditer(text))
                        if not matches: continue
                        phrase = rng.choice(matches).group(); pos = rng.randrange(3, len(phrase)-3)
                        length = rng.choice((2, 3, 4)); target = phrase[pos:pos+length]
                        if len(target) != length: continue
                        # Use the whole phrase to give pypinyin contextual polyphone information.
                        py = lazy_pinyin(phrase, style=Style.NORMAL, errors='default')[pos:pos+length]
                        pinyin = "'".join(py)
                        if not re.fullmatch("[a-z']+", pinyin): continue
                        proc.stdin.write(pinyin + '\n'); proc.stdin.flush()
                        candidates = proc.stdout.readline().rstrip('\n').split('\t')
                        if candidates == ['']: candidates = []
                        result[split].append({'source': source, 'document_key_sha256': hashlib.sha256(key.encode()).hexdigest(), 'context': phrase[max(0,pos-64):pos], 'pinyin': pinyin, 'target': target, 'candidates': candidates})
                        counts[split] += 1
                        if all(v >= a.per_source for v in counts.values()): break
                    if all(v >= a.per_source for v in counts.values()): break
                if all(v >= a.per_source for v in counts.values()): break
            print(source, counts, flush=True)
            if any(v < a.per_source for v in counts.values()): raise RuntimeError('not enough held-out data')
    finally:
        proc.stdin.close(); proc.wait()
    out = Path(a.output); out.mkdir(parents=True, exist_ok=True)
    for split, rows in result.items():
        (out / (split + '.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')

if __name__ == '__main__': main()
