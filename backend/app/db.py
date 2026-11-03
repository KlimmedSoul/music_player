import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from functools import wraps

from .config import settings

_lock = threading.RLock()
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
    tracks_loaded INTEGER NOT NULL DEFAULT 0,
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
    expires_at REAL NOT NULL,
    content_type TEXT
);

-- выданные токены на /stream/<token> (защита от открытого прокси)
CREATE INDEX IF NOT EXISTS idx_url_expiry ON url_cache(expires_at);
CREATE TABLE IF NOT EXISTS issued_streams (
    token TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stream_created ON issued_streams(created_at);

-- рантайм-токены (spotify oauth) и служебные значения
CREATE TABLE IF NOT EXISTS store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- глобальные любимые треки поверх библиотек сервисов
CREATE TABLE IF NOT EXISTS favorites (
    dedup_key TEXT NOT NULL,
    track_source TEXT NOT NULL,
    track_ext TEXT NOT NULL,
    added_at REAL NOT NULL,
    UNIQUE (track_source, track_ext)
);

-- прослушивания: «недавние» и основа для умных плейлистов в будущем
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY,
    track_source TEXT NOT NULL,
    track_ext TEXT NOT NULL,
    played_at REAL NOT NULL,
    position_s REAL NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_plays_time ON plays(played_at DESC);

-- на чём остановился: «продолжить слушать» с любого устройства
CREATE TABLE IF NOT EXISTS positions (
    track_source TEXT NOT NULL,
    track_ext TEXT NOT NULL,
    position_s REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (track_source, track_ext)
);
"""


def synchronized(func):
    """Serialize synchronous access to the one process-wide SQLite connection."""
    @wraps(func)
    def call(*args, **kwargs):
        with _lock:
            return func(*args, **kwargs)
    return call


@contextmanager
def transaction():
    # Never hold this lock across an await or a provider/network request.
    with _lock:
        with db() as conn:
            yield conn


def transactional(func):
    @wraps(func)
    def call(*args, **kwargs):
        with transaction():
            return func(*args, **kwargs)
    return call


@synchronized
def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            if "tracks_loaded" not in {row[1] for row in conn.execute("PRAGMA table_info(playlists)")}:
                conn.execute("ALTER TABLE playlists ADD COLUMN tracks_loaded INTEGER NOT NULL DEFAULT 0")
            if "content_type" not in {row[1] for row in conn.execute("PRAGMA table_info(url_cache)")}:
                conn.execute("ALTER TABLE url_cache ADD COLUMN content_type TEXT")
            conn.commit()
        except Exception:
            conn.close()
            raise
        _conn = conn
    return _conn


def now() -> float:
    return time.time()


def store_set(key: str, value) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO store(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


@synchronized
def fetch_one(sql: str, params=()):
    return db().execute(sql, params).fetchone()


def store_get(key: str, default=None):
    row = fetch_one("SELECT value FROM store WHERE key=?", (key,))
    return json.loads(row["value"]) if row else default
