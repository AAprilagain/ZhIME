from zhime.freeze_v2 import scenarios
from zhime.score_v2 import outcome
from zhime.benchmark_v2 import simplify,CONVERTER

def test_pairing_preserves_clean_and_legal_variants():
    item={'keys':'nan','syllables':['nan']}
    rows=list(scenarios(item,{'nan','lan','nang','lang'}))
    assert rows[:2]==[('clean_off',0,'nan','none'),('clean_on',63,'nan','all')]
    assert ('fuzzy_on',8,'lan','n_l') in rows
    assert ('fuzzy_off',0,'lan','n_l') in rows
    assert all(keys in {'nan','lan','nang'} for _,_,keys,_ in rows)

def test_partial_candidate_cannot_count_as_full_conversion():
    item={'target':'你好','keys':'nihao'}
    pool=[{'text':'你好','consumed':2},{'text':'你好','consumed':5}]
    score=outcome(item,pool,[0,1])
    assert score['top1']==0 and score['top5']==1 and score['mrr']==.5
    assert outcome(item,[],[])['recalled']==0

def test_normalization_equivalence_for_ambiguous_characters():
    for text in ['乾坤乾燥','著作著急','謝念聯合入村所見','頭髮發展','北京大学']:
        assert simplify(text)==CONVERTER.convert(text)
