"""Compare exact short-continuation matches using held-out Standard IME prompts."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

import sentencepiece as spm
import torch

from .model import LM
from .retrain import sha256
from .score_v2 import bootstrap
from .suggest import suggest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('items', 'old', 'new', 'tokenizer', 'output', 'overlap'):
        p.add_argument('--' + name, required=True, type=Path)
    p.add_argument('--device', choices=['cpu', 'cuda'], default='cuda')
    args = p.parse_args()
    overlaps = json.loads(args.overlap.read_text(encoding='utf-8'))
    excluded = set(overlaps['old']) | set(overlaps['new'])
    items = []
    for line in args.items.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        accepted = {text for text in [row['target'], *row['alternatives']]
                    if re.fullmatch(r'[\u3400-\u9fff]{2,8}', text)}
        if row['id'] not in excluded and accepted and len(row['left_context'].strip()) >= 2:
            items.append({'id': row['id'], 'context': row['left_context'], 'accepted': sorted(accepted),
                          'source': row['source'], 'document_sha256': f"{row['source']}:{row['dialogue_id']}",
                          'systems': {}})
    sp = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    torch.set_num_threads(2)
    for name in ('old', 'new'):
        checkpoint = torch.load(getattr(args, name), map_location='cpu', weights_only=True)
        model = LM(**checkpoint['config'])
        model.load_state_dict(checkpoint['model'])
        del checkpoint
        model.to(args.device).eval()
        for row in items:
            phrases = suggest(model, sp, row['context'], budget_seconds=float('inf'))
            rank = next((i for i, text in enumerate(phrases, 1) if text in row['accepted']), None)
            row['systems'][name] = {'suggestions': phrases, 'top1': int(rank == 1), 'top3': int(rank is not None)}
        del model
    grouped = defaultdict(list)
    for row in items:
        grouped['all'].append(row)
        grouped[row['source']].append(row)
    summary = {}
    for group, rows in grouped.items():
        summary[group] = {
            'n': len(rows),
            **{model: {metric: sum(row['systems'][model][metric] for row in rows) / len(rows)
                       for metric in ('top1', 'top3')} for model in ('old', 'new')},
            'top1_difference_ci95': bootstrap(rows, 'old', 'new'),
        }
    args.output.mkdir(parents=True, exist_ok=True)
    info = {'models': {name: sha256(getattr(args, name)) for name in ('old', 'new')},
            'tokenizer': sha256(args.tokenizer), 'items': sha256(args.items), 'results': summary,
            'protocol': 'Left context only, exact match of 2–8 Han-character targets or alternatives; '
                        'common eligible subset, no interactive deadline.'}
    (args.output / 'summary.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    with (args.output / 'predictions.jsonl').open('w', encoding='utf-8') as stream:
        for row in items:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
