"""Independent pinyin lattice, fuzzy syllable matching and word beam search."""
import heapq
import hashlib
import marshal
import math
import re
import sqlite3
from itertools import count
from pathlib import Path
from dataclasses import dataclass, asdict
from functools import lru_cache

FUZZY = {'z_zh':('z','zh','initial'),'c_ch':('c','ch','initial'),'s_sh':('s','sh','initial'),
         'n_l':('n','l','initial'),'an_ang':('an','ang','final'),'en_eng':('en','eng','final')}
INITIALS = ('zh','ch','sh','b','p','m','f','d','t','n','l','g','k','h','j','q','x','r','z','c','s','y','w')

def variants(syllable, enabled, legal):
    """Return legal readings reachable by enabled initial/final equivalences."""
    initial=next((x for x in INITIALS if syllable.startswith(x)), '')
    final=syllable[len(initial):]
    initials={initial:0}; finals={final:0}
    for name in enabled:
        if name not in FUZZY: raise ValueError('unknown fuzzy rule: '+name)
        a,b,part=FUZZY[name]; value=initial if part=='initial' else final
        if value in (a,b): (initials if part=='initial' else finals)[b if value==a else a]=1
    return {i+f:ci+cf for i,ci in initials.items() for f,cf in finals.items() if i+f in legal}

@dataclass(frozen=True)
class Candidate:
    text: str
    consumed: int
    score: float
    fuzzy_edits: int

class Engine:
    @classmethod
    def from_snapshot(cls, path, expected_sha256, beam, fuzzy_penalty):
        data=Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest()!=expected_sha256:
            raise ValueError('Compiled lexicon hash mismatch')
        engine=cls.__new__(cls)
        engine.root,engine.syllables,engine.single_characters,engine.abbreviations=marshal.loads(data)
        engine.beam=beam
        engine.fuzzy_penalty=fuzzy_penalty
        engine.max_syllable=max(map(len,engine.syllables))
        return engine

    def write_snapshot(self, path):
        data=marshal.dumps((self.root,self.syllables,self.single_characters,self.abbreviations))
        Path(path).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def __init__(self, lexicon, beam=64, fuzzy_penalty=3.0):
        self.root={}; self.syllables=set(); self.beam=beam; self.fuzzy_penalty=fuzzy_penalty
        self.single_characters={}
        db=sqlite3.connect(f'file:{lexicon}?mode=ro',uri=True)
        db.execute('PRAGMA temp_store=MEMORY')
        total=db.execute('SELECT SUM(weight) FROM entries').fetchone()[0]
        for py,text,w in db.execute('SELECT pinyin,text,weight FROM entries ORDER BY weight DESC,text'):
            node=self.root
            prior=math.log(w/total)
            if len(text)==1:
                self.single_characters.setdefault(py,[]).append((text,prior))
            for s in py.split():
                self.syllables.add(s)
                node=node.setdefault(s,{})
                node.setdefault(None,prior)  # Best terminal prior in this subtree.
            words=node.setdefault('',[])
            if len(words)<20: words.append((text,prior))
        db.close()
        self.max_syllable=max(map(len,self.syllables))
        self.abbreviations={}
        for syllable in sorted(self.syllables):
            for width in (1, 2) if syllable.startswith(('zh','ch','sh')) else (1,):
                if len(syllable)>width:
                    self.abbreviations.setdefault(syllable[:width],[]).append(syllable)

    @lru_cache(maxsize=1024)
    def query(self, raw, fuzzy=(), limit=20, abbreviated=False):
        raw=raw.lower().replace('ü','v').replace(' ','\'')
        if len(raw)>256 or not re.fullmatch("[a-z']*",raw): return []
        if not raw: return []
        fuzzy=tuple(sorted(fuzzy))
        exact=self._search(raw,fuzzy,limit,False)
        if not abbreviated or (exact and exact[0].consumed==len(raw)):
            return exact
        mixed=self._search(raw,fuzzy,limit,True)
        if mixed and (not exact or mixed[0].consumed>exact[0].consumed):
            return mixed
        return exact

    @lru_cache(maxsize=64)
    def _readings(self,fuzzy):
        reverse={}
        for s in self.syllables:
            for v,cost in variants(s,fuzzy,self.syllables).items():
                reverse.setdefault(v,[]).append((s,cost))
                if v in ('nve','lve'): reverse.setdefault(v[0]+'ue',[]).append((s,cost))
        return reverse

    @lru_cache(maxsize=4096)
    def _syllable_edges(self, fragment, fuzzy, abbreviated):
        # Relative offsets let successive edits reuse untouched lattice windows.
        start=int(fragment.startswith("'"))
        reverse=self._readings(fuzzy)
        result=[]
        for end in range(start+1,min(len(fragment),start+self.max_syllable)+1):
            key=fragment[start:end]
            if "'" in key: break
            result.extend((end,syllable,cost,0) for syllable,cost in reverse.get(key,()))
            if abbreviated:
                result.extend((end,syllable,0,1) for syllable in self.abbreviations.get(key,()))
        return tuple(result)

    def single_character_tail(self, raw, fuzzy, existing):
        """Expose every stored single character for one complete syllable."""
        key=raw.lower().replace('ü','v')
        seen={c.text for c in existing if c.consumed==len(key)}
        matches=[]
        for syllable,edits in self._readings(tuple(sorted(fuzzy))).get(key,()):
            for text,prior in self.single_characters.get(syllable,()):
                matches.append(Candidate(text,len(key),prior-self.fuzzy_penalty*edits,edits))
        result=[]
        for candidate in sorted(matches,key=lambda c:(c.fuzzy_edits,-c.score,c.text)):
            if candidate.text not in seen:
                seen.add(candidate.text)
                result.append(candidate)
        return result

    def guided_query(self, raw, fuzzy, lm_scores, weight, limit=20):
        raw=raw.lower().replace('ü','v').replace(' ','\'')
        if not raw or len(raw)>256 or not re.fullmatch("[a-z']*",raw):
            return []
        fuzzy=tuple(sorted(fuzzy))
        exact=self._search(raw,fuzzy,limit,False,lm_scores,weight)
        if exact and exact[0].consumed==len(raw):
            return exact
        mixed=self._search(raw,fuzzy,limit,True,lm_scores,weight)
        return mixed if mixed and (not exact or mixed[0].consumed>exact[0].consumed) else exact

    def _search(self,raw,fuzzy,limit,abbreviated,lm_scores=None,weight=0.):
        n=len(raw)

        @lru_cache(None)
        def syllable_edges(pos):
            fragment=raw[pos:pos+self.max_syllable+(raw[pos:pos+1]=="'")]
            return tuple((pos+end,syllable,cost,short) for end,syllable,cost,short in
                         self._syllable_edges(fragment,fuzzy,abbreviated))
        @lru_cache(None)
        def words_at(pos):
            found=[]
            def walk(node,start,edits,depth):
                if depth>16: return
                if '' in node:
                    for text,prior in node['']: found.append((start,text,prior-self.fuzzy_penalty*edits,edits))
                for end,s,cost,_ in syllable_edges(start):
                    if s in node: walk(node[s],end,edits+cost,depth+1)
            if not abbreviated:
                walk(self.root,pos,0,0)
                return found
            # Bound the trie search for ambiguous initials. The best descendant's
            # prior guides expansion, with a small preference for longer matches.
            serial=count()
            queue=[(0,next(serial),self.root,pos,0,0.,0)]
            for _ in range(128):
                if not queue: break
                _,_,node,start,edits,penalty,depth=heapq.heappop(queue)
                for text,prior in node.get('',()):
                    found.append((start,text,prior-penalty,edits))
                if depth==16: continue
                for end,s,cost,short in syllable_edges(start):
                    child=node.get(s)
                    if child is None: continue
                    next_penalty=penalty+self.fuzzy_penalty*cost+2.*short
                    priority=next_penalty-child[None]-.75*(end-pos)
                    heapq.heappush(queue,(priority,next(serial),child,end,edits+cost,next_penalty,depth+1))
            by_end={}
            for end,text,prior,edits in found:
                words=by_end.setdefault(end,{})
                previous=words.get(text)
                if previous is None or prior>previous[0]:
                    words[text]=(prior,edits)
            return [(end,text,prior,edits) for end,words in by_end.items()
                    for text,(prior,edits) in heapq.nlargest(24,words.items(),key=lambda item:(item[1][0],item[0]))]
        beam=min(self.beam,32) if abbreviated or lm_scores else self.beam
        retained=beam*2 if lm_scores else beam

        def select(bucket, size):
            proposals=heapq.nlargest(retained if lm_scores else size,bucket.items(),
                                     key=lambda x:(x[1][0],x[0]))
            if lm_scores:
                values=lm_scores([text for text,_ in proposals])
                proposals.sort(key=lambda x:-(x[1][0]+weight*values[x[0]]))
            return proposals[:size]

        beams=[{} for _ in range(n+1)]; beams[0]['']=(0.0,0)
        for pos in range(n):
            if not beams[pos]: continue
            active=select(beams[pos],beam) if pos else list(beams[0].items())
            for end,word,prior,edits in words_at(pos):
                bucket=beams[end]
                for prefix,(score,old_edits) in active:
                    text=prefix+word
                    score+=prior
                    old=bucket.get(text)
                    if old is None or score>old[0]:
                        bucket[text]=(score,old_edits+edits)
                if len(bucket)>beam*8:
                    ranked=sorted(bucket.items(),key=lambda x:(x[1][0],x[0]),reverse=True)
                    beams[end]=dict(ranked[:retained])
        positions=[n] if beams[n] else sorted((i for i in range(1,n) if beams[i]),reverse=True)[:1]
        result=[]
        for pos in positions:
            ranked=(select(beams[pos],limit) if lm_scores else
                    sorted(beams[pos].items(),key=lambda x:(-x[1][0],x[0]))[:limit])
            for text,(score,edits) in ranked:
                result.append(Candidate(text,pos,score,edits))
        return result

    def dictionaries(self, raw, fuzzy=()): return [asdict(c) for c in self.query(raw,tuple(fuzzy))]
