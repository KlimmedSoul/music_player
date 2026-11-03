import unittest
import test_frontend_contract as fixtures

class ActivitySafetyTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    tearDown = fixtures.ContractTests.tearDown
    refresh = fixtures.ContractTests.refresh

    def test_repeated_favorite_migration_is_idempotent_across_sources(self):
        self.refresh()
        for source in ['vk','soundcloud','vk']:
            response=self.client.post('/favorites/toggle',headers=self.headers,json={'source':source,'external_id':'1','favorite':True})
            self.assertEqual(response.json(),{'favorite':True})
        self.assertEqual(len(self.client.get('/favorites',headers=self.headers).json()['items']),1)
        for source in ['soundcloud','vk']:
            self.assertEqual(self.client.post('/favorites/toggle',headers=self.headers,json={'source':source,'external_id':'1','favorite':False}).json(),{'favorite':False})
        self.assertEqual(self.client.get('/favorites',headers=self.headers).json()['items'],[])

    def test_invalid_progress_is_rejected_before_writing_to_sqlite(self):
        self.refresh()
        for invalid in ['NaN','Infinity',-1,1000000]:
            response=self.client.post('/positions',headers=self.headers,json={'source':'vk','external_id':'1','position_s':invalid})
            self.assertEqual(response.status_code,422)
        self.assertEqual(self.client.get('/positions',headers=self.headers).json()['items'],[])
