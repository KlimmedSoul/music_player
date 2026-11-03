import time
import hmac
import threading
from collections import OrderedDict, deque
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from .auth import auth_required, create_token
from .artwork import find_cover
from . import activity
from .config import settings
from .connectors.base import SourceUnavailable
from .db import db, fetch_one
from . import library
from .lyrics import find_lyrics
from .stream import is_valid_token, proxy_stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(
        follow_redirects=True, timeout=httpx.Timeout(30.0, connect=10.0)
    )
    yield
    await app.state.http.aclose()


app = FastAPI(title="MyPlayer backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # фронт на GitHub Pages; доступ всё равно за JWT
    allow_methods=["*"],
    allow_headers=["*"],
)


def client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=512)


_login_attempts = OrderedDict()
_login_lock = threading.Lock()
_login_clock = time.monotonic


@app.post("/auth/login")
def login(request: Request, body: LoginBody):
    address = request.client.host if request.client else "unknown"
    current = _login_clock()
    with _login_lock:
        attempts = _login_attempts.setdefault(address, deque())
        _login_attempts.move_to_end(address)
        while len(_login_attempts) > 1024:
            _login_attempts.popitem(last=False)
        while attempts and attempts[0] <= current - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "Слишком много попыток. Подожди минуту.", headers={"Retry-After":str(max(1,int(attempts[0]+60-current)+1))})
        if not settings.auth_password or not hmac.compare_digest(body.password.encode(), settings.auth_password.encode()):
            attempts.append(current)
            raise HTTPException(403, "неверный пароль")
        _login_attempts.pop(address, None)
    return {"token":create_token()}


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": time.time()}


@app.get("/status", dependencies=[auth_required])
def status():
    from .db import store_get

    out = {}
    for name, conn in library.CONNECTORS.items():
        out[name] = "configured" if conn.is_configured() else "not_configured"
    return {
        "sources": out,
        "spotify_logged_in": bool(store_get("spotify_tokens")),
        "last_refresh": store_get("last_refresh"),
    }


# ---------- Избранное / история / «продолжить слушать» ----------
class TrackBody(BaseModel):
    source: str
    external_id: str


class FavoriteBody(TrackBody):
    favorite: bool | None = None


class PlayBody(TrackBody):
    position_s: float = Field(0, ge=0, le=86400, allow_inf_nan=False)
    duration_s: float = Field(0, ge=0, le=86400, allow_inf_nan=False)
    completed: bool = False


class PositionBody(TrackBody):
    position_s: float = Field(ge=0, le=86400, allow_inf_nan=False)


def _known_track(body: TrackBody):
    row = fetch_one(
        "SELECT 1 FROM tracks WHERE source=? AND external_id=?",
        (body.source, body.external_id),
    )
    if not row:
        raise HTTPException(404, "трек не найден в библиотеке")


@app.post("/favorites/toggle", dependencies=[auth_required])
def favorite_toggle(body: FavoriteBody):
    _known_track(body)
    return {"favorite": activity.toggle_favorite(body.source, body.external_id, body.favorite)}


@app.get("/favorites", dependencies=[auth_required])
def favorites():
    return activity.list_favorites()


@app.post("/plays", dependencies=[auth_required])
def play(body: PlayBody):
    _known_track(body)
    return activity.record_play(
        body.source, body.external_id,
        max(0, body.position_s), max(0, body.duration_s), body.completed,
    )


@app.get("/history", dependencies=[auth_required])
def history(limit: int = 30):
    return activity.list_history(max(1, min(limit, 100)))


@app.post("/positions", dependencies=[auth_required])
def position(body: PositionBody):
    _known_track(body)
    return activity.report_position(body.source, body.external_id, body.position_s)


@app.get("/positions", dependencies=[auth_required])
def positions():
    return activity.list_positions()


@app.get("/lyrics", dependencies=[auth_required])
async def lyrics(
    request: Request,
    title: str = Query(min_length=1, max_length=200),
    artist: str = Query(min_length=0, max_length=200),
    duration_s: float = Query(0, ge=0, le=86400),
):
    try:
        return await find_lyrics(client(request), title, artist, duration_s)
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(502, "Сервер текстов не отвечает. Попробуй позже.")


@app.post("/library/refresh", dependencies=[auth_required])
async def refresh(request: Request, source: str | None = Query(None)):
    if source and source not in library.CONNECTORS:
        raise HTTPException(400, "неизвестный источник")
    return await library.refresh(client(request), source)


@app.get("/library/tracks", dependencies=[auth_required])
def tracks(q: str = "", source: str = "all", cursor: int = 0, limit: int = 50):
    return library.list_tracks(q, source, cursor, min(limit, 200))


@app.get("/library/playlists", dependencies=[auth_required])
def playlists():
    return library.list_playlists()


@app.get("/artwork", dependencies=[auth_required])
async def artwork(
    request: Request,
    title: str = Query(min_length=1, max_length=200),
    artist: str = Query(min_length=1, max_length=200),
):
    return {"cover": await find_cover(client(request), title, artist)}


@app.get("/library/playlists/{source}/{ext}/tracks", dependencies=[auth_required])
async def playlist_tracks(source: str, ext: str, request: Request):
    if source not in library.CONNECTORS:
        raise HTTPException(400, "неизвестный источник")
    try:
        return {"items": await library.ensure_playlist_tracks(client(request), source, ext)}
    except SourceUnavailable as e:
        raise HTTPException(502, str(e))
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Источник вернул недоступный или некорректный ответ")


@app.post("/resolve", dependencies=[auth_required])
async def resolve(request: Request, body: dict):
    source = body.get("source")
    ext = body.get("external_id")
    if not source or not ext:
        raise HTTPException(400, "нужны source и external_id")
    try:
        return await library.resolve(client(request), source, str(ext), full_only=body.get("full_only") is True)
    except SourceUnavailable as e:
        raise HTTPException(502, str(e))
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Источник вернул недоступный или некорректный ответ")


@app.get("/stream/{token}")
async def stream(token: str, request: Request):
    """Без JWT: <audio src> не умеет заголовки. Токен одноразовый-на-сутки
    и привязан к URL, который выдал только что /resolve."""
    url = is_valid_token(library.lookup_stream, token)
    return await proxy_stream(client(request), url, request.headers.get("range"))


# ---------- Spotify OAuth ----------
@app.get("/auth/spotify/start", dependencies=[auth_required])
def spotify_start():
    conn = library.CONNECTORS["spotify"]
    if not conn.is_configured():
        raise HTTPException(400, "SPOTIFY_CLIENT_ID/SECRET не заданы")
    return {"url": conn.start_login()}


@app.get("/auth/spotify/callback")
async def spotify_callback(code: str, state: str, request: Request):
    from .db import store_set

    conn = library.CONNECTORS["spotify"]
    try:
        await conn.finish_login(client(request), code, state)
    except SourceUnavailable as e:
        raise HTTPException(502, str(e))
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Источник вернул недоступный или некорректный ответ")
    return HTMLResponse(
        "<h3>Spotify подключён 🙂</h3>Можно закрыть вкладку и нажать "
        "«Обновить» в приложении."
    )
