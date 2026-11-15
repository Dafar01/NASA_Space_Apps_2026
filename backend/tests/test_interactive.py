"""Interactive archive failures stay bounded; batch retries and cache reuse remain intact."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import requests

from backend import archive
from backend.main import create_app
from backend.series import SeriesRequest, prepare, series_id
from backend.tests.test_series import save_test_manifest


class InteractiveTests(unittest.TestCase):
    def test_expired_preparation_budget_does_not_publish_a_ready_manifest(self):
        request = SeriesRequest(ra=69.0008, dec=16.5291)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('backend.series.time.monotonic', side_effect=[0, 301]), patch('backend.series.processing.save_previews') as previews:
                with self.assertRaisesRegex(TimeoutError, 'exceeded five minutes'):
                    prepare(request, root, observations=[], interactive=True)
                previews.assert_not_called()
            self.assertFalse((root / 'series' / series_id(request) / 'manifest.json').exists())

    def test_archive_search_retries_and_falls_back_with_actionable_message(self):
        with patch('backend.discovery.requests.get', side_effect=requests.Timeout('slow archive')) as get, patch('backend.discovery.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Retry, or open a saved object'):
                archive.search(69.0008, 16.5291, 'D2')
        self.assertEqual(get.call_count, 4)
        self.assertLessEqual(sum(get.call_args.kwargs['timeout']), 30)

    def test_batch_requests_keep_longer_timeouts_and_retries(self):
        with patch('backend.archive.requests.get', side_effect=requests.Timeout('slow archive')) as get, patch('backend.archive.time.sleep'):
            with self.assertRaisesRegex(RuntimeError, 'IRSA request failed'):
                archive.fetch(archive.TAP)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(get.call_args.kwargs['timeout'], (15, 120))

    def test_interactive_failed_download_cleans_partial_and_reuses_valid_original(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.side_effect = requests.Timeout('cutout unavailable')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch('backend.archive.fetch', return_value=response) as fetch:
                with self.assertRaises(requests.Timeout):
                    archive.download_cutout('https://example.test/cutout', root, interactive=True)
            fetch.assert_called_once_with('https://example.test/cutout', timeout=(5, 20), attempts=2)
            self.assertEqual(list((root / 'cutouts').iterdir()), [])
            response.iter_content.side_effect = None
            response.iter_content.return_value = [b'SIMPLE  =' + b' ' * 80]
            with patch('backend.archive.fetch', return_value=response):
                cached = archive.download_cutout('https://example.test/cutout', root, interactive=True)
            with patch('backend.archive.fetch', side_effect=AssertionError('Must reuse local original')):
                self.assertEqual(archive.download_cutout('https://example.test/cutout', root, interactive=True), cached)

    def test_failed_background_job_reports_error_and_retry_can_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            query = SeriesRequest(ra=69.0008, dec=16.5291)
            def prepare(request, cache, progress, *, interactive):
                self.assertTrue(interactive)
                if prepare_mock.call_count == 1:
                    raise RuntimeError('IRSA search timed out; retry')
                return save_test_manifest(cache, request)
            with patch('backend.main.prepare', side_effect=prepare) as prepare_mock, TestClient(create_app(root, offline=False)) as client:
                pending = client.post('/api/series', json=query.model_dump()).json()
                def finished():
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        result = client.get(f"/api/series/{pending['id']}").json()
                        if result['state'] != 'preparing':
                            return result
                        time.sleep(.01)
                    self.fail('Background job did not finish')
                self.assertIn('timed out', finished()['message'])
                self.assertEqual(client.post('/api/series', json=query.model_dump()).status_code, 202)
                self.assertEqual(finished()['state'], 'ready')
                self.assertTrue(client.post('/api/series', json=query.model_dump()).json()['cached'])
                self.assertEqual(prepare_mock.call_count, 2)


if __name__ == '__main__':
    unittest.main()
