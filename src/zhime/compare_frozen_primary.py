"""Validate full coverage and compare two rerankers on each frozen input condition."""
import argparse
import json
from pathlib import Path

from .score_v2 import bootstrap


def read(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--parts', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    joined = {}
    for part in args.parts:
        for row in read(part):
            key = row['case_id']
            if key in joined:
                raise ValueError(f'Duplicate case {key}')
            joined[key] = row
    groups = {}
    for row in read(args.before):
        new = joined.pop(row['case_id'])
        if any(row[key] != new[key] for key in ('condition', 'document_sha256')):
            raise ValueError('Case identity mismatch')
        old = row['systems']['D']
        if old['recalled'] != new['D']['recalled']:
            raise ValueError('Frozen candidate recall changed')
        groups.setdefault(row['condition'], []).append({
            'document_sha256': row['document_sha256'],
            'systems': {'old': old, 'new': new['D']},
            'fallback': new['fallback'],
        })
    if joined:
        raise ValueError('Unexpected cases in new predictions')
    report = {}
    for condition, rows in groups.items():
        metrics = {}
        for version in ('old', 'new'):
            results = [r['systems'][version] for r in rows]
            metrics[version] = {key: sum(r[key] for r in results) / len(rows)
                                for key in ('top1', 'top5', 'mrr', 'recalled')}
        differences = [r['systems']['new']['top1'] - r['systems']['old']['top1'] for r in rows]
        report[condition] = {'n': len(rows), **metrics, 'corrections': differences.count(1),
                             'regressions': differences.count(-1),
                             'top1_delta_ci95': bootstrap(rows, 'old', 'new'),
                             'new_fallbacks': sum(bool(r['fallback']) for r in rows)}
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
