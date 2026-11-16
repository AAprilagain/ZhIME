from zhime.rerank import ranked_indices

def test_prior_and_model():
    scores = [(-10, 1), (-2, 1), (-5, 1)]
    assert ranked_indices(scores, alpha=0) == [0, 1, 2]
    assert ranked_indices(scores, alpha=1) == [1, 2, 0]

def test_length_and_ties():
    scores = [(-4, 2), (-3, 1)]
    assert ranked_indices(scores, alpha=1, length_exponent=1)[0] == 0
    assert ranked_indices(scores, alpha=1, length_exponent=0)[0] == 1
    assert ranked_indices([(-1, 1), (-1, 1)], alpha=1) == [0, 1]

def test_length_group_preserves_other_positions():
    scores = [(-3, 1), (-1, 1), (-2, 1)]
    assert ranked_indices(scores, 1, 0, ['北京', '北', '背景'], True) == [2, 1, 0]
