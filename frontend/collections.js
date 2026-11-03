"use strict";
/* Personal playlists stay on this device, independently of Render's temporary disk. */
const newPlaylistId = () => crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2,"0")).join("");
function localPlaylists() {
  const value = read("myplayer-playlists", []);
  return Array.isArray(value) ? value.filter(p => p && typeof p.external_id === "string" && Array.isArray(p.items)) : [];
}
function savePlaylists(items) {
  try { localStorage.setItem(`myplayer-playlists:${api.base}`, JSON.stringify(items)); }
  catch { throw new Error("Не удалось сохранить плейлист. Проверь свободное место и настройки браузера."); }
}
function playlistView(p) {
  return { ...p, source: "local", total: p.items.length, cover: p.items.find(t => t.cover)?.cover || null };
}
function allPlaylists() { return [...localPlaylists().map(playlistView), ...state.playlists]; }
function createPlaylistDialog(track = null) {
  showModal(`<span class="eyebrow">ТВОЯ НОВАЯ ГЛАВА</span><h2>Создать плейлист</h2><form id="playlist-form"><label for="playlist-name">Название</label><input id="playlist-name" name="title" maxlength="80" required placeholder="Например, последний поезд" autocomplete="off"><p>Сохраняется на этом устройстве. Можно собрать треки из разных источников.</p><button class="primary-button" type="submit">${icon("plus")}Создать${track ? " и добавить трек" : ""}</button></form>`);
  $("#playlist-form").addEventListener("submit", event => {
    event.preventDefault();
    const title = $("#playlist-name").value.trim();
    if (!title) return;
    try {
      const items = localPlaylists();
      if (items.length >= 100) throw new Error("Можно сохранить до 100 плейлистов.");
      const p = {external_id: newPlaylistId(), title, items: track ? [track] : []};
      savePlaylists([p, ...items]);
      closeModal(); navigate("playlist", playlistView(p)); toast("Плейлист создан на устройстве");
    } catch (error) { toast(error.message); }
  });
  $("#playlist-name").focus();
}
function trackMenu(queue, index) {
  const track = queues.get(queue)?.[index];
  if (!track) return;
  const playlists = localPlaylists();
  showModal(`<span class="eyebrow">ДОБАВИТЬ В ПЛЕЙЛИСТ</span><h2>${escapeHtml(track.title)}</h2><div class="playlist-picker">${playlists.map((p, i) => `<button class="load-more" data-pick="${i}">${icon("plus")}${escapeHtml(p.title)} <small>${p.items.length} треков</small></button>`).join("")}</div><button class="primary-button" id="new-playlist-with-track">${icon("plus")}Новый плейлист</button>${state.page === "playlist" && state.playlist?.source === "local" ? '<button class="text-button" id="remove-from-playlist">Убрать из этого плейлиста</button>' : ""}`);
  $$("[data-pick]").forEach(button => button.addEventListener("click", () => {
    try {
      const items = localPlaylists(), target = items.find(p => p.external_id === playlists[Number(button.dataset.pick)].external_id);
      if (!target) throw new Error("Плейлист уже удалён.");
      if (target.items.some(t => trackKey(t) === trackKey(track))) return toast("Трек уже в этом плейлисте");
      if (target.items.length >= 1000) throw new Error("В плейлисте уже 1000 треков.");
      target.items.push(track); savePlaylists(items); closeModal(); renderContent(); toast("Трек добавлен");
    } catch (error) { toast(error.message); }
  }));
  $("#new-playlist-with-track").addEventListener("click", () => createPlaylistDialog(track));
  $("#remove-from-playlist")?.addEventListener("click", () => {
    try {
      const items = localPlaylists(), target = items.find(p => p.external_id === state.playlist.external_id);
      if (!target) return;
      target.items = target.items.filter(t => trackKey(t) !== trackKey(track));
      savePlaylists(items); state.playlistTracks = target.items; state.playlist = playlistView(target);
      closeModal(); renderContent(); toast("Трек убран из плейлиста");
    } catch (error) { toast(error.message); }
  });
}
function editPlaylistDialog() {
  const selected = localPlaylists().find(p => p.external_id === state.playlist?.external_id);
  if (!selected) return;
  showModal(`<h2>Твой плейлист</h2><form id="edit-playlist-form"><label for="playlist-name">Название</label><input id="playlist-name" maxlength="80" value="${escapeHtml(selected.title)}" required><button class="primary-button" type="submit">Сохранить</button></form><button class="load-more" id="export-playlist">Скачать копию JSON</button><button class="text-button" id="delete-playlist">Удалить плейлист…</button>`);
  $("#edit-playlist-form").addEventListener("submit", event => {
    event.preventDefault(); const title = $("#playlist-name").value.trim(); if (!title) return;
    try { const items = localPlaylists(); items.find(p => p.external_id === selected.external_id).title = title; savePlaylists(items); state.playlist.title = title; closeModal(); renderContent(); }
    catch (error) { toast(error.message); }
  });
  $("#export-playlist").addEventListener("click", () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(selected, null, 2)], {type:"application/json"}));
    const a = document.createElement("a"); a.href = url; a.download = "myplayer-playlist.json"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("#delete-playlist").addEventListener("click", () => {
    showModal(`<h2>Удалить «${escapeHtml(selected.title)}»?</h2><p>Удалится только плейлист на этом устройстве. Сами треки останутся в библиотеке.</p><button class="primary-button" id="confirm-delete-playlist">Удалить плейлист</button>`);
    $("#confirm-delete-playlist").addEventListener("click", () => {
      try { savePlaylists(localPlaylists().filter(p => p.external_id !== selected.external_id)); closeModal(); navigate("library"); }
      catch (error) { toast(error.message); }
    });
  });
}
function recommendationPlaylists() {
  const pool = (state.recommendationTracks?.length ? state.recommendationTracks : state.tracks).filter(t => !t.meta?.preview_only && t.source !== "preview");
  if (!pool.length) return [];
  const artists = new Map(), recent = new Set(state.history.map(trackKey));
  for (const t of [...favorites(), ...state.history]) if (t.artist) artists.set(t.artist.toLocaleLowerCase(), (artists.get(t.artist.toLocaleLowerCase()) || 0) + 3);
  for (const t of pool) if (t.artist) artists.set(t.artist.toLocaleLowerCase(), (artists.get(t.artist.toLocaleLowerCase()) || 0) + .1);
  const day = new Date().toISOString().slice(0, 10);
  const hash = t => { let n = 0; for (const c of day + trackKey(t)) n = (n * 31 + c.charCodeAt(0)) >>> 0; return n; };
  const shuffled = pool.slice().sort((a,b) => hash(a)-hash(b));
  const ranked = shuffled.slice().sort((a,b) => Number(recent.has(trackKey(a)))-Number(recent.has(trackKey(b))) || (artists.get(b.artist?.toLocaleLowerCase()) || 0)-(artists.get(a.artist?.toLocaleLowerCase()) || 0));
  const mix = (id,title,subtitle,items,tag) => ({source:"mix",external_id:id,title,subtitle,items,total:items.length,cover:items.find(t=>t.cover)?.cover,tag});
  const mixes = [mix("daily", "Твой Daily Mix", "По избранному и недавним трекам · меняется каждый день", ranked.slice(0,24), "DAILY / 01")];
  for (const [artist] of [...artists].sort((a,b)=>b[1]-a[1]).slice(0,3)) {
    const tracks = shuffled.filter(t=>t.artist?.toLocaleLowerCase()===artist);
    if (tracks.length) mixes.push(mix("artist:"+artist, tracks[0].artist+" — микс", "Исполнитель из твоей библиотеки", tracks.slice(0,24), "ARTIST MIX"));
  }
  const saved = new Set(favorites().map(trackKey));
  const rediscover = shuffled.filter(t=>!saved.has(trackKey(t))&&!recent.has(trackKey(t)));
  if (rediscover.length) mixes.push(mix("rediscover", "Заново открыть", "Треки из библиотеки вне избранного и недавних", rediscover.slice(0,24), "REDISCOVER"));
  return mixes;
}
function findPlaylist(key) { return [...allPlaylists(), ...recommendationPlaylists()].find(p=>playlistKey(p)===key); }
function recommendationCard(p, i) {
  const covers = p.items.filter(t=>t.cover).slice(0,4);
  return `<button class="music-card mix-card" data-playlist="${escapeHtml(playlistKey(p))}"><div class="mix-cover mix-style-${i%3}"><div class="mix-collage">${(covers.length ? covers : p.items.slice(0,1)).map(t=>image(t.cover)).join("")}</div><span class="mix-tag">${escapeHtml(p.tag)}</span><strong>${escapeHtml(p.title)}</strong><span class="card-play">${icon("play")}</span></div><strong>${escapeHtml(p.title)}</strong><p>${escapeHtml(p.subtitle)} · ${p.total} треков</p></button>`;
}
function recommendationContent() {
  const mixes = recommendationPlaylists();
  return `<section class="music-section"><div class="chapter-line"><b>02 / ДЛЯ ТЕБЯ</b><span>ТВОЯ ЧАСТОТА</span></div><div class="section-heading"><div><h2>Специально для тебя</h2><p>Миксы из твоей музыки. Только полные треки.</p></div><button class="text-button" data-page="recommendations">Все миксы ${icon("arrow")}</button></div>${state.loading ? busy("Собираем твою частоту…") : mixes.length ? `<div class="card-row recommendation-row">${mixes.map(recommendationCard).join("")}</div>` : empty("Твои реки начнутся с музыки", "Подключи источник и обнови библиотеку. Здесь появятся Daily Mix и миксы исполнителей.", '<button class="primary-button" data-action="settings">Подключить музыку</button>')}</section>`;
}
function recommendationsPage() {
  const mixes = recommendationPlaylists();
  return `${state.errors.map(errorBanner).join("")}<div class="recommendation-intro"><span class="eyebrow">ТВОЯ МУЗЫКА / НОВАЯ ГЛАВА</span><h2>На одной частоте.</h2><p>Daily Mix, любимые исполнители и треки, которые стоит включить снова. Подборки составляются из твоей библиотеки; избранное и история помогают выбрать порядок.</p></div>${state.loading ? busy() : mixes.length ? `<div class="library-grid recommendation-grid">${mixes.map(recommendationCard).join("")}</div>` : recommendationContent()}`;
}
function saveCurrentMix() {
  const p = state.playlist;
  if (!p?.items) return;
  try {
    const items = localPlaylists();
    if (items.length >= 100) throw new Error("Можно сохранить до 100 плейлистов.");
    const saved = {external_id:newPlaylistId(),title:p.title,items:p.items.slice()};
    savePlaylists([saved,...items]); navigate("playlist",playlistView(saved)); toast("Микс сохранён в твои плейлисты");
  } catch (error) { toast(error.message); }
}
