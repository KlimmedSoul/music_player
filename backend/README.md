# MyPlayer backend

Бэкенд личного музыкального агрегатора (ТЗ: `/tz-music-player.md`).
Python 3.11+, FastAPI + SQLite. Один процесс, внешних сервисов не требует.

## Запуск

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # вписать токены, см. ниже
.venv/bin/python run.py            # http://0.0.0.0:8080
```

Доступ к API — по JWT: `POST /auth/login {"password": "***"}` →
`Authorization: Bearer <token>` на всех остальных запросах, кроме
`/healthz` и `/stream/<token>` (аудио в `<audio src>` заголовок не шлёт;
токен стрима выдаётся `/resolve` на сутки).

## Как получить токены в .env

### VK_TOKEN
Аудио в официальном API VK закрыто с 2017, работает только user-токен.
1. Авторизуйтесь на `id.vk.com`, откройте devtools → Application → Local Storage.
2. Либо через любой «vk-audio-клиент»/VK Auth-приложение с правами audio.
3. Токен вида `vk1.a....` — в `VK_TOKEN`.
Истёк/отозван — `/status` покажет `error` при refresh, нужен новый.

### SOUNDCLOUD_OAUTH_TOKEN + SOUNDCLOUD_CLIENT_ID
1. Залогиньтесь на soundcloud.com, devtools → Network.
2. Любой запрос к `api-v2.soundcloud.com/...` содержит `client_id` и
   `oauth_token` — скопировать оба в `.env`.
Токен живёт ~неделю; признаки протухания — `whoami 401` в логах.

### Spotify
1. developer.spotify.com → Create app → Redirect URI:
   `http://localhost:8600/callback` (порт = PORT).
2. Client ID/Secret → в `.env`.
3. Вход: открыть в браузере `GET /auth/spotify/start` (с JWT) → перейти по
   `url` → после согласия токен сохранится в базе автоматически.
Spotify отдаёт лайки/плейлисты полностью, но звук — превью 30 сек; полный
звук коннектор ищет аналогом на YouTube, если установлен `yt-dlp`
(`pip install yt-dlp`). Без него — превью.

## API (контракт для фронта)

| Метод | Путь | Описание |
|---|---|---|
| POST | `/auth/login` `{password}` | → `{token}` |
| GET | `/healthz` | без auth, `{ok:true}` |
| GET | `/status` | конфиг/статус источников, `spotify_logged_in` |
| POST | `/library/refresh?source=vk\|soundcloud\|spotify\|(пусто=all)` | pull лайков+плейлистов, ответ: статус по каждому источнику |
| GET | `/library/tracks?source=all&q=&cursor=0&limit=50` | дедуп-объединённые треки; `items[].sources` — где ещё есть; `next_cursor` |
| GET | `/library/playlists` | плейлисты всех источников (`source`,`external_id`,`title`,`cover`) |
| GET | `/library/playlists/{source}/{ext}/tracks` | состав плейлиста (ленивый добор с источника) |
| POST | `/resolve` `{source, external_id}` | → `{kind:"proxy", stream_path:"/stream/<token>"}` или `{kind:"direct", stream_url}` |
| GET | `/stream/<token>` | аудио-прокси с Range (seek). Без JWT |
| GET | `/auth/spotify/start` | → `{url}` для OAuth-перехода |
| GET | `/auth/spotify/callback` | обработчик OAuth (редирект Spotify) |

Ошибки: 401 нет/просрочен JWT, 502 + `detail` — источник недоступен
(фронту показывать плашку, остальное не блокировать).

Пример плеера: `refresh` раз в 6 ч и по кнопке → `tracks` →
по тапу `resolve` → `<audio src=stream_path>` (для `direct` — `stream_url`
напрямую, это HLS, iPhone играет сам) → при 404/403 у источника снова
`resolve` (ссылки VK живут ~час).

## Архитектура

```
app/
  main.py          — роуты FastAPI
  library.py       — единая база треков/плейлистов, дедуп, resolve+кэш URL
  stream.py        — Range-прокси аудио (выдача только по выданным токенам)
  auth.py          — mini-JWT (HS256)
  db.py            — SQLite (WAL), таблицы tracks/playlists/url_cache/...
  connectors/
    vk.py          — VK user-token (полный звук)
    soundcloud.py  — api-v2 (лайки + полные mp3/HLS)
    spotify.py     — официальный API (каталог) + yt-dlp-резолвер (опция)
```

Безопасность: `/stream` отдаёт только URL, выданные `/resolve` (защита от
открытого прокси); бэкенд за паролем+туннелем (Tailscale/Cloudflare),
наружу из домашней сети его лучше не выставлять.

## Дисклеймер

VK-аудио, SoundCloud api-v2 и YouTube-резолвинг — неофициальные пути,
нарушающие ToS соответствующих сервисов. Только чтение собственных данных и
личное прослушивание; ничего не тиражировать. Риски (отзыв токена, бан API)
осознанные и лежат на владельце аккаунтов.
