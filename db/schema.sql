-- MemoryBox local database (Phase 2). Tables follow the guide.
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS recording (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    folder      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    duration_ms INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0
);

-- Kept lean: this table holds the most rows.
CREATE TABLE IF NOT EXISTS event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    type         TEXT    NOT NULL,   -- click | scroll | key_down | key_up | protected | device
    x            INTEGER,
    y            INTEGER,
    "key"        TEXT,               -- 'a', 'Key.enter', or device detail for type=device
    button       TEXT,               -- 'Button.left'
    scroll       TEXT,               -- 'dx,dy'
    t_offset_ms  INTEGER NOT NULL    -- ms since the recording started
);
CREATE INDEX IF NOT EXISTS idx_event_rec ON event(recording_id, seq);

CREATE TABLE IF NOT EXISTS schedule (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
    rule         TEXT    NOT NULL,   -- 5-field cron expression, e.g. '30 9 * * mon'
    next_run     TEXT,
    retry        INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS run (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recording(id) ON DELETE CASCADE,
    started_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    status       TEXT    NOT NULL DEFAULT 'running',  -- running | success | failed | stopped | skipped
    message      TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS routine (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern    TEXT    NOT NULL,     -- JSON list of action tokens
    support    INTEGER NOT NULL,
    suggested  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS remote_access_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    client  TEXT,
    action  TEXT,
    granted INTEGER NOT NULL,
    at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS setting (
    "key" TEXT PRIMARY KEY,
    value TEXT
);
