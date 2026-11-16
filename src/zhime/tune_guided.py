"""Explore search-time fusion on disjoint, seeded WeChat benchmark subsets."""
from dataclasses import asdict
import json
from pathlib import Path
import random
import statistics
import time

import sentencepiece as spm
import torch

from .engine import Engine
from .guided_search import scorer
from .joint_score import ScoringFallback, order_weighted_pool, score_joint
from .model import LM
from .retrain import sha256
from .score_v2 import outcome
from .standard_eval import read


def main():
    root = Path('benchmark-wechat')
    config = json.loads(Path('artifacts/deployment-local-gpu.json').read_text())
    device = config['device']
    if sha256(Path(config['checkpoint'])) != config['model_sha256'] or sha256(Path('artifacts/tokenizer.model')) != config['tokenizer_sha256']:
        raise ValueError('Artifact hash mismatch')
    torch.set_num_threads(2)
    saved = torch.load(config['checkpoint'], map_location='cpu', mmap=True, weights_only=True)
    with torch.device('meta'):
        model = LM(**saved['config'])
    model.load_state_dict(saved['model'], assign=True)
    model.to(device=device, dtype=torch.float16).eval()
    del saved
    tokenizer = spm.SentencePieceProcessor(model_file='artifacts/tokenizer.model')
    engine = Engine('artifacts/lexicon/lexicon.sqlite', beam=64, fuzzy_penalty=1)
    grid = [0., .5, 2., 6., 12.]
    rng = random.Random(20261115)
    items = read(root / 'items.jsonl')
    rng.shuffle(items)

    def evaluate(subset, weights):
        data = {str(weight): {} for weight in weights}
        data['fixed'] = {}
        for index, item in enumerate(subset):
            variants = [('full', item['keys_full'])]
            for kind in ('abbrev', 'mixed'):
                first = next((v for v in item['abbreviations'] if v['kind'] == kind), None)
                if first:
                    variants.append((kind, first['keys']))
            for kind, keys in variants:
                case = {'keys': keys, 'target': item['target']}
                original = engine.query(keys, abbreviated=True)
                pool = [asdict(c) for c in original]
                order = list(range(len(pool)))
                try:
                    scores, _ = score_joint(model, tokenizer, '', [c.text for c in original], device)
                    order = order_weighted_pool(scores, [c.consumed for c in original], [c.score for c in original], 12.)
                except ScoringFallback:
                    pass
                data['fixed'].setdefault(kind, []).append(outcome(case, pool, order))
                lm_scores = scorer(model, tokenizer, '', device)
                for weight in weights:
                    start = time.perf_counter()
                    ranked = engine.guided_query(keys, (), lm_scores if weight else lambda texts: dict.fromkeys(texts, 0.), weight)
                    elapsed = 1000 * (time.perf_counter() - start)
                    pool = [asdict(c) for c in ranked]
                    data[str(weight)].setdefault(kind, []).append({**outcome(case, pool, list(range(len(pool)))), 'ms': elapsed})
            if (index + 1) % 10 == 0:
                print(f'{index + 1}/{len(subset)} clauses', flush=True)
        return {name: {kind: {'n': len(rows),
                              **{field: statistics.mean(r[field] for r in rows) for field in ('top1','top5','recalled')},
                              'median_ms': statistics.median(r['ms'] for r in rows) if name != 'fixed' else None}
                       for kind, rows in groups.items()} for name, groups in data.items()}

    dev = evaluate(items[:36], grid)
    best = max(grid, key=lambda weight: (statistics.mean(x['top1'] for x in dev[str(weight)].values()), -weight))
    print(f'Selected weight {best}', flush=True)
    test = evaluate(items[36:96], [best])
    result = {'weight': best, 'grid': grid, 'development': dev, 'test': test,
              'dev_ids': [i['id'] for i in items[:36]], 'test_ids': [i['id'] for i in items[36:96]],
              'model_sha256': config['model_sha256'], 'items_sha256': sha256(root / 'items.jsonl'),
              'protocol': 'Empty context. One full, pure abbreviated and first mixed variant per clause when available. '
                          'Select by equal mean of track Top-1. Dev latencies reuse scores across weights.'}
    (root / 'guided-search-results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(test), flush=True)


if __name__ == '__main__':
    main()
