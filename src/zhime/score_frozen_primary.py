"""Score the production C→D reranker on a partition of a frozen benchmark."""
import argparse
import json
from pathlib import Path

import sentencepiece as spm
import torch

from .joint_score import ScoringFallback, order_pool, score_joint, score_separate_strict
from .model import LM
from .retrain import sha256
from .score_v2 import outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('input', 'output', 'checkpoint', 'tokenizer', 'selection'):
        parser.add_argument('--' + field, type=Path, required=True)
    parser.add_argument('--parts', type=int, default=1)
    parser.add_argument('--part', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.part < args.parts:
        parser.error('Invalid partition')
    config = json.loads(args.selection.read_text())
    for key, path in [('model_sha256', args.checkpoint), ('tokenizer_sha256', args.tokenizer)]:
        if sha256(path) != config['identity'][key]:
            raise ValueError(f'Artifact identity differs at {key}')
    torch.set_num_threads(2)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    saved = torch.load(args.checkpoint, map_location='cpu', weights_only=True)
    model = LM(**saved['config'])
    model.load_state_dict(saved['model'])
    del saved
    model.cuda().eval()
    sp = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    setting = config['settings']['C']
    scorer = score_joint if setting['scorer'] == 'joint' else score_separate_strict
    partial = args.output.with_suffix('.partial')
    completed = 0
    with args.input.open(encoding='utf-8') as source, partial.open('w', encoding='utf-8') as dest:
        for index, line in enumerate(source):
            if index % args.parts != args.part:
                continue
            row = json.loads(line)
            pool = row['pools']['C']['candidates']
            order = list(range(len(pool)))
            fallback = None
            try:
                scores, _ = scorer(model, sp, row['context'], [c['text'] for c in pool], 'cuda')
                order = order_pool(scores, [c['consumed'] for c in pool], setting['alpha'])
            except ScoringFallback as exc:
                fallback = str(exc)
            result = {key: row[key] for key in ('case_id', 'condition', 'document_sha256')}
            result.update(D=outcome(row, pool, order), fallback=fallback)
            dest.write(json.dumps(result, ensure_ascii=False) + '\n')
            completed += 1
            if completed % 500 == 0:
                print(f'Part {args.part}: {completed}', flush=True)
    partial.replace(args.output)


if __name__ == '__main__':
    main()
