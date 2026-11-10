"""Module 6: AI Pattern Detection (Phase 7).

1. Turn raw events into meaningful action tokens (click:Button.left, type_text, ...).
2. Mine frequent ordered sub-sequences with PrefixSpan (falls back to a simple
   contiguous n-gram miner if the `prefixspan` package is not installed).
3. Rank by support and keep only routines that are long and frequent enough.
"""
from collections import Counter

from db import dal


# ------------------------------------------------------------------ step 1
def events_to_actions_with_spans(events):
    """Return [(token, first_event_index, last_event_index), ...]."""
    actions = []
    for i, e in enumerate(events):
        kind = e["type"]
        if kind == "click":
            token = f"click:{e['button']}"
        elif kind == "scroll":
            token = "scroll"
        elif kind == "key_down":
            key = e.get("key") or ""
            token = "type_text" if len(key) == 1 else f"key:{key}"
        else:
            continue                                  # key_up, device, protected: not actions
        if token in ("type_text", "scroll") and actions and actions[-1][0] == token:
            actions[-1] = (token, actions[-1][1], i)  # merge a run of typing / scrolling
        else:
            actions.append((token, i, i))
    return actions


def events_to_actions(events):
    return [token for token, _, _ in events_to_actions_with_spans(events)]


def split_into_chunks(actions, size=40):
    """Cut one long session into chunks so repeats inside it count as separate sightings."""
    return [actions[i:i + size] for i in range(0, len(actions), size) if actions[i:i + size]]


# ------------------------------------------------------------------ step 2
def _ngram_miner(sequences, min_support, min_len, max_len=8):
    counts = Counter()
    for seq in sequences:
        seen = set()
        for n in range(min_len, min(max_len, len(seq)) + 1):
            for i in range(len(seq) - n + 1):
                seen.add(tuple(seq[i:i + n]))
        counts.update(seen)                           # support = number of sequences containing it
    frequent = {p: s for p, s in counts.items() if s >= min_support}

    def contains(big, small):
        return any(big[i:i + len(small)] == small for i in range(len(big) - len(small) + 1))

    closed = []                                       # drop patterns hidden inside a longer one
    for p, s in frequent.items():
        if not any(len(q) > len(p) and t == s and contains(q, p) for q, t in frequent.items()):
            closed.append((s, list(p)))
    return closed


def mine_routines(sequences, min_support=5, min_len=3, top=10, drop_trivial=True):
    """Return [(support, pattern_list), ...] strongest first."""
    try:
        from prefixspan import PrefixSpan
        ps = PrefixSpan(sequences)
        ps.minlen = min_len
        candidates = [(s, list(p)) for s, p in ps.frequent(min_support, closed=True)]
    except ImportError:
        candidates = _ngram_miner(sequences, min_support, min_len)
    if drop_trivial:                                  # e.g. click, click, click is not a routine
        candidates = [(s, p) for s, p in candidates if len(set(p)) > 1]
    candidates.sort(key=lambda c: (c[0], len(c[1])), reverse=True)
    return candidates[:top]


# ------------------------------------------------------------------ step 3
def suggest_routine(pattern, support, db_path=None):
    """Store a suggestion so the dashboard can show it."""
    return dal.add_routine(pattern, support, db_path)


def find_first_occurrence(pattern, events):
    """Return the raw events of the first contiguous match of `pattern`, rebased to t=0.
    Used for the 'save this routine as a new recording' one-click action."""
    spans = events_to_actions_with_spans(events)
    tokens = [t for t, _, _ in spans]
    n = len(pattern)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == list(pattern):
            first, last = spans[i][1], spans[i + n - 1][2]
            chunk = [dict(e) for e in events[first:last + 1]]
            base = chunk[0]["t_offset_ms"]
            for e in chunk:
                e["t_offset_ms"] -= base
            return chunk
    return None


def run_miner(min_support=5, min_len=3, db_path=None):
    """Mine every stored recording as one session and save the strongest suggestions."""
    sequences = []
    for rec in dal.list_recordings(db_path=db_path):
        actions = events_to_actions(dal.get_events(rec["id"], db_path))
        sequences.extend(split_into_chunks(actions) if len(actions) > 80 else [actions])
    found = mine_routines(sequences, min_support, min_len)
    for support, pattern in found:
        suggest_routine(pattern, support, db_path)
    return found


def save_routine_as_recording(routine_id, name=None, db_path=None):
    """One-click action: turn a suggested routine into a real recording the user can schedule.
    Finds the first stored recording that contains the routine as a contiguous run of actions.
    Returns the new recording id, or None if no contiguous example exists."""
    routine = next((r for r in dal.list_routines(db_path) if r["id"] == routine_id), None)
    if routine is None:
        raise KeyError(f"routine {routine_id} not found")
    for rec in dal.list_recordings(db_path=db_path):
        chunk = find_first_occurrence(routine["pattern"], dal.get_events(rec["id"], db_path))
        if chunk:
            return dal.create_recording(name or f"Routine {routine_id}", chunk, db_path=db_path)
    return None
