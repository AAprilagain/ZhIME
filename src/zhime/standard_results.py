"""Summarize the official and independently generated Standard IME tracks."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from .score_v2 import bootstrap
from .standard_eval import read


def metrics(rows):
    ranks = [row['rank'] for row in rows if row['rank'] is not None]
    n = len(rows)
    result = {'n': n, 'in_pool': len(ranks), 'missing': n - len(ranks),
              'recall': len(ranks) / n, 'mrr': sum(1 / r for r in ranks) / n,
              'top1_given_recall': ranks.count(1) / len(ranks) if ranks else None,
              'fallbacks': dict(Counter(row['fallback'] for row in rows if row.get('fallback')))}
    for k in (1, 3, 5):
        result[f'top{k}_correct'] = sum(rank <= k for rank in ranks)
        result[f'top{k}'] = result[f'top{k}_correct'] / n
    return result


def compare(before, after):
    old = {row['id']: row for row in before}
    new = {row['id']: row for row in after}
    if set(old) != set(new):
        raise ValueError('Runs do not cover the same questions.')
    pairs = [{'document_sha256': old[key]['dialogue'],
              'systems': {'old': {'top1': int(old[key]['rank'] == 1)},
                          'new': {'top1': int(new[key]['rank'] == 1)}}}
             for key in sorted(old)]
    gains = sum(row['systems']['new']['top1'] - row['systems']['old']['top1'] == 1 for row in pairs)
    losses = sum(row['systems']['old']['top1'] - row['systems']['new']['top1'] == 1 for row in pairs)
    return {'n': len(pairs), 'corrections': gains, 'regressions': losses,
            'difference': (gains - losses) / len(pairs),
            'dialogue_bootstrap_ci95': bootstrap(pairs, 'old', 'new')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    root = args.root
    results = root / 'results'
    overlap = json.loads((root / 'training-exact-message-overlap.json').read_text(encoding='utf-8'))
    excluded = set(overlap['old']) | set(overlap['new'])
    official = {}
    for track in ('full', 'abbrev'):
        runs = {}
        for label in ('baseline', 'old', 'new', 'old-empty', 'new-empty') if track == 'full' else ('baseline', 'old', 'new'):
            rows = read(results / f'official-{track}-{label}' / 'items.jsonl')
            if label != 'baseline':
                model, _, variant = label.partition('-')
                suffix = f'-{variant}' if variant else ''
                failures = {row['id']: row['fallback'] for row in
                            read(root / model / f'official-{track}{suffix}-scores.jsonl')}
                for row in rows:
                    row['fallback'] = failures[row['id']]
            runs[label] = rows
        official[track] = runs
    local = {}
    for model in ('old', 'new'):
        rows = read(root / model / 'generated-full-predictions.jsonl')
        for system in 'ABCDE':
            current = [{**{k: v for k, v in row.items() if k != 'systems'},
                        **row['systems'][system]} for row in rows]
            label = system if system in 'ABC' else f'{system}-{model}'
            if label in local and local[label] != current:
                raise ValueError(f'Baseline mismatch for {label}')
            local[label] = current
    tracks = {**{f'official-{k}': v for k, v in official.items()}, 'generated-full': local}
    summary = {}
    for track, runs in tracks.items():
        section = {}
        for label, rows in runs.items():
            section[label] = {'all': metrics(rows),
                              'without_exact_training_message': metrics([r for r in rows if r['id'] not in excluded]),
                              'by_source': {source: metrics([r for r in rows if r['source'] == source])
                                            for source in sorted({r['source'] for r in rows})}}
        pairs = [('baseline', 'old'), ('baseline', 'new'), ('old', 'new')] if track.startswith('official') else [
            ('A', 'B'), ('B', 'C'), ('C', 'D-old'), ('C', 'D-new'), ('D-old', 'D-new'),
            ('B', 'E-old'), ('B', 'E-new'), ('E-old', 'E-new')]
        summary[track] = {'systems': section, 'comparisons': {
            f'{a}->{b}': compare(runs[a], runs[b]) for a, b in pairs}}
    (results / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    with (results / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['track', 'system', 'subset', 'n', 'top1_pct', 'top3_pct', 'top5_pct',
                         'recall_pct', 'mrr', 'missing', 'top1_given_recall_pct', 'fallbacks'])
        for track, section in summary.items():
            for label, groups in section['systems'].items():
                for subset, m in {**groups['by_source'], 'all': groups['all'],
                                  'without_exact_training_message': groups['without_exact_training_message']}.items():
                    conditional = m['top1_given_recall']
                    writer.writerow([track, label, subset, m['n'], *(round(m[f'top{k}'] * 100, 4) for k in (1, 3, 5)),
                                     round(m['recall'] * 100, 4), round(m['mrr'], 6), m['missing'],
                                     round(conditional * 100, 4) if conditional is not None else '',
                                     sum(m['fallbacks'].values())])


if __name__ == '__main__':
    main()
