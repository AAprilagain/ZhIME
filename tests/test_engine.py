import sqlite3
from zhime.engine import Engine, variants

def engine(tmp_path):
    path=tmp_path/'lex.sqlite'; db=sqlite3.connect(path)
    db.execute('CREATE TABLE entries(pinyin TEXT,text TEXT,weight REAL)')
    db.executemany('INSERT INTO entries VALUES(?,?,?)', [('ni','你',100),('hao','好',100),('ni hao','你好',1000),('xi an','西安',100),('xian','先',200),('nan','南',100),('lan','蓝',80),('lang','郎',50),('nv','女',100)])
    db.commit(); db.close(); return Engine(path)

def test_full_and_boundary(tmp_path):
    e=engine(tmp_path)
    assert e.query('nihao')[0].text=='你好'
    assert e.query("xi'an")[0].text=='西安'
    assert e.query('xian')[0].text=='先'
    assert e.query('nü')[0].text=='女'
    assert all(c.consumed==5 for c in e.query('nihao'))

def test_fuzzy_legal_and_disabled(tmp_path):
    e=engine(tmp_path)
    assert '蓝' not in [c.text for c in e.query('nan')]
    assert '蓝' in [c.text for c in e.query('nan',('n_l',))]
    assert set(variants('nan',('n_l','an_ang'),{'nan','lan','lang'}))=={'nan','lan','lang'}

def test_partial_and_empty(tmp_path):
    e=engine(tmp_path)
    assert e.query('')==[] and e.query('123')==[]
    assert e.query('nihaox')[0].consumed==5


def test_abbreviation_and_mixed_input_keep_full_pinyin_priority(tmp_path):
    e=engine(tmp_path)
    for keys in ('nh', 'nhao', 'nih', "n'hao"):
        candidates=e.query(keys,abbreviated=True)
        assert candidates[0].text=='你好'
        assert all(c.consumed==len(keys) for c in candidates)
    assert e.query('nh')==[]
    for keys in ('nihao', 'xian', "xi'an", 'nan'):
        assert e.query(keys,abbreviated=True)==e.query(keys)


def test_single_character_tail_preserves_main_pool(tmp_path):
    path=tmp_path/'single.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE entries(pinyin TEXT,text TEXT,weight REAL)')
        db.executemany('INSERT INTO entries VALUES(?,?,?)',
                       [('yi',chr(0x4e00+i),100-i) for i in range(30)])
    e=Engine(path)
    head=e.query('yi')
    tail=e.single_character_tail('yi',(),head)
    assert len(head)==20 and len(tail)==10
    assert [c.text for c in [*head,*tail]]==[chr(0x4e00+i) for i in range(30)]
    assert e.single_character_tail('y',(),[])==[]


def test_search_weight_can_rescue_a_prefix_before_beam_pruning(tmp_path):
    path = tmp_path / 'guided.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE entries(pinyin TEXT,text TEXT,weight REAL)')
        db.executemany('INSERT INTO entries VALUES(?,?,?)',
                       [('ni','你',100),('ni','拟',60),('hao','好',80)])
    e = Engine(path, beam=1)
    assert e.query('nihao')[0].text == '你好'
    def lm_scores(texts):
        return {text: 0. if text.startswith('拟') else -10. for text in texts}
    assert e.guided_query('nihao', (), lm_scores, 1.)[0].text == '拟好'


def test_compiled_lexicon_matches_sqlite(tmp_path):
    original=engine(tmp_path)
    snapshot=tmp_path/'lexicon.marshal'
    digest=original.write_snapshot(snapshot)
    compiled=Engine.from_snapshot(snapshot,digest,original.beam,original.fuzzy_penalty)
    for keys in ('nihao','nh','xian','nv'):
        first=original.query(keys,abbreviated=True)
        second=compiled.query(keys,abbreviated=True)
        assert first==second
        assert original.single_character_tail(keys,(),first)==compiled.single_character_tail(keys,(),second)
