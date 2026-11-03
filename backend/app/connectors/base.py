import re
from dataclasses import dataclass, field


@dataclass
class Track:
    source: str
    external_id: str
    title: str
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    isrc: str | None = None
    cover: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class Playlist:
    source: str
    external_id: str
    title: str
    cover: str | None = None
    total: int = 0
    track_ids: list[str] = field(default_factory=list)  # external_id треков


@dataclass
class Resolved:
    stream_url: str
    kind: str = "proxy"  # proxy = отдавать через /stream (CORS/IP-bound), direct = дать фронту URL как есть
    ttl_s: int = 1800


def clean_title(title: str) -> str:
    """Нормализация для дедупликации между сервисами."""
    t = title.lower().strip()
    t = re.sub(r"[\(\)\[\]{}]", " ", t)
    t = re.sub(r"\b(official( audio| video)?|lyrics?|hq|audio)\b", " ", t)
    t = re.sub(r"(?<=[\s,\-\(\[])(?:feat\.?|ft\.?)\s+.*$", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def dedup_key(title: str, artist: str, duration_ms: int, isrc: str | None = None) -> str:
    if isrc and isrc.strip():
        return "isrc:" + re.sub(r"[^A-Z0-9]", "", isrc.upper())
    dur = str(round(duration_ms / 5000)) if duration_ms else "u"
    return f"{clean_title(title)}|{artist.lower().strip()}|{dur}"


class SourceUnavailable(Exception):
    """Источник не настроен или недоступен — UI покажет плашку."""
