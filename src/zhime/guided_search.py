"""Request-local conditional LM score cache for lexicon-constrained search."""
from collections import defaultdict
import time

from .joint_score import ScoringFallback, common_prefix, normalize_context, score_joint


def scorer(model, sp, context, device, budget=None):
    context = normalize_context(context)
    context_ids = sp.encode(context, out_type=int)
    cache = {}
    deadline = None if budget is None else time.monotonic() + budget

    def check_deadline():
        if deadline is not None and time.monotonic() >= deadline:
            raise ScoringFallback('search_timeout')

    def scores(texts):
        check_deadline()
        groups = defaultdict(list)
        for text in texts:
            if text in cache:
                continue
            ids = sp.encode(context + text, out_type=int)
            if sp.decode(ids) != context + text or len(ids) > model.config['context']:
                # Keep these paths available, with a finite penalty.
                cache[text] = -1000.
                continue
            prefix = common_prefix([context_ids, ids])
            if len(ids) <= prefix:
                cache[text] = -1000.
                continue
            groups[prefix].append(text)
        for group in groups.values():
            check_deadline()
            values, _ = score_joint(model, sp, context, group, device)
            cache.update((text, pair[0]) for text, pair in zip(group, values))
        check_deadline()
        return {text: cache[text] for text in texts}
    return scores
