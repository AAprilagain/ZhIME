"""Score the external Standard Chinese IME development benchmark."""
import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path


def read(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write(path, rows):
    with path.open('w', encoding='utf-8', newline='\n') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def generate_rime_pools(args, keys_list, log_path=None):
    pools = {}
    log_path = log_path or args.output.parent / 'rime.stderr.log'
    with log_path.open('w', encoding='utf-8') as errors:
        with subprocess.Popen([str(args.rime_exe), str(args.weasel), str(args.profile.resolve()), 'bench_'],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                              text=True, encoding='utf-8', bufsize=1) as proc:
            for arm in ('A', 'B'):
                for keys in keys_list:
                    proc.stdin.write(f'{arm.lower()}_0\t{keys}\n')
                    proc.stdin.flush()
                    line = proc.stdout.readline()
                    if not line:
                        raise RuntimeError('Rime exited while producing candidates.')
                    fields = line.rstrip('\n').split('\t') if line.strip() else []
                    if len(fields) % 2:
                        raise ValueError('Invalid candidate response from Rime.')
                    seen = set()
                    candidates = []
                    for text, end in zip(fields[::2], fields[1::2]):
                        if text in seen:
                            continue
                        seen.add(text)
                        candidates.append({'text': text, 'consumed': int(end)})
                    pools[arm, keys] = candidates
            proc.stdin.close()
            if proc.wait():
                raise RuntimeError('Rime helper failed.')
    return pools


def generate(args):
    from dataclasses import asdict
    from .engine import Engine

    items = read(args.package / 'items.jsonl')
    engine = Engine(args.lexicon, beam=64, fuzzy_penalty=1)
    keys_list = sorted({item['keys_full'] for item in items})
    pools = generate_rime_pools(args, keys_list)
    for keys in keys_list:
        pools['C', keys] = [asdict(candidate) for candidate in engine.query(keys)]
    write(args.output, ({'id': item['id'], 'keys': item['keys_full'],
                         'pools': {arm: pools[arm, item['keys_full']] for arm in 'ABC'}}
                        for item in items))


def score(args):
    import sentencepiece as spm
    import torch
    from .joint_score import ScoringFallback, order_pool, score_joint, score_separate_strict
    from .model import LM

    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    saved = torch.load(args.model, map_location='cpu', weights_only=True)
    model = LM(**saved['config']).to(args.device).eval()
    model.load_state_dict(saved['model'])
    sp = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    torch.set_num_threads(2)
    selection = json.loads(args.selection.read_text(encoding='utf-8'))
    for key, path in [('model_sha256', args.model), ('tokenizer_sha256', args.tokenizer)]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != selection['identity'][key]:
            raise ValueError(f'Unexpected artifact for {key}.')
    items = {row['id']: row for row in read(args.package / 'items.jsonl')}

    def values(item_id, texts, family='joint', context=True):
        if not texts:
            return [], None
        scorer = score_joint if family == 'joint' else score_separate_strict
        try:
            result, _ = scorer(model, sp, items[item_id]['left_context'] if context else '', texts, args.device)
            return result, None
        except ScoringFallback as exc:
            return None, str(exc)

    for track in ('full', 'abbrev'):
        pools = read(args.package / f'pool-{track}.jsonl')
        expected = {key for key, item in items.items() if item[f'keys_{track}'] is not None}
        if len(pools) != len(expected) or {row['id'] for row in pools} != expected:
            raise ValueError(f'Unexpected ID coverage for the {track} pool.')
        for context in ((True, False) if track == 'full' else (True,)):
            def official_rows():
                for row in pools:
                    if row['keys'] != items[row['id']][f'keys_{track}']:
                        raise ValueError(f"Key mismatch at {row['id']}.")
                    texts = [candidate['text'] for candidate in row['candidates']]
                    scores, failure = values(row['id'], texts, context=context)
                    yield {'id': row['id'], 'candidates': texts,
                           'scores': [x[0] for x in scores] if scores is not None else None,
                           'fallback': failure}
            suffix = '' if context else '-empty'
            write(args.output / f'official-{track}{suffix}-scores.jsonl', official_rows())
        print('official', track, round(time.monotonic() - started, 1), flush=True)

    def own_rows():
        for row in read(args.pools):
            item = items[row['id']]
            accepted = {item['target'], *item['alternatives']}
            output = {'id': row['id'], 'source': item['source'],
                      'dialogue': f"{item['source']}:{item['dialogue_id']}",
                      'length': len(item['target']), 'target': item['target'], 'systems': {}}
            for arm, pool in row['pools'].items():
                def record(name, order, fallback=None):
                    rank = next((rank for rank, idx in enumerate(order, 1)
                                 if pool[idx]['consumed'] == len(row['keys'])
                                 and pool[idx]['text'] in accepted), None)
                    output['systems'][name] = {'rank': rank, 'choice': pool[order[0]]['text'] if order else None,
                                               'fallback': fallback}
                order = list(range(len(pool)))
                record(arm, order)
                if arm == 'A':
                    continue
                setting = selection['settings'][arm]
                name = 'D' if arm == 'C' else 'E'
                texts = [c['text'] for c in pool]
                coverage = [c['consumed'] for c in pool]
                scores, failure = values(row['id'], texts, setting['scorer'])
                record(name, order if failure else order_pool(scores, coverage, setting['alpha']), failure)
            yield output
    write(args.output / 'generated-full-predictions.jsonl', own_rows())
    print('generated full', round(time.monotonic() - started, 1), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    generation = sub.add_parser('generate')
    scoring = sub.add_parser('score')
    for command in (generation, scoring):
        command.add_argument('--package', required=True, type=Path)
        command.add_argument('--output', required=True, type=Path)
    generation.add_argument('--profile', required=True, type=Path)
    generation.add_argument('--lexicon', default='artifacts/lexicon/lexicon.sqlite', type=Path)
    generation.add_argument('--rime-exe', default='native/build/Release/rime_pool.exe', type=Path)
    generation.add_argument('--weasel', default=r'C:\Program Files\Rime\weasel-0.17.4', type=Path)
    for name in ('model', 'tokenizer', 'selection', 'pools'):
        scoring.add_argument(f'--{name}', required=True, type=Path)
    scoring.add_argument('--device', default='cpu', choices=['cpu', 'cuda'])
    args = parser.parse_args()
    {'generate': generate, 'score': score}[args.command](args)


if __name__ == '__main__':
    main()
