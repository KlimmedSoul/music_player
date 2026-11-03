import unittest
import httpx
import test_frontend_contract as fixtures

class LyricsTests(unittest.TestCase):
    setUp = fixtures.ContractTests.setUp
    tearDown = fixtures.ContractTests.tearDown

    def query(self, response):
        original=self.client.app.state.http
        def handler(request):
            if request.url.path.endswith('/get'):
                return httpx.Response(404,json={})
            return response
        mock=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.client.app.state.http=mock
        try:
            return self.client.get('/lyrics?title=Song&artist=Artist&duration_s=200',headers=self.headers)
        finally:
            self.client.portal.call(mock.aclose)
            self.client.app.state.http=original

    def test_search_uses_matching_title_and_artist_instead_of_first_synced_result(self):
        response=self.query(httpx.Response(200,json=[
            {'trackName':'Other song','artistName':'Other artist','syncedLyrics':'wrong'},
            {'trackName':'Song','artistName':'Artist','duration':200,'plainLyrics':'correct'},
        ]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json(),{'synced':None,'plain':'correct'})

    def test_provider_failure_is_reported_without_crashing_player(self):
        self.assertEqual(self.query(httpx.Response(503,text='unavailable')).status_code,502)
