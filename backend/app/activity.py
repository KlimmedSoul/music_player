"""Избранное, история прослушиваний и позиции «продолжить слушать»."""
from .db import db, now
from .library import _row_to_dict


def toggle_favorite(source: str, ext: str, desired: bool | None = None) -> bool:
    """Лайк привязан к дедуп-ключу: один трек из разных сервисов — одно избранное."""
    conn = db()
    trow = conn.execute(
        "SELECT dedup_key FROM tracks WHERE source=? AND external_id=?",
        (source, ext),
    ).fetchone()
    dedup = trow["dedup_key"] if trow else f"{source}|{ext}"
    match = "(track_source=? AND track_ext=?) OR dedup_key=?"
    existing = conn.execute(
        f"SELECT 1 FROM favorites WHERE {match}", (source, ext, dedup)
    ).fetchone()
    if desired is not None and bool(existing) == desired:
        return desired
    if existing:
        conn.execute(f"DELETE FROM favorites WHERE {match}", (source, ext, dedup))
        favorite = False
    else:
        conn.execute(
            "INSERT INTO favorites(dedup_key,track_source,track_ext,added_at) VALUES(?,?,?,?)",
            (dedup, source, ext, now()),
        )
        favorite = True
    conn.commit()
    return favorite


def list_favorites(limit: int = 1000):
    rows = db().execute(
        """SELECT t.* FROM favorites f
           JOIN tracks t ON t.source=f.track_source AND t.external_id=f.track_ext
           ORDER BY f.added_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    items, seen = [], set()
    for r in rows:
        if r["dedup_key"] in seen:
            continue
        seen.add(r["dedup_key"])
        items.append(_row_to_dict(r))
    return {"items": items}


def record_play(source, ext, position, duration, completed) -> dict:
    conn = db()
    conn.execute(
        "INSERT INTO plays(track_source,track_ext,played_at,position_s,duration_s,completed) "
        "VALUES(?,?,?,?,?,?)",
        (source, ext, now(), position, duration, 1 if completed else 0),
    )
    conn.execute(
        "DELETE FROM plays WHERE id NOT IN "
        "(SELECT id FROM plays ORDER BY played_at DESC LIMIT 1000)"
    )
    _update_position(conn, source, ext, position, duration, completed)
    conn.commit()
    return {"ok": True}


def _update_position(conn, source, ext, position, duration, completed) -> None:
    finished = bool(completed) or (duration and position >= duration - 10)
    if finished or position < 10:
        conn.execute(
            "DELETE FROM positions WHERE track_source=? AND track_ext=?",
            (source, ext),
        )
    else:
        save_position(conn, source, ext, position)


def save_position(conn, source, ext, position) -> None:
    conn.execute(
        "INSERT INTO positions VALUES(?,?,?,?) "
        "ON CONFLICT(track_source,track_ext) DO UPDATE SET "
        "position_s=excluded.position_s, updated_at=excluded.updated_at",
        (source, ext, position, now()),
    )


def list_history(limit: int = 30):
    rows = db().execute(
        """SELECT t.*, MAX(p.played_at) AS played_at FROM plays p
           JOIN tracks t ON t.source=p.track_source AND t.external_id=p.track_ext
           GROUP BY t.dedup_key ORDER BY played_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return {"items": [_row_to_dict(r) for r in rows]}


def report_position(source, ext, position) -> dict:
    conn = db()
    if position >= 10:
        save_position(conn, source, ext, position)
    else:
        conn.execute(
            "DELETE FROM positions WHERE track_source=? AND track_ext=?",
            (source, ext),
        )
    conn.commit()
    return {"ok": True}


def list_positions():
    rows = db().execute(
        "SELECT * FROM positions WHERE updated_at>? ORDER BY updated_at DESC LIMIT 500",
        (now() - 30 * 86400,),
    ).fetchall()
    return {"items": [dict(r) for r in rows]}
