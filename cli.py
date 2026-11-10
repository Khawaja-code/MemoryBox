"""Command line front end, so every phase can be tried from the VS Code terminal.

    python cli.py doctor                 check the setup first
    python cli.py init                   create the database
    python cli.py record "My routine"    Enter stops and saves
    python cli.py list
    python cli.py play 1 --speed 1.5 --loops 2
    python cli.py rename 1 "New name" | move 1 "Work" | duplicate 1 | delete 1
    python cli.py mine --min-support 3   find repeated routines
    python cli.py save-routine 1         turn a suggested routine into a recording
    python cli.py report
    python cli.py serve                  API + dashboard at http://127.0.0.1:8765

  AI helpers (work offline; no API key needed):
    python cli.py describe 1             plain-English summary, routine score, advice for recording 1
    python cli.py when "every weekday at 9am"   turn words into a schedule and preview the next runs
    python cli.py suggest                schedule ideas from your habits + duplicate recordings
    python cli.py ask "schedule morning report every monday at 8"   Copilot (shows a proposal; confirm in the dashboard)

EMERGENCY STOP during playback: move the mouse to the top-left corner of the screen.
"""
import argparse
import sys
import time

from db import dal


def doctor():
    import importlib
    import platform

    win = sys.platform == "win32"
    print(f"Python {sys.version.split()[0]} on {platform.system()}")
    if sys.version_info[:2] != (3, 12):
        print("  note: the guide uses Python 3.12; this version may still work.")
    checks = [
        ("pynput", "record and replay input", True),
        ("apscheduler", "scheduler", True),
        ("fastapi", "local API", True),
        ("uvicorn", "API server", True),
        ("prefixspan", "pattern mining (a simple fallback is used without it)", False),
        ("uiautomation", "password-field protection (Windows)", win),
        ("wmi", "device monitor (Windows)", win),
        ("pythoncom", "device monitor, from pywin32 (Windows)", win),
    ]
    for module, purpose, needed in checks:
        try:
            importlib.import_module(module)
            print(f"  OK       {module:<13} {purpose}")
        except Exception:
            print(f"  {'MISSING ' if needed else 'optional'} {module:<13} {purpose}")
    from capture.safety import is_protected_focus
    protected = is_protected_focus()
    print(f"\nPassword-field check says 'protected' = {protected} right now.")
    if protected:
        print("  If you are NOT in a password box, keyboard keys will not be recorded or replayed.\n"
              "  On Windows run: pip install uiautomation   (mouse actions still work either way)")
    dal.init_db()
    print(f"Database: {dal.DEFAULT_DB}")


def main():
    p = argparse.ArgumentParser(prog="memorybox")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("init")
    r = sub.add_parser("record"); r.add_argument("name")
    sub.add_parser("list")
    pl = sub.add_parser("play"); pl.add_argument("id", type=int)
    pl.add_argument("--speed", type=float, default=1.0)
    pl.add_argument("--loops", type=int, default=1)
    pl.add_argument("--delay", type=int, default=3, help="seconds to wait before starting")
    rn = sub.add_parser("rename"); rn.add_argument("id", type=int); rn.add_argument("name")
    mv = sub.add_parser("move"); mv.add_argument("id", type=int); mv.add_argument("folder")
    du = sub.add_parser("duplicate"); du.add_argument("id", type=int)
    de = sub.add_parser("delete"); de.add_argument("id", type=int)
    m = sub.add_parser("mine"); m.add_argument("--min-support", type=int, default=5)
    sr = sub.add_parser("save-routine"); sr.add_argument("id", type=int)
    sub.add_parser("report")
    sub.add_parser("serve")
    ds = sub.add_parser("describe"); ds.add_argument("id", type=int)
    wn = sub.add_parser("when"); wn.add_argument("text")
    sub.add_parser("suggest")
    ak = sub.add_parser("ask"); ak.add_argument("message")
    a = p.parse_args()

    if a.cmd == "doctor":
        doctor()
    elif a.cmd == "init":
        dal.init_db(); print("Database ready.")
    elif a.cmd == "record":
        from capture.recorder import record_from_terminal
        dal.init_db(); record_from_terminal(a.name)
    elif a.cmd == "list":
        dal.init_db()
        rows = dal.list_recordings()
        if not rows:
            print("No recordings yet. Try: python cli.py record \"My routine\"")
        for r in rows:
            folder = f"[{r['folder']}] " if r["folder"] else ""
            print(f"{r['id']:>3}  {folder}{r['name']:<28} {r['event_count']:>5} events  "
                  f"{r['duration_ms']} ms  {r['created_at']}")
    elif a.cmd == "play":
        from playback.runner import run_recording
        dal.init_db()
        print(f"Playback starts in {a.delay} s. Emergency stop: move the mouse to the top-left corner.")
        time.sleep(a.delay)
        run_id = run_recording(a.id, speed=a.speed, loops=a.loops)
        print([r for r in dal.list_runs() if r["id"] == run_id][0])
    elif a.cmd == "rename":
        dal.rename_recording(a.id, a.name); print("Renamed.")
    elif a.cmd == "move":
        dal.move_recording(a.id, a.folder); print("Moved.")
    elif a.cmd == "duplicate":
        print("Created recording", dal.duplicate_recording(a.id))
    elif a.cmd == "delete":
        dal.delete_recording(a.id); print("Deleted.")
    elif a.cmd == "mine":
        from intelligence.miner import run_miner
        dal.init_db()
        found = run_miner(min_support=a.min_support)
        if not found:
            print("No routines found. Record the same task several times, or lower --min-support.")
        for support, pattern in found:
            print(f"seen {support}x: " + " > ".join(pattern))
    elif a.cmd == "save-routine":
        from intelligence.miner import save_routine_as_recording
        rid = save_routine_as_recording(a.id)
        print(f"Saved as recording {rid}." if rid else "No contiguous example of that routine was found.")
    elif a.cmd == "report":
        from reports.report import format_report, run_report
        dal.init_db(); print(format_report(run_report()))
    elif a.cmd == "describe":
        from ai.service import recording_insights
        dal.init_db()
        try:
            i = recording_insights(a.id)
        except KeyError:
            print(f"No recording {a.id}."); return
        print(f"Suggested name: {i['suggested_name']}\n{i['description']}\nSteps: " + " > ".join(i["steps"]))
        if i["routine_score"]:
            print(f"Routine score: {i['routine_score']['routine_score']}/100 (how typical this is of your other recordings)")
        r = i["reliability"]
        print(f"Reliability: {r['label']} ({r['runs']} runs)")
        for tip in i["advice"]:
            print(f"  [{tip['level']}] {tip['text']}")
    elif a.cmd == "when":
        from ai import nlschedule
        try:
            r = nlschedule.parse_schedule(a.text)
        except nlschedule.ScheduleError as exc:
            print(exc); return
        print(f"{r['human']}\n  rule: {r['rule']}")
        for n in r["notes"]:
            print("  note:", n)
        print("  next runs: " + ", ".join(x.replace("T", " ") for x in r["next_runs"]))
    elif a.cmd == "suggest":
        from ai.service import overview
        dal.init_db()
        o = overview()
        if not o["schedule_suggestions"] and not o["duplicates"]:
            print("Nothing to suggest yet. Play your recordings at consistent times and I will spot the pattern.")
        for s_ in o["schedule_suggestions"]:
            print(f"Schedule '{s_['recording_name']}' {s_['human']}  (confidence {int(s_['confidence'] * 100)}%)\n  {s_['evidence']}")
        for g in o["duplicates"]:
            print(f"Very similar ({int(g['similarity'] * 100)}% alike): " + ", ".join(g["names"]))
    elif a.cmd == "ask":
        from ai.assistant import Assistant
        from ai.llm import LLM
        dal.init_db()
        out = Assistant(llm=LLM.from_settings()).handle(a.message)
        print(out["reply"])
        if out["proposal"]:
            print(f"Proposal: {out['proposal']['summary']}\n(Confirm it in the dashboard: python cli.py serve)")
    elif a.cmd == "serve":
        import uvicorn
        print("Dashboard: http://127.0.0.1:8765   API docs: http://127.0.0.1:8765/docs")
        uvicorn.run("api.main:app", host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
