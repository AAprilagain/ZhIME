import pytest
import torch
from zhime.model import LM
from zhime.joint_score import score_joint, ScoringFallback, order_pool, normalize_context, order_weighted_pool

class SP:
    def bos_id(self): return 1
    def encode(self,s,out_type=int): return [ord(c)-ord('a')+2 for c in s]
    def decode(self,ids): return ''.join(chr(i-2+ord('a')) for i in ids)

def test_joint_manual_and_fallback():
    torch.manual_seed(4); model=LM(vocab=32,width=16,layers=1,heads=4,context=8).eval(); sp=SP()
    scores,meta=score_joint(model,sp,'ab',['cd','ef'])
    logits,_=model(torch.tensor([[1,2,3,4]]))
    lp=logits.log_softmax(-1)
    assert abs(scores[0][0]-(lp[0,2,4]+lp[0,3,5]).item())<1e-5
    with pytest.raises(ScoringFallback): score_joint(model,sp,'abcdefgh',['a'])
    assert order_pool([(-9,1),(-1,1),(-2,1)],[5,2,5],1)==[2,1,0]

def test_context_normalization_preserves_unknowns_and_does_not_truncate():
    assert normalize_context('  你好，\n 世界？  ')== '你好, 世界?'
    assert normalize_context('甲'*200)=='甲'*200
    assert normalize_context('𠀀')=='𠀀'
def test_weighted_scores_keep_coverage_slots_and_stable_ties():
    scores = [(-5., 1), (-20., 1), (-1., 1)]
    coverage = [4, 2, 4]
    prior = [-1., -1., -5.]
    assert order_weighted_pool(scores, coverage, prior, 0.) == [0, 1, 2]
    assert order_weighted_pool(scores, coverage, prior, 1.) == [0, 1, 2]
    assert order_weighted_pool(scores, coverage, prior, 2.) == [2, 1, 0]
