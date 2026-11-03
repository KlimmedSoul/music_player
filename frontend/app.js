"use strict";
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHtml = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const names = {
  vk: "VK",
  soundcloud: "SoundCloud",
  spotify: "Spotify",
  ytmusic: "YouTube Music",
  all: "Все",
};
const api = new MusicAPI();
const player = new MusicPlayer(api);
const artwork = new MusicArtwork(api);
const state = {
  page: "home",
  source: "all",
  query: "",
  tracks: [],
  playlists: [],
  playlist: null,
  playlistTracks: [],
  status: {},
  refreshResults: {},
  cursor: 0,
  hasMore: false,
  loading: false,
  refreshing: false,
  errors: [],
  history: [],
  favorites: [],
  positions: new Map(),
  lastRefresh: null,
  spotifyLoggedIn: false,
};
let viewVersion = 0,
  controller,
  searchTimer,
  toastTimer,
  sessionVersion = 0,
  lastPlayed = null,
  lastPosSent = 0,
  lyricsActive = -2;
const queues = new Map();
const trackKey = (track) =>
  track.dedup_key || JSON.stringify([track.source, String(track.external_id)]);
const playlistKey = (playlist) =>
  JSON.stringify([playlist.source, String(playlist.external_id)]);
function read(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(`${key}:${api.base}`)) ?? fallback;
  } catch {
    return fallback;
  }
}
function write(key, value) {
  try {
    localStorage.setItem(`${key}:${api.base}`, JSON.stringify(value));
  } catch {}
}
function favorites() {
  return state.favorites;
}
function isFavorite(track) {
  return (
    track && favorites().some((item) => trackKey(item) === trackKey(track))
  );
}
const positionKey = (track) =>
  `${track.source}|${String(track.external_id)}`;
async function syncFavorites(options) {
  state.favorites = (await api.favorites(options)).items || [];
}
async function syncPositions(options) {
  const data = await api.positions(options);
  state.positions = new Map(
    (data.items || []).map((p) => [
      `${p.track_source}|${p.track_ext}`,
      Number(p.position_s) || 0,
    ]),
  );
}
async function migrateLegacyFavorites() {
  const legacy = read("myplayer-favorites", []);
  if (!Array.isArray(legacy) || !legacy.length) return;
  try {
    for (const track of legacy.slice(0, 200))
      if (!isFavorite(track)) await api.toggleFavorite(track);
    state.favorites = (await api.favorites()).items || [];
    localStorage.removeItem(`myplayer-favorites:${api.base}`);
    toast("Любимые треки теперь хранятся на сервере");
  } catch {}
  if (api.token) {
    renderSidebar();
    if (state.page === "favorites") renderContent();
  }
}
function time(seconds) {
  if (!Number.isFinite(seconds)) return "0:00";
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
function imageURL(value) {
  try {
    const url = new URL(value);
    if (["http:", "https:"].includes(url.protocol)) return url.href;
  } catch {}
  return "assets/cover.svg";
}
function image(value, alt = "", className = "") {
  return `<img src="${escapeHtml(imageURL(value))}" alt="${escapeHtml(alt)}" class="${className}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
}
function trackImage(track) {
  return `<img src="${escapeHtml(imageURL(track.cover))}" alt="${escapeHtml(`Обложка: ${track.title || "трек"}`)}" data-artwork-title="${escapeHtml(track.title)}" data-artwork-artist="${escapeHtml(track.artist)}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`;
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 4200);
}
function showModal(content) {
  $("#modal-body").innerHTML = content;
  if (!$("#modal").open) $("#modal").showModal();
}
function closeModal() {
  if ($("#modal").open) $("#modal").close();
}
function busy(label = "Загружаем твою музыку…") {
  return `<div class="loading-state" role="status"><span class="spinner"></span>${label}</div>`;
}
function empty(title, text, action = "") {
  return `<div class="empty-state">${icon("library")}<h3>${title}</h3><p>${text}</p>${action}</div>`;
}
function refreshButton() {
  return `<button class="primary-button" data-action="refresh" ${state.refreshing ? "disabled" : ""}>${icon("repeat")}${state.refreshing ? "Синхронизация…" : "Обновить библиотеку"}</button>`;
}
function filters() {
  return `<div class="filters source-filters" aria-label="Источник музыки">${Object.entries(
    names,
  )
    .sort(([a], [b]) => (a === "all" ? -1 : b === "all" ? 1 : 0))
    .map(
      ([key, name]) =>
        `<button class="filter-chip ${state.source === key ? "active" : ""}" data-source="${key}" aria-pressed="${state.source === key}">${name}</button>`,
    )
    .join(
      "",
    )}<button class="icon-button sync-button" data-action="refresh" aria-label="Обновить библиотеку" ${state.refreshing ? "disabled" : ""}>${icon("repeat")}</button></div>`;
}
function errorBanner(message) {
  return `<div class="error-banner" role="status">${escapeHtml(message)}<button data-action="reload">Повторить</button></div>`;
}
function trackList(items, queue = "main") {
  queues.set(queue, items.slice());
  return `<div class="track-list">${items.map((track, i) => `<button class="track ${trackKey(track) === (player.current && trackKey(player.current)) ? "current-track" : ""}" data-queue="${queue}" data-index="${i}" aria-label="Включить ${escapeHtml(track.title)}"><span class="track-number">${i + 1}</span>${trackImage(track)}<span class="track-info"><strong>${escapeHtml(track.title || "Без названия")}</strong><small>${escapeHtml(track.artist || "Неизвестный исполнитель")} <span class="source-tag">${escapeHtml((track.sources || [track.source]).map((source) => names[source] || source).join(" · "))}</span></small></span><span class="track-duration">${time((track.duration_ms || 0) / 1000)}</span>${icon("play")}</button>`).join("")}</div>`;
}
function card(playlist) {
  return `<button class="music-card" data-playlist="${escapeHtml(playlistKey(playlist))}"><div class="cover-wrap">${image(playlist.cover)}<span class="source-badge">${escapeHtml(names[playlist.source] || playlist.source)}</span><span class="card-play">${icon("play")}</span></div><strong>${escapeHtml(playlist.title)}</strong><p>${Number(playlist.total) || 0} треков · ${escapeHtml(names[playlist.source] || playlist.source)}</p></button>`;
}
function loadMoreButton() {
  return state.hasMore
    ? '<button class="load-more" data-action="more">Показать ещё</button>'
    : "";
}
function renderSidebar() {
  $("#sidebar-list").innerHTML =
    `<button class="side-item" data-page="favorites"><img src="assets/liked.svg" alt=""><span><strong>Любимые треки</strong><small>На сервере · ${favorites().length}</small></span></button>${state.playlists
      .slice(0, 12)
      .map(
        (p) =>
          `<button class="side-item" data-playlist="${escapeHtml(playlistKey(p))}">${image(p.cover)}<span><strong>${escapeHtml(p.title)}</strong><small>${escapeHtml(names[p.source])}</small></span></button>`,
      )
      .join("")}`;
}
function homeContent() {
  const playlists = state.playlists.filter(
    (p) => state.source === "all" || p.source === state.source,
  );
  return `${state.errors.map(errorBanner).join("")}<div class="quick-grid"><button class="quick-card" data-page="favorites"><img src="assets/liked.svg" alt=""><strong>Любимые треки</strong></button>${playlists
    .slice(0, 3)
    .map(
      (p) =>
        `<button class="quick-card" data-playlist="${escapeHtml(playlistKey(p))}">${image(p.cover)}<strong>${escapeHtml(p.title)}</strong></button>`,
    )
    .join("")}</div>
  <div class="chapter-line"><b>01 / ПОСЛЕ ПОЛУНОЧИ</b><span>音楽 · MUSIC ARCHIVE</span></div><section class="hero"><div class="hero-content"><span class="eyebrow">${icon("logo")}MYPLAYER / ГЛАВА 01</span><h2>Город затих.<br><span>Твой звук — нет.</span></h2><p>VK, SoundCloud, Spotify и YouTube Music.<br>Одна библиотека. Твой любимый звук.</p>${state.tracks.length ? `<button class="primary-button" data-queue="main" data-index="0">${icon("play")}Слушать мою музыку</button>` : refreshButton()}</div><span class="hero-stamp" aria-hidden="true">夜の音</span><span class="hero-index">AFTER DARK / VOL. 01</span></section>
  ${playlists.length ? `<section class="music-section"><div class="section-heading"><div><h2>Твои плейлисты</h2><p>Из любимых музыкальных сервисов</p></div><button class="text-button" data-page="library">Все ${icon("arrow")}</button></div><div class="card-row">${playlists.slice(0, 8).map(card).join("")}</div></section>` : ""}
  <section class="music-section"><div class="section-heading"><div><h2>Твоя музыка</h2><p>${state.source === "all" ? "Все источники. Без дублей." : names[state.source]}</p></div><button class="text-button" data-action="settings">Источники ${icon("arrow")}</button></div>${state.loading ? busy() : state.tracks.length ? trackList(state.tracks) + loadMoreButton() : empty("Здесь будет твой звук", "Подключи источники и обнови библиотеку.", '<button class="primary-button" data-action="settings">Подключить музыку</button>')}</section>`;
}
function searchContent() {
  return `${state.errors.map(errorBanner).join("")}${state.loading ? busy("Ищем в твоей библиотеке…") : state.tracks.length ? `<div class="section-heading"><h2>${state.query ? "Нашлось в библиотеке" : "Все твои треки"}</h2></div>${trackList(state.tracks)}${loadMoreButton()}` : empty(state.query ? "Ничего не найдено" : "Музыка ещё не загружена", state.query ? "Попробуй другое название или выбери другой источник." : "Поиск работает по импортированным трекам. Нажми «Обновить», чтобы загрузить музыку из сервисов.", state.query ? "" : refreshButton())}`;
}
function libraryContent() {
  const playlists = state.playlists.filter(
    (p) => state.source === "all" || p.source === state.source,
  );
  return `${state.errors.map(errorBanner).join("")}<div class="library-top"><div class="section-heading"><h2>Всегда под рукой</h2></div><button class="text-button" data-action="refresh" ${state.refreshing ? "disabled" : ""}>${icon("repeat")}${state.refreshing ? "Обновляем…" : "Обновить"}</button></div><button class="favorites-tile" data-page="favorites"><img src="assets/liked.svg" alt=""><span><strong>Любимые треки</strong><small>${favorites().length} треков · На сервере</small></span>${icon("arrow")}</button>${playlists.length ? `<div class="library-grid">${playlists.map(card).join("")}</div>` : state.loading ? busy() : empty("Плейлисты появятся здесь", "Они загружаются из подключённых сервисов.", '<button class="primary-button" data-action="settings">Мои источники</button>')}`;
}
function playlistContent() {
  const p = state.playlist;
  if (!p) return empty("Плейлист не найден", "Открой его из медиатеки.");
  return `<button class="back-button" data-page="library">${icon("arrow")}В медиатеку</button><section class="playlist-hero">${image(p.cover, `Обложка ${p.title}`)}<div><span class="eyebrow">ПЛЕЙЛИСТ · ${escapeHtml(names[p.source])}</span><h2>${escapeHtml(p.title)}</h2><p>${state.loading ? "Загружаем состав…" : `${state.playlistTracks.length} треков`}</p></div></section>${state.errors.map(errorBanner).join("")}${state.loading ? busy() : state.playlistTracks.length ? `<div class="playlist-actions"><button class="primary-button" data-queue="playlist" data-index="0">${icon("play")}Слушать</button></div>${trackList(state.playlistTracks, "playlist")}` : empty("В плейлисте пока нет треков", "Обнови библиотеку или выбери другой плейлист.")}`;
}
function favoritesContent() {
  const items = favorites().filter(
    (t) =>
      state.source === "all" ||
      t.source === state.source ||
      t.sources?.includes(state.source),
  );
  return `<div class="section-heading"><div><h2>То, что в самое сердце</h2><p>Хранятся на твоём сервере — доступны с любого устройства</p></div></div>${items.length ? trackList(items, "favorites") : empty("Пока ни одного трека", "Нажми «+» в плеере, и трек останется в любимых на всех устройствах.")}`;
}
function renderContent() {
  const target = $("#view-content");
  if (!target || !api.token) return;
  queues.clear();
  target.innerHTML = {
    home: homeContent,
    search: searchContent,
    library: libraryContent,
    playlist: playlistContent,
    favorites: favoritesContent,
  }[state.page]();
  renderSidebar();
}
function renderShell() {
  if (!api.token) return renderLogin();
  document.body.classList.remove("logged-out");
  const titles = {
    home: "На твоей частоте",
    search: "Найди свой звук",
    library: "Моя медиатека",
    playlist: "Твоя музыка",
    favorites: "Любимые треки",
  };
  $("#page-title").innerHTML =
    `${titles[state.page]}<span class="green-dot">.</span>`;
  $$("[data-page]").forEach((button) => {
    const active =
      button.dataset.page === state.page ||
      (["playlist", "favorites"].includes(state.page) &&
        button.dataset.page === "library");
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("#main").innerHTML =
    `${state.page !== "playlist" ? filters() : ""}${state.page === "search" ? `<label class="search-field">${icon("search")}<input type="search" id="search-input" value="${escapeHtml(state.query)}" autocomplete="off" placeholder="Треки и исполнители" aria-label="Поиск по библиотеке"></label>` : ""}<div id="view-content" aria-live="polite"></div>`;
  renderContent();
  updatePlayer();
}
function renderLogin(message = "") {
  document.body.classList.add("logged-out");
  closeModal();
  $("#page-title").textContent = "MyPlayer / After dark";
  $("#main").innerHTML =
    `<section class="login-layout"><div class="login-art"><span class="eyebrow">ЛИЧНЫЙ МУЗЫКАЛЬНЫЙ АРХИВ / VOL. 01</span><h2>Весь город спит.<br><span>Ты слушаешь.</span></h2><p>Собери VK, SoundCloud, Spotify и YouTube Music<br>в одном музыкальном пространстве.</p><span class="login-stamp" aria-hidden="true">夜 / AFTER DARK</span></div><form id="login-form" class="login-card"><span class="eyebrow">НАЧАТЬ НОВУЮ ГЛАВУ</span><h2>Включай своё.</h2><p>Подключись к своему музыкальному серверу.</p><label for="api-url">Адрес сервера</label><input type="url" id="api-url" name="server" value="${escapeHtml(api.base)}" placeholder="https://myplayer.example.com" required autocomplete="url" inputmode="url"><label for="login-password">Пароль</label><input id="login-password" name="password" type="password" placeholder="Пароль твоего сервера" required autocomplete="current-password"><p id="login-error" class="form-error" role="alert">${escapeHtml(message)}</p><button class="primary-button" type="submit">${icon("play")}Войти в мою музыку</button><small>Твои аккаунты остаются у тебя.<br>Пароль не сохраняется в браузере.</small></form></section>`;
}
async function loadView() {
  if (!api.token) return;
  clearTimeout(searchTimer);
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  const version = ++viewVersion;
  const token = api.token;
  const page = state.page;
  state.loading = true;
  state.errors = [];
  renderContent();
  try {
    if (page === "playlist") {
      const p = state.playlist;
      if (!p) return;
      const data = await api.playlistTracks(p.source, p.external_id, {
        signal,
      });
      if (version !== viewVersion || token !== api.token) return;
      state.playlistTracks = data.items || [];
    } else if (page === "favorites") {
      await syncFavorites({ signal });
      if (version !== viewVersion || token !== api.token) return;
    } else {
      const jobs =
        page === "library"
          ? [api.playlists({ signal })]
          : [
              api.tracks({
                source: state.source,
                q: page === "search" ? state.query : "",
                signal,
              }),
              api.playlists({ signal }),
            ];
      const results = await Promise.allSettled(jobs);
      if (version !== viewVersion || token !== api.token) return;
      results.forEach((result, index) => {
        if (result.status === "rejected") {
          if (result.reason.name !== "AbortError")
            state.errors.push(result.reason.message);
          return;
        }
        if (page !== "library" && index === 0) {
          state.tracks = result.value.items || [];
          state.cursor = result.value.next_cursor;
          state.hasMore =
            state.tracks.length === 50 && Number.isFinite(state.cursor);
        } else state.playlists = result.value.items || [];
      });
      if (results[0].status === "rejected" && page !== "library") {
        state.tracks = [];
        state.hasMore = false;
      }
    }
  } catch (error) {
    if (error.name !== "AbortError" && version === viewVersion)
      state.errors = [error.message];
  } finally {
    if (version === viewVersion && token === api.token) {
      state.loading = false;
      renderContent();
    }
  }
}
async function more(button) {
  const version = viewVersion,
    token = api.token;
  button.disabled = true;
  button.textContent = "Загружаем…";
  try {
    const data = await api.tracks({
      source: state.source,
      q: state.page === "search" ? state.query : "",
      cursor: state.cursor,
    });
    if (version !== viewVersion || token !== api.token) return;
    const keys = new Set(state.tracks.map(trackKey));
    state.tracks.push(
      ...(data.items || []).filter((t) => !keys.has(trackKey(t))),
    );
    state.cursor = data.next_cursor;
    state.hasMore =
      data.items?.length === 50 && Number.isFinite(data.next_cursor);
    renderContent();
  } catch (error) {
    toast(error.message);
    button.disabled = false;
    button.textContent = "Показать ещё";
  }
}
function navigate(page, playlist = null) {
  if (!api.token) return;
  if (playlist) state.playlist = playlist;
  state.page = page;
  state.errors = [];
  state.playlistTracks = [];
  state.tracks = [];
  state.loading = true;
  const hash =
    page === "playlist"
      ? `playlist/${encodeURIComponent(playlistKey(state.playlist))}`
      : page;
  history.pushState(null, "", `#${hash}`);
  renderShell();
  loadView();
  window.scrollTo({ top: 0, behavior: "instant" });
  $("#main").focus({ preventScroll: true });
}
async function checkStatus() {
  const token = api.token;
  try {
    const data = await api.status();
    if (token !== api.token) return;
    state.status = data.sources || {};
    state.lastRefresh = data.last_refresh || null;
    state.spotifyLoggedIn = Boolean(data.spotify_logged_in);
  } catch (error) {
    if (api.token) toast(error.message);
  }
}
function resetSession() {
  ++sessionVersion;
  ++viewVersion;
  controller?.abort();
  clearTimeout(searchTimer);
  player.reset();
  lastPlayed = null;
  state.tracks = [];
  state.playlists = [];
  state.playlistTracks = [];
  state.status = {};
  state.refreshResults = {};
  state.history = [];
  state.favorites = [];
  state.positions = new Map();
  state.lastRefresh = null;
  state.spotifyLoggedIn = false;
  state.errors = [];
  state.loading = false;
  state.refreshing = false;
  state.page = "home";
  state.source = "all";
  state.query = "";
}
async function enter() {
  state.page = "home";
  renderShell();
  await Promise.all([
    checkStatus(),
    loadView(),
    syncFavorites().catch(() => {}),
    syncPositions().catch(() => {}),
  ]);
  if (!api.token) return;
  renderSidebar();
  if (state.page === "favorites") renderContent();
  migrateLegacyFavorites();
}
async function sync() {
  if (state.refreshing || !api.token) return;
  const session = sessionVersion;
  state.refreshing = true;
  renderShell();
  try {
    const results = await api.refresh(state.source);
    if (session !== sessionVersion) return;
    state.refreshResults = results;
    const failures = Object.values(results).some((value) =>
      String(value).startsWith("error"),
    );
    toast(
      failures
        ? "Часть источников недоступна. Остальная музыка доступна — подробности в «Источниках»."
        : Object.values(results).every((value) => value === "not_configured")
          ? "Источники ещё не настроены. Открой настройки подключения."
          : "Библиотека обновлена",
    );
    await Promise.all([checkStatus(), loadView()]);
  } catch (error) {
    if (session === sessionVersion && api.token) {
      state.errors = [error.message];
      toast(error.message);
    }
  } finally {
    if (session === sessionVersion) {
      state.refreshing = false;
      if (api.token) renderShell();
    }
  }
}
function settings() {
  const configured = (key) => state.status[key] === "configured";
  const refresh = state.refreshResults;
  const last = state.lastRefresh?.sources || {};
  const health = (key) => refresh[key] || last[key] || "";
  const healthClass = (value) =>
    String(value).startsWith("error")
      ? "error"
      : String(value).startsWith("ok")
        ? "ok"
        : "";
  const checkedAt = state.lastRefresh?.ts
    ? new Date(state.lastRefresh.ts * 1000).toLocaleString("ru", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
  showModal(
    `<span class="eyebrow">ТВОИ ИСТОЧНИКИ</span><h2>Вместе звучит лучше.</h2><p>Подключение, синхронизация и здоровье музыкальных сервисов.</p><div class="source-list">${["vk", "soundcloud", "spotify", "ytmusic"].map((key) => {
      const value = health(key);
      const ready = configured(key) && !String(value).startsWith("error");
      return `<div class="source-item"><strong>${names[key]}</strong><span class="status-dot ${ready ? "ready" : ""} ${String(value).startsWith("error") ? "error" : ""}"></span><small>${configured(key) ? (key === "spotify" && !state.spotifyLoggedIn ? "Настроен — нужен вход" : String(value).startsWith("error") ? "Не отвечает" : "Работает") : "Не настроен"}</small></div>${value ? `<p class="sync-result ${healthClass(value)}">${escapeHtml(String(value))}</p>` : ""}`;
    }).join("")}</div><p class="settings-note">${checkedAt ? `Последняя синхронизация: ${checkedAt}` : "Библиотека ещё не синхронизировалась."}</p><button class="load-more" data-action="check-status">Проверить источники</button>${configured("spotify") ? '<button class="primary-button" data-action="spotify">Подключить Spotify</button><p id="spotify-result" role="status"></p>' : ""}<button class="load-more" data-action="refresh">Обновить библиотеку</button><p class="settings-note">VK, YouTube Music и SoundCloud подключаются токенами в настройках сервера. Для Spotify нужен настроенный Redirect URI.</p><p class="server-address">${escapeHtml(api.base)}</p><button class="text-button" data-action="logout">Выйти / сменить сервер ${icon("arrow")}</button>`,
  );
}
async function spotify(button) {
  const popup = window.open("about:blank", "_blank");
  if (popup) popup.opener = null;
  button.disabled = true;
  try {
    const data = await api.spotify();
    if (!api.token) {
      popup?.close();
      return;
    }
    const url = new URL(data.url);
    if (
      url.origin !== "https://accounts.spotify.com" ||
      url.pathname !== "/authorize"
    )
      throw new Error("Сервер вернул неожиданный адрес входа Spotify.");
    if (popup) {
      popup.location = url.href;
      $("#spotify-result").innerHTML =
        "После входа вернись сюда и нажми «Обновить библиотеку».";
    } else {
      const target = $("#spotify-result");
      target.innerHTML =
        '<a class="primary-button" id="spotify-link" target="_blank" rel="noopener noreferrer">Открыть вход Spotify</a>';
      $("#spotify-link").href = url.href;
    }
  } catch (error) {
    popup?.close();
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}
function updatePlayer() {
  const track = player.current;
  if (
    track &&
    $("#mobile-seek") &&
    $("#modal-body").dataset.track !== trackKey(track)
  ) {
    currentTrackModal();
    return;
  }
  $("#player-title").textContent = track?.title || "Выбери трек";
  $("#player-artist").textContent = player.loading
    ? "Подключаем аудио…"
    : track?.artist || "Твоя музыка ждёт";
  artwork.setTrack($("#player-cover"), track);
  $("#play-toggle").innerHTML = player.loading
    ? '<span class="spinner"></span>'
    : icon(player.playing ? "pause" : "play");
  $("#play-toggle").disabled = !track || player.loading;
  $("#play-toggle").setAttribute(
    "aria-label",
    player.loading
      ? "Загрузка аудио"
      : player.playing
        ? "Пауза"
        : "Воспроизвести",
  );
  $("#play-toggle").setAttribute("aria-pressed", String(player.playing));
  document.body.classList.toggle("is-playing", player.playing);
  $("#like-track").disabled = !track;
  $("#like-track").innerHTML = icon(isFavorite(track) ? "check" : "plus");
  $("#like-track").setAttribute("aria-pressed", String(!!isFavorite(track)));
  $("#like-track").setAttribute(
    "aria-label",
    isFavorite(track) ? "Убрать из любимых треков" : "В любимые треки",
  );
  $("#progress").max = player.duration || 0;
  $("#progress").value = player.audio.currentTime || 0;
  $("#progress").disabled = !track || player.audio.readyState < 1;
  $("#current-time").textContent = time(player.audio.currentTime);
  $(".progress-row>span:last-child").textContent = time(player.duration);
  $("#mobile-progress").style.width =
    `${player.duration ? (player.audio.currentTime / player.duration) * 100 : 0}%`;
  $("#next").disabled =
    !track || (!player.shuffle && player.index >= player.queue.length - 1);
  $("#previous").disabled = !track;
  $("#shuffle").setAttribute("aria-pressed", String(player.shuffle));
  $("#repeat").setAttribute("aria-pressed", String(player.repeat));
  $$('[data-action="toggle-audio"]').forEach((button) => {
    button.innerHTML =
      icon(player.playing ? "pause" : "play") +
      (player.playing ? "Пауза" : "Слушать");
    button.disabled = player.loading;
  });
  if ($("#mobile-seek") && document.activeElement !== $("#mobile-seek")) {
    $("#mobile-seek").max = player.duration || 0;
    $("#mobile-seek").value = player.audio.currentTime || 0;
    $("#mobile-time").textContent =
      `${time(player.audio.currentTime)} / ${time(player.duration)}`;
  }
  highlightLyrics();
}
async function toggleFavorite() {
  const track = player.current;
  if (!track) return;
  let result;
  try {
    result = await api.toggleFavorite(track);
    await syncFavorites();
  } catch (error) {
    return toast(error.message);
  }
  toast(
    result.favorite
      ? "Добавлено в любимые треки — на всех устройствах"
      : "Убрано из любимых треков",
  );
  updatePlayer();
  renderSidebar();
  if (state.page === "favorites") renderContent();
}
function currentTrackModal() {
  const track = player.current;
  if (!track) return toast("Сначала выбери трек в библиотеке");
  $("#modal-body").dataset.track = trackKey(track);
  showModal(
    `<span class="eyebrow">СЕЙЧАС В ПЛЕЕРЕ · ${escapeHtml(names[track.source])}</span><section class="playlist-hero">${trackImage(track)}<div><h2>${escapeHtml(track.title)}</h2><p>${escapeHtml(track.artist)}</p></div></section><button class="text-button" data-action="lyrics">Текст трека ${icon("arrow")}</button><label class="seek-label" for="mobile-seek">Позиция трека <span id="mobile-time"></span></label><input id="mobile-seek" type="range" min="0" max="${player.duration}" value="${player.audio.currentTime}" aria-label="Позиция трека"><div class="modal-playback"><button class="icon-button" data-action="previous" aria-label="Предыдущий">${icon("next").replace("<svg ", '<svg style="transform:rotate(180deg)" ')}</button><button class="primary-button" data-action="toggle-audio"></button><button class="icon-button" data-action="next" aria-label="Следующий" ${player.index >= player.queue.length - 1 && !player.shuffle ? "disabled" : ""}>${icon("next")}</button></div>`,
  );
  updatePlayer();
}
/* ---------- История прослушиваний и «продолжить с места остановки» ---------- */
function flushPlayed(completed = false) {
  if (!lastPlayed) return;
  const { track, position } = lastPlayed;
  lastPlayed = null;
  const duration = player.duration || (track.duration_ms || 0) / 1000;
  api.scrobble(track, { position, duration, completed });
  if (completed) state.positions.delete(positionKey(track));
  else if (position > 10) {
    state.positions.set(positionKey(track), position);
    api.savePosition(track, position);
  }
}
player.audio.addEventListener("timeupdate", () => {
  const track = player.current;
  if (!track) return;
  const key = positionKey(track);
  if (lastPlayed && lastPlayed.key !== key) flushPlayed(false);
  if (!lastPlayed || lastPlayed.key !== key)
    lastPlayed = { key, track, position: 0 };
  lastPlayed.position = player.audio.currentTime || 0;
  const now = Date.now();
  if (player.playing && lastPlayed.position > 10 && now - lastPosSent > 15000) {
    lastPosSent = now;
    state.positions.set(key, lastPlayed.position);
    api.savePosition(track, lastPlayed.position);
  }
});
player.addEventListener("trackend", ({ detail }) => {
  lastPlayed = { key: positionKey(detail.track), track: detail.track, position: detail.position };
  flushPlayed(true);
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && lastPlayed) flushPlayed(false);
});
function resumePosition(track) {
  const saved = state.positions.get(positionKey(track)) || 0;
  const duration = (track.duration_ms || 0) / 1000;
  return saved >= 10 && (!duration || saved < duration - 15) ? saved : 0;
}
function startTrack(items, index) {
  const track = items[index];
  state.history = [
    track,
    ...state.history.filter((t) => trackKey(t) !== trackKey(track)),
  ].slice(0, 30);
  const resume = resumePosition(track);
  if (resume) toast(`Продолжаем с ${time(resume)}`);
  player.select(items, index, resume);
  closeModal();
}
/* ---------- Тексты (Lrclib) ---------- */
async function lyricsModal() {
  const track = player.current;
  if (!track) return toast("Сначала выбери трек в библиотеке");
  lyricsActive = -2;
  showModal(
    `<span class="eyebrow">ТЕКСТ · ${escapeHtml(names[track.source] || track.source)}</span><h2>${escapeHtml(track.title)}</h2><p>${escapeHtml(track.artist)}</p><div class="lyrics" id="lyrics-body">${busy("Ищем текст…")}</div>`,
  );
  try {
    const data = await api.lyrics(track);
    const box = $("#lyrics-body");
    if (!box) return;
    if (!data.synced && !data.plain) {
      box.innerHTML = "<p>Текста для этого трека не нашлось.</p>";
      return;
    }
    const lines = String(data.synced || data.plain)
      .split(/\r?\n/)
      .map((line) => {
        const m = line.match(/^\[(\d+):(\d+)(?:[.:](\d+))?\]\s*(.*)$/);
        return {
          t: m ? Number(m[1]) * 60 + Number(m[2]) + Number(`0.${m[3] || "0"}`) : null,
          text: (m ? m[4] : line).trim(),
        };
      })
      .filter((line) => line.text);
    box.innerHTML = lines
      .map((line) =>
        line.t == null
          ? `<p>${escapeHtml(line.text)}</p>`
          : `<p data-t="${line.t.toFixed(2)}">${escapeHtml(line.text)}</p>`,
      )
      .join("");
    if (!data.synced)
      box.insertAdjacentHTML(
        "beforeend",
        '<p class="lyrics-note">Несинхронный текст — просто список строк.</p>',
      );
    highlightLyrics(true);
  } catch (error) {
    const box = $("#lyrics-body");
    if (box) box.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
}
function highlightLyrics(force = false) {
  const box = $("#lyrics-body");
  if (!box || !$("#modal").open) return;
  const lines = $$("p[data-t]", box);
  if (!lines.length) return;
  const at = (player.audio.currentTime || 0) + 0.5;
  let active = -1;
  lines.forEach((line, i) => {
    if (Number(line.dataset.t) <= at) active = i;
  });
  if (active === lyricsActive && !force) return;
  lyricsActive = active;
  lines.forEach((line, i) => line.classList.toggle("active", i === active));
  if (active >= 0)
    lines[active].scrollIntoView({ block: "center", behavior: "smooth" });
}
async function historyModal() {
  showModal(`<h2>Недавно прослушано</h2>${busy()}`);
  try {
    const data = await api.history();
    const items = data.items || [];
    showModal(
      `<h2>Недавно прослушано</h2><p class="settings-note">Общая история со всех устройств.</p>${items.length ? trackList(items, "history") : "<p>Пока пусто. Здесь появятся треки, которые ты слушал.</p>"}`,
    );
  } catch (error) {
    showModal(
      `<h2>Недавно в плеере</h2>${state.history.length ? trackList(state.history, "history") : `<p>${escapeHtml(error.message)}</p>`}`,
    );
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.classList.contains("modal-close")) {
    closeModal();
    return;
  }
  if (button.dataset.page) {
    closeModal();
    navigate(button.dataset.page);
  }
  if (button.dataset.playlist) {
    const p = state.playlists.find(
      (item) => playlistKey(item) === button.dataset.playlist,
    );
    if (p) {
      closeModal();
      navigate("playlist", p);
    }
  }
  if (button.dataset.source) {
    state.source = button.dataset.source;
    state.tracks = [];
    state.loading = true;
    renderShell();
    loadView();
  }
  if (button.dataset.queue) {
    const items = queues.get(button.dataset.queue);
    const index = Number(button.dataset.index);
    if (items?.[index]) startTrack(items, index);
  }
  const action =
    button.dataset.action ||
    {
      "profile-button": "settings",
      "open-settings": "settings",
      notifications: "settings",
      history: "history",
      "play-toggle": "toggle-audio",
      next: "next",
      previous: "previous",
      "like-track": "favorite",
      shuffle: "shuffle",
      repeat: "repeat",
      device: "device",
      "open-current": "current",
    }[button.id];
  switch (action) {
    case "refresh":
      closeModal();
      sync();
      break;
    case "settings":
      settings();
      break;
    case "reload":
      loadView();
      break;
    case "more":
      more(button);
      break;
    case "spotify":
      spotify(button);
      break;
    case "logout":
      api.logout(false);
      resetSession();
      renderLogin();
      break;
    case "favorite":
      toggleFavorite();
      break;
    case "lyrics":
      lyricsModal();
      break;
    case "check-status":
      button.disabled = true;
      checkStatus().then(settings);
      break;
    case "toggle-audio":
      player.toggle();
      break;
    case "next":
      player.next();
      break;
    case "previous":
      player.previous();
      break;
    case "shuffle":
      player.shuffle = !player.shuffle;
      updatePlayer();
      break;
    case "repeat":
      player.repeat = !player.repeat;
      updatePlayer();
      break;
    case "current":
      currentTrackModal();
      break;
    case "history":
      historyModal();
      break;
    case "device":
      showModal(
        "<h2>Это устройство</h2><p>Музыка играет в этом браузере. На iPhone громкость регулируется кнопками устройства.</p>",
      );
      break;
  }
});
document.addEventListener("input", (event) => {
  if (event.target.id === "search-input") {
    state.query = event.target.value;
    controller?.abort();
    ++viewVersion;
    clearTimeout(searchTimer);
    state.tracks = [];
    state.loading = true;
    renderContent();
    searchTimer = setTimeout(loadView, 300);
  }
  if (["progress", "mobile-seek"].includes(event.target.id))
    player.seek(Number(event.target.value));
  if (event.target.id === "volume")
    player.audio.volume = Number(event.target.value) / 100;
});
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "login-form") return;
  event.preventDefault();
  const form = event.target,
    button = $("button[type=submit]", form);
  const passwordInput = $("#login-password");
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span> Подключаемся…';
  $("#login-error").textContent = "";
  try {
    const server = $("#api-url").value.trim();
    api.configure(server);
    const password = passwordInput.value;
    passwordInput.value = "";
    await api.login(password);
    resetSession();
    await enter();
  } catch (error) {
    const target = $("#login-error");
    if (target)
      target.textContent =
        error instanceof TypeError
          ? "Укажи корректный адрес сервера, начиная с http:// или https://."
          : error.message;
  } finally {
    button.disabled = false;
    button.innerHTML = `${icon("play")}Войти в мою музыку`;
  }
});
$("#modal").addEventListener("click", (event) => {
  if (event.target !== $("#modal")) return;
  const box = $("#modal").getBoundingClientRect();
  if (
    event.clientX < box.left ||
    event.clientX > box.right ||
    event.clientY < box.top ||
    event.clientY > box.bottom
  )
    closeModal();
});
player.addEventListener("change", updatePlayer);
player.addEventListener("error", (event) => toast(event.detail));
api.addEventListener("unauthorized", () => {
  resetSession();
  renderLogin("Сессия истекла. Войди снова.");
});
window.addEventListener("popstate", () => {
  if (!api.token) return;
  const [page, key] = location.hash.slice(1).split("/");
  state.page = ["home", "search", "library", "favorites"].includes(page)
    ? page
    : "home";
  if (page === "playlist" && key) {
    try {
      state.playlist = state.playlists.find(
        (p) => playlistKey(p) === decodeURIComponent(key),
      );
      if (state.playlist) state.page = "playlist";
    } catch {}
  }
  renderShell();
  loadView();
});
document.addEventListener("keydown", (event) => {
  if (
    event.code === "Space" &&
    api.token &&
    !["INPUT", "TEXTAREA", "BUTTON", "A"].includes(
      document.activeElement.tagName,
    ) &&
    !$("#modal").open
  ) {
    event.preventDefault();
    player.toggle();
  }
});
setInterval(
  () => {
    if (api.token && !document.hidden) sync();
  },
  6 * 60 * 60 * 1000,
);
player.audio.volume = 0.7;
if (api.token) enter();
else renderLogin();
updatePlayer();

document.querySelector(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  navigate("home");
});
