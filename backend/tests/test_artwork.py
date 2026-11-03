"""Artwork fallbacks stay optional, authenticated, bounded, and cached."""
import unittest

import httpx

import test_frontend_contract as fixtures
from app.artwork import _cache
from app.connectors.vk import VKConnector
from app.db import db


class ArtworkTests(unittest.TestCase):
    tearDown = fixtures.ContractTests.tearDown
    refresh = fixtures.ContractTests.refresh

    def setUp(self):
        fixtures.ContractTests.setUp(self)
        _cache.clear()

    def with_upstream(self, handler, callback):
        original = self.client.app.state.http
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.client.app.state.http = mock
        try:
            return callback()
        finally:
            self.client.portal.call(mock.aclose)
            self.client.app.state.http = original

    def test_cover_search_matches_artist_and_caches(self):
        calls = []
        def upstream(request):
            calls.append(request)
            self.assertEqual(request.url.host, 'itunes.apple.com')
            self.assertEqual(request.url.params['term'], 'Artist Shared song')
            return httpx.Response(200, json={'results': [
                {'trackName': 'Shared song', 'artistName': 'Another performer', 'artworkUrl100': 'https://is1-ssl.mzstatic.com/wrong/100x100bb.jpg'},
                {'trackName': 'Shared song', 'artistName': 'Artist', 'artworkUrl100': 'https://is1-ssl.mzstatic.com/right/100x100bb.jpg'},
            ]})
        def check():
            for _ in range(2):
                response = self.client.get('/artwork?title=Shared+song&artist=Artist', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['cover'], 'https://is1-ssl.mzstatic.com/right/600x600bb.jpg')
            self.assertEqual(len(calls), 1)
        self.with_upstream(upstream, check)

    def test_failure_does_not_break_tracks(self):
        def upstream(request):
            raise httpx.ConnectError('offline', request=request)
        def check():
            self.assertEqual(self.client.get('/artwork?title=Song&artist=Artist', headers=self.headers).json(), {'cover': None})
            self.assertEqual(self.client.get('/library/tracks', headers=self.headers).status_code, 200)
        self.with_upstream(upstream, check)

    def test_rejects_unknown_artwork_hosts_and_requires_auth(self):
        self.assertEqual(self.client.get('/artwork?title=Song&artist=Artist').status_code, 401)
        def check():
            self.assertEqual(self.client.get('/artwork?title=Song&artist=Artist', headers=self.headers).json(), {'cover': None})
        self.with_upstream(lambda r: httpx.Response(200, json={'results': [
            {'trackName': 'Song', 'artistName': 'Artist', 'artworkUrl100': 'https://unrelated.test/art.jpg'},
        ]}), check)
        self.assertEqual(self.client.get('/artwork?title=' + 'x' * 201 + '&artist=Artist', headers=self.headers).status_code, 422)

    def test_dedup_uses_cover_from_another_source(self):
        self.refresh()
        db().execute("UPDATE tracks SET cover=? WHERE source='soundcloud'", ('https://example.test/cover.jpg',))
        db().commit()
        track = self.client.get('/library/tracks?source=vk', headers=self.headers).json()['items'][0]
        self.assertEqual(track['source'], 'vk')
        self.assertEqual(track['cover'], 'https://example.test/cover.jpg')

    def test_vk_album_thumbnail(self):
        self.assertEqual(VKConnector._cover({'album': {'thumb': {'photo_600': 'https://example.test/600.jpg', 'photo_68': 'https://example.test/68.jpg'}}}), 'https://example.test/600.jpg')
