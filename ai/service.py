"""Glue between the AI modules and the rest of MemoryBox (shared by the API and the CLI)."""
from collections import Counter

from ai import describe, habits, predict, similarity
from ai.llm import LLMError, polish_summary
from db import dal
from intelligence.miner import events_to_actions

MAX_TIMELINE_EVENTS = 3000


def reliability(rid, db_path=None):
    runs = [r for r in dal.list_runs(100000, db_path) if r["recording_id"] == rid]
    counts = Counter(r["status"] for r in runs)
    finished = counts.get("success", 0) + counts.get("failed", 0)
    rate = counts["success"] / finished if finished else None
    if finished < 2:
        label = "not enough history"
    elif rate >= 0.9:
        label = "reliable"
    elif rate >= 0.5:
        label = "flaky"
    else:
        label = "failing"
    last_failure = next((r["message"] for r in runs if r["status"] == "failed"), None)
    return {"runs": len(runs), "by_status": dict(counts), "success_rate": None if rate is None else round(rate, 2),
            "label": label, "last_failure": last_failure,
            "recent": [r["status"] for r in runs[:10]][::-1]}


def recording_insights(rid, db_path=None, llm=None, use_llm=False):
    rec = dal.get_recording(rid, db_path)
    if rec is None:
        raise KeyError(rid)
    events = dal.get_events(rid, db_path)
    out = describe.summarize(events)
    out["recording_id"] = rid

    others = predict.train_from_db(db_path, exclude=rid)
    out["routine_score"] = others.score(out["tokens"]) if others.sequences >= 2 else None
    everyone = predict.train_from_db(db_path)
    out["predicted_next"] = [{"action": describe.humanize(t) if t != "</s>" else "Finish", "probability": round(p, 2)}
                             for t, p in everyone.predict(out["tokens"])]

    names = {r["id"]: r["name"] for r in dal.list_recordings(db_path=db_path)}
    out["similar"] = [{"recording_id": k, "name": names[k], "similarity": s}
                      for k, s in similarity.similar_to(rid, similarity.token_lists_from_db(db_path))]
    out["reliability"] = reliability(rid, db_path)

    if use_llm:
        if llm is None or not llm.available:
            out["ai_note"] = "Cloud AI is off. Turn it on in Settings to polish names with a language model."
        else:
            try:
                polished = polish_summary(llm, out)
                out["suggested_name"], out["description"], out["source"] = polished["name"], polished["description"], "cloud"
            except LLMError as exc:
                out["ai_note"] = f"Cloud AI failed ({exc}). Showing the local analysis instead."
    return out


def overview(db_path=None, now=None):
    """Everything the dashboard's AI panel shows at once."""
    names = {r["id"]: r["name"] for r in dal.list_recordings(db_path=db_path)}
    groups = similarity.duplicate_groups(similarity.token_lists_from_db(db_path))
    for g in groups:
        g["names"] = [names[i] for i in g["recording_ids"]]
    return {"schedule_suggestions": habits.suggest_schedules(db_path, now), "duplicates": groups}


def timeline(rid, db_path=None):
    """Event timeline for the dashboard visual. Typed characters are never included."""
    if dal.get_recording(rid, db_path) is None:
        raise KeyError(rid)
    events = dal.get_events(rid, db_path)
    out = []
    for e in events[:MAX_TIMELINE_EVENTS]:
        kind = e["type"]
        if kind == "click":
            out.append({"t": e["t_offset_ms"], "type": "click", "x": e["x"], "y": e["y"], "button": (e["button"] or "").split(".")[-1]})
        elif kind == "scroll":
            out.append({"t": e["t_offset_ms"], "type": "scroll", "x": e["x"], "y": e["y"]})
        elif kind == "key_down":
            key = e.get("key") or ""
            out.append({"t": e["t_offset_ms"], "type": "key", "special": len(key) != 1})
        elif kind == "protected":
            out.append({"t": e["t_offset_ms"], "type": "protected"})
    return {"events": out, "total_events": len(events), "truncated": len(events) > MAX_TIMELINE_EVENTS,
            "duration_ms": max((e["t_offset_ms"] for e in events), default=0)}
