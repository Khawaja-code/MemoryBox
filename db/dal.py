"""Data access layer (Phase 2). No other module writes raw SQL."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_DB = HERE / "memorybox.db"
SCHEMA_FILE = HERE / "schema.sql"


@contextmanager
def connect(db_path=None):
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    with connect(db_path) as c:
        c.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- recordings
def create_recording(name, events, folder="", db_path=None):
    """events: list of dicts with type, x, y, key, button, scroll, t_offset_ms."""
    duration = max((e["t_offset_ms"] for e in events), default=0)
    with connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO recording(name, folder, duration_ms, event_count) VALUES (?,?,?,?)",
            (name, folder, duration, len(events)),
        )
        rid = cur.lastrowid
        c.executemany(
            'INSERT INTO event(recording_id, seq, type, x, y, "key", button, scroll, t_offset_ms) '
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (rid, i, e["type"], e.get("x"), e.get("y"), e.get("key"),
                 e.get("button"), e.get("scroll"), e["t_offset_ms"])
                for i, e in enumerate(events)
            ],
        )
    return rid


def get_events(rid, db_path=None):
    with connect(db_path) as c:
        rows = c.execute(
            'SELECT type, x, y, "key", button, scroll, t_offset_ms '
            "FROM event WHERE recording_id=? ORDER BY seq",
            (rid,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recording(rid, db_path=None):
    with connect(db_path) as c:
        row = c.execute("SELECT * FROM recording WHERE id=?", (rid,)).fetchone()
    return dict(row) if row else None


def list_recordings(folder=None, db_path=None):
    with connect(db_path) as c:
        if folder is None:
            rows = c.execute("SELECT * FROM recording ORDER BY created_at DESC, id DESC").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM recording WHERE folder=? ORDER BY created_at DESC, id DESC", (folder,)
            ).fetchall()
    return [dict(r) for r in rows]


def rename_recording(rid, name, db_path=None):
    with connect(db_path) as c:
        c.execute("UPDATE recording SET name=? WHERE id=?", (name, rid))


def move_recording(rid, folder, db_path=None):
    with connect(db_path) as c:
        c.execute("UPDATE recording SET folder=? WHERE id=?", (folder, rid))


def duplicate_recording(rid, db_path=None):
    rec = get_recording(rid, db_path)
    if rec is None:
        raise KeyError(f"recording {rid} not found")
    return create_recording(rec["name"] + " (copy)", get_events(rid, db_path), rec["folder"], db_path)


def delete_recording(rid, db_path=None):
    """Also deletes the recording's events, schedules and run history (ON DELETE CASCADE)."""
    with connect(db_path) as c:
        c.execute("DELETE FROM recording WHERE id=?", (rid,))


# ----------------------------------------------------------------- schedules
def add_schedule(rid, rule, retry=0, db_path=None):
    with connect(db_path) as c:
        cur = c.execute(
            "INSERT INTO schedule(recording_id, rule, retry) VALUES (?,?,?)", (rid, rule, retry)
        )
        return cur.lastrowid


def list_schedules(active_only=False, db_path=None):
    sql = "SELECT * FROM schedule" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    with connect(db_path) as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


def update_next_run(sid, next_run, db_path=None):
    with connect(db_path) as c:
        c.execute("UPDATE schedule SET next_run=? WHERE id=?", (next_run, sid))


def set_schedule_active(sid, active, db_path=None):
    with connect(db_path) as c:
        c.execute("UPDATE schedule SET active=? WHERE id=?", (1 if active else 0, sid))


# ---------------------------------------------------------------------- runs
def start_run(rid, db_path=None):
    with connect(db_path) as c:
        return c.execute("INSERT INTO run(recording_id) VALUES (?)", (rid,)).lastrowid


def finish_run(run_id, status, message="", db_path=None):
    with connect(db_path) as c:
        c.execute("UPDATE run SET status=?, message=? WHERE id=?", (status, message, run_id))


def list_runs(limit=100, db_path=None):
    with connect(db_path) as c:
        rows = c.execute("SELECT * FROM run ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ routines
def add_routine(pattern, support, db_path=None):
    with connect(db_path) as c:
        return c.execute(
            "INSERT INTO routine(pattern, support) VALUES (?,?)", (json.dumps(pattern), support)
        ).lastrowid


def list_routines(db_path=None):
    with connect(db_path) as c:
        rows = c.execute("SELECT * FROM routine ORDER BY support DESC, id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["pattern"] = json.loads(d["pattern"])
        out.append(d)
    return out


# ------------------------------------------------------------ remote log
def log_remote(client, action, granted, db_path=None):
    with connect(db_path) as c:
        c.execute(
            "INSERT INTO remote_access_log(client, action, granted) VALUES (?,?,?)",
            (client, action, 1 if granted else 0),
        )


def list_remote_log(limit=100, db_path=None):
    with connect(db_path) as c:
        rows = c.execute("SELECT * FROM remote_access_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ settings
def set_setting(key, value, db_path=None):
    with connect(db_path) as c:
        c.execute(
            'INSERT INTO setting("key", value) VALUES (?, ?) '
            'ON CONFLICT("key") DO UPDATE SET value=excluded.value',
            (key, value),
        )


def load_setting(key, default=None, db_path=None):
    with connect(db_path) as c:
        row = c.execute('SELECT value FROM setting WHERE "key"=?', (key,)).fetchone()
    return row["value"] if row else default
