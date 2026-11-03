import unittest
from unittest.mock import AsyncMock, patch
import test_frontend_contract as fixtures
from app import library
from app.connectors.base import Resolved, SourceUnavailable
from app.db import db

class ResolveRetryTests(unittest.TestCase):
    setUp=fixtures.ContractTests.setUp
    tearDown=fixtures.ContractTests.tearDown
    refresh=fixtures.ContractTests.refresh

    def url(self,data):
        if data['kind']=='direct':return data['stream_url']
        return library.lookup_stream(data['stream_path'].rsplit('/',1)[1])

    def test_retry_bypasses_valid_ttl_cache_and_gets_a_new_url(self):
        self.refresh()
        connector=library.CONNECTORS['vk']
        body={'source':'vk','external_id':'1','full_only':True}
        with patch.object(connector,'resolve',AsyncMock(side_effect=[Resolved('https://media.test/old'),Resolved('https://media.test/fresh')])) as resolve:
            first=self.client.post('/resolve',headers=self.headers,json=body).json()
            cached=self.client.post('/resolve',headers=self.headers,json=body).json()
            fresh=self.client.post('/resolve',headers=self.headers,json={**body,'refresh':True}).json()
        self.assertEqual(self.url(first),'https://media.test/old')
        self.assertEqual(self.url(cached),'https://media.test/old')
        self.assertEqual(self.url(fresh),'https://media.test/fresh')
        self.assertEqual(resolve.await_count,2)

    def test_failed_refresh_does_not_leave_dead_url_in_cache(self):
        self.refresh()
        db().execute('INSERT INTO url_cache(key,stream_url,kind,expires_at) VALUES(?,?,?,?)',('vk|1|full','https://dead.test/audio','direct',9999999999));db().commit()
        with patch.object(library.CONNECTORS['vk'],'resolve',AsyncMock(side_effect=SourceUnavailable('unavailable'))):
            response=self.client.post('/resolve',headers=self.headers,json={'source':'vk','external_id':'1','full_only':True,'refresh':True})
        self.assertEqual(response.status_code,502)
        self.assertIsNone(db().execute("SELECT 1 FROM url_cache WHERE key='vk|1|full'").fetchone())

    def test_mime_type_is_returned_and_cached_for_extensionless_hls(self):
        self.refresh()
        body={'source':'vk','external_id':'1'}
        with patch.object(library.CONNECTORS['vk'],'resolve',AsyncMock(return_value=Resolved('https://media.test/signed',kind='direct',content_type='application/vnd.apple.mpegurl'))) as resolve:
            for _ in range(2):
                response=self.client.post('/resolve',headers=self.headers,json=body)
                self.assertEqual(response.json()['content_type'],'application/vnd.apple.mpegurl')
            self.assertEqual(resolve.await_count,1)
