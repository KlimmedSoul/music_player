"""Best-effort public cover lookup when a provider has no usable artwork."""
import asyncio
import re
import time
from collections import OrderedDict
from difflib import SequenceMatcher
from urllib.parse import urlparse

import httpx

_cache: OrderedDict[tuple[str, str], tuple[float, str | None]] = OrderedDict()
_slots = asyncio.Semaphore(4)


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold()))


async def find_cover(client: httpx.AsyncClient, title: str, artist: str) -> str | None:
    key = (_normalize(title), _normalize(artist))
    if not all(key):
        return None
    cached = _cache.get(key)
    if cached and cached[0] > time.monotonic():
        _cache.move_to_end(key)
        return cached[1]
    async with _slots:
        cached = _cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            response = await client.get(
                "https://itunes.apple.com/search",
                params={"term": f"{artist} {title}", "entity": "song", "limit": 8},
                timeout=8,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            best, cover = 0.0, None
            for item in results:
                title_score = SequenceMatcher(None, key[0], _normalize(item.get("trackName", ""))).ratio()
                artist_score = SequenceMatcher(None, key[1], _normalize(item.get("artistName", ""))).ratio()
                url = item.get("artworkUrl100") or item.get("artworkUrl60") or ""
                parsed = urlparse(url)
                if (title_score < .65 or artist_score < .55 or parsed.scheme != "https"
                        or not (parsed.hostname or "").endswith(".mzstatic.com")):
                    continue
                score = title_score + artist_score
                if score > best:
                    best = score
                    cover = re.sub(r"/\d+x\d+bb\.", "/600x600bb.", url)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            # A failed artwork service never prevents loading/playing a track.
            return None
        _cache[key] = (time.monotonic() + (86400 if cover else 3600), cover)
        _cache.move_to_end(key)
        while len(_cache) > 512:
            _cache.popitem(last=False)
        return cover
