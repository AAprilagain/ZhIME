"""Freeze local WeChat pinyin candidates and compare fixed-pool rerankers."""
import argparse
import csv
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path

from .engine import Engine
from .rime_profiles import prepare
from .standard_eval import generate_rime_pools, read, write


def freeze(args):
    from .retrain import sha256

    inputs = args.root / 'items.jsonl'
    manifest = json.loads((args.root / 'manifest.json').read_text(encoding='utf-8'))
    if sha256(inputs) != manifest['items_sha256']:
        raise ValueError('Chat input file does not match its manifest.')
    items = read(inputs)
    cases = []
    for item in items:
        variants = [('full', item['keys_full']), *((v['kind'], v['keys']) for v in item['abbreviations'])]
        for index, (kind, keys) in enumerate(variants):
            cases.append({'case_id': f"{item['id']}-{index}", 'document_sha256': item['id'],
                          'kind': kind, 'source': item['source_group'], 'keys': keys,
                          'dataset': item.get('dataset', 'wechat'), 'target': item['target']})
    args.profile = args.output / 'rime-profile'
    prepare(args.profile, args.lexicon.with_name('zhime_shared.dict.yaml'), abbreviated=True)
    keys_list = sorted({case['keys'] for case in cases})
    path = args.output / 'frozen.jsonl'
    candidates = generate_rime_pools(args, keys_list, args.output / 'rime.stderr.log')
    engine = Engine(args.lexicon, beam=64, fuzzy_penalty=1)
    for keys in keys_list:
        candidates['C', keys] = [asdict(candidate) for candidate in engine.query(keys, abbreviated=True)]
    for case in cases:
        case['pools'] = {arm: candidates[arm, case['keys']] for arm in 'ABC'}
    write(path, cases)
    return cases, {'items_sha256': manifest['items_sha256'], 'pools_sha256': sha256(path),
                   'rime_dll_sha256': sha256(args.weasel / 'rime.dll'),
                   'lexicon_sha256': sha256(args.lexicon)}


def score(args, cases):
    import sentencepiece as spm
    import torch
    from .joint_score import ScoringFallback, order_pool, score_joint
    from .model import LM
    from .retrain import sha256
    from .score_v2 import outcome

    torch.set_num_threads(2)
    torch.backends.cuda.matmul.allow_tf32 = False
    sp = spm.SentencePieceProcessor(model_file=str(args.tokenizer))
    for case in cases:
        case['systems'] = {}
        for arm, pool in case['pools'].items():
            case['systems'][arm] = outcome(case, pool, list(range(len(pool))))
    identities = {}
    for name, checkpoint in zip(('100m', '1b', '200m'), args.models):
        identities[name] = sha256(checkpoint)
        saved = torch.load(checkpoint, map_location='cpu', weights_only=True, mmap=True)
        with torch.device('meta'):
            model = LM(**saved['config'])
        model.load_state_dict(saved['model'], assign=True)
        model.to(args.device).eval()
        del saved
        arms = ('B', 'C') if name == '200m' else ('C',)
        for case in cases:
            for arm in arms:
                label = ('D' if arm == 'C' else 'E') + '-' + name
                pool = case['pools'][arm]
                order = list(range(len(pool)))
                fallback = None
                if len(pool) > 1:
                    try:
                        scores, _ = score_joint(model, sp, '', [c['text'] for c in pool], args.device)
                        order = order_pool(scores, [c['consumed'] for c in pool], 1.)
                    except ScoringFallback as exc:
                        fallback = str(exc)
                case['systems'][label] = {**outcome(case, pool, order), 'fallback': fallback}
        del model
        print(f'Completed checkpoint {name}', flush=True)
    return identities


def summarize(cases):
    from .score_v2 import bootstrap

    result = {}
    datasets = sorted({case['dataset'] for case in cases})
    groups = []
    for dataset in datasets:
        chosen = [case for case in cases if case['dataset'] == dataset]
        prefix = f'{dataset}/' if len(datasets) > 1 else ''
        groups.append((prefix + 'all', chosen))
        for kind in ('full', 'abbrev', 'mixed'):
            group = [case for case in chosen if case['kind'] == kind]
            groups.append((prefix + kind, group))
            for source in sorted({case['source'] for case in group}):
                groups.append((f'{prefix}{kind}/{source}', [case for case in group if case['source'] == source]))
    for key, rows in groups:
        if not rows:
            continue
        n = len(rows)
        systems = {}
        for label in rows[0]['systems']:
            values = [row['systems'][label] for row in rows]
            hits = sum(v['top1'] for v in values)
            recall = sum(v['recalled'] for v in values)
            systems[label] = {'top1_count': hits, 'top1': hits / n,
                              'top5': sum(v['top5'] for v in values) / n,
                              'mrr': sum(v['mrr'] for v in values) / n, 'recall': recall / n,
                              'missing': n - recall, 'top1_given_recall': hits / recall if recall else None,
                              'fallbacks': dict(Counter(v['fallback'] for v in values if v.get('fallback')))}
        pairs = [('A', 'B'), ('B', 'C'), ('C', 'D-200m'), ('D-100m', 'D-200m'),
                 ('D-1b', 'D-200m'), ('B', 'E-200m')]
        comparisons = {}
        for left, right in pairs:
            differences = [r['systems'][right]['top1'] - r['systems'][left]['top1'] for r in rows]
            comparisons[f'{left}->{right}'] = {'corrections': differences.count(1),
                                              'regressions': differences.count(-1),
                                              'delta': sum(differences) / n,
                                              'source_clause_bootstrap_ci95': bootstrap(rows, left, right)}
        result[key] = {'n': n, 'original_clauses': len({r['document_sha256'] for r in rows}),
                       'systems': systems, 'comparisons': comparisons}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--models', nargs=3, type=Path, required=True, metavar=('100M-HAN', '1B-HAN', '200M-PARAM'))
    parser.add_argument('--tokenizer', type=Path, default=Path('artifacts/tokenizer.model'))
    parser.add_argument('--lexicon', type=Path, default=Path('artifacts/lexicon/lexicon.sqlite'))
    parser.add_argument('--rime-exe', type=Path, default=Path('native/build/Release/rime_pool.exe'))
    parser.add_argument('--weasel', type=Path, default=Path(r'C:\Program Files\Rime\weasel-0.17.4'))
    parser.add_argument('--device', default='cuda:0')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases, identity = freeze(args)
    print(f'Frozen {len(cases)} input scenarios', flush=True)
    identity['models'] = score(args, cases)
    from .retrain import sha256
    identity['tokenizer_sha256'] = sha256(args.tokenizer)
    summary = summarize(cases)
    write(args.output / 'predictions.jsonl', ({k: v for k, v in row.items() if k != 'pools'} for row in cases))
    report = {'identity': identity, 'protocol': {'context': 'empty', 'candidate_limit': 20,
              'score': 'joint sum', 'alpha': 1., 'precision': 'float32', 'device': args.device,
              'rime_profiles': 'isolated Luna and shared Rime Ice with standard abbreviation rules, no user learning'},
              'metrics': summary}
    (args.output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    with (args.output / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ('top1', 'top5', 'mrr', 'recall', 'missing', 'top1_given_recall')
        writer = csv.writer(stream)
        writer.writerow(('input_group', 'system', 'n', *fields))
        for group, values in summary.items():
            for label, row in values['systems'].items():
                writer.writerow((group, label, values['n'], *(row[k] for k in fields)))


if __name__ == '__main__':
    main()
