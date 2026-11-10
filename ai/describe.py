"""AI feature 1: understand a recording.

Turns raw events into (a) statistics, (b) a plain-English step list, (c) a suggested name and
(d) honest warnings about how reliable a replay is likely to be. Runs fully offline.

Privacy: nothing here ever looks at WHICH keys were typed, only that typing happened. The
summary produced by `summarize()` is also the only thing that may be sent to a cloud model
(see ai/llm.py), so typed text and coordinates never leave the machine.
"""
from collections import Counter

from intelligence.miner import events_to_actions

LONG_PAUSE_MS = 2000


# ----------------------------------------------------------------- humanising tokens
def humanize(token):
    """'click:Button.left' -> 'Left-click', 'key:Key.enter' -> 'Press Enter'."""
    if token.startswith("click:"):
        side = token.split(".", 1)[-1].lower()
        return {"left": "Left-click", "right": "Right-click", "middle": "Middle-click"}.get(side, "Click")
    if token == "type_text":
        return "Type text"
    if token == "scroll":
        return "Scroll"
    if token.startswith("key:"):
        raw = token[4:]
        if raw.startswith("Key."):
            name = raw[4:]
            for suffix in ("_l", "_r"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
            return "Press " + name.replace("_", " ").title()
        return "Press shortcut"
    return token


def steps_from_tokens(tokens):
    """Collapse runs of the same action: ['Left-click x3', 'Type text', ...]."""
    steps, i = [], 0
    while i < len(tokens):
        j = i
        while j < len(tokens) and tokens[j] == tokens[i]:
            j += 1
        count = j - i
        label = humanize(tokens[i])
        steps.append(f"{label} ×{count}" if count > 1 else label)
        i = j
    return steps


# ---------------------------------------------------------------------- statistics
def compute_stats(events):
    types = Counter(e["type"] for e in events)
    times = [e["t_offset_ms"] for e in events]
    gaps = [b - a for a, b in zip(times, times[1:])]
    duration = max(times, default=0)
    long_gaps = [g for g in gaps if g >= LONG_PAUSE_MS]
    typed = sum(1 for e in events if e["type"] == "key_down" and len(e.get("key") or "") == 1)
    clicks = [e for e in events if e["type"] == "click"]
    return {
        "events": len(events),
        "clicks": types.get("click", 0),
        "scrolls": types.get("scroll", 0),
        "keys_pressed": types.get("key_down", 0),
        "characters_typed": typed,                      # a count only, never the characters
        "protected_skips": types.get("protected", 0),
        "duration_ms": duration,
        "avg_gap_ms": round(sum(gaps) / len(gaps)) if gaps else 0,
        "longest_gap_ms": max(gaps, default=0),
        "long_pause_ms_total": sum(long_gaps),
        "long_pause_count": len(long_gaps),
        "idle_share": round(sum(long_gaps) / duration, 2) if duration else 0.0,
        "distinct_click_points": len({(e["x"], e["y"]) for e in clicks}),
    }


# ---------------------------------------------------------------------------- naming
def suggest_name(tokens, stats):
    has = lambda t: t in tokens
    if not tokens:
        return "Empty recording"
    typing = has("type_text")
    submit = has("key:Key.enter")
    clicks = stats["clicks"]
    scrolls = stats["scrolls"]
    if typing and submit and clicks:
        return "Fill and submit"
    if typing and clicks:
        return "Click and type"
    if typing and submit:
        return "Type and confirm"
    if typing:
        return "Typing routine"
    if scrolls and scrolls >= max(clicks, 1) * 2:
        return "Scroll and browse"
    if clicks and not scrolls:
        return f"{clicks}-click sequence" if clicks > 1 else "Single click"
    if scrolls:
        return "Click and scroll"
    return "Keyboard shortcuts"


# --------------------------------------------------------------------------- advice
def advice(stats):
    """Plain-language warnings and tips. Each item: {level: info|warn, text}."""
    out = []
    if stats["clicks"] or stats["scrolls"]:
        out.append({"level": "warn", "text": (
            "Clicks replay at the exact screen positions you recorded. Keep the target window in "
            "the same place and size, or the replay may hit the wrong spot.")})
    if stats["protected_skips"]:
        out.append({"level": "info", "text": (
            f"{stats['protected_skips']} password entry/entries were deliberately not recorded and "
            "will be skipped on replay. You will need to type those yourself.")})
    if stats["long_pause_count"]:
        secs = round(stats["long_pause_ms_total"] / 1000, 1)
        out.append({"level": "info", "text": (
            f"{stats['long_pause_count']} long pause(s) add up to {secs}s "
            f"({int(stats['idle_share'] * 100)}% of the run). Playback speed 1.5x trims them, but "
            "keep 1x if the app needs time to load between steps.")})
    if stats["events"] < 4:
        out.append({"level": "warn", "text": "Very short recording. Record the whole task to get useful routines."})
    if stats["events"] > 3000:
        out.append({"level": "info", "text": "Long recording. Consider splitting it into smaller routines so failures are easier to retry."})
    return out


# -------------------------------------------------------------------------- summary
def describe_text(steps, stats):
    if not steps:
        return "This recording has no actions."
    shown = ", then ".join(s.lower() if i else s for i, s in enumerate(steps[:6]))
    more = f" and {len(steps) - 6} more step(s)" if len(steps) > 6 else ""
    secs = round(stats["duration_ms"] / 1000, 1)
    return f"{shown}{more}. Takes about {secs}s."


def summarize(events):
    """Full local analysis. The returned dict contains no typed characters and no coordinates."""
    tokens = events_to_actions(events)
    stats = compute_stats(events)
    steps = steps_from_tokens(tokens)
    return {
        "tokens": tokens,
        "steps": steps,
        "stats": stats,
        "suggested_name": suggest_name(tokens, stats),
        "description": describe_text(steps, stats),
        "advice": advice(stats),
        "source": "local",
    }
