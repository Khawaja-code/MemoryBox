"""AI feature 3: habit detection -> schedule suggestions.

Looks at WHEN you play each recording by hand and, if the runs cluster around a time of day
(and certain weekdays), proposes a schedule for it. Pure statistics, runs offline.

Note: the database stores timestamps in UTC (SQLite datetime('now')), while schedules fire in
local time, so every timestamp is converted to local time before looking for a pattern.
"""
from collections import Counter
from datetime import datetime, timezone
from statistics import median

from ai import nlschedule

MIN_RUNS = 3
WINDOW_MIN = 90                 # runs within +-45 minutes count as "the same time of day"
MIN_COVERAGE = 0.6              # share of runs that must fall in the window


def parse_db_time(text):
    """'2026-10-20 07:45:00' (UTC) -> aware local datetime."""
    return datetime.strptime(text[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()


def _densest_window(minutes):
    """Circular sliding window over minute-of-day values. Returns (members, centre_minute)."""
    best = []
    for anchor in minutes:
        members = [m for m in minutes if (m - anchor) % 1440 <= WINDOW_MIN]
        if len(members) > len(best):
            best = members
    anchor = min(best, key=lambda a: sum((m - a) % 1440 for m in best)) if best else 0
    offsets = sorted((m - anchor) % 1440 for m in best)
    return best, int((anchor + median(offsets)) % 1440)


def _weekday_spec(weekdays):
    """weekdays: list of 0..6 (Mon..Sun). Returns (cron day field, human text, share).

    Evidence rules, so a coincidence is not mistaken for a habit:
      * "every weekday" / "daily" need at least 5 runs spread over several days;
      * a named day needs at least 2 runs on that day, and at most 3 such days are suggested.
    """
    count = Counter(weekdays)
    total = len(weekdays)
    names = nlschedule.DAY_ORDER
    work = sum(count[d] for d in range(5))
    work_days = {d for d in count if d < 5}
    if total >= 5 and work / total >= 0.85 and len(work_days) >= 4:
        return "mon-fri", "weekdays", work / total
    if total >= 5 and len(set(count)) >= 6:
        return "*", "every day", 1.0
    top = [d for d, c in count.most_common(3) if c >= 2]
    if top and sum(count[d] for d in top) / total >= 0.7:
        label = ", ".join(names[d].title() for d in sorted(top))
        return ",".join(names[d] for d in sorted(top)), label, sum(count[d] for d in top) / total
    return None, "", 0.0


def suggest_from_runs(runs_by_recording, names, existing_rules=None, now=None):
    """runs_by_recording: {recording_id: [db timestamp, ...]} (manual or scheduled, any status).
    existing_rules: {recording_id: [cron, ...]} so we do not suggest what already exists."""
    existing_rules = existing_rules or {}
    out = []
    for rid, stamps in runs_by_recording.items():
        if len(stamps) < MIN_RUNS:
            continue
        local = [parse_db_time(s) for s in stamps]
        minutes = [d.hour * 60 + d.minute for d in local]
        members, centre = _densest_window(minutes)
        time_share = len(members) / len(minutes)
        if time_share < MIN_COVERAGE:
            continue
        in_window = [d for d, m in zip(local, minutes) if m in members]
        field, day_text, day_share = _weekday_spec([d.weekday() for d in in_window])
        if field is None:
            continue
        centre = int(round(centre / 5.0) * 5) % 1440          # tidy to 5 minutes
        hour, minute = divmod(centre, 60)
        rule = nlschedule.validate_rule(f"{minute} {hour} * * {field}")
        if rule in existing_rules.get(rid, []):
            continue
        confidence = round(time_share * day_share * min(1.0, len(stamps) / 6), 2)
        if confidence < 0.4:
            continue
        out.append({
            "recording_id": rid,
            "recording_name": names.get(rid, f"#{rid}"),
            "rule": rule,
            "human": nlschedule.humanize_rule(rule),
            "confidence": confidence,
            "evidence": f"You ran it {len(stamps)} times; {len(members)} of them near {hour:02d}:{minute:02d}"
                        f" ({day_text}).",
            "next_runs": [d.isoformat(timespec="minutes") for d in nlschedule.next_runs(rule, now)],
        })
    out.sort(key=lambda s: -s["confidence"])
    return out


def suggest_schedules(db_path=None, now=None):
    from db import dal
    names = {r["id"]: r["name"] for r in dal.list_recordings(db_path=db_path)}
    runs = {}
    for r in dal.list_runs(100000, db_path):
        if r["status"] in ("success", "stopped", "failed"):          # "skipped"/"running" say nothing about habit
            runs.setdefault(r["recording_id"], []).append(r["started_at"])
    existing = {}
    for s in dal.list_schedules(active_only=True, db_path=db_path):
        existing.setdefault(s["recording_id"], []).append(s["rule"])
    return suggest_from_runs(runs, names, existing, now)
