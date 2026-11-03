"""Единая библиотека: upsert треков/плейлистов, дедуп, выдача, resolve+кэш."""
import json
import uuid

import httpx

from .connectors.base import SourceUnavailable, Track, dedup_key
from .db import db, now, transaction, synchronized, fetch_one
from .connectors import SoundCloudConnector, SpotifyConnector, VKConnector, YouTubeMusicConnector

_youtube = YouTubeMusicConnector()
CONNECTORS = {
    "vk": VKConnector(),
    "soundcloud": SoundCloudConnector(),
    "spotify": SpotifyConnector(youtube=_youtube),
    "ytmusic": _youtube,
}


def _upsert_tracks(conn, tracks):
    for t in tracks:
        key = dedup_key(t.title, t.artist, t.duration_ms, t.isrc)
        conn.execute(
            """INSERT INTO tracks(source,external_id,dedup_key,title,artist,
               album,duration_ms,isrc,cover,meta,added_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source,external_id) DO UPDATE SET
                 dedup_key=excluded.dedup_key, title=excluded.title, artist=excluded.artist,
                 album=excluded.album, duration_ms=excluded.duration_ms, isrc=excluded.isrc,
                 cover=excluded.cover, meta=excluded.meta""",
            (t.source,t.external_id,key,t.title,t.artist,t.album,t.duration_ms,t.isrc,
             t.cover,json.dumps(t.meta),now()),
        )
        conn.execute("UPDATE favorites SET dedup_key=? WHERE track_source=? AND track_ext=?", (key,t.source,t.external_id))


async def refresh(client: httpx.AsyncClient, source: str | None = None) -> dict:
    result = {}
    for name in ([source] if source else list(CONNECTORS)):
        conn = CONNECTORS.get(name)
        if not conn or not conn.is_configured():
            result[name] = "not_configured"
            continue
        try:
            tracks, playlists = await conn.fetch_library(client)
            with transaction() as sql:
                _upsert_tracks(sql, tracks)
                for pl in playlists:
                    sql.execute(
                        """INSERT INTO playlists(source,external_id,title,cover,total,synced_at,tracks_loaded)
                           VALUES(?,?,?,?,?,?,0) ON CONFLICT(source,external_id) DO UPDATE SET
                           title=excluded.title,cover=excluded.cover,total=excluded.total,
                           synced_at=excluded.synced_at,tracks_loaded=0""",
                        (pl.source,pl.external_id,pl.title,pl.cover,pl.total,now()),
                    )
                    sql.execute("DELETE FROM playlist_items WHERE playlist_source=? AND playlist_ext=?", (pl.source,pl.external_id))
                    for i, tid in enumerate(pl.track_ids):
                        sql.execute("INSERT OR IGNORE INTO playlist_items VALUES(?,?,?,?,?)", (pl.source,pl.external_id,pl.source,tid,i))
                    found = len(playlist_tracks(pl.source,pl.external_id))
                    if pl.track_ids and found == len(pl.track_ids) == pl.total:
                        sql.execute("UPDATE playlists SET tracks_loaded=1 WHERE source=? AND external_id=?", (pl.source,pl.external_id))
            result[name] = f"ok: {len(tracks)} треков, {len(playlists)} плейлистов"
        except SourceUnavailable as error:
            result[name] = f"error: {error}"
        except Exception as error:
            result[name] = f"error: {error.__class__.__name__}: {error}"
    from .db import store_set
    store_set("last_refresh", {"ts":now(), "sources":result})
    return result


def _row_to_dict(r, sources=None):
    d = dict(r)
    d["meta"] = json.loads(d.get("meta") or "{}")
    if sources is not None:
        d["sources"] = sources
    return d


@synchronized
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


@synchronized
def list_playlists():
    rows = db().execute(
        "SELECT * FROM playlists ORDER BY source, title"
    ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows]}


@synchronized
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
    cached = fetch_one("SELECT tracks_loaded FROM playlists WHERE source=? AND external_id=?", (source,ext))
    if cached and cached["tracks_loaded"]:
        return playlist_tracks(source,ext)
    conn = CONNECTORS.get(source)
    if not conn or not hasattr(conn,"fetch_playlist_tracks"):
        raise SourceUnavailable(f"{source}: плейлисты не поддерживаются")
    tracks = await conn.fetch_playlist_tracks(client,ext)
    with transaction() as sql:
        _upsert_tracks(sql, tracks)
        sql.execute("DELETE FROM playlist_items WHERE playlist_source=? AND playlist_ext=?", (source,ext))
        for i,t in enumerate(tracks):
            sql.execute("INSERT OR IGNORE INTO playlist_items VALUES(?,?,?,?,?)", (source,ext,t.source,t.external_id,i))
        sql.execute(
            """INSERT INTO playlists(source,external_id,title,cover,total,synced_at,tracks_loaded)
               VALUES(?,?,?,NULL,?,?,1) ON CONFLICT(source,external_id) DO UPDATE SET
               total=excluded.total,synced_at=excluded.synced_at,tracks_loaded=1""",
            (source,ext,ext,len(tracks),now()),
        )
    return playlist_tracks(source,ext)


async def resolve(client: httpx.AsyncClient, source: str, external_id: str, full_only: bool = False, refresh: bool = False) -> dict:
    """source+ext -> {stream_path | stream_url, kind}. Кэш URL с TTL."""
    cache_key = f"{source}|{external_id}" + ("|full" if full_only else "")
    with transaction() as sql:
        if refresh:
            sql.execute("DELETE FROM url_cache WHERE key=?", (cache_key,))
        sql.execute("DELETE FROM url_cache WHERE expires_at<=?", (now(),))
        sql.execute("DELETE FROM issued_streams WHERE created_at<=?", (now()-86400,))
    row = None if refresh else fetch_one(
        "SELECT * FROM url_cache WHERE key=? AND expires_at>?", (cache_key, now())
    )
    if not row:
        conn = CONNECTORS.get(source)
        if not conn or not conn.is_configured():
            raise SourceUnavailable(f"{source}: не настроен")
        trow = fetch_one(
            "SELECT * FROM tracks WHERE source=? AND external_id=?", (source, external_id)
        )
        meta = json.loads(trow["meta"]) if trow else {}
        if source == "soundcloud":
            resolved = await conn.resolve(client, external_id, media=meta.get("media"), full_only=full_only)
        elif source == "spotify":
            resolved = await conn.resolve(
                client, external_id, full_only=full_only,
                meta={"title": trow["title"], "artist": trow["artist"],
                      "duration_ms": trow["duration_ms"],
                      "preview_url": meta.get("preview_url")} if trow else None,
            )
        else:
            resolved = await conn.resolve(client, external_id)
        with transaction():
            db().execute(
                "INSERT INTO url_cache(key,stream_url,kind,expires_at,content_type) VALUES(?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET stream_url=excluded.stream_url,"
                " kind=excluded.kind, expires_at=excluded.expires_at, content_type=excluded.content_type",
                (cache_key, resolved.stream_url, resolved.kind, now() + resolved.ttl_s, resolved.content_type),
            )
        row = {"stream_url":resolved.stream_url, "kind":resolved.kind, "content_type":resolved.content_type}

    media = {"content_type":row["content_type"]} if row["content_type"] else {}
    if row["kind"] == "direct":
        return {"kind":"direct", "stream_url":row["stream_url"], **media}
    # выдаём одноразовый токен на прокси + добавляем хост в allowlist
    token = uuid.uuid4().hex
    with transaction():
        db().execute(
            "INSERT INTO issued_streams(token,url,created_at) VALUES(?,?,?)",
            (token, row["stream_url"], now()),
        )
    return {"kind":"proxy", "stream_path":f"/stream/{token}", **media}


@synchronized
def lookup_stream(token: str) -> str | None:
    row = fetch_one(
        "SELECT url FROM issued_streams WHERE token=? AND created_at>?",
        (token, now() - 24 * 3600),
    )
    return row["url"] if row else None
