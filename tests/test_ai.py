"""Tests for the AI package. Everything runs offline with fakes; no network, mouse or keyboard."""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai import describe, habits, nlschedule
from ai.assistant import Assistant, extract_loops, extract_speed, validate_proposal
from ai.llm import LLM, LLMError, polish_summary, schedule_from_text
from ai.predict import NextActionModel
from ai.similarity import duplicate_groups, similar_to
from db import dal

NOW = datetime(2026, 10, 20, 12, 45)          # a Tuesday


def _db():
    path = Path(tempfile.mkdtemp()) / "t.db"
    dal.init_db(path)
    return path


def ev(type_, t, **kw):
    base = dict(type=type_, x=None, y=None, key=None, button=None, scroll=None, t_offset_ms=t)
    base.update(kw)
    return base


def form_events():
    return [ev("click", 0, x=10, y=20, button="Button.left"),
            ev("key_down", 500, key="h"), ev("key_up", 520, key="h"),
            ev("key_down", 600, key="i"), ev("key_up", 620, key="i"),
            ev("key_down", 5000, key="Key.enter"), ev("key_up", 5020, key="Key.enter")]


# ------------------------------------------------------------- natural-language scheduling
def test_schedule_phrases():
    cases = {
        "every weekday at 9:30am": "30 9 * * mon-fri",
        "daily at 8": "0 8 * * *",
        "every monday and friday at 5pm": "0 17 * * mon,fri",
        "monthly on the 15th at 10am": "0 10 15 * *",
        "every year on March 3 at 6am": "0 6 3 3 *",
        "25 December at 7am": "0 7 25 12 *",
        "every 15 minutes": "*/15 * * * *",
        "every 2 hours": "0 */2 * * *",
        "weekends at noon": "0 12 * * sat,sun",
        "last day of the month at 23:00": "0 23 last * *",
        "at 9am and 5pm every day": "0 9,17 * * *",
        "every mon to fri at 7:15 pm": "15 19 * * mon-fri",
    }
    for text, rule in cases.items():
        assert nlschedule.parse_schedule(text, NOW)["rule"] == rule, text


def test_schedule_rejects_unsafe_or_unclear_input():
    for text in ["every 3 minutes", "every 7 minutes", "tomorrow at 9", "blah", "every day at 25:00",
                 "feb 30 at 9am", "", "at 9am and 5:30pm every day"]:
        try:
            nlschedule.parse_schedule(text, NOW)
        except nlschedule.ScheduleError:
            continue
        raise AssertionError(f"should reject: {text!r}")
    for rule in ("* * * * *", "*/2 * * * *", "0 0 * *", "61 * * * *", "0 0 32 * *"):
        try:
            nlschedule.validate_rule(rule)
        except nlschedule.ScheduleError:
            continue
        raise AssertionError(f"should reject rule: {rule!r}")


def test_next_runs_preview():
    runs = nlschedule.next_runs("30 9 * * mon-fri", NOW, 3)
    assert [r.strftime("%a %H:%M") for r in runs] == ["Wed 09:30", "Thu 09:30", "Fri 09:30"]
    assert nlschedule.next_runs("0 0 last * *", NOW, 1)[0] == datetime(2026, 10, 31, 0, 0)
    assert nlschedule.next_runs("0 6 3 3 *", NOW, 1)[0].year == 2027


def test_humanize_rule():
    assert nlschedule.humanize_rule("30 9 * * mon-fri") == "Every weekday at 09:30"
    assert nlschedule.humanize_rule("0 */2 * * *") == "Every 2 hours"
    assert nlschedule.humanize_rule("0 17 * * mon,fri") == "Every Monday and Friday at 17:00"


# ------------------------------------------------------------------- describing recordings
def test_describe_recording_never_leaks_typed_text():
    s = describe.summarize(form_events())
    assert s["steps"] == ["Left-click", "Type text", "Press Enter"]
    assert s["suggested_name"] == "Fill and submit"
    assert s["stats"]["characters_typed"] == 2
    assert "h" not in " ".join(s["steps"]).lower().replace("enter", "").replace("left-click", "").replace("type text", "")
    assert any("exact screen positions" in a["text"] for a in s["advice"])
    assert any("long pause" in a["text"] for a in s["advice"])        # the 4.4 s gap before Enter


def test_humanize_tokens():
    assert describe.humanize("key:Key.ctrl_l") == "Press Ctrl"
    assert describe.steps_from_tokens(["scroll", "scroll", "click:Button.right"]) == ["Scroll ×2", "Right-click"]


# ----------------------------------------------------------------------- habit detection
def _utc_stamp_for_local(day, hour, minute):
    local = datetime(2026, 10, day, hour, minute).astimezone()
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def test_habits_find_weekday_morning_routine_in_local_time():
    # Mon-Fri around 08:30 local, stored as UTC like SQLite does
    stamps = [_utc_stamp_for_local(d, 8, m) for d, m in [(5, 25), (6, 31), (7, 28), (8, 33), (9, 30), (12, 27)]]
    out = habits.suggest_from_runs({1: stamps}, {1: "Morning report"}, now=NOW)
    assert len(out) == 1 and out[0]["rule"] == "30 8 * * mon-fri" and out[0]["confidence"] >= 0.8


def test_habits_ignore_scattered_runs_few_runs_and_existing_schedules():
    scattered = [_utc_stamp_for_local(5, h, 0) for h in (1, 6, 11, 16, 21)]
    assert habits.suggest_from_runs({1: scattered}, {1: "x"}, now=NOW) == []
    assert habits.suggest_from_runs({1: scattered[:2]}, {1: "x"}, now=NOW) == []
    stamps = [_utc_stamp_for_local(d, 8, 30) for d in (5, 6, 7, 8, 9)]
    assert habits.suggest_from_runs({1: stamps}, {1: "x"}, {1: ["30 8 * * mon-fri"]}, now=NOW) == []


def test_habits_do_not_mistake_coincidence_for_a_habit():
    # 4 runs at the same hour but on 4 different days, and 3 runs on 3 weekdays: not enough evidence
    four = [_utc_stamp_for_local(d, 15, 20) for d in (7, 8, 10, 11)]
    assert habits.suggest_from_runs({1: four}, {1: "x"}, now=NOW) == []
    three = [_utc_stamp_for_local(d, 11, 10) for d in (5, 7, 8)]
    assert habits.suggest_from_runs({1: three}, {1: "x"}, now=NOW) == []
    # but the same weekday seen 3 times is a real weekly habit
    mondays = [_utc_stamp_for_local(d, 7, 45) for d in (5, 12, 19)]
    out = habits.suggest_from_runs({1: mondays}, {1: "x"}, now=NOW)
    assert out and out[0]["rule"] == "45 7 * * mon"


def test_habits_from_database_skips_skipped_runs():
    db = _db()
    rid = dal.create_recording("Daily", form_events(), db_path=db)
    with dal.connect(db) as c:
        for d in (5, 6, 7, 8, 9):
            c.execute("INSERT INTO run(recording_id, started_at, status) VALUES (?,?,?)",
                      (rid, _utc_stamp_for_local(d, 9, 0), "success"))
        c.execute("INSERT INTO run(recording_id, started_at, status) VALUES (?,?,?)",
                  (rid, _utc_stamp_for_local(9, 3, 0), "skipped"))
    out = habits.suggest_schedules(db, NOW)
    assert out and out[0]["recording_id"] == rid and out[0]["rule"].startswith("0 9 ")


# ------------------------------------------------------------------- next-action model
ROUTINE = ["click:Button.left", "type_text", "key:Key.enter"]


def test_markov_predicts_next_action_and_flags_odd_sequences():
    model = NextActionModel().fit([ROUTINE] * 6 + [ROUTINE + ["scroll"]])
    top = model.predict(["click:Button.left"])
    assert top[0][0] == "type_text" and top[0][1] > 0.8
    assert model.predict(ROUTINE)[0][0] == "</s>"
    normal = model.score(ROUTINE)
    odd = model.score(["scroll", "key:Key.esc", "scroll", "key:Key.esc"])
    assert normal["routine_score"] > odd["routine_score"]
    assert odd["surprises"]


def test_markov_handles_empty_and_unseen():
    model = NextActionModel().fit([])
    assert not model.trained and model.predict(["x"]) == []
    assert model.score([])["routine_score"] is None


# ---------------------------------------------------------------------------- similarity
def test_similarity_and_duplicates():
    docs = {1: ROUTINE * 2, 2: ROUTINE * 2, 3: ["scroll", "scroll", "key:Key.esc", "click:Button.right"], 4: ROUTINE + ["scroll"]}
    assert duplicate_groups(docs, 0.95) == [{"recording_ids": [1, 2], "similarity": 1.0}]
    ranked = similar_to(1, docs, top=3, minimum=0.1)
    assert ranked[0][0] == 2 and ranked[0][1] > ranked[-1][1]
    assert similar_to(99, docs) == []


# ---------------------------------------------------------------------------------- LLM
def _fake_transport(reply_text, seen=None):
    def transport(url, headers, payload, timeout):
        if seen is not None:
            seen.append((url, headers, payload))
        return {"content": [{"type": "text", "text": reply_text}]}
    return transport


def test_llm_is_off_until_enabled_and_keyed():
    assert not LLM("key", enabled=False).available
    assert not LLM("", enabled=True).available
    try:
        LLM("key", enabled=False).complete("s", "u")
    except LLMError:
        pass
    else:
        raise AssertionError("must refuse when off")
    db = _db()
    assert not LLM.from_settings(db).available
    dal.set_setting("ai_api_key", "sk-test-1234", db)
    assert not LLM.from_settings(db).available                 # key alone is not consent
    dal.set_setting("ai_cloud_enabled", "1", db)
    assert LLM.from_settings(db).available and LLM.from_settings(db).status()["key_hint"] == "...1234"


def test_polish_summary_sends_no_typed_text_or_coordinates():
    seen = []
    llm = LLM("k", enabled=True, transport=_fake_transport('{"name": "Greeting form", "description": "Types a greeting and submits."}', seen))
    out = polish_summary(llm, describe.summarize(form_events()))
    assert out["name"] == "Greeting form"
    sent = str(seen[0][2])
    assert "'h'" not in sent and "'x'" not in sent and "10, 20" not in sent and "Key.enter" not in sent


def test_model_schedule_is_revalidated():
    ok = LLM("k", enabled=True, transport=_fake_transport('{"rule": "0 7 * * mon"}'))
    assert schedule_from_text(ok, "first thing on mondays")["rule"] == "0 7 * * mon"
    for bad in ('{"rule": "* * * * *"}', '{"rule": "rm -rf /"}', '{"rule": null}', "no json here"):
        try:
            schedule_from_text(LLM("k", enabled=True, transport=_fake_transport(bad)), "x")
        except (nlschedule.ScheduleError, LLMError):
            continue
        raise AssertionError(f"must reject model output {bad!r}")


# ------------------------------------------------------------------------------ assistant
def _assistant_db():
    db = _db()
    dal.create_recording("Invoice export", form_events(), db_path=db)
    dal.create_recording("Morning report", form_events(), db_path=db)
    return db


def test_assistant_plays_with_slots_and_requires_confirmation():
    db = _assistant_db()
    out = Assistant(db, now=NOW).handle("play the invoice export twice at 1.5x")
    p = out["proposal"]
    assert (p["action"], p["recording_name"], p["loops"], p["speed"]) == ("play", "Invoice export", 2, 1.5)
    assert dal.list_runs(10, db) == []                            # proposing never runs anything


def test_assistant_schedules_from_plain_english():
    db = _assistant_db()
    out = Assistant(db, now=NOW).handle("schedule morning report every weekday at 8:30")
    assert out["proposal"]["rule"] == "30 8 * * mon-fri" and out["proposal"]["recording_name"] == "Morning report"
    out = Assistant(db, now=NOW).handle("schedule recording 1 every monday at 6pm")
    assert out["proposal"]["recording_id"] == 1 and out["proposal"]["rule"] == "0 18 * * mon"


def test_assistant_asks_when_unclear_and_answers_questions():
    db = _assistant_db()
    a = Assistant(db, now=NOW)
    assert a.handle("play something")["proposal"] is None
    assert "Which recording" in a.handle("play something")["reply"]
    assert "Invoice export" in a.handle("what does invoice export do?")["reply"]
    assert "No runs yet" in a.handle("how reliable are my runs?")["reply"]
    assert a.handle("find routines")["proposal"]["action"] == "mine"
    assert "2 recording" in a.handle("list my recordings")["reply"]
    assert "Try:" in a.handle("asdf qwerty")["reply"]


def test_assistant_uses_cloud_only_as_fallback_and_validates_it():
    db = _assistant_db()
    reply = '{"intent": "play", "recording_id": 2, "speed": 99, "loops": 1, "schedule_text": null}'
    llm = LLM("k", enabled=True, transport=_fake_transport(reply))
    try:
        Assistant(db, llm=llm).handle("could you do the thing from earlier")
    except ValueError:
        pass                                                      # speed 99 is rejected by validation
    else:
        raise AssertionError("out-of-range model output must be rejected")
    reply = '{"intent": "play", "recording_id": 2, "speed": 2, "loops": 1, "schedule_text": null}'
    out = Assistant(db, llm=LLM("k", enabled=True, transport=_fake_transport(reply))).handle("could you do the thing from earlier")
    assert out["source"] == "cloud" and out["proposal"]["recording_id"] == 2
    reply = '{"intent": "play", "recording_id": 999, "speed": 1, "loops": 1}'
    out = Assistant(db, llm=LLM("k", enabled=True, transport=_fake_transport(reply))).handle("do the thing")
    assert out["proposal"] is None                                # unknown ids never become proposals


def test_validate_proposal_blocks_bad_input():
    db = _assistant_db()
    bad = [{"action": "delete", "recording_id": 1}, {"action": "play", "recording_id": 99},
           {"action": "play", "recording_id": 1, "speed": 0}, {"action": "play", "recording_id": 1, "loops": 500},
           {"action": "schedule", "recording_id": 1, "rule": "* * * * *"}, {"action": "schedule", "recording_id": 1, "rule": "x"},
           {"action": "schedule", "recording_id": 1, "rule": "0 9 * * *", "retry": 99}, "play", None]
    for p in bad:
        try:
            validate_proposal(p, db)
        except ValueError:
            continue
        raise AssertionError(f"should reject {p!r}")
    assert validate_proposal({"action": "schedule", "recording_id": 1, "rule": "0 9 * * *"}, db)["human"] == "Every day at 09:00"


def test_slot_extraction():
    assert extract_speed("go 2x") == 2.0 and extract_speed("at half speed") == 0.5 and extract_speed("plain") == 1.0
    assert extract_loops("5 times") == 5 and extract_loops("twice") == 2 and extract_loops("run it") == 1
