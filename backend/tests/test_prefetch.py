"""Failure/resume tests use isolated files and mocked archive responses."""
from argparse import Namespace
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from astropy.io import fits
from astropy.io.votable import from_table, writeto
from astropy.table import Table
import numpy as np
import requests

from backend import archive
from backend.prefetch_all import (InsufficientSpace, batch_lock, discover, download_job,
                                  fetch_product, load_targets, retry, validate_fits)
from backend.series import SeriesRequest, prepare


def write_fits(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(np.ones((2, 2)), name="IMAGE"),
                  fits.ImageHDU(np.zeros((2, 2), dtype=np.int32), name="FLAGS"),
                  fits.ImageHDU(np.ones((2, 2)), name="VARIANCE"),
                  fits.BinTableHDU.from_columns([], name="WCS-WAVE")]).writeto(path, overwrite=True, checksum=True)


def row(index: int) -> dict:
    return {"uri": f"ibe/data/spherex/dr1/level2/exposure{index}.fits", "mjd": 60000 + index,
            "detector": "D2", "release": "DR1"}


class PrefetchTests(unittest.TestCase):
    def test_atomic_checkpoint_retries_temporary_locks_and_preserves_existing_file(self):
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "checkpoint.json"
            archive.atomic_json(path, {"saved": 1})
            attempts = []

            def replace(source, destination):
                attempts.append(destination)
                if len(attempts) < 3:
                    raise PermissionError("Temporary Windows lock")
                return real_replace(source, destination)

            with patch("backend.archive.os.replace", side_effect=replace), patch("backend.archive.time.sleep"):
                archive.atomic_json(path, {"saved": 2})
            self.assertEqual(json.loads(path.read_text()), {"saved": 2})
            self.assertEqual(len(attempts), 3)
            with patch("backend.archive.os.replace", side_effect=PermissionError("Locked")) as replace, patch("backend.archive.time.sleep"):
                with self.assertRaises(PermissionError):
                    archive.atomic_json(path, {"saved": 3})
            self.assertEqual(replace.call_count, 6)
            self.assertEqual(json.loads(path.read_text()), {"saved": 2})
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_checkpoint_failure_does_not_cancel_subsequent_jobs(self):
        args = Namespace(refresh_catalog=False, page_size=500, attempts=1, dry_run=False, workers=1, min_free_gb=0)
        stop = threading.Event()
        with tempfile.TemporaryDirectory() as temp, patch("backend.prefetch_all.discover", return_value={"observations": [row(1)]}), \
                patch("backend.prefetch_all.fetch_product", return_value={"status": "saved", "reused": True}), \
                patch("backend.prefetch_all.prepare", return_value={"frame_count": 2, "excluded": []}):
            request = SeriesRequest(ra=1, dec=2)
            with patch("backend.archive.atomic_json", side_effect=PermissionError("Checkpoint locked")):
                with self.assertRaises(PermissionError):
                    download_job({"name": "Test", "slug": "test"}, request, Path(temp), args, stop)
            self.assertFalse(stop.is_set())
            result = download_job({"name": "Next", "slug": "next"}, SeriesRequest(ra=3, dec=4), Path(temp), args, stop)
            self.assertTrue(result["data_complete"])
            self.assertTrue(result["playback_ready"])

    def test_all_eight_ui_targets_are_read_without_duplicate_coordinate_lists(self):
        targets = load_targets()
        self.assertEqual(len(targets), 8)
        self.assertEqual(targets[0]["slug"], "barnards-star")
        self.assertEqual(next(t for t in targets if t["slug"] == "wise-0855")["ra"], 133.795129)

    def test_catalog_resumes_after_a_failed_page_and_skips_completed_discovery(self):
        request = SeriesRequest(ra=1, dec=2)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            options = dict(refresh=False, page_size=1, attempts=1, stop=threading.Event())
            with patch("backend.archive.search_page", side_effect=[([row(1)], row(1)["uri"], False), RuntimeError("offline")]):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    discover(request, folder, **options)
            with patch("backend.archive.search_page", return_value=([row(2)], row(2)["uri"], True)) as search:
                catalog = discover(request, folder, **options)
                self.assertEqual(search.call_args.kwargs["after"], row(1)["uri"])
                self.assertTrue(catalog["complete"])
                self.assertEqual(len(catalog["observations"]), 2)
            with patch("backend.archive.search_page", side_effect=AssertionError("No re-query")):
                self.assertEqual(len(discover(request, folder, **options)["observations"]), 2)

    def test_tap_paging_uses_uri_cursor_and_refuses_silent_overflow(self):
        table = Table({"uri": [row(1)["uri"]], "time_bounds_lower": [60001.0], "energy_bandpassname": ["SPHEREx-D2"]})
        buffer = io.BytesIO()
        writeto(from_table(table), buffer)
        payload = buffer.getvalue()
        response = requests.Response()
        response.status_code = 200
        response._content = payload
        response._content_consumed = True
        with patch("backend.archive.fetch", return_value=response) as fetch:
            rows, cursor, complete = archive.search_page(1, 2, "D2", page_size=1)
            self.assertFalse(complete)
            self.assertEqual(cursor, row(1)["uri"])
            self.assertEqual(len(rows), 1)
            self.assertIn("ORDER BY a.uri", fetch.call_args.kwargs["params"]["QUERY"])
        response = requests.Response()
        response._content = b'<VOTABLE><RESOURCE><INFO name="QUERY_STATUS" value="OVERFLOW">truncated</INFO></RESOURCE></VOTABLE>'
        response._content_consumed = True
        with patch("backend.archive.fetch", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "OVERFLOW"):
                archive.search_page(1, 2, "D2")

    def test_corrupt_download_is_quarantined_retried_and_then_reused(self):
        request = SeriesRequest(ra=1, dec=2)
        stop = threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            calls = []

            def download(url, root):
                import hashlib
                path = root / "cutouts" / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.fits"
                calls.append(url)
                if len(calls) == 1:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"truncated")
                else:
                    write_fits(path)
                return path

            options = dict(attempts=2, min_free_bytes=0, stop=stop)
            with patch("backend.archive.download_cutout", side_effect=download), patch.object(stop, "wait", return_value=False):
                result = fetch_product(row(1), request, root, {}, **options)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(list((root / "cutouts").glob("*.corrupt-*"))), 1)
            with patch("backend.archive.download_cutout", side_effect=AssertionError("Do not redownload")):
                reused = fetch_product(row(1), request, root, result, **options)
            self.assertTrue(reused["reused"])
            self.assertEqual(reused["sha256"], result["sha256"])

    def test_uri_cursor_preserves_server_collation_instead_of_python_sort_order(self):
        uris = ["ibe/data/spherex/dr1/level2/l2b_retry/file.fits", "ibe/data/spherex/dr1/level2/l2b-v27/file.fits"]
        self.assertNotEqual(uris, sorted(uris))
        table = Table({"uri": uris, "time_bounds_lower": [60001.0, 60002.0]})
        buffer = io.BytesIO()
        writeto(from_table(table), buffer)
        response = requests.Response()
        response._content = buffer.getvalue()
        response._content_consumed = True
        with patch("backend.archive.fetch", return_value=response):
            rows, cursor, complete = archive.search_page(1, 2, "D2", page_size=2)
        self.assertEqual([row["uri"] for row in rows], uris)
        self.assertEqual(cursor, uris[-1])
        self.assertFalse(complete)

    def test_sha256_change_and_low_disk_are_not_called_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "file.fits"
            write_fits(path)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                validate_fits(path, "0" * 64)
            with patch("backend.prefetch_all.shutil.disk_usage", return_value=Namespace(free=0)):
                with self.assertRaises(InsufficientSpace):
                    fetch_product(row(1), SeriesRequest(ra=1, dec=2), Path(temp), {},
                                  attempts=1, min_free_bytes=0, stop=threading.Event())

    def test_one_failure_does_not_discard_successful_products_or_fake_completeness(self):
        request = SeriesRequest(ra=1, dec=2)
        args = Namespace(refresh_catalog=False, page_size=500, attempts=1, dry_run=False, workers=1, min_free_gb=0)
        with tempfile.TemporaryDirectory() as temp, patch("backend.prefetch_all.discover", return_value={"observations": [row(1), row(2)]}), \
                patch("backend.prefetch_all.fetch_product", side_effect=[{"status": "saved", "reused": False, "sha256": "test"}, RuntimeError("503")]), \
                patch("backend.prefetch_all.prepare", return_value={"frame_count": 1, "excluded": []}) as preview:
            result = download_job({"name": "Test", "slug": "test"}, request, Path(temp), args, threading.Event())
            self.assertEqual(result["state"], "partial")
            self.assertFalse(result["data_complete"])
            self.assertFalse(result["playback_ready"])
            self.assertEqual(result["saved_products"], 1)
            self.assertEqual(result["failed_products"], 1)
            self.assertEqual(preview.call_args.kwargs["observations"], [row(1)])
            self.assertTrue(preview.call_args.kwargs["local_only"])

    def test_permanent_http_errors_do_not_repeat(self):
        response = requests.Response()
        response.status_code = 404
        error = requests.HTTPError("missing", response=response)
        operation = Mock(side_effect=error)
        with self.assertRaises(requests.HTTPError):
            retry(operation, 3, threading.Event())
        self.assertEqual(operation.call_count, 1)

    def test_local_only_preview_does_not_discover_archive(self):
        with tempfile.TemporaryDirectory() as temp, patch("backend.archive.search", side_effect=AssertionError("Network forbidden")):
            result = prepare(SeriesRequest(ra=1, dec=2), Path(temp), observations=[], local_only=True, refresh=True)
            self.assertEqual(result["frame_count"], 0)

    def test_concurrent_batch_process_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, batch_lock(Path(temp)):
            with self.assertRaisesRegex(RuntimeError, "Another prefetch"):
                with batch_lock(Path(temp)):
                    self.fail("Second process obtained the lock")


if __name__ == "__main__":
    unittest.main()
