"""Единая библиотека: upsert треков/плейлистов, дедуп, выдача, resolve+кэш."""
import json
import uuid

import httpx

from .connectors.base import SourceUnavailable, Track, dedup_key
from .db import db, now
from .connectors import SoundCloudConnector, SpotifyConnector, VKConnector, YouTubeMusicConnector

CONNECTORS = {
    "vk": VKConnector(),
    "soundcloud": SoundCloudConnector(),
    "spotify": SpotifyConnector(),
    "ytmusic": YouTubeMusicConnector(),
}


async def refresh(client: httpx.AsyncClient, source: str | None = None) -> dict:
    """Обновление библиотеки. Возвращает статус по каждому источнику."""
    result = {}
    names = [source] if source else list(CONNECTORS)
    for name in names:
        conn = CONNECTORS.get(name)
        if not conn or not conn.is_configured():
            result[name] = "not_configured"
            continue
        try:
            tracks, playlists = await conn.fetch_library(client)
            with db():
                for t in tracks:
                    db().execute(
                        """INSERT INTO tracks(source,external_id,dedup_key,title,artist,
                           album,duration_ms,isrc,cover,meta,added_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(source,external_id) DO UPDATE SET
                             title=excluded.title, artist=excluded.artist,
                             album=excluded.album, duration_ms=excluded.duration_ms,
                             isrc=excluded.isrc, cover=excluded.cover,
                             meta=excluded.meta""",
                        (
                            t.source, t.external_id,
                            dedup_key(t.title, t.artist, t.duration_ms),
                            t.title, t.artist, t.album, t.duration_ms, t.isrc,
                            t.cover, json.dumps(t.meta), now(),
                        ),
                    )
                for pl in playlists:
                    db().execute(
                        """INSERT INTO playlists(source,external_id,title,cover,total,synced_at)
                           VALUES(?,?,?,?,?,?)
                           ON CONFLICT(source,external_id) DO UPDATE SET
                             title=excluded.title, cover=excluded.cover,
                             total=excluded.total, synced_at=excluded.synced_at""",
                        (pl.source, pl.external_id, pl.title, pl.cover, pl.total, now()),
                    )
                    db().execute(
                        "DELETE FROM playlist_items WHERE playlist_source=? AND playlist_ext=?",
                        (pl.source, pl.external_id),
                    )
                    for i, tid in enumerate(pl.track_ids):
                        db().execute(
                            "INSERT OR IGNORE INTO playlist_items VALUES(?,?,?,?,?)",
                            (pl.source, pl.external_id, pl.source, tid, i),
                        )
                db().commit()
            result[name] = f"ok: {len(tracks)} треков, {len(playlists)} плейлистов"
        except SourceUnavailable as e:
            result[name] = f"error: {e}"
        except Exception as e:
            result[name] = f"error: {e.__class__.__name__}: {e}"
    from .db import store_set

    store_set("last_refresh", {"ts": now(), "sources": result})
    return result


def _row_to_dict(r, sources=None):
    d = dict(r)
    d["meta"] = json.loads(d.get("meta") or "{}")
    if sources is not None:
        d["sources"] = sources
    return d


def list_tracks(q: str = "", source: str = "all", cursor: int = 0, limit: int = 50):
    conn = db()
    sql = "SELECT * FROM tracks"
    params: list = []
    where = []
    if q:
        where.append("(lower(title) LIKE ? OR lower(artist) LIKE ?)")
        params += [f"%{q.lower()}%", f"%{q.lower()}%"]
    if source != "all":
        where.append("source=?")
        params.append(source)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # группировка дублей между сервисами
    sql += " GROUP BY dedup_key ORDER BY MAX(added_at) DESC LIMIT ? OFFSET ?"
    params += [limit, cursor]
    keys = [r[0] for r in conn.execute(
        sql.replace("SELECT *", "SELECT dedup_key"), params)]
    items = []
    for key in keys:
        rows = conn.execute(
            "SELECT * FROM tracks WHERE dedup_key=? ORDER BY source", (key,)
        ).fetchall()
        primary = next((r for r in rows if r["source"] == source), rows[0])
        d = _row_to_dict(primary)
        # Prefer the selected source, but use another copy's cover if it is absent.
        d["cover"] = d.get("cover") or next((r["cover"] for r in rows if r["cover"]), None)
        d["sources"] = [r["source"] for r in rows]
        items.append(d)
    return {"items": items, "next_cursor": cursor + limit}


def list_playlists():
    rows = db().execute(
        "SELECT * FROM playlists ORDER BY source, title"
    ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows]}


def playlist_tracks(source: str, ext: str):
    conn = db()
    rows = conn.execute(
        """SELECT t.* FROM playlist_items pi
           JOIN tracks t ON t.source=pi.track_source AND t.external_id=pi.track_ext
           WHERE pi.playlist_source=? AND pi.playlist_ext=?
           ORDER BY pi.position""",
        (source, ext),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


async def ensure_playlist_tracks(client: httpx.AsyncClient, source: str, ext: str):
    """Если состава плейлиста в базе нет — тяаем его у коннектора, кладём
    в tracks и прописываем playlist_items. Возвращаем список треков."""
    items = playlist_tracks(source, ext)
    if items:
        return items
    conn = CONNECTORS.get(source)
    if not conn or not hasattr(conn, "fetch_playlist_tracks"):
        raise SourceUnavailable(f"{source}: плейлисты не поддерживаются")
    tracks = await conn.fetch_playlist_tracks(client, ext)
    with db():
        for i, t in enumerate(tracks):
            db().execute(
                """INSERT INTO tracks(source,external_id,dedup_key,title,artist,
                   album,duration_ms,isrc,cover,meta,added_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source,external_id) DO UPDATE SET
                     title=excluded.title, artist=excluded.artist,
                     album=excluded.album, duration_ms=excluded.duration_ms,
                     isrc=excluded.isrc, cover=excluded.cover, meta=excluded.meta""",
                (
                    t.source, t.external_id,
                    dedup_key(t.title, t.artist, t.duration_ms),
                    t.title, t.artist, t.album, t.duration_ms, t.isrc,
                    t.cover, json.dumps(t.meta), now(),
                ),
            )
            db().execute(
                "INSERT OR IGNORE INTO playlist_items VALUES(?,?,?,?,?)",
                (source, ext, t.source, t.external_id, i),
            )
        db().commit()
    return playlist_tracks(source, ext)


async def resolve(client: httpx.AsyncClient, source: str, external_id: str) -> dict:
    """source+ext -> {stream_path | stream_url, kind}. Кэш URL с TTL."""
    cache_key = f"{source}|{external_id}"
    row = db().execute(
        "SELECT * FROM url_cache WHERE key=? AND expires_at>?", (cache_key, now())
    ).fetchone()
    if not row:
        conn = CONNECTORS.get(source)
        if not conn or not conn.is_configured():
            raise SourceUnavailable(f"{source}: не настроен")
        trow = db().execute(
            "SELECT * FROM tracks WHERE source=? AND external_id=?", (source, external_id)
        ).fetchone()
        meta = json.loads(trow["meta"]) if trow else {}
        if source == "soundcloud":
            resolved = await conn.resolve(client, external_id, media=meta.get("media"))
        elif source == "spotify":
            resolved = await conn.resolve(
                client, external_id,
                meta={"title": trow["title"], "artist": trow["artist"],
                      "duration_ms": trow["duration_ms"],
                      "preview_url": meta.get("preview_url")} if trow else None,
            )
        else:
            resolved = await conn.resolve(client, external_id)
        db().execute(
            "INSERT INTO url_cache(key,stream_url,kind,expires_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET stream_url=excluded.stream_url,"
            " kind=excluded.kind, expires_at=excluded.expires_at",
            (cache_key, resolved.stream_url, resolved.kind, now() + resolved.ttl_s),
        )
        db().commit()
        row = {"stream_url": resolved.stream_url, "kind": resolved.kind}

    if row["kind"] == "direct":
        return {"kind": "direct", "stream_url": row["stream_url"]}
    # выдаём одноразовый токен на прокси + добавляем хост в allowlist
    token = uuid.uuid4().hex
    db().execute(
        "INSERT INTO issued_streams(token,url,created_at) VALUES(?,?,?)",
        (token, row["stream_url"], now()),
    )
    db().commit()
    return {"kind": "proxy", "stream_path": f"/stream/{token}"}


def lookup_stream(token: str) -> str | None:
    row = db().execute(
        "SELECT url FROM issued_streams WHERE token=? AND created_at>?",
        (token, now() - 24 * 3600),
    ).fetchone()
    return row["url"] if row else None
