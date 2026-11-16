"""Reproducible, streaming corpus sampling from the supplied Parquet export."""
import argparse
import hashlib
import json
import random
import re
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from pathlib import Path

import pyarrow.parquet as pq

HAN = re.compile(r"[\u3400-\u9fff]")
HAN_CLAUSE = re.compile(r"[\u4e00-\u9fff]+")

def _init_screen(documents, targets):
    global _screen_documents, _screen_targets, _simplify
    from .benchmark_v2 import simplify
    _screen_documents, _screen_targets, _simplify = documents, targets, simplify


def _screen_row(value):
    if not value:
        return None, 0
    text = ' '.join(value.split())
    count = len(HAN.findall(text))
    if count < 4:
        return None, 0
    clauses = HAN_CLAUSE.findall(_simplify(text))
    if (_screen_targets.intersection(clauses)
            or hashlib.sha256(''.join(clauses).encode()).hexdigest() in _screen_documents):
        return None, -1
    return text, count


def sample(root, output, target, seed, excluded_shards=(), heldout=None, screen_workers=1):
    rng = random.Random(seed)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    quotas = {"web": int(target * .8), "review": int(target * .1), "chat": int(target * .1)}
    if (out / 'manifest.json').exists():
        raise FileExistsError(f'completed corpus already exists in {out}')
    excluded_shards = set(excluded_shards)
    heldout_documents, heldout_targets = set(), set()
    if heldout:
        heldout_bytes = Path(heldout).read_bytes()
        for line in heldout_bytes.splitlines():
            item = json.loads(line)
            excluded_shards.add(item['shard'])
            heldout_documents.add(item['document_sha256'])
            heldout_targets.add(item['target'])
    manifest = {"seed": seed, "target_han": target, "source_manifest": str(Path(root) / 'manifest.json'), "sources": {},
                "excluded_shards": sorted(excluded_shards)}
    if heldout:
        manifest['heldout_items_sha256'] = hashlib.sha256(heldout_bytes).hexdigest()
        manifest['heldout_screening'] = 'exact simplified Han document SHA256 or exact simplified Han clause'
    partial = out / 'train.txt.partial'
    if heldout and screen_workers == 1:
        _init_screen(heldout_documents, heldout_targets)
    pool_context = (ProcessPoolExecutor(max_workers=screen_workers, initializer=_init_screen,
                                       initargs=(heldout_documents, heldout_targets))
                    if heldout and screen_workers > 1 else nullcontext())
    with pool_context as pool, partial.open('w', encoding='utf-8', newline='\n') as dst:
        for source, quota in quotas.items():
            paths = sorted(path for path in (Path(root) / 'train').glob(source + '*.parquet')
                           if not excluded_shards.intersection(path.stem.split('-')[1:2]))
            rng.shuffle(paths)
            count = rows = 0
            rejected = 0
            used = []
            for path in paths:
                print(f'{source}: {count:,}/{quota:,} Han characters before {path.name}', flush=True)
                used.append(path.name)
                parquet = pq.ParquetFile(path)
                groups = list(range(parquet.num_row_groups))
                rng.shuffle(groups)
                for group in groups:
                    for batch in parquet.iter_batches(row_groups=[group], columns=['text'], batch_size=2048):
                        values = batch.column(0).to_pylist()
                        rng.shuffle(values)
                        if heldout:
                            screened = pool.map(_screen_row, values, chunksize=64) if pool else map(_screen_row, values)
                        else:
                            cleaned = (' '.join(value.split()) for value in values if value)
                            screened = ((text, len(HAN.findall(text))) for text in cleaned)
                        for text, n in screened:
                            if n < 4:
                                if n < 0:
                                    rejected += 1
                                continue
                            dst.write(text + '\n')
                            count += n
                            rows += 1
                            if count >= quota:
                                break
                        if count >= quota:
                            break
                    if count >= quota:
                        break
                if count >= quota:
                    break
            manifest['sources'][source] = {'quota_han': quota, 'han': count, 'rows': rows, 'files': used}
            if heldout:
                manifest['sources'][source]['heldout_matches_rejected'] = rejected
            print(source, count, rows, flush=True)
            if count < quota:
                raise RuntimeError(f'{source}: only {count} Han characters, wanted {quota}')
    h = hashlib.sha256()
    with partial.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    manifest['train_sha256'] = h.hexdigest()
    partial.replace(out / 'train.txt')
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--han', type=int, default=100_000_000)
    p.add_argument('--seed', type=int, default=20261110)
    p.add_argument('--exclude-shard', action='append', default=[])
    p.add_argument('--heldout', help='benchmark-v2 items.jsonl to exclude by shard, document and exact clause')
    p.add_argument('--screen-workers', type=int, default=1)
    a = p.parse_args()
    if a.screen_workers < 1:
        p.error('--screen-workers must be positive')
    sample(a.root, a.output, a.han, a.seed, a.exclude_shard, a.heldout, a.screen_workers)
