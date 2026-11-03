"""Тексты треков через открытый Lrclib API (бесплатно, без ключей)."""
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
        # Точного совпадения нет — ищем по строке и берём самый подходящий вариант.
        search = await client.get(
            f"{LRCLIB}/search",
            params={"q": f"{artist} {title}"},
            headers=HEADERS,
            timeout=15,
        )
        if search.status_code == 200 and isinstance(search.json(), list):
            found = search.json()
            found.sort(key=lambda x: 0 if x.get("syncedLyrics") else 1)
            item = found[0] if found else None
    if not item:
        return {"synced": None, "plain": None}
    return {"synced": item.get("syncedLyrics"), "plain": item.get("plainLyrics")}
