"""SoundCloud: лайки и полные потоки через api-v2.
Токен и client_id берутся из сессии пользователя (см. README). Вне ToS
SoundCloud — personal use."""
import logging

import httpx

from ..config import settings
from .base import Playlist, Resolved, SourceUnavailable, Track

log = logging.getLogger("sc")

API = "https://api-v2.soundcloud.com"


class SoundCloudConnector:
    name = "soundcloud"

    def is_configured(self) -> bool:
        return bool(settings.sc_token and settings.sc_client_id)

    def _params(self, **kw) -> dict:
        base = {"client_id": settings.sc_client_id, "oauth_token": settings.sc_token}
        return {**base, **kw}

    async def _me_id(self, client: httpx.AsyncClient) -> int:
        r = await client.get(API + "/i/whoami", params=self._params(), timeout=20)
        if r.status_code != 200 or not r.text:
            raise SourceUnavailable(f"SoundCloud: whoami {r.status_code} (токен протух?)")
        return r.json()["id"]

    @staticmethod
    def _to_track(item: dict) -> Track:
        cover = item.get("artwork_url") or (item.get("user") or {}).get("avatar_url")
        return Track(
            source="soundcloud",
            external_id=str(item["id"]),
            title=item.get("title", ""),
            artist=(item.get("user") or {}).get("username", ""),
            duration_ms=int(item.get("full_duration") or item.get("duration") or 0),
            cover=cover.replace("-large", "-t500x500") if cover else None,
            meta={
                "media": (item.get("media") or {}).get("transcodings", []),
                "format": item.get("format"),
            },
        )

    async def fetch_library(self, client: httpx.AsyncClient):
        uid = await self._me_id(client)
        tracks: list[Track] = []
        offset = 0
        while True:
            r = await client.get(
                API + "/i/likes",
                params=self._params(user_id=uid, limit=50, offset=offset),
                timeout=20,
            )
            if r.status_code != 200:
                raise SourceUnavailable(f"SoundCloud: likes {r.status_code}")
            data = r.json()
            coll = data.get("collection", [])
            for it in coll:
                t = it.get("track") or it  # формат /i/likes: {track: {...}}
                if t.get("id"):
                    tracks.append(self._to_track(t))
            if not data.get("next_href") or not coll:
                break
            offset += 50

        # сеты = плейлисты
        playlists: list[Playlist] = []
        try:
            r = await client.get(
                API + f"/users/{uid}/sets",
                params=self._params(limit=50),
                timeout=20,
            )
            for s in r.json().get("collection", []):
                playlists.append(
                    Playlist(
                        source=self.name,
                        external_id=str(s["id"]),
                        title=s.get("title", ""),
                        cover=s.get("artwork_url"),
                        total=s.get("track_count", 0),
                        track_ids=[str(t["id"]) for t in s.get("tracks", [])],
                    )
                )
        except Exception as e:  # сеты не критичны
            log.warning("SC сеты недоступны: %s", e)
        return tracks, playlists

    async def fetch_playlist_tracks(self, client, playlist_ext: str) -> list[Track]:
        r = await client.get(
            API + f"/playlists/{playlist_ext}/tracks",
            params=self._params(limit=200),
            timeout=20,
        )
        if r.status_code != 200:
            raise SourceUnavailable(f"SoundCloud: set {r.status_code}")
        return [self._to_track(t) for t in r.json().get("collection", [])]

    async def resolve(self, client: httpx.AsyncClient, external_id: str,
                      media: list | None = None, full_only: bool = False) -> Resolved:
        """media = transcodings из списка лайков; иначе запрашиваем трек."""
        transcodings = media or []
        if not transcodings:
            r = await client.get(
                API + f"/tracks/{external_id}",
                params={"client_id": settings.sc_client_id},
                timeout=20,
            )
            transcodings = ((r.json().get("media") or {}).get("transcodings") or [])
        if full_only:
            transcodings = [t for t in transcodings if not t.get("snipped")]
        if not transcodings:
            raise SourceUnavailable("SoundCloud: полный звук недоступен" if full_only else "SoundCloud: нет доступных транскодингов")

        progressive = next((t for t in transcodings if t.get("url") and (t.get("format") or {}).get("protocol") in ("http", "progressive")), None)
        selected = progressive or next((t for t in transcodings if t.get("url") and (t.get("format") or {}).get("protocol") == "hls"), None)
        if not selected:
            raise SourceUnavailable("SoundCloud: нет поддерживаемого полного потока")
        response = await client.get(selected["url"], params=self._params(), follow_redirects=False, timeout=20)
        if response.status_code in (301,302,303,307,308):
            stream_url = response.headers.get("location")
        elif response.status_code == 200:
            try:
                stream_url = response.json().get("url")
            except (ValueError, AttributeError):
                raise SourceUnavailable("SoundCloud: некорректный ответ потока")
        else:
            raise SourceUnavailable(f"SoundCloud: stream {response.status_code}")
        if not isinstance(stream_url,str) or not stream_url.startswith(("https://","http://")):
            raise SourceUnavailable("SoundCloud: ссылка потока отсутствует")
        return Resolved(stream_url=stream_url,kind="proxy" if progressive else "direct",ttl_s=86400,content_type=((selected.get("format") or {}).get("mime_type") or "audio/mpeg") if progressive else "application/vnd.apple.mpegurl")
