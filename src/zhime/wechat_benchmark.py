"""Build a pinyin benchmark from user-selected, plaintext chat messages."""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import random
import re

import jieba
from pypinyin import Style, lazy_pinyin


HAN = re.compile(r'[\u3400-\u9fff]+')
CLAUSE_END = re.compile(r'[，。！？；、：,.!?;:\r\n]+')
CONTACT = re.compile(r'https?://|www\.|[@＠]|\d{5,}', re.IGNORECASE)


def read_messages(path, field):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        if path.suffix.lower() == '.txt':
            yield from (line.strip() for line in stream)
        elif path.suffix.lower() == '.csv':
            reader = csv.DictReader(stream)
            if field not in (reader.fieldnames or ()):
                raise ValueError(f'CSV is missing the text column {field!r}.')
            yield from (row[field] or '' for row in reader)
        elif path.suffix.lower() == '.jsonl':
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get(field), str):
                    raise ValueError(f'Line {number} requires a string field {field!r}.')
                yield row[field]
        else:
            raise ValueError('Use a UTF-8 TXT, CSV, or JSONL file.')


def variants(full, initials, rng):
    # Sample masks over words, so an entire word uses either full or abbreviated input.
    eligible = [i for i, (word, short) in enumerate(zip(full, initials)) if word != short]
    all_words = (1 << len(eligible)) - 1
    masks = rng.sample(range(1, all_words), min(32, max(0, all_words - 1)))
    seen = {''.join(full)}
    for mask in [all_words, *masks]:
        words = list(full)
        for bit, pos in enumerate(eligible):
            if mask & (1 << bit):
                words[pos] = initials[pos]
        keys = ''.join(words)
        if keys not in seen:
            seen.add(keys)
            yield {'keys': keys, 'word_keys': words,
                   'kind': 'abbrev' if mask == all_words else 'mixed'}
            if len(seen) == 6:
                return


def make_item(text, syllables, tokenizer, rng):
    words = list(tokenizer.cut(text, HMM=False))
    full, initials, pos = [], [], 0
    for word in words:
        reading = syllables[pos:pos + len(word)]
        full.append(''.join(reading))
        initials.append(''.join(s[0] for s in reading))
        pos += len(word)
    return {'target': text, 'words': words, 'syllables': syllables,
            'keys_full': ''.join(syllables), 'abbreviations': list(variants(full, initials, rng))}


def build(messages, count, seed, minimum, maximum, readings=None, balance=False):
    rng = random.Random(seed)
    tokenizer = jieba.Tokenizer()
    readings = readings or {}
    counters = Counter()
    unique = set()
    quotas = {'short_line': (count + 1) // 2, 'long_line': count // 2} if balance else {'all': count}
    selected = {group: [] for group in quotas}
    eligible_counts = Counter()
    for message in messages:
        if not message.strip():
            continue
        counters['messages'] += 1
        if CONTACT.search(message):
            counters['skipped_contact_like'] += 1
            continue
        eligible = []
        for segment in CLAUSE_END.split(message):
            text = segment.strip()
            if minimum <= len(text) <= maximum and HAN.fullmatch(text) and text not in unique:
                eligible.append(text)
        if not eligible:
            counters['no_eligible_clause'] += 1
            continue
        text = rng.choice(eligible)
        unique.add(text)
        # Convert the whole phrase before splitting readings by word boundaries.
        syllables = readings.get(text)
        if syllables is None:
            syllables = lazy_pinyin(text, style=Style.NORMAL, errors='default')
        else:
            counters['manual_reading_used'] += 1
        if len(syllables) != len(text) or any(not re.fullmatch('[a-z]+', s) for s in syllables):
            counters['invalid_reading'] += 1
            continue
        row = make_item(text, syllables, tokenizer, rng)
        if not row['abbreviations']:
            counters['no_abbreviation'] += 1
            continue
        source = 'short_line' if len(message.strip()) <= maximum else 'long_line'
        row['source_group'] = source
        counters['eligible'] += 1
        group = source if balance else 'all'
        eligible_counts[group] += 1
        bucket = selected[group]
        if len(bucket) < quotas[group]:
            bucket.append(row)
        else:
            index = rng.randrange(eligible_counts[group])
            if index < quotas[group]:
                bucket[index] = row
    rows = [row for bucket in selected.values() for row in bucket]
    if balance:
        rng.shuffle(rows)
    for index, row in enumerate(rows, 1):
        row['id'] = f'wechat-{index:04d}'
    return rows, {**counters, 'eligible_by_sampling_group': dict(eligible_counts)}


def render(rows):
    blocks = []
    for index, row in enumerate(rows, 1):
        lines = [f"[{index}] {row['target']}",
                 '分词: ' + ' / '.join(row['words']),
                 '全拼: ' + ' '.join(row['syllables']),
                 '全拼输入: ' + row['keys_full']]
        for n, variant in enumerate(row['abbreviations'], 1):
            lines.append(f"简拼{n}: {variant['keys']}  （词边界: {' / '.join(variant['word_keys'])}）")
        blocks.append('\n'.join(lines))
    return '\n\n'.join(blocks) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--text-field', default='text')
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--seed', type=int, default=20261115)
    parser.add_argument('--min-length', type=int, default=5)
    parser.add_argument('--max-length', type=int, default=16)
    parser.add_argument('--balance-source-length', action='store_true',
                        help='allocate half the samples to source lines longer than --max-length')
    parser.add_argument('--readings', type=Path, help='JSON mapping a target clause to corrected pinyin syllables')
    args = parser.parse_args()
    if args.count < 1 or not 2 <= args.min_length <= args.max_length <= 32:
        parser.error('Require a positive sample count and length limits in 2–32.')
    readings = {}
    if args.readings:
        readings = json.loads(args.readings.read_text(encoding='utf-8-sig'))
        if not isinstance(readings, dict) or any(
            not HAN.fullmatch(text) or not isinstance(syllables, list) or len(text) != len(syllables)
            or any(not isinstance(s, str) or not re.fullmatch('[a-z]+', s) for s in syllables)
            for text, syllables in readings.items()
        ):
            raise ValueError('Each corrected clause needs one lowercase pinyin syllable per character.')
    rows, counters = build(read_messages(args.input, args.text_field), args.count, args.seed,
                           args.min_length, args.max_length, readings, args.balance_source_length)
    if not rows:
        raise ValueError('No eligible Chinese clauses were found in the selected messages.')
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'items.txt').write_text(render(rows), encoding='utf-8')
    with (args.output / 'items.jsonl').open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    counts = {'samples': len(rows), 'full_inputs': len(rows),
              'abbreviated_inputs': sum(len(row['abbreviations']) for row in rows),
              'variant_counts': dict(Counter(v['kind'] for row in rows for v in row['abbreviations'])),
              'source_counts': dict(Counter(row['source_group'] for row in rows))}
    manifest = {**counts, 'seed': args.seed, 'length_range': [args.min_length, args.max_length],
                'selection': 'up to one unique clause per message, then seeded reservoir sampling',
                'balanced_by_source_line_length': args.balance_source_length,
                'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
                'protocol': 'Real selected chat text with synthetic key sequences.',
                'items_sha256': hashlib.sha256((args.output / 'items.jsonl').read_bytes()).hexdigest(),
                'input_statistics': counters}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
