/* HTTP contract: backend/app/main.py. Tokens are scoped to one server and tab. */
"use strict";
class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}
class MusicAPI extends EventTarget {
  constructor() {
    super();
    this.base =
      this.read(localStorage, "myplayer-api") ||
      (location.protocol.startsWith("http")
        ? location.port === "8081"
          ? `${location.protocol}//${location.hostname}:8080`
          : "https://myplayer-after-dark-api.onrender.com"
        : "http://localhost:8080");
    this.token =
      this.read(sessionStorage, `myplayer-session:${this.base}`) || "";
  }
  read(storage, key) {
    try {
      return storage.getItem(key);
    } catch {
      return null;
    }
  }
  configure(value) {
    const url = new URL(value);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    )
      throw new ApiError(
        "Укажи HTTP(S)-адрес сервера без пароля, параметров и #.",
      );
    const base = url.href.replace(/\/$/, "");
    if (
      location.protocol === "https:" &&
      url.protocol === "http:" &&
      !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
    )
      throw new ApiError("Для этой страницы нужен HTTPS-адрес сервера.");
    if (base !== this.base) this.logout(false);
    this.base = base;
    try {
      localStorage.setItem("myplayer-api", this.base);
    } catch {}
    this.token =
      this.read(sessionStorage, `myplayer-session:${this.base}`) || "";
  }
  logout(notify = true) {
    try {
      sessionStorage.removeItem(`myplayer-session:${this.base}`);
    } catch {}
    this.token = "";
    if (notify) this.dispatchEvent(new Event("unauthorized"));
  }
  async request(
    path,
    { method = "GET", body, signal, timeout = 90000, auth = true } = {},
  ) {
    const controller = new AbortController();
    const abort = () => controller.abort(signal.reason);
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(
      () => controller.abort(new DOMException("Timeout", "TimeoutError")),
      timeout,
    );
    const token = this.token;
    try {
      const response = await fetch(`${this.base}${path}`, {
        method,
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        headers: {
          ...(auth && token ? { Authorization: `Bearer ${token}` } : {}),
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 401 && auth && token === this.token)
          this.logout();
        const detail = typeof data?.detail === "string" ? data.detail : "";
        throw new ApiError(
          response.status === 403 && path === "/auth/login"
            ? "Неверный пароль."
            : response.status === 401
              ? "Сессия истекла. Войди снова."
              : detail || `Сервер вернул ошибку ${response.status}.`,
          response.status,
        );
      }
      if (data === null)
        throw new ApiError(
          "Сервер вернул неожиданный ответ. Проверь адрес API.",
        );
      return data;
    } catch (error) {
      if (signal?.aborted) throw new DOMException("Отменено", "AbortError");
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        controller.signal.aborted
          ? "Сервер долго не отвечает. Попробуй ещё раз — облачный сервер мог просыпаться."
          : "Не удалось подключиться к серверу. Проверь адрес, сеть и доступность API.",
      );
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    }
  }
  async login(password) {
    const data = await this.request("/auth/login", {
      method: "POST",
      body: { password },
      auth: false,
    });
    if (!data.token) throw new ApiError("Сервер не выдал токен входа.");
    this.token = data.token;
    try {
      sessionStorage.setItem(`myplayer-session:${this.base}`, data.token);
    } catch {}
  }
  status(options) {
    return this.request("/status", options);
  }
  tracks({ q = "", source = "all", cursor = 0, limit = 50, signal } = {}) {
    return this.request(
      `/library/tracks?${new URLSearchParams({ q, source, cursor, limit })}`,
      { signal },
    );
  }
  playlists(options) {
    return this.request("/library/playlists", options);
  }
  playlistTracks(source, id, options) {
    return this.request(
      `/library/playlists/${encodeURIComponent(source)}/${encodeURIComponent(id)}/tracks`,
      options,
    );
  }
  refresh(source = "all") {
    return this.request(
      `/library/refresh${source === "all" ? "" : `?source=${encodeURIComponent(source)}`}`,
      { method: "POST", timeout: 180000 },
    );
  }
  resolve(track, options) {
    return this.request("/resolve", {
      ...options,
      method: "POST",
      body: { source: track.source, external_id: String(track.external_id), full_only: true },
    });
  }
  artwork(track, options) {
    return this.request(`/artwork?${new URLSearchParams({
      title: String(track.title || "").slice(0, 200),
      artist: String(track.artist || "").slice(0, 200),
    })}`, { timeout: 12000, ...options });
  }
  lyrics(track, options) {
    return this.request(`/lyrics?${new URLSearchParams({
      title: String(track.title || "").slice(0, 200),
      artist: String(track.artist || "").slice(0, 200),
      duration_s: Math.round((track.duration_ms || 0) / 1000),
    })}`, { timeout: 20000, ...options });
  }
  favorites(options) {
    return this.request("/favorites", options);
  }
  toggleFavorite(track, options) {
    return this.request("/favorites/toggle", {
      ...options,
      method: "POST",
      body: { source: track.source, external_id: String(track.external_id) },
    });
  }
  setFavorite(track, favorite = true, options) {
    return this.request("/favorites/toggle", {
      ...options, method: "POST",
      body: {source:track.source, external_id:String(track.external_id), favorite},
    });
  }
  history(options) {
    return this.request(`/history?${new URLSearchParams({ limit: 30 })}`, options);
  }
  scrobble(track, { position = 0, duration = 0, completed = false }) {
    return this.request("/plays", {
      method: "POST",
      timeout: 15000,
      body: {
        source: track.source,
        external_id: String(track.external_id),
        position_s: Math.round(position),
        duration_s: Math.round(duration),
        completed,
      },
    }).catch(() => {});
  }
  savePosition(track, position) {
    return this.request("/positions", {
      method: "POST",
      timeout: 15000,
      body: {
        source: track.source,
        external_id: String(track.external_id),
        position_s: Math.round(position),
      },
    }).catch(() => {});
  }
  positions(options) {
    return this.request("/positions", options);
  }
  spotify() {
    return this.request("/auth/spotify/start");
  }
}
window.MusicAPI = MusicAPI;
