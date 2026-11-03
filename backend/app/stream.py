"""Прокси аудио: Range pass-through (seek), выдача только по выданному токену.
Защита от открытого прокси: /stream/<token> работает лишь для URL,
которые сами отдали коннекторы через /resolve (таблица issued_streams)."""
import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

_HOP_BY_HOP = {
    "transfer-encoding", "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers", "upgrade", "content-encoding",
}
MAX_STREAM_BYTES = 512 * 1024 * 1024

_FORWARD = {"content-type", "content-length", "content-range", "accept-ranges"}


def is_valid_token(lookup, token: str) -> str:
    url = lookup(token)
    if not url:
        raise HTTPException(404, "токен не найден или просрочен")
    return url


async def proxy_stream(client: httpx.AsyncClient, url: str, range_header: str | None):
    headers = {"Accept-Encoding":"identity", **({"Range":range_header} if range_header else {})}
    try:
        upstream = await client.send(
            client.build_request("GET", url, headers=headers, timeout=httpx.Timeout(60, connect=10)), stream=True
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, "источник недоступен")
    if upstream.status_code >= 400:
        await upstream.aclose()
        raise HTTPException(502, f"источник отдал {upstream.status_code}")

    try:
        length = int(upstream.headers.get("content-length", "0"))
    except ValueError:
        length = 0
    if length > MAX_STREAM_BYTES:
        await upstream.aclose()
        raise HTTPException(502, "Аудиопоток превышает допустимый размер")
    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() in _FORWARD}
    resp_headers["accept-ranges"] = "bytes"

    async def body():
        received = 0
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                received += len(chunk)
                if received > MAX_STREAM_BYTES:
                    break
                yield chunk
        finally:
            await upstream.aclose()

    status = 206 if upstream.status_code == 206 else 200
    return StreamingResponse(body(), status_code=status, headers=resp_headers,
                             media_type=upstream.headers.get("content-type"))
