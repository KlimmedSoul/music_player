"use strict";
/* Audio events are the only source of playback state; no simulated timer. */
class MusicPlayer extends EventTarget {
  constructor(api) {
    super();
    this.api = api;
    this.audio = new Audio();
    this.audio.preload = "metadata";
    this.queue = [];
    this.index = -1;
    this.loading = false;
    this.shuffle = false;
    this.repeat = false;
    this.version = 0;
    this.retried = false;
    this.controller = null;
    this.prewarm = new Map(); // source|ext -> {data, at}: уже разрешённый следующий трек
    this.mediaPositionAt = 0;
    this.sessionEpoch = 0;
    this.prewarmControllers = new Set();
    for (const event of [
      "play",
      "pause",
      "timeupdate",
      "durationchange",
      "loadedmetadata",
      "waiting",
      "playing",
      "volumechange",
    ])
      this.audio.addEventListener(event, () => this.emit());
    this.audio.addEventListener("play", () => this.syncMediaSession());
    this.audio.addEventListener("pause", () => this.syncMediaSession());
    this.audio.addEventListener("loadedmetadata", () =>
      this.syncMediaPosition(true),
    );
    this.audio.addEventListener("timeupdate", () => this.syncMediaPosition());
    this.audio.addEventListener("ended", () => {
      const finished = this.current;
      if (finished)
        this.dispatchEvent(
          new CustomEvent("trackend", {
            detail: { track: finished, position: this.duration || 0, duration: this.duration || 0 },
          }),
        );
      if (this.repeat) {
        this.audio.currentTime = 0;
        this.play();
      } else if (this.index + 1 < this.queue.length || this.shuffle)
        this.next();
      else this.emit();
    });
    this.audio.addEventListener("error", () => {
      if (!this.current || this.loading || !this.audio.getAttribute("src"))
        return;
      if (!this.retried) {
        this.retried = true;
        this.load(this.audio.currentTime, true);
      } else
        this.fail(
          "Не удалось воспроизвести трек. Попробуй другой или обнови библиотеку.",
        );
    });
    if ("mediaSession" in navigator) {
      const actions = {
        play: () => this.play(),
        pause: () => this.audio.pause(),
        previoustrack: () => this.previous(),
        nexttrack: () => this.next(),
        seekto: (details) => this.seek(details.seekTime),
      };
      for (const [name, handler] of Object.entries(actions)) {
        try {
          navigator.mediaSession.setActionHandler(name, handler);
        } catch {}
      }
    }
  }
  get current() {
    return this.queue[this.index] || null;
  }
  get playing() {
    return !this.audio.paused && !this.audio.ended;
  }
  get duration() {
    return Number.isFinite(this.audio.duration)
      ? this.audio.duration
      : (this.current?.duration_ms || 0) / 1000;
  }
  emit() {
    this.dispatchEvent(new Event("change"));
  }
  /* Lock-screen / Bluetooth controls: metadata, play state and the progress bar. */
  syncMediaSession() {
    if (!("mediaSession" in navigator)) return;
    const track = this.current;
    if (track && typeof MediaMetadata !== "undefined") {
      const cover = document.querySelector("#player-cover");
      const artwork =
        cover && !cover.src.endsWith("/assets/cover.svg")
          ? [{ src: cover.src }]
          : [];
      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        album: track.album || "",
        artwork,
      });
    }
    try {
      navigator.mediaSession.playbackState = this.playing
        ? "playing"
        : this.current
          ? "paused"
          : "none";
    } catch {}
    this.syncMediaPosition();
  }
  syncMediaPosition(force = false) {
    if (!("mediaSession" in navigator) || !navigator.mediaSession.setPositionState)
      return;
    const duration = this.duration;
    const now = Date.now();
    if (!force && now - this.mediaPositionAt < 3000) return;
    if (!Number.isFinite(duration) || duration <= 0) return;
    this.mediaPositionAt = now;
    try {
      navigator.mediaSession.setPositionState({
        duration,
        position: Math.min(this.audio.currentTime || 0, duration),
        playbackRate: 1,
      });
    } catch {}
  }
  prewarmNext() {
    const next = !this.shuffle && this.queue[this.index + 1];
    if (!next) return;
    const key = `${this.api.base}|${next.source}|${next.external_id}`;
    if (this.prewarm.has(key)) return;
    const epoch = this.sessionEpoch, token = this.api.token;
    const controller = new AbortController();
    this.prewarmControllers.add(controller);
    const warm = {at:Date.now(),data:null,token};
    warm.promise = this.api.resolve(next, {signal:controller.signal}).then(data => {
      if (epoch === this.sessionEpoch && token === this.api.token) { warm.data = data; warm.at = Date.now(); }
      return warm.data;
    }).catch(() => { if (this.prewarm.get(key) === warm) this.prewarm.delete(key); return null; })
      .finally(() => this.prewarmControllers.delete(controller));
    this.prewarm.set(key, warm);
    while (this.prewarm.size > 6) this.prewarm.delete(this.prewarm.keys().next().value);
  }
  fail(message) {
    this.loading = false;
    this.audio.pause();
    this.emit();
    this.dispatchEvent(new CustomEvent("error", { detail: message }));
  }
  select(queue, index, position = 0) {
    this.dispatchEvent(new Event("trackchange"));
    this.queue = queue.slice();
    this.index = index;
    this.retried = false;
    return this.load(position);
  }
  async load(position = 0, retry = false) {
    const track = this.current;
    if (!track) return;
    const version = ++this.version;
    this.controller?.abort();
    this.controller = new AbortController();
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
    this.loading = true;
    this.emit();
    try {
      const warmKey = `${this.api.base}|${track.source}|${track.external_id}`;
      const warm = this.prewarm.get(warmKey);
      const prefetched = !retry && warm && warm.token === this.api.token && Date.now() - warm.at < 5 * 60 * 1000
        ? (warm.data || await warm.promise) : null;
      if (version !== this.version) return;
      const data = prefetched || await this.api.resolve(track, {signal:this.controller.signal});
      this.prewarm.delete(warmKey);
      if (version !== this.version) return;
      const url = new URL(
        data.kind === "proxy" ? data.stream_path : data.stream_url,
        `${this.api.base}/`,
      );
      if (!["https:", "http:"].includes(url.protocol))
        throw new Error("Сервер выдал неподдерживаемую аудиоссылку.");
      if (
        /\.m3u8(?:$|\?)/i.test(url.href) &&
        !this.audio.canPlayType("application/vnd.apple.mpegurl")
      )
        throw new Error(
          "Этот поток HLS поддерживается в Safari на iPhone. Открой приложение в Safari или выбери другой трек.",
        );
      this.audio.src = url.href;
      this.audio.onloadedmetadata = () => {
        if (version === this.version && position > 0) this.seek(position);
      };
      this.loading = false;
      if ("mediaSession" in navigator && typeof MediaMetadata !== "undefined") {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: track.title,
          artist: track.artist,
          album: track.album || "",
          artwork: [{ src: document.querySelector("#player-cover").src }],
        });
      }
      await this.audio.play();
      this.prewarmNext();
    } catch (error) {
      if (version !== this.version || error.name === "AbortError") return;
      if (error.name === "NotAllowedError")
        this.fail("Трек готов. Нажми Play, чтобы начать воспроизведение.");
      else if (!retry && !this.retried && this.audio.error) {
        this.retried = true;
        return this.load(position, true);
      } else this.fail(error.message || "Не удалось воспроизвести трек.");
    } finally {
      if (version === this.version) {
        this.loading = false;
        this.emit();
      }
    }
  }
  async play() {
    if (this.loading) return;
    if (!this.current) return;
    if (!this.audio.getAttribute("src") || this.audio.error) return this.load();
    try {
      await this.audio.play();
    } catch (error) {
      this.fail(
        error.name === "NotAllowedError"
          ? "Нажми Play ещё раз для запуска аудио."
          : "Не удалось запустить аудио. Попробуй другой трек.",
      );
    }
  }
  toggle() {
    if (this.playing) this.audio.pause();
    else return this.play();
  }
  next() {
    if (!this.queue.length) return;
    if (this.index + 1 >= this.queue.length && !this.shuffle) {
      this.audio.pause();
      this.emit();
      return;
    }
    const next =
      this.shuffle && this.queue.length > 1
        ? (this.index +
            1 +
            Math.floor(Math.random() * (this.queue.length - 1))) %
          this.queue.length
        : this.index + 1;
    return this.select(this.queue, next);
  }
  previous() {
    if (this.audio.currentTime > 3) {
      this.seek(0);
      return;
    }
    if (this.index > 0) return this.select(this.queue, this.index - 1);
    this.seek(0);
  }
  seek(seconds) {
    if (this.audio.readyState >= 1 && Number.isFinite(seconds)) {
      this.audio.currentTime = Math.max(
        0,
        Math.min(seconds, this.duration || seconds),
      );
      this.emit();
    }
  }
  reset() {
    ++this.version;
    this.controller?.abort();
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
    this.queue = [];
    this.index = -1;
    this.loading = false;
    this.sessionEpoch++;
    for (const controller of this.prewarmControllers) controller.abort();
    this.prewarmControllers.clear();
    this.prewarm.clear();
    if ("mediaSession" in navigator) {
      navigator.mediaSession.metadata = null;
      try {
        navigator.mediaSession.playbackState = "none";
      } catch {}
    }
    this.emit();
  }
}
window.MusicPlayer = MusicPlayer;
