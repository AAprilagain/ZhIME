"""Build resumable token shards directly from the permitted Parquet files."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import itertools
import json
from pathlib import Path
import re
import time

import numpy as np
from opencc import OpenCC
import pyarrow.parquet as pq
import sentencepiece as spm


HAN = re.compile(r'[\u3400-\u9fff]')
CLAUSE = re.compile(r'[\u4e00-\u9fff]+')
EXCLUDED = {'000_00009', '000_00027', '000_00045', '001_00007', '001_00025'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def init(tokenizer, heldout, external):
    global sp, cc, traditional, docs, targets, messages
    sp = spm.SentencePieceProcessor(model_file=str(tokenizer))
    cc = OpenCC('t2s')
    chars = set()
    for table in cc.dict_cache.values():
        for key, candidates in table[2].items():
            value = candidates.split(' ')[0]
            if len(key) == len(value):
                chars.update(a for a, b in zip(key, value) if a != b)
            elif key != value:
                chars.update(key)
    traditional = re.compile('[' + re.escape(''.join(sorted(chars))) + ']')
    items = [json.loads(line) for line in heldout.read_text(encoding='utf-8').splitlines()]
    if not {item['shard'] for item in items} <= EXCLUDED:
        raise ValueError('Held-out manifest contains an unblocked shard.')
    docs = {item['document_sha256'] for item in items}
    targets = {item['target'] for item in items}
    messages = {' '.join(json.loads(line)['message'].split())
                for line in external.read_text(encoding='utf-8').splitlines()}


def encode(task):
    path, output, bytes_expected, provenance, wait_seconds = task
    stem = path.stem
    tokens = output / f'{stem}.u16'
    metadata = output / f'{stem}.json'
    if metadata.exists():
        saved = json.loads(metadata.read_text())
        if (saved['parquet_bytes'] != bytes_expected or saved['provenance'] != provenance
                or tokens.stat().st_size != 2 * saved['tokens']):
            raise ValueError(f'Stale token shard {stem}')
        return saved
    deadline = time.monotonic() + wait_seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(2)
    if path.stat().st_size != bytes_expected:
        raise ValueError(f'Truncated input {path}')
    hashes = {'parquet_sha256': digest(path)}
    partial = tokens.with_suffix('.partial')
    counts = dict(rows=0, han=0, tokens=0, overlap_rejected=0, short_rejected=0)
    with partial.open('wb') as destination:
        for batch in pq.ParquetFile(path).iter_batches(columns=['text'], batch_size=2048):
            texts = []
            for value in batch.column(0).to_pylist():
                text = ' '.join((value or '').split())
                n = len(HAN.findall(text))
                if n < 4:
                    counts['short_rejected'] += 1
                    continue
                normalized = cc.convert(text) if traditional.search(text) else text
                clauses = CLAUSE.findall(normalized)
                if (text in messages or targets.intersection(clauses)
                        or hashlib.sha256(''.join(clauses).encode()).hexdigest() in docs):
                    counts['overlap_rejected'] += 1
                    continue
                texts.append(text)
                counts['rows'] += 1
                counts['han'] += n
            if texts:
                encoded = sp.encode(texts, out_type=int, add_eos=True)
                values = np.fromiter(itertools.chain.from_iterable(encoded), dtype='<u2')
                counts['tokens'] += len(values)
                values.tofile(destination)
    partial.replace(tokens)
    saved = {'parquet': path.name, 'file': tokens.name, 'parquet_bytes': bytes_expected,
             'provenance': provenance, **hashes, **counts, 'sha256': digest(tokens)}
    tmp = metadata.with_suffix('.tmp')
    tmp.write_text(json.dumps(saved, indent=2))
    tmp.replace(metadata)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('input', 'output', 'inventory', 'tokenizer', 'heldout', 'external'):
        parser.add_argument('--' + arg, required=True, type=Path)
    parser.add_argument('--workers', type=int, default=12)
    parser.add_argument('--wait-input', type=int, default=0, help='seconds to wait for each shard transfer')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    inventory = json.loads(args.inventory.read_text(encoding='utf-8-sig'))
    names = [row['Name'] for row in inventory]
    if len(set(names)) != len(names) or any(
            not re.fullmatch(r'(?:web|review)-\d{3}_\d{5}-\d{3}\.parquet|chat-lccc-train\.parquet', name)
            or name.split('-')[1] in EXCLUDED for name in names):
        raise ValueError('Inventory has invalid or held-out shards.')
    provenance = {key + '_sha256': digest(getattr(args, key)) for key in ('tokenizer', 'heldout', 'external')}
    tasks = [(args.input / row['Name'], args.output, row['Length'], provenance, args.wait_input)
             for row in inventory]
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init,
                             initargs=(args.tokenizer, args.heldout, args.external)) as pool:
        futures = {pool.submit(encode, task): task[0] for task in tasks}
        shards = []
        for future in as_completed(futures):
            shard = future.result()
            shards.append(shard)
            print(json.dumps({'prepared': len(shards), 'total': len(tasks), **shard}), flush=True)
    manifest = {**provenance, 'excluded_shards': sorted(EXCLUDED),
                'shards': sorted(shards, key=lambda x: x['file']),
                'totals': {key: sum(s[key] for s in shards) for key in ('rows', 'han', 'tokens', 'overlap_rejected', 'short_rejected')}}
    tmp = args.output / 'manifest.tmp'
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(args.output / 'manifest.json')
    print(json.dumps(manifest['totals']), flush=True)


if __name__ == '__main__':
    main()
