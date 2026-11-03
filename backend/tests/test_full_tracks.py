import unittest
from unittest.mock import AsyncMock, patch

import httpx
import test_frontend_contract as fixtures
from app.connectors.spotify import SpotifyConnector
from app.connectors.soundcloud import SoundCloudConnector
from app.connectors.base import SourceUnavailable, Resolved
from app import library
from app.db import db


class FullTrackTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_mode_rejects_soundcloud_snippets(self):
        connector = SoundCloudConnector()
        async with httpx.AsyncClient() as client:
            with self.assertRaises(SourceUnavailable):
                await connector.resolve(client, "1", media=[{"url":"https://example.test/snippet", "snipped":True}], full_only=True)

    async def test_full_mode_never_returns_spotify_preview(self):
        connector = SpotifyConnector()
        meta = {'title':'Song','artist':'Artist','duration_ms':200000,'preview_url':'https://preview.example/30s.mp3'}
        async with httpx.AsyncClient() as client:
            with patch.object(connector.youtube, 'search_and_resolve', AsyncMock(return_value=None)):
                with self.assertRaises(SourceUnavailable):
                    await connector.resolve(client,'song',meta=meta,full_only=True)
                preview = await connector.resolve(client,'song',meta=meta)
                self.assertEqual(preview.stream_url,meta['preview_url'])
            with patch.object(connector.youtube, 'search_and_resolve', AsyncMock(return_value=Resolved('https://full.example/song.m4a'))):
                full = await connector.resolve(client,'song',meta=meta,full_only=True)
                self.assertEqual(full.stream_url,'https://full.example/song.m4a')
                self.assertEqual(full.kind,'proxy')


class FullCacheTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    tearDown = fixtures.ContractTests.tearDown

    def test_full_mode_does_not_reuse_preview_cache(self):
        db().execute('INSERT INTO url_cache VALUES(?,?,?,?)', ('spotify|1','https://preview.example/30s.mp3','direct',9999999999))
        db().commit()
        connector = fixtures.Connector('spotify')
        connector.resolve = AsyncMock(side_effect=SourceUnavailable('full track unavailable'))
        with patch.dict(library.CONNECTORS, {'spotify':connector}):
            response=self.client.post('/resolve',headers=self.headers,json={'source':'spotify','external_id':'1','full_only':True})
            self.assertEqual(response.status_code,502)
            self.assertTrue(connector.resolve.call_args.kwargs['full_only'])
            response=self.client.post('/resolve',headers=self.headers,json={'source':'spotify','external_id':'1'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['stream_url'],'https://preview.example/30s.mp3')
