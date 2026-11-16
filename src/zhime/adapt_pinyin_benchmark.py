"""Create a paired full/initial/mixed benchmark from existing labeled pinyin items."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random
import re

import jieba

from .standard_eval import read, write
from .wechat_benchmark import make_item, render


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--web', type=Path, required=True)
    p.add_argument('--standard', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--per-dataset', type=int, default=300)
    p.add_argument('--seed', type=int, default=20261115)
    args = p.parse_args()
    if args.per_dataset < 1:
        p.error('--per-dataset must be positive')
    rng = random.Random(args.seed)
    tokenizer = jieba.Tokenizer()
    records, counts = [], {}
    for dataset, path, split, source_key in (
        ('web', args.web, 'test', 'shard'),
        ('standard', args.standard, 'development', 'source'),
    ):
        buckets = defaultdict(list)
        sources = read(path)
        for row in sources:
            text = row['target']
            syllables = row['syllables']
            if row['split'] == split and 5 <= len(text) <= 16 and len(syllables) == len(text) \
                    and all(re.fullmatch('[a-z]+', s) for s in syllables) \
                    and ''.join(syllables) == row.get('keys_full', row.get('keys')):
                length_group = '5-8' if len(text) <= 8 else '9-12' if len(text) <= 12 else '13-16'
                buckets[row[source_key], length_group].append(row)
        distribution = Counter()
        selected = []
        # Round-robin across nonempty source/length strata after a seeded shuffle.
        for pool in buckets.values():
            rng.shuffle(pool)
        strata = sorted(buckets)
        while len(selected) < args.per_dataset and any(buckets.values()):
            for group in strata:
                pool = buckets[group]
                if not pool:
                    continue
                original = pool.pop()
                row = make_item(original['target'], original['syllables'], tokenizer, rng)
                if not row['abbreviations']:
                    continue
                row.update(id=f"{dataset}-{original['id']}", original_id=original['id'],
                           dataset=dataset, source_group=group[0], length_group=group[1])
                selected.append(row)
                distribution['/'.join(group)] += 1
                if len(selected) == args.per_dataset:
                    break
        if len(selected) != args.per_dataset:
            raise ValueError(f'Only {len(selected)} eligible {dataset} rows.')
        counts[dataset] = {'split': split, 'original_items_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'original_split_rows': sum(r['split'] == split for r in sources),
                           'sampled': len(selected), 'strata': dict(distribution)}
        records.extend(selected)
    args.output.mkdir(parents=True, exist_ok=False)
    items = args.output / 'items.jsonl'
    write(items, records)
    (args.output / 'items.txt').write_text(render(records), encoding='utf-8')
    manifest = {'seed': args.seed, 'samples': len(records),
                'variants': dict(Counter(v['kind'] for row in records for v in row['abbreviations'])),
                'datasets': counts, 'items_sha256': hashlib.sha256(items.read_bytes()).hexdigest(),
                'protocol': '5–16 character targets, original annotated syllables, empty-context exact match.'}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({'samples': len(records), 'variants': manifest['variants']}))


if __name__ == '__main__':
    main()
