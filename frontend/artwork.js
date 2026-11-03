"use strict";
/* Provider art first; missing/broken covers are resolved only near the viewport. */
class MusicArtwork {
  constructor(api) {
    this.api = api;
    this.cache = new Map();
    this.pending = [];
    this.active = 0;
    this.fallback = "assets/cover.svg";
    this.visible = "IntersectionObserver" in window
      ? new IntersectionObserver((entries) => {
          for (const entry of entries) if (entry.isIntersecting) {
            this.visible.unobserve(entry.target);
            this.resolveImage(entry.target);
          }
        }, { rootMargin: "200px" }) : null;
    this.mutations = new MutationObserver(() => this.scan());
    this.mutations.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("error", (event) => {
      const img = event.target;
      if (!(img instanceof HTMLImageElement)) return;
      if (img.hasAttribute("data-artwork-title")) {
        if (img.dataset.artworkFallback === "true") {
          img.src = this.fallback;
        } else {
          img.src = this.fallback;
          this.resolveImage(img);
        }
      } else if (!img.src.endsWith("/assets/cover.svg")) img.src = this.fallback;
    }, true);
    document.querySelector("#player-cover")?.addEventListener("load", (event) => {
      const img = event.target;
      if (navigator.mediaSession?.metadata &&
          navigator.mediaSession.metadata.title === img.dataset.artworkTitle) {
        navigator.mediaSession.metadata.artwork = [{ src: img.src }];
      }
    });
    this.scan();
  }
  scan() {
    for (const img of document.querySelectorAll("img[data-artwork-title]:not([data-artwork-observed])")) {
      img.dataset.artworkObserved = "true";
      if (img.getAttribute("src") === this.fallback) {
        if (this.visible) this.visible.observe(img);
        else this.resolveImage(img);
      }
    }
  }
  setTrack(img, track) {
    const key = JSON.stringify([this.api.base, track?.source, track?.external_id, track?.cover]);
    if (img.dataset.artworkTrack === key) return;
    this.visible?.unobserve(img);
    img.dataset.artworkTrack = key;
    delete img.dataset.artworkFallback;
    delete img.dataset.artworkObserved;
    if (!track) {
      delete img.dataset.artworkTitle;
      delete img.dataset.artworkArtist;
      img.src = this.fallback;
      return;
    }
    img.dataset.artworkTitle = track.title || "";
    img.dataset.artworkArtist = track.artist || "";
    img.alt = `Обложка: ${track.title || "трек"}`;
    try {
      const url = new URL(track.cover);
      if (!["https:", "http:"].includes(url.protocol)) throw new Error();
      img.src = url.href;
    } catch { img.src = this.fallback; }
    this.scan();
  }
  async resolveImage(img) {
    if (img.dataset.artworkFallback === "true") return;
    img.dataset.artworkFallback = "true";
    const title = img.dataset.artworkTitle || "";
    const artist = img.dataset.artworkArtist || "";
    const base = this.api.base;
    if (!title || !artist || !this.api.token) return;
    const key = JSON.stringify([base, title, artist]);
    if (!this.cache.has(key)) {
      if (this.cache.size >= 512) this.cache.delete(this.cache.keys().next().value);
      this.cache.set(key, new Promise((resolve) => {
        this.pending.push({ track: { title, artist }, base, resolve });
        this.pump();
      }));
    }
    const url = await this.cache.get(key);
    // A slow lookup must never overwrite the next track's artwork.
    if (url && img.isConnected && this.api.base === base &&
        img.dataset.artworkTitle === title && img.dataset.artworkArtist === artist) {
      try {
        const parsed = new URL(url);
        if (["https:", "http:"].includes(parsed.protocol)) img.src = parsed.href;
      } catch { /* Keep the local ink illustration. */ }
    }
  }
  pump() {
    while (this.active < 3 && this.pending.length) {
      const job = this.pending.shift();
      if (job.base !== this.api.base || !this.api.token) { job.resolve(null); continue; }
      this.active++;
      this.api.artwork(job.track).then(
        (data) => job.resolve(data.cover || null),
        () => job.resolve(null),
      ).finally(() => { this.active--; this.pump(); });
    }
  }
}
window.MusicArtwork = MusicArtwork;
