"""Тексты треков через открытый Lrclib API (бесплатно, без ключей)."""
import re
from difflib import SequenceMatcher

import httpx

LRCLIB = "https://lrclib.net/api"
# Lrclib просит описательный User-Agent и ограничивает агрессивные запросы.
HEADERS = {"User-Agent": "MyPlayer/1.0 (personal music aggregator)"}


async def find_lyrics(
    client: httpx.AsyncClient, title: str, artist: str, duration_s: float = 0
) -> dict:
    params = {"track_name": title, "artist_name": artist}
    if duration_s and duration_s < 86400:
        params["duration"] = int(duration_s)
    item = None
    exact = await client.get(f"{LRCLIB}/get", params=params, headers=HEADERS, timeout=15)
    if exact.status_code == 200 and isinstance(exact.json(), dict):
        item = exact.json()
    else:
        if exact.status_code != 404:
            exact.raise_for_status()
        # Точного совпадения нет — ищем по строке и берём самый подходящий вариант.
        search = await client.get(
            f"{LRCLIB}/search",
            params={"q": f"{artist} {title}"},
            headers=HEADERS,
            timeout=15,
        )
        search.raise_for_status()
        found = search.json()
        if not isinstance(found, list):
            raise ValueError("Invalid lyrics response")
        normalize = lambda value: " ".join(re.findall(r"\w+", str(value or "").casefold()))
        ranked = []
        for candidate in found:
            if not isinstance(candidate, dict):
                continue
            title_score = SequenceMatcher(None, normalize(title), normalize(candidate.get("trackName"))).ratio()
            artist_score = SequenceMatcher(None, normalize(artist), normalize(candidate.get("artistName"))).ratio() if artist else 1
            if title_score < .65 or artist_score < .55:
                continue
            candidate_duration = candidate.get("duration")
            if duration_s and isinstance(candidate_duration, (int, float)) and abs(candidate_duration-duration_s) > max(15, duration_s*.1):
                continue
            ranked.append((title_score + artist_score + (.01 if candidate.get("syncedLyrics") else 0), candidate))
        item = max(ranked, key=lambda result: result[0])[1] if ranked else None
    if not item:
        return {"synced": None, "plain": None}
    return {"synced": item.get("syncedLyrics"), "plain": item.get("plainLyrics")}
