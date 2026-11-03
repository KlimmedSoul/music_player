"""YouTube Music: лайкнутые треки (LM) и полные аудиопотоки через yt-dlp.

Для «Мне нравится» нужны cookies браузера, залогиненного в music.youtube.com:
экспортировать их в cookies.txt (расширение "Get cookies.txt LOCALLY") и задать
YTM_COOKIES_FILE (путь) или YTM_COOKIES_B64 (base64 всего файла — для облака).
Вне ToS Google — personal use."""
import base64
import atexit
import logging
import os
import tempfile

import httpx
from starlette.concurrency import run_in_threadpool

from ..config import settings
from .base import Playlist, Resolved, SourceUnavailable, Track

log = logging.getLogger("ytmusic")

LIKES_URL = "https://music.youtube.com/library/likes"
_cookies_tmp: str | None = None


def _cleanup_cookies():
    global _cookies_tmp
    if _cookies_tmp:
        try:
            os.unlink(_cookies_tmp)
        except FileNotFoundError:
            pass
        _cookies_tmp = None


atexit.register(_cleanup_cookies)


def _cookies_file() -> str | None:
    global _cookies_tmp
    if settings.ytm_cookies_file and os.path.isfile(settings.ytm_cookies_file):
        return settings.ytm_cookies_file
    if settings.ytm_cookies_b64:
        if not _cookies_tmp:
            try:
                content = base64.b64decode(settings.ytm_cookies_b64, validate=True)
            except ValueError as error:
                raise SourceUnavailable("YouTube Music: некорректный base64 cookies") from error
            fd, _cookies_tmp = tempfile.mkstemp(prefix="ytm_cookies_", suffix=".txt")
            with os.fdopen(fd, "wb") as f:
                f.write(content)
        return _cookies_tmp
    return None


def _ydl(**extra):
    try:
        import yt_dlp  # type: ignore
    except ImportError as e:
        raise SourceUnavailable("yt-dlp не установлен (pip install yt-dlp)")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        **extra,
    }
    cf = _cookies_file()
    if cf:
        opts["cookiefile"] = cf
    return yt_dlp.YoutubeDL(opts)


def _entry_track(entry: dict) -> Track:
    thumbs = entry.get("thumbnails") or []
    cover = thumbs[-1].get("url") if thumbs else None
    album = entry.get("album") or ""
    if isinstance(album,dict):
        album = album.get("title") or album.get("name") or ""
    return Track(
        source="ytmusic",
        external_id=str(entry["id"]),
        title=entry.get("title") or "",
        artist=entry.get("channel") or entry.get("artist") or "",
        album=str(album),
        duration_ms=int((entry.get("duration") or 0) * 1000),
        cover=cover,
    )


# ---------- синхронные внутренности (вызываются в потоке) ----------
def _fetch_liked():
    with _ydl() as ydl:
        info = ydl.extract_info(LIKES_URL, download=False)
    tracks, playlists = [], []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        tracks.append(_entry_track(entry))
    # страницы библиотеки (try: вкладка «Плейлисты») — best effort
    try:
        with _ydl() as ydl:
            tab = ydl.extract_info("https://music.youtube.com/feed/library/playlists", download=False)
        for entry in tab.get("entries") or []:
            raw = entry.get("id") or ""
            if not raw.startswith("VL"):
                continue  # только плейлисты (id вида VLPL…)
            playlists.append(
                Playlist(
                    source="ytmusic",
                    external_id=raw[2:],
                    title=entry.get("title") or "Плейлист",
                    cover=(entry.get("thumbnails") or [{}])[-1].get("url"),
                    total=entry.get("playlist_count") or 0,
                )
            )
    except Exception as e:
        log.info("YTM плейлисты недоступны: %s", e)
    return tracks, playlists


def _fetch_playlist(ext: str):
    with _ydl() as ydl:
        info = ydl.extract_info(f"https://music.youtube.com/playlist?list={ext}", download=False)
    return [_entry_track(e) for e in info.get("entries") or [] if e and e.get("id")]


def _resolve(video_id: str) -> Resolved:
    with _ydl(extract_flat=False,
              format="bestaudio[ext=m4a]/bestaudio/best[ext=m4a]/bestaudio/best") as ydl:
        info = ydl.extract_info(
            f"https://music.youtube.com/watch?v={video_id}",
            download=False, process=False,
        )
        info = ydl.process_ie_result(info, download=False)
    url = info.get("url")
    if not url and info.get("requested_formats"):
        url = info["requested_formats"][0].get("url")
    if not url:
        raise SourceUnavailable("YouTube Music: не удалось получить аудиопоток")
    # googlevideo-ссылки привязаны к IP и живут ~6 часов
    return Resolved(stream_url=url, kind="proxy", ttl_s=3500)


def _search_resolve(title, artist, duration_ms):
    if not title:
        return None
    with _ydl(extract_flat=True, playlistend=8, noplaylist=True) as ydl:
        info = ydl.extract_info(f"ytsearch8:{artist} {title} official audio", download=False)
    want = duration_ms / 1000
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        duration = entry.get("duration") or 0
        if want and duration and not (want*.9 <= duration <= want*1.15):
            continue
        try:
            return _resolve(entry["id"])
        except Exception:
            continue
    return None


class YouTubeMusicConnector:
    name = "ytmusic"

    def is_configured(self) -> bool:
        try:
            import yt_dlp  # noqa: F401  type: ignore
        except ImportError:
            return False
        try:
            return bool(_cookies_file())
        except SourceUnavailable:
            return False

    async def search_and_resolve(self, title, artist, duration_ms):
        try:
            return await run_in_threadpool(_search_resolve, title, artist, duration_ms)
        except Exception as error:
            log.info("YouTube search unavailable: %s", type(error).__name__)
            return None

    async def fetch_library(self, client: httpx.AsyncClient):
        if not self.is_configured():
            raise SourceUnavailable("YTM: нет cookies (YTM_COOKIES_FILE / YTM_COOKIES_B64)")
        try:
            return await run_in_threadpool(_fetch_liked)
        except SourceUnavailable:
            raise
        except Exception as e:
            msg = str(e)
            if "Sign in" in msg or "cookies" in msg.lower():
                msg += " — cookies протухли, экспортируйте заново"
            raise SourceUnavailable(f"YouTube Music: {msg[:200]}")

    async def fetch_playlist_tracks(self, client, playlist_ext: str):
        try:
            return await run_in_threadpool(_fetch_playlist, playlist_ext)
        except SourceUnavailable:
            raise
        except Exception as error:
            raise SourceUnavailable("YouTube Music: плейлист недоступен") from error

    async def resolve(self, client: httpx.AsyncClient, external_id: str,
                      **_) -> Resolved:
        try:
            return await run_in_threadpool(_resolve, external_id)
        except SourceUnavailable:
            raise
        except Exception as e:
            raise SourceUnavailable(f"YouTube Music: {str(e)[:200]}")
