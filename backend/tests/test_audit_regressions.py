import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import test_frontend_contract as fixtures
from app import library, main
from app.config import settings
from app.connectors.base import Track, Resolved, clean_title, dedup_key
from app.connectors import ytmusic
from app.connectors.soundcloud import SoundCloudConnector
from app.connectors.spotify import SpotifyConnector
from app.db import db, now

class CatalogTests(unittest.TestCase):
    setUp=fixtures.ContractTests.setUp
    tearDown=fixtures.ContractTests.tearDown
    refresh=fixtures.ContractTests.refresh

    def test_featured_artist_pattern_keeps_ft_at_start_and_duration_unknown_is_distinct(self):
        self.assertEqual(clean_title('Ft. Lauderdale'),'ft. lauderdale')
        self.assertEqual(clean_title('Song (feat. Guest)'),'song')
        self.assertNotEqual(dedup_key('A','B',0),dedup_key('A','B',2400))
        self.assertEqual(dedup_key('A','B',200000,'USABC1234567'),dedup_key('Different','Other',210000,'USABC1234567'))

    def test_empty_playlist_is_fetched_once_and_has_an_exact_count(self):
        self.refresh()
        connector=library.CONNECTORS['vk']
        with patch.object(connector,'fetch_playlist_tracks',AsyncMock(return_value=[])) as fetch:
            for _ in range(2):
                response=self.client.get('/library/playlists/vk/empty/tracks',headers=self.headers)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['items'],[])
            self.assertEqual(fetch.await_count,1)
        row=db().execute("SELECT total,tracks_loaded FROM playlists WHERE source='vk' AND external_id='empty'").fetchone()
        self.assertEqual(tuple(row),(0,1))

    def test_partial_playlist_is_completed_and_track_count_is_corrected(self):
        self.refresh()
        db().execute("UPDATE playlists SET tracks_loaded=0,total=99 WHERE source='vk' AND external_id='p1'")
        db().commit()
        connector=library.CONNECTORS['vk']
        with patch.object(connector,'fetch_playlist_tracks',AsyncMock(return_value=[connector.track(),connector.track('second')])):
            items=self.client.get('/library/playlists/vk/p1/tracks',headers=self.headers).json()['items']
            self.assertEqual(len(items),2)
        self.assertEqual(db().execute("SELECT total FROM playlists WHERE source='vk' AND external_id='p1'").fetchone()[0],2)

    def test_refresh_rekeys_tracks_and_existing_favorites_when_isrc_arrives(self):
        self.refresh()
        self.client.post('/favorites/toggle',headers=self.headers,json={'source':'vk','external_id':'1','favorite':True})
        connector=library.CONNECTORS['vk'];track=connector.track();track.isrc='USABC1234567'
        with patch.object(connector,'fetch_library',AsyncMock(return_value=([track],[]))):
            self.client.post('/library/refresh?source=vk',headers=self.headers)
        self.assertEqual(db().execute("SELECT dedup_key FROM tracks WHERE source='vk'").fetchone()[0],'isrc:USABC1234567')
        self.assertEqual(db().execute('SELECT dedup_key FROM favorites').fetchone()[0],'isrc:USABC1234567')

    def test_resolve_cleans_expired_tokens_and_urls_only(self):
        self.refresh()
        db().execute('INSERT INTO url_cache VALUES(?,?,?,?)',('expired','https://old.example/audio','proxy',now()-1))
        db().execute('INSERT INTO url_cache VALUES(?,?,?,?)',('valid','https://valid.example/audio','proxy',now()+3600))
        db().execute('INSERT INTO issued_streams VALUES(?,?,?)',('old','https://old.example/audio',now()-86401))
        db().execute('INSERT INTO issued_streams VALUES(?,?,?)',('valid','https://valid.example/audio',now()))
        db().commit()
        response=self.client.post('/resolve',headers=self.headers,json={'source':'vk','external_id':'1'})
        self.assertEqual(response.status_code,200)
        self.assertIsNone(db().execute("SELECT 1 FROM url_cache WHERE key='expired'").fetchone())
        self.assertIsNotNone(db().execute("SELECT 1 FROM url_cache WHERE key='valid'").fetchone())
        self.assertIsNone(db().execute("SELECT 1 FROM issued_streams WHERE token='old'").fetchone())
        self.assertIsNotNone(db().execute("SELECT 1 FROM issued_streams WHERE token='valid'").fetchone())

    def test_all_private_endpoints_require_a_token(self):
        endpoints=[('GET','/status'),('POST','/library/refresh'),('GET','/library/tracks'),('GET','/library/playlists'),('GET','/library/playlists/vk/p/tracks'),('GET','/artwork?title=A&artist=B'),('POST','/resolve'),('GET','/auth/spotify/start'),('GET','/favorites'),('POST','/favorites/toggle'),('POST','/plays'),('GET','/history'),('GET','/positions'),('POST','/positions'),('GET','/lyrics?title=A&artist=B')]
        for method,path in endpoints:
            with self.subTest(path=path,method=method):
                response=self.client.request(method,path,**({'json':{}} if method=='POST' else {}))
                self.assertEqual(response.status_code,401)

    def test_login_rate_limit_expires_and_unicode_password_works(self):
        with patch.dict(main._login_attempts,clear=True),patch('app.main._login_clock',return_value=1000):
            for _ in range(10):self.assertEqual(self.client.post('/auth/login',json={'password':'wrong'}).status_code,403)
            response=self.client.post('/auth/login',json={'password':'wrong'})
            self.assertEqual(response.status_code,429)
            self.assertIn('retry-after',response.headers)
            with patch('app.main._login_clock',return_value=1061),patch.object(settings,'auth_password','пароль-音'):
                self.assertEqual(self.client.post('/auth/login',json={'password':'пароль-音'}).status_code,200)

class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_spotify_uses_the_shared_youtube_resolver_off_the_event_loop(self):
        event_thread=threading.get_ident();threads=[]
        class FakeYDL:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,url,**kwargs):
                threads.append(threading.get_ident())
                return {'entries':[{'id':'match','duration':180}]} if url.startswith('ytsearch') else {'requested_formats':[{'url':'https://media.example/full.m4a'}]}
            def process_ie_result(self,info,**kwargs):return info
        youtube=ytmusic.YouTubeMusicConnector()
        spotify=SpotifyConnector(youtube=youtube)
        with patch.object(ytmusic,'_ydl',return_value=FakeYDL()):
            async with httpx.AsyncClient() as client:
                resolved=await spotify.resolve(client,'track',meta={'title':'Song','artist':'Artist','duration_ms':180000},full_only=True)
        self.assertEqual(resolved.stream_url,'https://media.example/full.m4a')
        self.assertTrue(threads and all(thread!=event_thread for thread in threads))

    async def test_soundcloud_resolves_progressive_json_and_skips_malformed_formats(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'url':'https://media.example/song.mp3'}))) as client:
            resolved=await SoundCloudConnector().resolve(client,'track',media=[{'url':'https://api.example/bad'},{'url':'https://api.example/resolve','format':{'protocol':'progressive','mime_type':'audio/mpeg'}}],full_only=True)
        self.assertEqual(resolved.stream_url,'https://media.example/song.mp3')
        self.assertEqual(resolved.kind,'proxy')

    async def test_youtube_album_dictionary_becomes_text(self):
        track=ytmusic._entry_track({'id':'x','title':'Song','album':{'title':'Album'}})
        self.assertEqual(track.album,'Album')

class StreamLimitTests(unittest.TestCase):
    setUp=fixtures.ContractTests.setUp
    tearDown=fixtures.ContractTests.tearDown
    refresh=fixtures.ContractTests.refresh

    def test_oversized_upstream_is_closed_with_bounded_timeouts(self):
        from app.stream import MAX_STREAM_BYTES
        self.refresh()
        path=self.client.post('/resolve',headers=self.headers,json={'source':'vk','external_id':'1'}).json()['stream_path']
        seen=[]
        def handler(request):
            seen.append(request)
            return httpx.Response(200,headers={'content-length':str(MAX_STREAM_BYTES+1)},content=b'')
        original=self.client.app.state.http
        mock=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.client.app.state.http=mock
        try:
            self.assertEqual(self.client.get(path).status_code,502)
            self.assertEqual(seen[0].extensions['timeout']['connect'],10)
            self.assertEqual(seen[0].extensions['timeout']['read'],60)
        finally:
            self.client.portal.call(mock.aclose)
            self.client.app.state.http=original
