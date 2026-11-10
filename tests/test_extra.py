"""Tests for reports, routine saving and the playback emergency stop."""
import tempfile
from pathlib import Path

from db import dal
from intelligence.miner import save_routine_as_recording
from playback.player import Player
from playback.runner import run_recording
from reports.report import format_report, log_view, run_report


def _db():
    path = Path(tempfile.mkdtemp()) / "t.db"
    dal.init_db(path)
    return path


def ev(type_, t, **kw):
    base = dict(type=type_, x=None, y=None, key=None, button=None, scroll=None, t_offset_ms=t)
    base.update(kw)
    return base


SAMPLE = [ev("click", 0, x=10, y=20, button="Button.left"),
          ev("key_down", 100, key="h"), ev("key_up", 120, key="h"),
          ev("scroll", 400, x=10, y=20, scroll="0,-2")]


class FakeMouse:
    def __init__(self, pos=None): self.position = pos
    def click(self, b): pass
    def scroll(self, dx, dy): pass


class FakeKeyboard:
    def press(self, k): pass
    def release(self, k): pass


def test_report_counts_runs_and_remote_calls():
    db = _db()
    rid = dal.create_recording("r", SAMPLE, db_path=db)
    ok = Player(FakeMouse(), FakeKeyboard(), sleep=lambda s: None, protected_check=lambda: False,
                resolve_key=lambda s: s, resolve_button=lambda s: s)

    class Broken:
        def play(self, *a, **k): raise RuntimeError("target window closed")

    run_recording(rid, db_path=db, player=ok)
    run_recording(rid, db_path=db, player=Broken())
    dal.log_remote("1.2.3.4", "POST /api/remote/run/1", False, db)
    rep = run_report(db)
    assert rep["total_runs"] == 2 and rep["by_status"] == {"success": 1, "failed": 1}
    assert rep["success_rate_percent"] == 50.0
    assert rep["recent_failures"][0]["message"].endswith("target window closed")
    assert rep["remote_calls"] == 1 and rep["remote_refused"] == 1
    assert "Success rate:      50.0%" in format_report(rep)
    assert len(log_view("runs", 10, db)) == 2 and len(log_view("remote", 10, db)) == 1


def test_report_with_no_runs():
    rep = run_report(_db())
    assert rep["success_rate_percent"] is None and "n/a" in format_report(rep)


def test_save_routine_as_recording():
    db = _db()
    events = [ev("click", 0, x=1, y=1, button="Button.left"),
              ev("key_down", 50, key="a"), ev("key_down", 90, key="b"),
              ev("key_down", 200, key="Key.enter"),
              ev("scroll", 400, x=1, y=1, scroll="0,-1")]
    dal.create_recording("session", events, db_path=db)
    pattern = ["click:Button.left", "type_text", "key:Key.enter"]
    routine_id = dal.add_routine(pattern, 5, db)
    new_id = save_routine_as_recording(routine_id, "Saved routine", db)
    saved = dal.get_events(new_id, db)
    assert saved[0]["t_offset_ms"] == 0 and [e["type"] for e in saved] == ["click", "key_down", "key_down", "key_down"]
    assert dal.get_recording(new_id, db)["name"] == "Saved routine"
    missing = dal.add_routine(["scroll", "scroll", "scroll"], 5, db)
    assert save_routine_as_recording(missing, db_path=db) is None


def test_failsafe_corner_stops_playback():
    p = Player(FakeMouse(pos=(0, 0)), FakeKeyboard(), sleep=lambda s: None, protected_check=lambda: False,
               resolve_key=lambda s: s, resolve_button=lambda s: s)
    stats = p.play(SAMPLE)
    assert stats["stopped"] is True and stats["played"] == 0


def test_failsafe_can_be_disabled():
    p = Player(FakeMouse(pos=(0, 0)), FakeKeyboard(), sleep=lambda s: None, protected_check=lambda: False,
               resolve_key=lambda s: s, resolve_button=lambda s: s, failsafe=False)
    assert p.play(SAMPLE)["played"] == len(SAMPLE)
