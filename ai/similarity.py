"""AI feature 5: find similar and duplicate recordings.

Each recording becomes a TF-IDF vector of its action n-grams (1-3 actions). Cosine similarity
then says how alike two recordings are. Used to spot duplicates and to group related routines.
Pure Python (small data), no dependencies.
"""
import math
from collections import Counter

from intelligence.miner import events_to_actions


def _ngrams(tokens, max_n=3):
    grams = Counter()
    for n in range(1, max_n + 1):
        for i in range(len(tokens) - n + 1):
            grams[" > ".join(tokens[i:i + n])] += 1
    return grams


def build_vectors(token_lists):
    """{id: tokens} -> {id: {gram: tfidf}} (L2-normalised)."""
    docs = {k: _ngrams(v) for k, v in token_lists.items()}
    n = len(docs)
    df = Counter(g for d in docs.values() for g in d)
    vectors = {}
    for k, d in docs.items():
        vec = {g: (1 + math.log(c)) * (math.log((1 + n) / (1 + df[g])) + 1) for g, c in d.items()}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors[k] = {g: v / norm for g, v in vec.items()}
    return vectors


def cosine(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(g, 0.0) for g, v in a.items())


def similar_to(target, token_lists, top=3, minimum=0.35):
    """[(other_id, similarity)] most similar first."""
    vectors = build_vectors(token_lists)
    if target not in vectors:
        return []
    scored = [(k, round(cosine(vectors[target], v), 3)) for k, v in vectors.items() if k != target]
    return sorted([s for s in scored if s[1] >= minimum], key=lambda s: -s[1])[:top]


def duplicate_groups(token_lists, threshold=0.95):
    """Groups of recordings whose action sequences are (almost) the same. Union-find on pairs."""
    vectors = build_vectors({k: v for k, v in token_lists.items() if v})
    ids = list(vectors)
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    best = {}
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            s = cosine(vectors[a], vectors[b])
            if s >= threshold:
                parent[find(a)] = find(b)
                best[(a, b)] = s
    groups = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) > 1:
            sims = [s for (a, b), s in best.items() if a in members and b in members]
            out.append({"recording_ids": sorted(members), "similarity": round(min(sims), 2)})
    return sorted(out, key=lambda g: -g["similarity"])


def token_lists_from_db(db_path=None):
    from db import dal
    return {r["id"]: events_to_actions(dal.get_events(r["id"], db_path)) for r in dal.list_recordings(db_path=db_path)}
