"""Core tests (Phase 9). Run with `pytest` from the project root. Uses only fakes, so no
real mouse or keyboard is touched."""
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from capture.keys import key_to_str
from capture.recorder import Recorder
from db import dal
from intelligence.miner import (events_to_actions, find_first_occurrence, mine_routines)
from playback.player import Player
from playback.runner import run_recording
from scheduler.scheduler import cron_daily, cron_monthly, cron_weekly, cron_yearly, validate_cron


def _db():
    path = Path(tempfile.mkdtemp()) / "t.db"
    dal.init_db(path)
    return path


def ev(type_, t, **kw):
    base = dict(type=type_, x=None, y=None, key=None, button=None, scroll=None, t_offset_ms=t)
    base.update(kw)
    return base


SAMPLE = [
    ev("click", 0, x=10, y=20, button="Button.left"),
    ev("key_down", 100, key="h"), ev("key_up", 120, key="h"),
    ev("key_down", 200, key="i"), ev("key_up", 220, key="i"),
    ev("scroll", 400, x=10, y=20, scroll="0,-2"),
]


# ------------------------------------------------------------------ Phase 2
def test_db_roundtrip_and_manager_functions():
    db = _db()
    rid = dal.create_recording("demo", SAMPLE, db_path=db)
    assert dal.get_events(rid, db) == SAMPLE                 # order and values preserved
    rec = dal.get_recording(rid, db)
    assert rec["event_count"] == len(SAMPLE) and rec["duration_ms"] == 400
    copy = dal.duplicate_recording(rid, db)
    assert dal.get_recording(copy, db)["name"] == "demo (copy)"
    dal.rename_recording(rid, "renamed", db); dal.move_recording(rid, "work", db)
    assert dal.get_recording(rid, db)["name"] == "renamed"
    assert [r["id"] for r in dal.list_recordings("work", db)] == [rid]
    dal.delete_recording(rid, db)
    assert dal.get_events(rid, db) == []                      # events deleted with it (cascade)


def test_settings_and_remote_log():
    db = _db()
    assert dal.load_setting("hermes_token", None, db) is None
    dal.set_setting("hermes_token", "a", db); dal.set_setting("hermes_token", "b", db)
    assert dal.load_setting("hermes_token", None, db) == "b"
    dal.log_remote("1.2.3.4", "POST /x", False, db)
    assert dal.list_remote_log(10, db)[0]["granted"] == 0


# ------------------------------------------------------------------ Phase 3
def test_recorder_orders_events_and_never_stores_password_keys():
    now = [0.0]
    protected = [False]
    rec = Recorder(protected_check=lambda: protected[0], clock=lambda: now[0])
    rec._begin_session()
    now[0] = 1.0; rec.on_click(5, 6, "Button.left", True)
    now[0] = 1.5; rec.on_press(SimpleNamespace(char="a"))
    protected[0] = True                                       # focus moves to a password box
    now[0] = 2.0
    for ch in "secret":
        rec.on_press(SimpleNamespace(char=ch)); rec.on_release(SimpleNamespace(char=ch))
    protected[0] = False
    events = rec._events
    assert [e["type"] for e in events] == ["click", "key_down", "protected"]
    assert [e["t_offset_ms"] for e in events] == [1000, 1500, 2000]
    assert all(e["key"] != s for e in events for s in "secret")  # no password character stored


def test_recorder_pause_removes_paused_time():
    now = [0.0]
    rec = Recorder(protected_check=lambda: False, clock=lambda: now[0])
    rec._begin_session()
    now[0] = 1.0; rec.pause()
    now[0] = 11.0; rec.resume()                               # 10 s paused
    now[0] = 12.0; rec.on_click(1, 1, "Button.left", True)
    assert rec._events[0]["t_offset_ms"] == 2000              # 12 s on the clock minus 10 s paused


def test_key_to_str():
    assert key_to_str(SimpleNamespace(char="a")) == "a"
    assert key_to_str(SimpleNamespace(name="enter")) == "Key.enter"
    assert key_to_str(SimpleNamespace(char=None, vk=65)) == "vk:65"


# ------------------------------------------------------------------ Phase 5
class FakeMouse:
    def __init__(self): self.log = []; self._pos = None
    @property
    def position(self): return self._pos
    @position.setter
    def position(self, v): self._pos = v
    def click(self, b): self.log.append(("click", self._pos, b))
    def scroll(self, dx, dy): self.log.append(("scroll", dx, dy))


class FakeKeyboard:
    def __init__(self): self.log = []
    def press(self, k): self.log.append(("down", k))
    def release(self, k): self.log.append(("up", k))


def _player(sleeps, protected=lambda: False):
    return Player(FakeMouse(), FakeKeyboard(), sleep=sleeps.append, protected_check=protected,
                  resolve_key=lambda s: s, resolve_button=lambda s: s)


def test_playback_timing_speed_and_actions():
    sleeps = []; p = _player(sleeps)
    stats = p.play(SAMPLE, speed=2.0)
    assert stats["played"] == len(SAMPLE)
    assert sleeps == [0.05, 0.01, 0.04, 0.01, 0.09]           # gaps halved by speed=2
    assert p._keyboard.log[:2] == [("down", "h"), ("up", "h")]
    assert p._mouse.log[0] == ("click", (10, 20), "Button.left")


def test_playback_loops():
    p = _player([]); stats = p.play(SAMPLE, loops=3)
    assert stats["loops_done"] == 3 and stats["played"] == 3 * len(SAMPLE)


def test_playback_never_types_into_password_field():
    p = _player([], protected=lambda: True)
    stats = p.play(SAMPLE + [ev("protected", 500)])
    assert p._keyboard.log == [] and stats["skipped_protected"] == 5


def test_playback_stop_event():
    stop = threading.Event(); stop.set()
    assert _player([]).play(SAMPLE, stop_event=stop)["stopped"] is True


def test_runner_logs_success_failure_and_retries():
    db = _db(); rid = dal.create_recording("r", SAMPLE, db_path=db)
    run_id = run_recording(rid, db_path=db, player=_player([]))
    assert dal.list_runs(5, db)[0]["status"] == "success"

    class Flaky:
        calls = 0
        def play(self, *a, **k):
            Flaky.calls += 1
            if Flaky.calls < 3: raise RuntimeError("boom")
            return {"played": 1, "skipped_protected": 0, "loops_done": 1, "stopped": False}
    run_recording(rid, retries=2, db_path=db, player=Flaky())
    assert Flaky.calls == 3 and dal.list_runs(1, db)[0]["status"] == "success"

    class Broken:
        def play(self, *a, **k): raise RuntimeError("always")
    run_recording(rid, retries=1, db_path=db, player=Broken())
    last = dal.list_runs(1, db)[0]
    assert last["status"] == "failed" and "always" in last["message"]


# ------------------------------------------------------------------ Phase 6
def test_cron_builders():
    assert cron_daily(9, 30) == "30 9 * * *"
    assert cron_weekly("Monday", 8, 0) == "0 8 * * mon"
    assert cron_monthly(1, 7, 15) == "15 7 1 * *"
    assert cron_yearly(12, 25, 6, 0) == "0 6 25 12 *"
    for bad in ("* * *", ""):
        try: validate_cron(bad)
        except ValueError: pass
        else: raise AssertionError("should reject")


# ------------------------------------------------------------------ Phase 7
def test_miner_recovers_seeded_routine():
    # The routine appears in all 6 sessions; the surrounding noise differs each time,
    # as it would in real use. (Identical noise in every session would itself be
    # reported as part of the routine, which is correct behaviour.)
    routine = ["click:Button.left", "type_text", "key:Key.enter", "scroll"]
    noises = [["key:Key.tab"], ["scroll", "click:Button.right"], [], ["key:Key.esc"],
              ["click:Button.right"] * 2, ["key:Key.tab", "key:Key.esc"]]
    seqs = [n + routine + n[::-1] for n in noises]
    found = mine_routines(seqs, min_support=5, min_len=3)
    assert found and found[0][0] == 6 and found[0][1] == routine


def test_events_to_actions_and_extract_occurrence():
    events = SAMPLE + [ev("key_down", 500, key="Key.enter"), ev("key_up", 510, key="Key.enter")]
    assert events_to_actions(events) == ["click:Button.left", "type_text", "scroll", "key:Key.enter"]
    chunk = find_first_occurrence(["type_text", "scroll"], events)
    assert chunk[0]["t_offset_ms"] == 0 and chunk[-1]["type"] == "scroll"
