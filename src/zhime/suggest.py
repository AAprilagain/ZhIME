"""Bounded Chinese phrase continuation for the local IME."""
import re
import time

import torch

from .joint_score import normalize_context


HAN = re.compile(r'[\u3400-\u9fff]{2,8}')


@torch.inference_mode()
def suggest(model, tokenizer, context, budget_seconds=.20):
    context = normalize_context(context)
    if len(context) < 2 or not '\u3400' <= context[-1] <= '\u9fff':
        return []
    limit = model.config['context']
    prefix = tokenizer.encode(context, out_type=int)[-(limit - 4):]
    base = tokenizer.decode(prefix)
    if not base or not context.endswith(base) or tokenizer.unk_id() in prefix:
        return []
    prefix = [tokenizer.bos_id(), *prefix]
    deadline = time.monotonic() + budget_seconds
    beams = [((), 0.)]
    found = {}
    for _ in range(3):
        if time.monotonic() >= deadline:
            break
        inputs = torch.tensor([prefix + list(ids) for ids, _ in beams], device=model.token.weight.device)
        if hasattr(model, 'encode') and hasattr(model, 'project'):
            logits = model.project(model.encode(inputs)[:, -1])
        else:
            logits = model(inputs)[0][:, -1]
        scores, tokens = logits.float().log_softmax(-1).topk(12)
        expanded = []
        for (ids, previous), row_scores, row_tokens in zip(beams, scores.tolist(), tokens.tolist()):
            for score, token in zip(row_scores, row_tokens):
                if tokenizer.is_control(token) or tokenizer.is_unknown(token) or tokenizer.is_unused(token):
                    continue
                sequence = (*ids, token)
                decoded = tokenizer.decode(prefix[1:] + list(sequence))
                if not decoded.startswith(base):
                    continue
                suffix = decoded[len(base):]
                if not re.fullmatch(r'[\u3400-\u9fff]{1,8}', suffix):
                    continue
                total = previous + score
                expanded.append((sequence, total))
                if HAN.fullmatch(suffix) and suffix not in context:
                    found[suffix] = max(found.get(suffix, float('-inf')), total)
        beams = sorted(expanded, key=lambda entry: -entry[1])[:4]
        if not beams:
            break
    result = []
    for phrase in sorted(found, key=lambda phrase: (-found[phrase], phrase)):
        if any(phrase.startswith(other) or other.startswith(phrase) for other in result):
            continue
        result.append(phrase)
        if len(result) == 3:
            break
    return result
