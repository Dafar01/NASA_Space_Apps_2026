import io
import unittest
from unittest.mock import patch

from astropy.table import Table
import requests

from backend import archive, discovery


def response(rows=None, *, sia=True, status=200, query_status=None):
    result = requests.Response()
    result.status_code = status
    result._content_consumed = True
    if status != 200:
        result._content = b'Proxy Error: Error reading from remote server'
        return result
    rows = rows if rows is not None else [("ibe/data/spherex/dr1/level2/test/level2_test_2D2_spx_v27.fits", 61000., "SPHEREx-D2")]
    if sia:
        rows = [(f'{archive.IRSA}/{uri}', mjd, band) for uri, mjd, band in rows]
    table = Table(rows=rows, names=["access_url" if sia else "uri", "t_min" if sia else "time_bounds_lower", "energy_bandpassname"], dtype=['U300', float, 'U30'])
    output = io.BytesIO()
    table.write(output, format='votable')
    payload = output.getvalue()
    if query_status:
        payload = payload.replace(b'</RESOURCE>', f'<INFO name="QUERY_STATUS" value="{query_status}" /></RESOURCE>'.encode())
    result._content = payload
    return result


class DiscoveryTests(unittest.TestCase):
    def test_sia_filters_detector_deduplicates_and_sorts_without_tap_or_images(self):
        a = 'ibe/data/spherex/dr1/level2/test/older.fits'
        b = 'ibe/data/spherex/qr3/level2/test/newer.fits'
        rows = [(b, 61002., 'SPHEREx-D2'), (a, 61001., 'SPHEREx-D2'), (a, 61001., 'SPHEREx-D2'), (a, 61001., 'SPHEREx-D1')]
        with patch('backend.discovery.requests.get', return_value=response(rows)) as get, patch('backend.archive.download_cutout', side_effect=AssertionError('Metadata only')):
            result = archive.search(258.661698, 14.390973, 'D2')
        self.assertEqual(result.source, 'NASA IRSA SIA')
        self.assertEqual([row['uri'] for row in result.rows], [a, b])
        self.assertEqual([row['release'] for row in result.rows], ['DR1', 'QR3'])
        self.assertFalse(result.limited)
        get.assert_called_once()
        self.assertEqual(get.call_args.args, (discovery.SIA,))
        params = get.call_args.kwargs['params']
        self.assertEqual([value for key, value in params if key == 'COLLECTION'], list(discovery.COLLECTIONS))
        self.assertIn(('POS', 'CIRCLE 258.661698 14.390973 0'), params)

    def test_transient_502_is_retried_then_sia_succeeds(self):
        failed = response(status=502)
        failed.headers['Retry-After'] = '2'
        progress = []
        with patch('backend.discovery.requests.get', side_effect=[failed, response()]) as get, patch('backend.discovery.time.sleep') as sleep:
            result = discovery.discover(1, 2, 'D2', progress=progress.append)
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(2.)
        self.assertTrue(any('HTTP 502' in message and 'Retrying SIA' in message for message in progress))

    def test_sia_outage_falls_back_to_tap_and_preserves_provenance(self):
        with patch('backend.discovery.requests.get', side_effect=[response(status=502), response(status=503), response(sia=False)]) as get, patch('backend.discovery.time.sleep'):
            result = discovery.discover(1, 2, 'D2')
        self.assertEqual(result.source, 'NASA IRSA TAP')
        self.assertEqual(get.call_args_list[-1].args, (archive.TAP,))
        self.assertIn("SPHEREx-D2", get.call_args.kwargs['params']['QUERY'])

    def test_permanent_error_is_not_retried_and_valid_empty_sia_checks_ingestion_lag(self):
        for first in [response(status=400), response([])]:
            with self.subTest(status=first.status_code), patch('backend.discovery.requests.get', side_effect=[first, response(sia=False)]) as get:
                self.assertEqual(discovery.discover(1, 2).source, 'NASA IRSA TAP')
                self.assertEqual(get.call_count, 2)

    def test_overflow_and_invalid_payloads_are_not_accepted_as_complete(self):
        for first in [response(query_status='OVERFLOW'), response(query_status='ERROR')]:
            with self.subTest(payload=first.content[-100:]), patch('backend.discovery.requests.get', side_effect=[first, response(sia=False)]):
                self.assertEqual(discovery.discover(1, 2).source, 'NASA IRSA TAP')
        failed = response(query_status='OVERFLOW')
        with patch('backend.discovery.requests.get', return_value=failed):
            with self.assertRaisesRegex(discovery.DiscoveryUnavailable, 'incomplete'):
                discovery.discover(1, 2)

    def test_unsafe_paths_and_nonfinite_times_are_rejected(self):
        for path in ['https://evil.example/file.fits', f'{archive.IRSA}/ibe/data/spherex/dr1/level2/../file.fits', f'{archive.IRSA}/ibe/data/spherex/dr1/level2/file.fits?other=1', f'{archive.IRSA}/ibe/data/spherex/dr1/level2/%2e%2e/file.fits']:
            with self.subTest(path=path), self.assertRaises(discovery.DiscoveryUnavailable):
                discovery.product_uri(path, absolute=True)
        bad = response([('ibe/data/spherex/dr1/level2/test/a.fits', float('nan'), 'SPHEREx-D2')])
        with patch('backend.discovery.requests.get', side_effect=[bad, response(sia=False)]):
            self.assertEqual(discovery.discover(1, 2).source, 'NASA IRSA TAP')

    def test_both_services_failing_exposes_status_and_does_not_return_empty(self):
        with patch('backend.discovery.requests.get', return_value=response(status=502)) as get, patch('backend.discovery.time.sleep'):
            with self.assertRaisesRegex(discovery.DiscoveryUnavailable, 'SIA returned HTTP 502; IRSA TAP returned HTTP 502'):
                discovery.discover(1, 2)
        self.assertEqual(get.call_count, 4)

    def test_metadata_deadline_bounds_retries_and_retains_time_for_fallback(self):
        clock = [0.]
        def slow_get(*args, **kwargs):
            clock[0] += sum(kwargs['timeout'])
            raise requests.Timeout('slow service')
        with patch('backend.discovery.time.monotonic', side_effect=lambda: clock[0]), patch('backend.discovery.time.sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), patch('backend.discovery.requests.get', side_effect=slow_get):
            with self.assertRaises(discovery.DiscoveryUnavailable):
                discovery.discover(1, 2, metadata_only=True)
        self.assertLessEqual(clock[0], 20.)

    def test_tap_capped_results_are_labelled_as_a_sample(self):
        rows = [(f'ibe/data/spherex/dr1/level2/test/{i}.fits', 61000. + i, 'SPHEREx-D2') for i in range(3)]
        with patch('backend.discovery.MAX_RECORDS', 3), patch('backend.discovery.requests.get', side_effect=[response(query_status='OVERFLOW'), response(rows, sia=False)]):
            self.assertTrue(discovery.discover(1, 2).limited)


if __name__ == '__main__':
    unittest.main()
