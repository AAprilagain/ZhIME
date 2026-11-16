import hashlib
import json
import struct

import pyarrow as pa
import pyarrow.parquet as pq

from zhime.data import sample


def test_expanded_corpus_excludes_shards_documents_and_exact_clauses(tmp_path):
    source = tmp_path / 'export' / 'train'
    source.mkdir(parents=True)
    holdout = tmp_path / 'holdout.jsonl'
    holdout.write_text(json.dumps({
        'shard': 'reserved',
        'document_sha256': hashlib.sha256('完全相同文档'.encode()).hexdigest(),
        'target': '排除词句',
    }), encoding='utf-8')

    def parquet(name, texts):
        values = [text.encode() for text in texts]
        offsets = [0]
        for value in values:
            offsets.append(offsets[-1] + len(value))
        array = pa.Array.from_buffers(pa.string(), len(values), [
            None, pa.py_buffer(struct.pack(f'<{len(offsets)}i', *offsets)),
            pa.py_buffer(b''.join(values)),
        ])
        pq.write_table(pa.Table.from_arrays([array], names=['text']), source / name)

    parquet('web-reserved-000.parquet', ['不能使用的评测分片' * 50])
    parquet('web-reserved-001.parquet', ['同一评测分片的另一文件' * 50])
    parquet('web-training-000.parquet', ['完全相同文檔', '標題：排除詞句。後續內容'])
    parquet('web-training-001.parquet', ['普通网页用来测试独立抽样' * 10])
    parquet('review-training-000.parquet', ['普通评论可以采样'])
    parquet('chat-training-000.parquet', ['普通对话可以采样'])
    out = tmp_path / 'corpus'
    sample(source.parent, out, 50, 0, heldout=holdout, screen_workers=2)
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['excluded_shards'] == ['reserved']
    assert manifest['sources']['web']['files'] == ['web-training-000.parquet', 'web-training-001.parquet']
    assert manifest['sources']['web']['heldout_matches_rejected'] == 2
    text = (out / 'train.txt').read_text(encoding='utf-8')
    assert '普通网页' in text
    assert all(word not in text for word in ('完全相同', '排除詞句', '評測分片', '评测分片'))
