"""Evaluate frozen Rime candidate pools; never synthesize missing candidates.

JSONL fields: source, context, pinyin, target, candidates (ordered string list).
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

import sentencepiece as spm
import torch
from .model import LM
from .rerank import ranked_indices, score_candidates

def exact_p(a, b):
    n = a + b
    if not n: return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(a, b) + 1)) / 2**n)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', required=True); p.add_argument('--checkpoint', required=True)
    p.add_argument('--tokenizer', required=True); p.add_argument('--output', required=True)
    p.add_argument('--alpha', type=float, default=.5); p.add_argument('--length-exponent', type=float, default=1)
    p.add_argument('--tune', action='store_true', help='Select alpha and length exponent on development data only')
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cpu')
    p.add_argument('--preserve-length', action='store_true')
    a = p.parse_args(); torch.set_num_threads(2)
    ckpt = torch.load(a.checkpoint, map_location='cpu', weights_only=True)
    model = LM(**ckpt['config']); model.load_state_dict(ckpt['model']); model.to(a.device); model.eval()
    sp = spm.SentencePieceProcessor(model_file=a.tokenizer)
    groups = defaultdict(lambda: defaultdict(int)); cases = []; cached = []
    for line in Path(a.input).read_text(encoding='utf-8').splitlines():
        row = json.loads(line); c = row['candidates']; target = row['target']
        contextual = score_candidates(model, sp, row['context'], c, a.device) if c else []
        no_context = score_candidates(model, sp, '', c, a.device) if c else []
        cached.append((row, contextual, no_context))
    if a.tune:
        choices = []
        for preserve in (False, True):
          for exponent in (0.0, .5, 1.0):
            for alpha in (0.0, .25, .5, .75, 1.0):
                correct = 0
                for row, contextual, _ in cached:
                    c = row['candidates']; order = ranked_indices(contextual, alpha, exponent, c, preserve)
                    correct += bool(order and c[order[0]] == row['target'])
                choices.append((correct, -alpha, -exponent, not preserve, alpha, exponent, preserve))
        _, _, _, _, a.alpha, a.length_exponent, a.preserve_length = max(choices)
    for row, contextual, no_context in cached:
        c = row['candidates']; target = row['target']
        orders = {'rime': list(range(len(c))), 'no_context': ranked_indices(no_context, 1, a.length_exponent),
                  'context': ranked_indices(contextual, 1, a.length_exponent),
                  'combined_no_context': ranked_indices(no_context, a.alpha, a.length_exponent, c, a.preserve_length),
                  'combined_unrestricted': ranked_indices(contextual, a.alpha, a.length_exponent, c, False),
                  'combined': ranked_indices(contextual, a.alpha, a.length_exponent, c, a.preserve_length)}
        hits = {k: bool(v and c[v[0]] == target) for k, v in orders.items()}
        for name in ('all', row.get('source', 'unknown')):
            g = groups[name]; g['total'] += 1
            found = target in c; g['recalled'] += int(found)
            for k, order in orders.items():
                g[k + '_top1'] += int(hits[k]); g[k + '_top5'] += int(any(c[i] == target for i in order[:5]))
            g['combined_only'] += int(hits['combined'] and not hits['rime'])
            g['rime_only'] += int(hits['rime'] and not hits['combined'])
        cases.append({'source': row.get('source'), 'pinyin': row['pinyin'], 'target': target, 'rime': c[0] if c else None, 'combined': c[orders['combined'][0]] if c else None, 'recalled': target in c})
    summary = {}
    for name, g in groups.items():
        d = dict(g); d['missing'] = g['total'] - g['recalled']; d['recall'] = g['recalled'] / g['total']
        for k in ('rime', 'no_context', 'context', 'combined_no_context', 'combined_unrestricted', 'combined'):
            for rank in ('top1', 'top5'):
                d[f'{k}_{rank}_overall'] = g[f'{k}_{rank}'] / g['total']
                d[f'{k}_{rank}_conditional'] = g[f'{k}_{rank}'] / g['recalled'] if g['recalled'] else None
        d['paired_exact_p'] = exact_p(g['combined_only'], g['rime_only']); summary[name] = d
    Path(a.output).write_text(json.dumps({'parameters': vars(a), 'results': summary, 'cases': cases}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
