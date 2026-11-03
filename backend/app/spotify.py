"""Spotify — официальный API (бесплатный). Лайки/плейлисты/метаданные +
превью 30 сек. Полный звук бесплатно официально невозможен: resolve
ищет вариант на YouTube через yt-dlp (если установлен), иначе превью."""
import base64
import hashlib
import logging
import re
import secrets
import urllib.parse

import httpx

from ..config import settings
from ..db import store_get, store_set
from .base import Playlist, Resolved, SourceUnavailable, Track

log = logging.getLogger("spotify")

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "user-library-read playlist-read-private"


class SpotifyConnector:
    name = "spotify"

    def is_configured(self) -> bool:
        return bool(settings.spotify_client_id and settings.spotify_client_secret)

    # ---------- OAuth PKCE ----------
    def start_login(self) -> str:
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(16)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        store_set("spotify_pkce", {"verifier": verifier, "state": state})
        q = urllib.parse.urlencode(
            {
                "client_id": settings.spotify_client_id,
                "response_type": "code",
                "redirect_uri": settings.spotify_redirect_uri,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "scope": SCOPES,
                "state": state,
            }
        )
        return f"{AUTH_URL}?{q}"

    async def finish_login(self, client: httpx.AsyncClient, code: str, state: str):
        pkce = store_get("spotify_pkce") or {}
        if pkce.get("state") != state:
            raise SourceUnavailable("Spotify: state не совпал (CSRF?)")
        r = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.spotify_redirect_uri,
                "client_id": settings.spotify_client_id,
                "code_verifier": pkce["verifier"],
            },
            timeout=20,
        )
        if r.status_code != 200:
            raise SourceUnavailable(f"Spotify: exchange {r.status_code} {r.text[:200]}")
        tok = r.json()
        store_set(
            "spotify_tokens",
            {
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token"),
                "expires_at": tok["expires_in"] + __import__("time").time(),
            },
        )

    async def _access(self, client: httpx.AsyncClient) -> str:
        tok = store_get("spotify_tokens")
        if not tok:
            if settings.spotify_refresh_token:
                tok = {
                    "access_token": None,
                    "refresh_token": settings.spotify_refresh_token,
                    "expires_at": 0,
                }
            else:
                raise SourceUnavailable(
                    "Spotify: не выполнен вход (вход или SPOTIFY_REFRESH_TOKEN)"
                )
        if tok["expires_at"] - 60 > __import__("time").time():
            return tok["access_token"]
        r = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tok["refresh_token"],
                "client_id": settings.spotify_client_id,
                "client_secret": settings.spotify_client_secret,
            },
            timeout=20,
        )
        if r.status_code != 200:
            store_set("spotify_tokens", None)
            raise SourceUnavailable("Spotify: refresh не удался, нужен новый вход")
        new = r.json()
        store_set(
            "spotify_tokens",
            {
                "access_token": new["access_token"],
                "refresh_token": new.get("refresh_token", tok["refresh_token"]),
                "expires_at": new["expires_in"] + __import__("time").time(),
            },
        )
        return new["access_token"]

    async def _get(self, client, url, **kw):
        token = await self._access(client)
        r = await client.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=20, **kw
        )
        if r.status_code == 401:
            store_set("spotify_tokens", None)
            raise SourceUnavailable("Spotify: токен отозван, нужен новый вход")
        if r.status_code != 200:
            raise SourceUnavailable(f"Spotify: {r.status_code}")
        return r.json()

    # ---------- библиотека ----------
    @staticmethod
    def _to_track(item: dict) -> Track:
        album = item.get("album") or {}
        imgs = album.get("images") or []
        return Track(
            source="spotify",
            external_id=item["id"],
            title=item.get("name", ""),
            artist=", ".join(a["name"] for a in item.get("artists", [])),
            album=album.get("name", ""),
            duration_ms=int(item.get("duration_ms") or 0),
            isrc=album.get("isrc") or item.get("external_ids", {}).get("isrc"),
            cover=imgs[0]["url"] if imgs else None,
            meta={"preview_url": item.get("preview_url")},
        )

    async def fetch_library(self, client: httpx.AsyncClient):
        tracks: list[Track] = []
        url = API + "/me/tracks?limit=50"
        while url:
            data = await self._get(client, url)
            for it in data.get("items", []):
                if it.get("track"):
                    tracks.append(self._to_track(it["track"]))
            url = data.get("next")

        playlists: list[Playlist] = []
        url = API + "/me/playlists?limit=50"
        while url:
            data = await self._get(client, url)
            for pl in data.get("items", []):
                playlists.append(
                    Playlist(
                        source="spotify",
                        external_id=pl["id"],
                        title=pl.get("name", ""),
                        cover=(pl.get("images") or [{}])[0].get("url"),
                        total=(pl.get("tracks") or {}).get("total", 0),
                    )
                )
            url = data.get("next")
        return tracks, playlists

    async def fetch_playlist_tracks(self, client, playlist_ext: str) -> list[Track]:
        out: list[Track] = []
        url = f"{API}/playlists/{playlist_ext}/tracks?limit=100"
        while url and len(out) < 1000:
            data = await self._get(client, url)
            for it in data.get("items", []):
                if it.get("track"):
                    out.append(self._to_track(it["track"]))
            url = data.get("next")
        return out

    # ---------- resolve ----------
    @staticmethod
    def _ytdlp(url: str) -> str | None:
        """Резолв YouTube-URL через yt-dlp (optional dependency)."""
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            return None
        try:
            with yt_dlp.YoutubeDL(
                {"quiet": True, "no_warnings": True, "noplaylist": True}
            ) as ydl:
                info = ydl.extract_info(url, download=False)
            return info.get("url") or None
        except Exception as e:
            log.warning("yt-dlp resolve failed: %s", e)
            return None

    async def search_youtube(self, title: str, artist: str, duration_ms: int) -> str | None:
        """Бесплатный обход: ищем аудио-аналог на YouTube (нарушает ToS YT)."""
        if not title:
            return None
        q = f"{artist} {title} official audio".strip()
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            return None
        try:
            with yt_dlp.YoutubeDL(
                {
                    "quiet": True, "no_warnings": True, "extract_flat": True,
                    "playlistend": 8,
                }
            ) as ydl:
                info = ydl.extract_info(f"ytsearch8:{q}", download=False)
            want = duration_ms / 1000
            for entry in info.get("entries") or []:
                dur = entry.get("duration") or 0
                if want and not (want * 0.9 <= dur <= want * 1.15):
                    continue
                return self._ytdlp("https://youtu.be/" + entry["id"])
            return None
        except Exception as e:
            log.warning("yt search failed: %s", e)
            return None

    async def resolve(self, client: httpx.AsyncClient, external_id: str,
                      meta: dict | None = None) -> Resolved:
        meta = meta or {}
        track = None
        if meta.get("title"):
            track = meta
        else:
            data = await self._get(client, f"{API}/tracks/{external_id}")
            track = {
                "title": data.get("name"),
                "artist": ", ".join(a["name"] for a in data.get("artists", [])),
                "duration_ms": data.get("duration_ms", 0),
                "preview_url": data.get("preview_url"),
            }
        url = await self.search_youtube(
            track.get("title", ""), track.get("artist", ""), track.get("duration_ms", 0)
        )
        if url:
            return Resolved(stream_url=url, kind="proxy", ttl_s=2 * 3600)
        if track.get("preview_url"):
            return Resolved(stream_url=track["preview_url"], kind="direct", ttl_s=86400)
        raise SourceUnavailable("Spotify: ни полного звука, ни превью")
