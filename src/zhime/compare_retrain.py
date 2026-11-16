"""Compare two frozen-pool test runs by matching cases and resampling documents."""
import argparse
import json
from itertools import zip_longest
from pathlib import Path

from .score_v2 import bootstrap


def compare(before, after):
    reports = [json.loads((root / 'test-results-v2.json').read_text(encoding='utf-8'))
               for root in (before, after)]
    for field in ('frozen_input_sha256', 'tokenizer_sha256', 'scorer_version'):
        if reports[0]['identity'][field] != reports[1]['identity'][field]:
            raise ValueError(f'Test runs disagree on {field}.')
    conditions = {}
    controls = ('condition', 'rule', 'keys', 'target', 'case_id', 'document_sha256')
    with ((before / 'test-predictions.jsonl').open(encoding='utf-8') as old_stream,
          (after / 'test-predictions.jsonl').open(encoding='utf-8') as new_stream):
        for old_line, new_line in zip_longest(old_stream, new_stream):
            if old_line is None or new_line is None:
                raise ValueError('The test runs have different row counts.')
            old, new = json.loads(old_line), json.loads(new_line)
            if any(old[field] != new[field] for field in controls):
                raise ValueError(f"Test case mismatch at {old['case_id']}.")
            if any(old['systems'][arm] != new['systems'][arm] for arm in 'ABC'):
                raise ValueError(f"Candidate baseline mismatch at {old['case_id']}.")
            conditions.setdefault(old['condition'], []).append({
                'document_sha256': old['document_sha256'],
                'systems': {'old': old['systems']['D'], 'new': new['systems']['D']},
            })
    if any(sum(map(len, conditions.values())) != report['cases'] for report in reports):
        raise ValueError('Prediction count does not match the test reports.')
    summary = {}
    for condition, rows in conditions.items():
        paired = [(row['systems']['old']['top1'], row['systems']['new']['top1']) for row in rows]
        fixes = sum(old == 0 and new == 1 for old, new in paired)
        regressions = sum(old == 1 and new == 0 for old, new in paired)
        summary[condition] = {
            'cases': len(rows), 'old_top1': sum(old for old, _ in paired) / len(rows),
            'new_top1': sum(new for _, new in paired) / len(rows),
            'difference': (fixes - regressions) / len(rows),
            'document_bootstrap_difference_ci95': bootstrap(rows, 'old', 'new'),
            'corrections': fixes, 'regressions': regressions,
            'old_and_new_top1_pure_joint': [
                report['metrics'][condition]['systems']['D_joint_pure']['top1'] for report in reports
            ],
        }
    return {'identities': [report['identity'] for report in reports],
            'settings': [report['settings']['C'] for report in reports],
            'conditions': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.before, args.after)
    partial = args.output.with_suffix('.partial')
    partial.write_text(json.dumps(result, indent=2), encoding='utf-8')
    partial.replace(args.output)


if __name__ == '__main__':
    main()
