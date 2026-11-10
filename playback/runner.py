"""Runs a stored recording and writes a run record (success / failed / stopped / skipped)."""
import threading

from db import dal
from playback.player import Player

_lock = threading.Lock()          # only one playback at a time (prevents overlap)
_current_stop = None


def request_stop():
    """Ask the running playback (if any) to stop. Returns True if one was running."""
    if _current_stop is not None:
        _current_stop.set()
        return True
    return False


def run_recording(rid, speed=1.0, loops=1, retries=0, db_path=None, player=None, stop_event=None):
    global _current_stop
    run_id = dal.start_run(rid, db_path)
    if not _lock.acquire(blocking=False):
        dal.finish_run(run_id, "skipped", "another playback is already running", db_path)
        return run_id
    stop = stop_event or threading.Event()
    _current_stop = stop
    try:
        events = dal.get_events(rid, db_path)
        if not events:
            dal.finish_run(run_id, "failed", "recording has no events", db_path)
            return run_id
        player = player or Player()
        last_error = ""
        for attempt in range(retries + 1):
            try:
                stats = player.play(events, speed=speed, loops=loops, stop_event=stop)
            except Exception as exc:                  # retry on failure
                last_error = f"attempt {attempt + 1}: {exc}"
                continue
            status = "stopped" if stats["stopped"] else "success"
            message = (f"played {stats['played']} events, skipped {stats['skipped_protected']} "
                       f"protected, loops {stats['loops_done']}")
            dal.finish_run(run_id, status, message, db_path)
            return run_id
        dal.finish_run(run_id, "failed", last_error, db_path)
        return run_id
    finally:
        _current_stop = None
        _lock.release()
