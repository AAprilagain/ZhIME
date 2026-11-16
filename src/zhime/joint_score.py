"""Joint-tokenized fixed-pool suffix likelihood, adapted from VimeML.

Reference: Voltline/VimeML commit 1357e179a0393af954fe3d415ea2a12e14042264,
src/vimeml/benchmarks/zh13_lm_scores.py. GPL-3.0-or-later; upstream notices
are retained in third_party/VimeML and THIRD_PARTY.md. Adapted to this LM API.
"""
import math
import unicodedata
import torch

class ScoringFallback(ValueError): pass

def normalize_context(text):
    """Match SentencePiece's NFKC/whitespace domain before strict roundtrip checks.

    Does not substitute unknown characters or silently truncate the model window.
    Candidate output strings are left untouched.
    """
    return ' '.join(unicodedata.normalize('NFKC',text).split())

def common_prefix(rows):
    for i in range(min(map(len,rows))):
        if any(row[i]!=rows[0][i] for row in rows): return i
    return min(map(len,rows))

@torch.inference_mode()
def score_joint(model, sp, context, candidates, device='cpu'):
    if not candidates: return [], {'fallback':None, 'common_prefix':0}
    if len(set(candidates))!=len(candidates) or any(not c for c in candidates): raise ScoringFallback('invalid_pool')
    raw_context=context;context=normalize_context(context)
    context_ids=[sp.bos_id()]+sp.encode(context,out_type=int)
    rows=[]
    for c in candidates:
        ids=sp.encode(context+c,out_type=int)
        if sp.decode(ids)!=context+c: raise ScoringFallback('roundtrip')
        if not 1<=len(ids)<=model.config['context']: raise ScoringFallback('overflow')
        rows.append([sp.bos_id()]+ids)
    prefix=common_prefix([context_ids]+rows)
    if prefix<1 or any(len(row)<=prefix for row in rows): raise ScoringFallback('no_suffix')
    width=max(len(row)-1 for row in rows)
    if torch.device(device).type == 'cuda' and hasattr(model, 'encode') and hasattr(model, 'project'):
        padded = [row[:-1] + [0] * (width - len(row) + 1) for row in rows]
        positions = [(i, j - 1, row[j]) for i, row in enumerate(rows) for j in range(prefix, len(row))]
        indices, columns, targets = torch.tensor(positions, device=device).unbind(1)
        hidden = model.encode(torch.tensor(padded, device=device))
        logits = model.project(hidden[indices, columns]).float()
        values = logits.gather(1, targets[:, None]).squeeze(1) - logits.logsumexp(1)
        counts = [len(row) - prefix for row in rows]
        scores = [sum(part) for part in values.cpu().split(counts)]
        scores = [score.item() for score in scores]
        if not all(math.isfinite(x) for x in scores): raise ScoringFallback('nonfinite')
        return list(zip(scores, counts)), {'fallback':None,'context_normalized':context!=raw_context,'common_prefix':prefix,'boundary_retokenized':any(row[:len(context_ids)]!=context_ids for row in rows)}
    ids=torch.zeros((len(rows),width),dtype=torch.long,device=device)
    target=ids.clone(); mask=torch.zeros_like(ids,dtype=torch.bool)
    for i,row in enumerate(rows):
        ids[i,:len(row)-1]=torch.tensor(row[:-1],device=device)
        target[i,:len(row)-1]=torch.tensor(row[1:],device=device)
        mask[i,prefix-1:len(row)-1]=True
    logits,_=model(ids)
    values=logits.float().log_softmax(-1).gather(-1,target.unsqueeze(-1)).squeeze(-1)
    scores=values.masked_fill(~mask,0).sum(-1).cpu().tolist()
    if not all(math.isfinite(x) for x in scores): raise ScoringFallback('nonfinite')
    return list(zip(scores,mask.sum(-1).cpu().tolist())), {'fallback':None,'context_normalized':context!=raw_context,'common_prefix':prefix,'boundary_retokenized':any(row[:len(context_ids)]!=context_ids for row in rows)}

def order_pool(scores, coverage, alpha=.5):
    """Stable order within equal input coverage; no candidate-length normalization."""
    order=list(range(len(scores)))
    for span in sorted(set(coverage)):
        indexes=[i for i,s in enumerate(coverage) if s==span]
        values=[scores[i][0] for i in indexes]; lo,hi=min(values),max(values)
        def value(i):
            lm=(scores[i][0]-lo)/(hi-lo) if hi>lo else 0
            prior=1-indexes.index(i)/max(1,len(indexes)-1)
            return alpha*lm+(1-alpha)*prior
        ranked=sorted(indexes,key=lambda i:(-value(i),i))
        for slot,index in zip(indexes,ranked): order[slot]=index
    return order

def order_weighted_pool(scores, coverage, gen_scores, lm_weight):
    """Rerank equal-coverage slots by the raw sum of the two log scores."""
    if not math.isfinite(lm_weight) or lm_weight < 0:
        raise ValueError('lm_weight must be finite and nonnegative')
    order = list(range(len(scores)))
    for span in sorted(set(coverage)):
        slots = [i for i, end in enumerate(coverage) if end == span]
        ranked = sorted(slots, key=lambda i: -(gen_scores[i] + lm_weight * scores[i][0]))
        for slot, index in zip(slots, ranked):
            order[slot] = index
    return order


def score_separate_strict(model,sp,context,candidates,device='cpu'):
    """Separate-boundary diagnostic with the same whole-case failure policy."""
    from .rerank import score_candidates
    if not candidates:return [],{}
    if len(set(candidates))!=len(candidates) or any(not c for c in candidates):raise ScoringFallback('invalid_pool')
    context=normalize_context(context)
    prefix=sp.encode(context,out_type=int)
    for c in candidates:
        if sp.decode(sp.encode(context+c,out_type=int))!=context+c:raise ScoringFallback('roundtrip')
        if len(prefix)+len(sp.encode(c,out_type=int))>model.config['context']:raise ScoringFallback('overflow')
    return score_candidates(model,sp,context,candidates,device),{}
