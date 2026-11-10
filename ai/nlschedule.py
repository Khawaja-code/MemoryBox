"""AI feature 2: natural-language scheduling.

    parse_schedule("every weekday at 9:30am")  ->  {"rule": "30 9 * * mon-fri", ...}

A small rule-based language model of scheduling phrases (no network, no dependencies). If the
rules cannot understand the text, the API may ask a cloud model (ai/llm.py) and then checks its
answer with `validate_rule()`, so a model can never invent an invalid or runaway schedule.

Rules are 5-field cron strings: "minute hour day-of-month month day-of-week" with weekday names
(mon..sun), exactly what scheduler/scheduler.py stores.
"""
import calendar
import re
from datetime import datetime, timedelta

DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_WORDS = {
    "mon": "mon", "monday": "mon", "mondays": "mon", "tue": "tue", "tues": "tue", "tuesday": "tue", "tuesdays": "tue",
    "wed": "wed", "weds": "wed", "wednesday": "wed", "wednesdays": "wed", "thu": "thu", "thur": "thu",
    "thurs": "thu", "thursday": "thu", "thursdays": "thu", "fri": "fri", "friday": "fri", "fridays": "fri",
    "sat": "sat", "saturday": "sat", "saturdays": "sat", "sun": "sun", "sunday": "sun", "sundays": "sun",
}
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
PART_OF_DAY = {"morning": (9, 0), "noon": (12, 0), "afternoon": (14, 0), "evening": (18, 0),
               "night": (21, 0), "midnight": (0, 0)}
MIN_INTERVAL_MINUTES = 5          # guard rail: automation must never run in a tight loop


class ScheduleError(ValueError):
    """Raised with a message that is safe to show to the user."""


# --------------------------------------------------------------------- cron expansion
def _expand(field, lo, hi, names=None, allow_last=False):
    names = names or {}
    values = set()
    for part in field.lower().split(","):
        if not part:
            raise ScheduleError(f"Empty item in '{field}'")
        if allow_last and part == "last":
            values.add(-1)
            continue
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise ScheduleError(f"Bad step in '{field}'")
            step = int(step_text)
        if part == "*":
            a, b = lo, hi
        else:
            a_text, _, b_text = part.partition("-")
            a = names.get(a_text, a_text)
            b = names.get(b_text, b_text) if b_text else (hi if step > 1 else a)
            try:
                a, b = int(a), int(b)
            except ValueError:
                raise ScheduleError(f"Cannot read '{part}' in '{field}'")
        if a < lo or b > hi or a > b:
            raise ScheduleError(f"'{field}' is outside {lo}-{hi}")
        values.update(range(a, b + 1, step))
    return values


def parse_cron(rule):
    """Validate a 5-field rule and return its expanded fields. Raises ScheduleError."""
    parts = (rule or "").split()
    if len(parts) != 5:
        raise ScheduleError("A schedule needs 5 fields: minute hour day month weekday")
    minute, hour, dom, month, dow = parts
    day_names = {d: i for i, d in enumerate(DAY_ORDER)}
    month_names = {m: i + 1 for i, m in enumerate(MONTHS)}
    return {
        "minute": _expand(minute, 0, 59), "hour": _expand(hour, 0, 23),
        "dom": _expand(dom, 1, 31, allow_last=True), "month": _expand(month, 1, 12, month_names),
        "dow": _expand(dow, 0, 6, day_names),
        "dom_any": dom == "*", "dow_any": dow == "*",
    }


def validate_rule(rule):
    """Return the normalised rule or raise ScheduleError. Also rejects runaway schedules."""
    fields = parse_cron(rule)
    if len(fields["hour"]) == 24 and len(fields["minute"]) > 1:
        gaps = sorted(fields["minute"])
        smallest = min([b - a for a, b in zip(gaps, gaps[1:])] + [60 - gaps[-1] + gaps[0]])
        if smallest < MIN_INTERVAL_MINUTES:
            raise ScheduleError(f"Runs more often than every {MIN_INTERVAL_MINUTES} minutes are not allowed")
    if len(fields["hour"]) == 24 and len(fields["minute"]) == 60:
        raise ScheduleError("Running every minute is not allowed")
    return " ".join(rule.split())


def next_runs(rule, now=None, count=3):
    """The next `count` local run times for a rule (preview only; APScheduler does the real work)."""
    f = parse_cron(rule)
    now = now or datetime.now()
    out, day = [], now.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(800):
        last = calendar.monthrange(day.year, day.month)[1]
        dom_ok = day.day in f["dom"] or (-1 in f["dom"] and day.day == last)
        if day.month in f["month"] and dom_ok and day.weekday() in f["dow"]:
            for h in sorted(f["hour"]):
                for m in sorted(f["minute"]):
                    t = day.replace(hour=h, minute=m)
                    if t > now:
                        out.append(t)
                        if len(out) == count:
                            return out
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------------- human wording
def _hhmm(h, m):
    return f"{h:02d}:{m:02d}"


def _ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def humanize_rule(rule):
    """Best-effort English for any rule this module can produce (falls back to the raw rule)."""
    try:
        f = parse_cron(rule)
    except ScheduleError:
        return rule
    minute, hour, dom, month, dow = rule.split()
    if hour.startswith("*/") and dom == month == dow == "*":
        n = hour[2:]
        return f"Every {n} hours" if n != "1" else "Every hour"
    if minute.startswith("*/") and hour == dom == month == dow == "*":
        return f"Every {minute[2:]} minutes"
    if hour == "*" and dom == month == dow == "*" and len(f["minute"]) == 1:
        return f"Every hour at :{min(f['minute']):02d}"
    times = [_hhmm(h, m) for h in sorted(f["hour"]) for m in sorted(f["minute"])]
    when = " and ".join(times) if len(times) <= 3 else f"{len(times)} times a day"
    if dow == "mon-fri":
        day = "Every weekday"
    elif dow == "sat,sun":
        day = "Every weekend day"
    elif dow != "*":
        names = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
                 "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
        full = [names.get(d, d) for d in dow.split(",")]
        day = "Every " + (full[0] if len(full) == 1 else ", ".join(full[:-1]) + " and " + full[-1])
    elif dom != "*" and month != "*":
        day = f"Every year on {calendar.month_name[int(month)]} {_ordinal(int(dom))}" if dom.isdigit() and month.isdigit() else "Yearly"
    elif dom == "last":
        day = "On the last day of every month"
    elif dom != "*":
        day = f"On the {_ordinal(int(dom))} of every month" if dom.isdigit() else "Monthly"
    else:
        day = "Every day"
    return f"{day} at {when}"


# -------------------------------------------------------------------------- the parser
_TIME_COLON = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b")
_TIME_AMPM = re.compile(r"\b(\d{1,2})\s*(am|pm)\b")
_TIME_AT = re.compile(r"\bat\s+(\d{1,2})\b(?!\s*(?:st|nd|rd|th|:|\d|minute|min|hour|hr))")


def _to_24h(hour, minute, ampm):
    if ampm:
        if not 1 <= hour <= 12:
            raise ScheduleError(f"'{hour}{ampm}' is not a valid time")
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"'{hour}:{minute:02d}' is not a valid time")
    return hour, minute


def _find_times(text, notes):
    times, spans = [], []
    for rx in (_TIME_COLON, _TIME_AMPM):
        for m in rx.finditer(text):
            if any(s <= m.start() < e for s, e in spans):
                continue
            spans.append(m.span())
            if rx is _TIME_COLON:
                times.append(_to_24h(int(m.group(1)), int(m.group(2)), m.group(3)))
            else:
                times.append(_to_24h(int(m.group(1)), 0, m.group(2)))
    if not times:
        for m in _TIME_AT.finditer(text):
            h = int(m.group(1))
            times.append(_to_24h(h, 0, None))
            if 1 <= h <= 6:
                notes.append(f"Read 'at {h}' as {h:02d}:00 (24-hour clock). Say '{h}pm' if you meant the evening.")
    if not times:
        for word, hm in PART_OF_DAY.items():
            if re.search(rf"\b{word}\b", text):
                times.append(hm)
                notes.append(f"Read '{word}' as {_hhmm(*hm)}.")
                break
    return times


def _find_days(text):
    if re.search(r"\bweekdays?\b", text) or re.search(r"\b(?:mon|monday)\s*(?:to|through|-|thru)\s*(?:fri|friday)\b", text):
        return "mon-fri"
    if re.search(r"\bweekends?\b", text):
        return "sat,sun"
    found = []
    for word in re.findall(r"[a-z]+", text):
        day = DAY_WORDS.get(word)
        if day and day not in found:
            found.append(day)
    return ",".join(sorted(found, key=DAY_ORDER.index)) if found else None


def parse_schedule(text, now=None):
    """Understand a scheduling phrase. Returns a dict, or raises ScheduleError with advice.

    {rule, human, next_runs[iso...], notes[], source: 'rules'}
    """
    original = text or ""
    t = re.sub(r"[,;]", " ", original.lower())
    t = re.sub(r"\ba\.m\.?", "am", t)
    t = re.sub(r"\bp\.m\.?", "pm", t)
    t = re.sub(r"\bo'?clock\b", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        raise ScheduleError("Describe when it should run, for example: every weekday at 9am")
    notes = []

    if re.search(r"\b(once|tomorrow|today|tonight|next week|in \d+ (?:minutes?|hours?|days?))\b", t):
        raise ScheduleError("MemoryBox schedules repeat. For a one-off run use the Play button, "
                            "or say how often it should repeat (for example 'every day at 8am').")

    # ---- intervals: "every 15 minutes", "every 2 hours", "hourly", "every other hour"
    m = re.search(r"\bevery\s+(\d+|other)\s*(minutes?|mins?|hours?|hrs?)\b", t)
    hourly = re.search(r"\b(hourly|every hour)\b", t)
    if m or hourly:
        if m:
            n = 2 if m.group(1) == "other" else int(m.group(1))
            unit = "m" if m.group(2).startswith("m") else "h"
        else:
            n, unit = 1, "h"
        if unit == "m":
            if n < MIN_INTERVAL_MINUTES:
                raise ScheduleError(f"The shortest repeat is every {MIN_INTERVAL_MINUTES} minutes.")
            if 60 % n:
                raise ScheduleError("Use a repeat that divides an hour evenly: 5, 10, 15, 20 or 30 minutes.")
            rule = f"*/{n} * * * *"
        else:
            if 24 % n:
                raise ScheduleError("Use a repeat that divides a day evenly: 1, 2, 3, 4, 6, 8 or 12 hours.")
            rule = "0 * * * *" if n == 1 else f"0 */{n} * * *"
        return _result(rule, now, notes)

    times = _find_times(t, notes)
    if len({m for _, m in times}) > 1:
        raise ScheduleError("Use one schedule per set of times. Several times are fine if they share the same minutes (for example 9am and 5pm).")
    hours = sorted({h for h, _ in times})
    minute = times[0][1] if times else 0

    dom, month, dow = "*", "*", "*"
    month_hit = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t) \
        or re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b", t)
    days = _find_days(t)

    if month_hit and not re.search(r"\bmonth(ly)?\b", t):
        a, b = month_hit.group(1), month_hit.group(2)
        mon_word, day_num = (a, b) if a[:3] in MONTHS else (b, a)
        month, dom = str(MONTHS.index(mon_word[:3]) + 1), str(int(day_num))
        try:
            parse_cron(f"0 0 {dom} {month} *")
        except ScheduleError:
            raise ScheduleError(f"'{day_num}' is not a valid day")
        if int(dom) > calendar.monthrange(2024, int(month))[1]:
            raise ScheduleError(f"{calendar.month_name[int(month)]} has no day {dom}")
        kind = "yearly"
    elif re.search(r"\blast day of (?:the |every |each )?month\b", t) or re.search(r"\bend of (?:the |every |each )?month\b", t):
        dom, kind = "last", "monthly"
    elif re.search(r"\bfirst day of (?:the |every |each )?month\b", t):
        dom, kind = "1", "monthly"
    elif re.search(r"\bmonth(ly)?\b", t):
        d = re.search(r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b", t)
        if not d:
            raise ScheduleError("Which day of the month? For example: monthly on the 15th at 10am")
        if not 1 <= int(d.group(1)) <= 31:
            raise ScheduleError(f"'{d.group(1)}' is not a valid day of the month")
        dom, kind = str(int(d.group(1))), "monthly"
        if int(dom) > 28:
            notes.append(f"Months without a {_ordinal(int(dom))} will be skipped.")
    elif days:
        dow, kind = days, "weekly"
    elif re.search(r"\b(daily|every ?day|each day|every (?:morning|afternoon|evening|night))\b", t):
        kind = "daily"
    elif times:
        kind = "daily"
        notes.append("No day given, so I assumed every day.")
    else:
        raise ScheduleError("I could not find a time or a day. Try: 'every weekday at 9am' or 'monthly on the 1st at 08:00'.")

    if not times:
        hours, minute = [9], 0
        notes.append("No time given, so I assumed 09:00.")
    rule = f"{minute} {','.join(str(h) for h in hours)} {dom} {month} {dow}"
    return _result(rule, now, notes)


def _result(rule, now, notes):
    rule = validate_rule(rule)
    return {
        "rule": rule,
        "human": humanize_rule(rule),
        "next_runs": [d.isoformat(timespec="minutes") for d in next_runs(rule, now)],
        "notes": notes,
        "source": "rules",
    }
