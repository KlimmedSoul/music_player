import json
import sqlite3
import threading
import time

from .config import settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    dedup_key TEXT NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    album TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    isrc TEXT,
    cover TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    added_at REAL NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_tracks_dedup ON tracks(dedup_key);
CREATE INDEX IF NOT EXISTS idx_tracks_added ON tracks(added_at DESC);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    cover TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    synced_at REAL NOT NULL,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_source TEXT NOT NULL,
    playlist_ext TEXT NOT NULL,
    track_source TEXT NOT NULL,
    track_ext TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (playlist_source, playlist_ext, track_source, track_ext, position)
);

-- кэш разрешённых stream-URL: source|ext -> url, expire
CREATE TABLE IF NOT EXISTS url_cache (
    key TEXT PRIMARY KEY,           -- source|external_id
    stream_url TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'proxy',   -- proxy | direct
    expires_at REAL NOT NULL
);

-- выданные токены на /stream/<token> (защита от открытого прокси)
CREATE TABLE IF NOT EXISTS issued_streams (
    token TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    created_at REAL NOT NULL
);

-- рантайм-токены (spotify oauth) и служебные значения
CREATE TABLE IF NOT EXISTS store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        with _lock:
            _conn.executescript(SCHEMA)
            _conn.commit()
    return _conn


def now() -> float:
    return time.time()


def store_set(key: str, value) -> None:
    with _lock:
        db().execute(
            "INSERT INTO store(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        db().commit()


def store_get(key: str, default=None):
    row = db().execute("SELECT value FROM store WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default
