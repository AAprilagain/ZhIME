"""Score candidates with an explicitly tokenized left context."""
import math
import torch
import torch.nn.functional as F

@torch.inference_mode()
def candidate_logprob(model, tokenizer, context, candidate, device='cpu'):
    prefix = [tokenizer.bos_id()] + tokenizer.encode(context, out_type=int)
    suffix = tokenizer.encode(candidate, out_type=int)
    if not suffix:
        return float('-inf'), 0
    # Long candidates are scored in chunks; each prediction sees at most context-1 predecessors.
    total = 0.0
    sequence = prefix + suffix
    start = len(prefix)
    for i in range(start, len(sequence)):
        left = max(0, i - model.config['context'])
        ids = torch.tensor([sequence[left:i]], dtype=torch.long, device=device)
        logits, _ = model(ids)
        total += F.log_softmax(logits[0, -1], dim=-1)[sequence[i]].item()
    return total, len(suffix)

@torch.inference_mode()
def score_candidates(model, tokenizer, context, candidates, device='cpu'):
    prefix = [tokenizer.bos_id()] + tokenizer.encode(context, out_type=int)
    suffixes = [tokenizer.encode(c, out_type=int) for c in candidates]
    limit = model.config['context']
    if any(len(s) >= limit for s in suffixes):
        return [candidate_logprob(model, tokenizer, context, c, device) for c in candidates]
    sequences, starts = [], []
    for suffix in suffixes:
        before = prefix[-max(1, limit - len(suffix) + 1):]
        sequences.append(before + suffix)
        starts.append(len(before))
    length = max(len(s) for s in sequences)
    ids = torch.zeros((len(sequences), length - 1), dtype=torch.long, device=device)
    for i, seq in enumerate(sequences):
        ids[i, :len(seq)-1] = torch.tensor(seq[:-1], device=device)
    logits, _ = model(ids)
    logprobs = F.log_softmax(logits, dim=-1)
    targets=torch.zeros_like(ids)
    mask=torch.zeros_like(ids,dtype=torch.bool)
    for i, (seq, start) in enumerate(zip(sequences, starts)):
        if len(seq)>start:
            targets[i,start-1:len(seq)-1]=torch.tensor(seq[start:],device=device)
            mask[i,start-1:len(seq)-1]=True
    totals=logprobs.gather(-1,targets.unsqueeze(-1)).squeeze(-1).masked_fill(~mask,0).sum(-1).cpu().tolist()
    return [(total if suffix else float('-inf'),len(suffix)) for total,suffix in zip(totals,suffixes)]

def ranked_indices(scores, alpha=0.5, length_exponent=1.0, candidates=None, preserve_length=False):
    # Original candidate order contributes a bounded prior, with stable tie breaking.
    if not 0 <= alpha <= 1 or not 0 <= length_exponent <= 1:
        raise ValueError('alpha and length exponent must be between 0 and 1')
    n = len(scores)
    normalized = [s / (max(1, count) ** length_exponent) for s, count in scores]
    finite = [x for x in normalized if math.isfinite(x)]
    if not finite:
        return list(range(n))
    low, high = min(finite), max(finite)
    def score(i):
        lm = (normalized[i] - low) / (high - low) if high > low and math.isfinite(normalized[i]) else 0.0
        prior = 1 - i / max(1, n - 1)
        return alpha * lm + (1 - alpha) * prior
    if not preserve_length:
        return sorted(range(n), key=lambda i: (-score(i), i))
    if candidates is None or len(candidates) != n:
        raise ValueError('length-preserving ranking requires candidate texts')
    result = list(range(n))
    for length in {len(c) for c in candidates}:
        positions = [i for i, c in enumerate(candidates) if len(c) == length]
        reordered = sorted(positions, key=lambda i: (-score(i), i))
        for position, index in zip(positions, reordered): result[position] = index
    return result
