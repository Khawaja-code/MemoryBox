"""AI feature 4: learning your action patterns.

A variable-order Markov model (orders 1-3 with backoff) trained on the action tokens of every
recording. It can

  * predict what you are most likely to do next after a given sequence, and
  * score how "routine" a recording is (perplexity-based) and point at the steps that were
    unusual compared with everything else you have recorded.

Pure Python, trains in milliseconds on personal-sized data, no dependencies.
"""
import math
from collections import Counter, defaultdict

from intelligence.miner import events_to_actions

MAX_ORDER = 3
SMOOTHING = 0.1


class NextActionModel:
    def __init__(self, max_order=MAX_ORDER):
        self.max_order = max_order
        self.counts = [defaultdict(Counter) for _ in range(max_order + 1)]   # counts[k][context tuple]
        self.vocab = set()
        self.sequences = 0

    def fit(self, sequences):
        for seq in sequences:
            if not seq:
                continue
            self.sequences += 1
            padded = ["<s>"] + list(seq) + ["</s>"]
            self.vocab.update(seq)
            for i in range(1, len(padded)):
                for k in range(self.max_order + 1):
                    if i - k < 0:
                        break
                    self.counts[k][tuple(padded[i - k:i])][padded[i]] += 1
        return self

    @property
    def trained(self):
        return self.sequences > 0

    def _distribution(self, context):
        """Back off from the longest known context to shorter ones."""
        full = ["<s>"] + list(context)               # training windows start with the same marker
        for k in range(min(self.max_order, len(full)), -1, -1):
            ctx = tuple(full[len(full) - k:]) if k else ()
            dist = self.counts[k].get(ctx)
            if dist:
                total = sum(dist.values())
                return {tok: c / total for tok, c in dist.items()}, k
        return {}, 0

    def predict(self, context, top=3):
        """[(token, probability), ...] most likely next actions. '</s>' means 'probably finished'."""
        dist, _ = self._distribution(context)
        return sorted(dist.items(), key=lambda kv: -kv[1])[:top]

    def prob(self, context, token):
        dist, _ = self._distribution(context)
        v = len(self.vocab) + 2
        return (dist.get(token, 0.0) + SMOOTHING / v) / (1 + SMOOTHING)

    def score(self, seq):
        """Return {perplexity, routine_score 0-100, surprises: [(index, token, prob)]}."""
        if not seq:
            return {"perplexity": None, "routine_score": None, "surprises": []}
        padded = list(seq) + ["</s>"]
        logs, surprises = 0.0, []
        for i, tok in enumerate(padded):
            p = self.prob(padded[:i], tok)
            logs += -math.log(p)
            if p < 0.05 and tok != "</s>":
                surprises.append((i, tok, round(p, 3)))
        perplexity = math.exp(logs / len(padded))
        # perplexity 1 = fully predictable; map to 0..100 relative to the vocabulary size
        ceiling = max(len(self.vocab) + 1, 2)
        routine = max(0.0, min(1.0, 1 - (math.log(perplexity) / math.log(ceiling))))
        return {"perplexity": round(perplexity, 2), "routine_score": int(round(routine * 100)), "surprises": surprises[:5]}


def train_from_db(db_path=None, exclude=None):
    """Train on every stored recording, optionally leaving one out (to judge it fairly)."""
    from db import dal
    seqs = []
    for rec in dal.list_recordings(db_path=db_path):
        if rec["id"] == exclude:
            continue
        seqs.append(events_to_actions(dal.get_events(rec["id"], db_path)))
    return NextActionModel().fit(seqs)
