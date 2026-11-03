"""API integration checks for the vanilla frontend; no provider credentials needed.
Run: python -m unittest discover -s backend/tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = str(Path(_temp.name) / "contract.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi.testclient import TestClient
from app.main import app
from app import library
from app.config import settings
from app.connectors.base import Playlist, Resolved, SourceUnavailable, Track, clean_title, dedup_key
from app.db import db


class Connector:
    def __init__(self, name, fail=False):
        self.name, self.fail = name, fail

    def is_configured(self):
        return True

    def track(self, suffix="1"):
        return Track(source=self.name, external_id=suffix, title="Shared song", artist="Artist", duration_ms=5000)

    async def fetch_library(self, client):
        if self.fail:
            raise SourceUnavailable("test provider unavailable")
        return [self.track()], [Playlist(self.name, "p1", "My playlist", total=1, track_ids=["1"])]

    async def fetch_playlist_tracks(self, client, ext):
        return [self.track("lazy")]

    async def resolve(self, client, external_id, **kwargs):
        return Resolved("https://media.example.test/song.wav")


class ContractTests(unittest.TestCase):
    def setUp(self):
        for table in ["tracks", "playlists", "playlist_items", "url_cache", "issued_streams", "store", "favorites", "plays", "positions"]:
            db().execute(f"DELETE FROM {table}")
        db().commit()
        self.connectors = patch.dict(library.CONNECTORS, {"vk": Connector("vk"), "soundcloud": Connector("soundcloud"), "spotify": Connector("spotify", fail=True)}, clear=True)
        self.password = patch.object(settings, "auth_password", "contract-only-password")
        self.connectors.start(); self.password.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.token = self.client.post("/auth/login", json={"password": "contract-only-password"}).json()["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.password.stop(); self.connectors.stop()

    def refresh(self):
        response = self.client.post("/library/refresh", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_track_names_are_not_truncated_by_featured_artist_pattern(self):
        self.assertEqual(clean_title("After Hours"), "after hours")
        self.assertNotEqual(dedup_key("Track 00", "Artist", 5000), dedup_key("Track 01", "Artist", 5000))
        self.assertEqual(clean_title("Song (feat. Guest)"), "song")

    def test_spotify_start_uses_configured_callback(self):
        from urllib.parse import parse_qs, urlparse
        from app.connectors.spotify import SpotifyConnector
        with patch.dict(library.CONNECTORS, {"spotify": SpotifyConnector()}), patch.object(settings, "spotify_client_id", "test-client"), patch.object(settings, "spotify_client_secret", "test-secret"), patch.object(settings, "spotify_redirect_uri", "https://api.example.test/auth/spotify/callback"):
            response = self.client.get("/auth/spotify/start", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            url = urlparse(response.json()["url"])
            self.assertEqual(url.netloc, "accounts.spotify.com")
            self.assertEqual(parse_qs(url.query)["redirect_uri"], ["https://api.example.test/auth/spotify/callback"])

    def test_auth_and_status(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/library/tracks").status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={"password": "wrong"}).status_code, 403)
        self.assertEqual(self.client.get("/status", headers=self.headers).json()["sources"]["vk"], "configured")

    def test_refresh_partial_failure_search_dedup_and_selected_source(self):
        results = self.refresh()
        self.assertTrue(results["vk"].startswith("ok:"), results)
        self.assertTrue(results["spotify"].startswith("error:"), results)
        data = self.client.get("/library/tracks?q=Shared&limit=50", headers=self.headers).json()
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(set(data["items"][0]["sources"]), {"vk", "soundcloud"})
        filtered = self.client.get("/library/tracks?source=vk", headers=self.headers).json()
        self.assertEqual(filtered["items"][0]["source"], "vk")
        self.assertEqual(self.client.get("/library/tracks?q=not-found", headers=self.headers).json()["items"], [])
        self.assertEqual(self.client.get("/library/tracks?cursor=50", headers=self.headers).json()["items"], [])

    def test_existing_and_lazy_playlist(self):
        self.refresh()
        self.assertEqual(len(self.client.get("/library/playlists", headers=self.headers).json()["items"]), 2)
        self.assertEqual(self.client.get("/library/playlists/vk/p1/tracks", headers=self.headers).json()["items"][0]["external_id"], "1")
        response = self.client.get("/library/playlists/vk/new/tracks", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["external_id"], "lazy")

    def test_resolve_proxy_direct_and_unauthenticated_stream_with_range(self):
        self.refresh()
        response = self.client.post("/resolve", headers=self.headers, json={"source": "vk", "external_id": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        stream = response.json()["stream_path"]
        def upstream(request):
            self.assertEqual(request.headers["range"], "bytes=0-3")
            return httpx.Response(206, content=b"RIFF", headers={"content-type": "audio/wav", "content-range": "bytes 0-3/8"})
        original = app.state.http
        app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        try:
            audio = self.client.get(stream, headers={"Range": "bytes=0-3"})
            self.assertEqual(audio.status_code, 206)
            self.assertEqual(audio.content, b"RIFF")
        finally:
            self.client.portal.call(app.state.http.aclose)
            app.state.http = original
        db().execute("INSERT INTO url_cache(key,stream_url,kind,expires_at) VALUES(?,?,?,?)", ("vk|direct", "https://media.example.test/live.m3u8", "direct", 9999999999))
        db().commit()
        direct = self.client.post("/resolve", headers=self.headers, json={"source": "vk", "external_id": "direct"}).json()
        self.assertEqual(direct["kind"], "direct")
        self.assertEqual(self.client.get("/stream/invalid").status_code, 404)

    def test_favorites_are_shared_and_deduped_across_sources(self):
        self.refresh()  # vk and soundcloud copies of "Shared song" share a dedup key
        self.assertEqual(self.client.get("/favorites", headers=self.headers).json()["items"], [])
        toggle = self.client.post("/favorites/toggle", headers=self.headers, json={"source": "vk", "external_id": "1"})
        self.assertEqual(toggle.json(), {"favorite": True})
        items = self.client.get("/favorites", headers=self.headers).json()["items"]
        self.assertEqual([(i["source"], i["external_id"]) for i in items], [("vk", "1")])
        # the same track from another service refers to the same favorite
        self.assertEqual(self.client.post("/favorites/toggle", headers=self.headers, json={"source": "soundcloud", "external_id": "1"}).json(), {"favorite": False})
        self.assertEqual(self.client.get("/favorites", headers=self.headers).json()["items"], [])
        self.assertEqual(self.client.post("/favorites/toggle", headers=self.headers, json={"source": "vk", "external_id": "ghost"}).status_code, 404)

    def test_plays_positions_and_history(self):
        self.refresh()
        play = lambda **kw: self.client.post("/plays", headers=self.headers, json={"source": "vk", "external_id": "1", **kw})
        self.assertEqual(play(position_s=20, duration_s=60, completed=False).status_code, 200)
        positions = self.client.get("/positions", headers=self.headers).json()["items"]
        self.assertEqual([(p["track_source"], p["position_s"]) for p in positions], [("vk", 20)])
        # history dedupes across services
        play(source="soundcloud", position_s=5, duration_s=60)
        history = self.client.get("/history", headers=self.headers).json()["items"]
        self.assertEqual([h["title"] for h in history], ["Shared song"])
        self.assertEqual(play(position_s=60, duration_s=60, completed=True).status_code, 200)
        self.assertEqual(self.client.get("/positions", headers=self.headers).json()["items"], [])

    def test_positions_upsert_and_status_reports_last_refresh(self):
        self.refresh()
        body = {"source": "vk", "external_id": "1", "position_s": 42}
        self.client.post("/positions", headers=self.headers, json=body)
        self.client.post("/positions", headers=self.headers, json={**body, "position_s": 55})
        items = self.client.get("/positions", headers=self.headers).json()["items"]
        self.assertEqual([i["position_s"] for i in items], [55])
        status = self.client.get("/status", headers=self.headers).json()
        self.assertTrue(status["last_refresh"]["sources"]["vk"].startswith("ok:"))


if __name__ == "__main__":
    unittest.main()
