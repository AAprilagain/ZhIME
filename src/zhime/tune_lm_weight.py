"""Tune raw beam + LM score fusion on clean development cases."""
import argparse
import json
from pathlib import Path

import sentencepiece as spm
import torch

from .joint_score import ScoringFallback, order_pool, order_weighted_pool, score_joint
from .model import LM
from .retrain import sha256
from .score_v2 import bootstrap, outcome


GRID = (0., .0625, .125, .25, .5, .75, 1., 1.5, 2., 3., 4., 6., 8., 12., 16., 32., 64.)


def evaluate(path, model, tokenizer, weights, device):
    records = []
    fallback = 0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['condition'] != 'clean_off':
                continue
            pool = row['pools']['C']['candidates']
            coverage = [c['consumed'] for c in pool]
            gen = [c['score'] for c in pool]
            original = list(range(len(pool)))
            scores = None
            if len(pool) > 1:
                try:
                    scores, _ = score_joint(model, tokenizer, row['context'], [c['text'] for c in pool], device)
                except ScoringFallback:
                    fallback += 1
            orders = {'beam': original, 'lm_only': order_pool(scores, coverage, 1.) if scores else original}
            for weight in weights:
                orders[str(weight)] = order_weighted_pool(scores, coverage, gen, weight) if scores else original
            records.append({'document_sha256': row['document_sha256'],
                            'systems': {key: outcome(row, pool, order) for key, order in orders.items()}})
    if not records:
        raise ValueError(f'No clean cases in {path}')
    metrics = {key: {metric: sum(r['systems'][key][metric] for r in records) / len(records)
                     for metric in ('top1', 'top5', 'mrr', 'recalled')} for key in records[0]['systems']}
    return {'n': len(records), 'fallbacks': fallback, 'metrics': metrics}, records


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('dev', 'test', 'config', 'tokenizer', 'output'):
        p.add_argument('--' + name, required=True, type=Path)
    args = p.parse_args()
    config = json.loads(args.config.read_text())
    checkpoint = Path(config['checkpoint'])
    if sha256(checkpoint) != config['model_sha256'] or sha256(args.tokenizer) != config['tokenizer_sha256']:
        raise ValueError('Deployment artifact mismatch')
    device = config['device']
    dtype = {'float16': torch.float16, 'float32': torch.float32}[config['precision']]
    torch.set_num_threads(2)
    saved = torch.load(checkpoint, map_location='cpu', weights_only=True, mmap=True)
    with torch.device('meta'):
        model = LM(**saved['config'])
    model.load_state_dict(saved['model'], assign=True)
    model.to(device=device, dtype=dtype).eval()
    del saved
    sp = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    dev, _ = evaluate(args.dev, model, sp, GRID, device)
    best = max(GRID, key=lambda w: (dev['metrics'][str(w)]['top1'], -w))
    print(json.dumps({'lm_weight': best, 'development': dev}), flush=True)
    test, rows = evaluate(args.test, model, sp, (best,), device)
    test['top1_delta_ci95_vs_lm_only'] = bootstrap(rows, 'lm_only', str(best))
    result = {'lm_weight': best, 'development': dev, 'test': test, 'grid': GRID,
              'model_sha256': config['model_sha256'], 'tokenizer_sha256': config['tokenizer_sha256'],
              'precision': config['precision'], 'dev_sha256': sha256(args.dev), 'test_sha256': sha256(args.test)}
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(test), flush=True)


if __name__ == '__main__':
    main()
