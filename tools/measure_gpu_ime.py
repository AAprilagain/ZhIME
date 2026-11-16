"""Measure warmed HTTP round trip times through the native IME backend."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import time
from urllib.parse import urlencode
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18770)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=30)
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'
    with urlopen(base + '/health', timeout=3) as response:
        health = json.load(response)
    text = '我们今天讨论中文输入法的候选排序方法还有系统实验'
    workloads = [
        ('dictionary', 'query', 'jintianwomenqubeijing', '', 0),
        ('short_rerank', 'query', 'nihao', '', 1),
        ('phrase_rerank', 'query', 'jintianwomenqubeijing', (text * 2)[:32], 1),
        ('long_context_rerank', 'query', 'jintianwomenqubeijing', (text * 5)[:96], 1),
        ('short_suggestion', 'suggest', '', '今天晚上', 0),
        ('long_context_suggestion', 'suggest', '', (text * 5)[:96], 0),
    ]
    results = []
    for name, endpoint, keys, context, rank in workloads:
        times, statuses, nonempty = [], Counter(), 0
        for index in range(args.repeat + 3):
            params = urlencode({'keys': keys, 'context': context.encode().hex(), 'rank': rank,
                                'format': 'json', 'id': index, 'session': 'performance-test'})
            start = time.perf_counter()
            with urlopen(f'{base}/{endpoint}?{params}', timeout=5) as response:
                payload = json.load(response)
            elapsed = (time.perf_counter() - start) * 1000
            if index >= 3:
                times.append(elapsed)
                statuses[payload['status']] += 1
                nonempty += bool(payload.get('suggestions', payload.get('candidates')))
        times.sort()
        results.append({'scenario': name, 'p50_ms': statistics.median(times),
                        'p95_ms': times[math.ceil(.95 * len(times)) - 1],
                        'max_ms': times[-1], 'statuses': dict(statuses), 'nonempty': nonempty})
    result = {'health': health, 'repeat': args.repeat, 'warmup_excluded_per_scenario': 3,
              'scope': 'Warm loopback HTTP round trips; excludes keystroke and paint.', 'results': results}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
