import torch
from zhime.model import LM
from zhime.rerank import candidate_logprob, score_candidates

class Tokenizer:
    def bos_id(self): return 1
    def encode(self, s, out_type=int): return [2 + (ord(c) % 20) for c in s]

def test_batched_scoring_matches_single():
    torch.manual_seed(1)
    model = LM(vocab=32, width=16, layers=1, heads=4, context=16).eval()
    tokenizer = Tokenizer(); candidates = ['你好', '你', '世界']
    actual = score_candidates(model, tokenizer, '中文输入', candidates)
    expected = [candidate_logprob(model, tokenizer, '中文输入', c) for c in candidates]
    for (a, n), (b, m) in zip(actual, expected):
        assert n == m and abs(a-b) < 1e-4
