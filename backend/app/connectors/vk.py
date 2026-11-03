"""Коннектор VK Музыки. Работает только user-токеном (аудио вырезано из
официального API в 2017). Использует legacy-методы api.vk.com — вне ToS VK,
только чтение своих данных, personal use."""
import logging

import httpx

from ..config import settings
from .base import Resolved, SourceUnavailable, Track, Playlist

log = logging.getLogger("vk")

API = "https://api.vk.com/method/"
V = "5.131"


class VKConnector:
    name = "vk"

    def is_configured(self) -> bool:
        return bool(settings.vk_token)

    async def _call(self, client: httpx.AsyncClient, method: str, **params):
        if not self.is_configured():
            raise SourceUnavailable("VK_TOKEN не задан")
        r = await client.get(
            API + method,
            params={"access_token": settings.vk_token, "v": V, **params},
            timeout=20,
        )
        data = r.json()
        if "error" in data:
            raise SourceUnavailable(f"VK: {data['error'].get('error_msg', 'error')}")
        return data["response"]

    @staticmethod
    def _cover(item: dict) -> str | None:
        thumb = (item.get("album") or {}).get("thumb") or {}
        for size in ("photo_1200", "photo_600", "photo_300", "photo_135", "photo_68"):
            if thumb.get(size):
                return thumb[size]
        # обложки лежат в album.images (новый формат) или в cover_* (старый)
        album = item.get("album") or {}
        imgs = album.get("images") or []
        if imgs:
            return imgs[-1].get("url")
        for k in ("cover_big", "cover_medium", "cover_small"):
            if item.get(k):
                return item[k]
        return None

    @staticmethod
    def _ext_id(item: dict) -> str:
        return f"{item['owner_id']}_{item['id']}"

    def _to_track(self, item: dict) -> Track:
        return Track(
            source=self.name,
            external_id=self._ext_id(item),
            title=item.get("title", ""),
            artist=item.get("artist", ""),
            album=(item.get("album") or {}).get("title", ""),
            duration_ms=int(item.get("duration", 0)) * 1000,
            cover=self._cover(item),
            meta={
                "owner_id": item.get("owner_id"),
                "url_mp3": item.get("url_mp3") or item.get("url"),
                "url_ogg": item.get("url_ogg"),
            },
        )

    async def fetch_library(self, client: httpx.AsyncClient):
        tracks: list[Track] = []
        offset = 0
        while True:
            resp = await self._call(client, "audio.get", offset=offset, count=200)
            items = resp.get("items", [])
            tracks += [self._to_track(it) for it in items]
            offset += len(items)
            if offset >= resp.get("count", 0) or not items:
                break

        playlists: list[Playlist] = []
        try:
            resp = await self._call(client, "audio.getPlaylistV2List", count=200)
            for pl in resp.get("items", []):
                info = pl.get("info") or {}
                playlists.append(
                    Playlist(
                        source=self.name,
                        external_id=str(pl.get("playlist_id") or pl.get("id")),
                        title=info.get("title") or pl.get("title") or "Без названия",
                        cover=(info.get("cover") or [{}])[-1].get("url")
                        if isinstance(info.get("cover"), list) and info.get("cover")
                        else None,
                        total=0,
                    )
                )
        except SourceUnavailable as e:
            log.warning("VK плейлисты недоступны: %s", e)

        return tracks, playlists

    async def fetch_playlist_tracks(self, client, playlist_ext: str) -> list[Track]:
        resp = await self._call(client, "audio.playlistGet", playlist_id=playlist_ext)
        return [self._to_track(it) for it in resp.get("items", [])]

    async def resolve(self, client: httpx.AsyncClient, external_id: str) -> Resolved:
        # ссылки VK короткоживущие и IP-привязанные -> резолвим при каждом старте
        owner_id, _, aud_id = external_id.partition("_")
        resp = await self._call(client, "audio.getById", owner_id=owner_id, audios=aud_id)
        items = resp if isinstance(resp, list) else resp.get("items", [])
        for item in items:
            for key in ("url_mp3", "url_ogg", "url"):
                url = item.get(key)
                if url and url.startswith("http"):
                    return Resolved(stream_url=url, kind="proxy", ttl_s=1800)
        raise SourceUnavailable("VK: трек без ссылки (нет лицензии/регион)")
