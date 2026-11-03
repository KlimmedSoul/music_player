import time
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .auth import auth_required, create_token
from .artwork import find_cover
from .config import settings
from .connectors.base import SourceUnavailable
from .db import db
from . import library
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
    password: str


@app.post("/auth/login")
def login(body: LoginBody):
    if not settings.auth_password or body.password != settings.auth_password:
        raise HTTPException(403, "неверный пароль")
    return {"token": create_token()}


@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": time.time()}


@app.get("/status", dependencies=[auth_required])
def status():
    from .db import store_get

    out = {}
    for name, conn in library.CONNECTORS.items():
        out[name] = "configured" if conn.is_configured() else "not_configured"
    out["spotify_logged_in"] = bool(store_get("spotify_tokens"))
    return {"sources": out}


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
    return HTMLResponse(
        "<h3>Spotify подключён 🙂</h3>Можно закрыть вкладку и нажать "
        "«Обновить» в приложении."
    )
