from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from astropy.table import Table
from fastapi.testclient import TestClient
import requests

from backend import metadata
from backend.main import create_app


def observation(**changes):
    return metadata.Observation(observation_id="level2_test_2D2", detector="D2", release="DR1",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), **changes)


def votable_response(rows):
    table = Table(rows=rows, names=["uri", "time_bounds_lower", "energy_bandpassname"])
    output = io.BytesIO()
    table.write(output, format="votable")
    response = requests.Response()
    response.status_code = 200
    response._content = output.getvalue()
    response._content_consumed = True
    return response


class MetadataTests(unittest.TestCase):
    def test_saved_field_metadata_needs_no_images_and_handles_ra_wrap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "series" / ("a" * 24) / "manifest.json"
            path.parent.mkdir(parents=True)
            frame = observation(wavelength_um=1.576, bandwidth_um=.038, valid_fraction=.637).model_dump(mode="json")
            path.write_text(json.dumps({"id": "a" * 24, "pipeline_version": 1,
                "query": {"ra": 359.99, "dec": 0, "detector": "D2", "size_deg": .1}, "frames": [frame]}))
            with patch("backend.metadata.requests.get", side_effect=AssertionError("Do not contact IRSA")), \
                    patch("backend.archive.download_cutout", side_effect=AssertionError("Never download images")):
                result = metadata.lookup(.01, 0, root)
                self.assertEqual(result["source"], "saved_cutouts")
                saved = result["observations"][0]
                self.assertEqual(saved["wavelength_um"], 1.576)
                self.assertEqual(saved["valid_fraction"], .637)
                self.assertEqual(saved["center_ra"], 359.99)
                self.assertNotIn("image_url", saved)
                self.assertEqual(metadata.lookup(1, 0, root, offline=True)["observations"], [])

    def test_archive_query_is_small_metadata_only_and_keeps_pixel_fields_unknown(self):
        row = ("ibe/data/spherex/dr1/level2/test/level2_test_2D2_spx_version.fits", 61000., "SPHEREx-D2")
        with patch("backend.metadata.requests.get", return_value=votable_response([row, row])) as get:
            observations, limited = metadata.query_archive(10., -20.)
            self.assertEqual(len(observations), 1)
            self.assertFalse(limited)
            item = observations[0]
            self.assertEqual(item.observation_id, "level2_test_2D2")
            self.assertEqual(item.release, "DR1")
            self.assertIsNone(item.wavelength_um)
            self.assertIsNone(item.bandwidth_um)
            self.assertIsNone(item.valid_fraction)
            args, options = get.call_args
            self.assertEqual(args, (metadata.TAP,))
            self.assertIn("TOP 25", options["params"]["QUERY"])
            self.assertEqual(options["timeout"], (5, 15))

    def test_live_metadata_and_empty_results_are_cached_and_reused_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("backend.metadata.query_archive", return_value=([observation()], False)) as query:
                first = metadata.lookup(370, -20, root)
                self.assertFalse(first["cached"])
                self.assertEqual(query.call_count, 1)
            with patch("backend.metadata.query_archive", side_effect=AssertionError("No network")):
                saved = metadata.lookup(10, -20, root, offline=True)
                self.assertTrue(saved["cached"])
                self.assertIsNone(saved["observations"][0]["wavelength_um"])
            with patch("backend.metadata.query_archive", return_value=([], False)):
                metadata.lookup(11, -20, root)
            with patch("backend.metadata.query_archive", side_effect=AssertionError("Reuse empty results")):
                self.assertEqual(metadata.lookup(11, -20, root)["observations"], [])

    def test_unavailable_and_invalid_responses_do_not_become_empty_caches(self):
        response = requests.Response()
        response.status_code = 503
        response._content = b"down"
        response._content_consumed = True
        with patch("backend.metadata.requests.get", return_value=response):
            with self.assertRaises(metadata.MetadataUnavailable):
                metadata.query_archive(1, 2)
        response = requests.Response()
        response.status_code = 200
        response._content = b'<VOTABLE><RESOURCE><INFO name="QUERY_STATUS" value="OVERFLOW" /></RESOURCE></VOTABLE>'
        response._content_consumed = True
        with tempfile.TemporaryDirectory() as directory, patch("backend.metadata.requests.get", return_value=response):
            with self.assertRaises(metadata.MetadataUnavailable):
                metadata.lookup(1, 2, Path(directory))
            self.assertFalse(list(Path(directory).rglob("*.json")))

    def test_result_limit_and_cache_write_failure_are_explicit(self):
        rows = [(f"ibe/data/spherex/dr1/level2/test/level2_{index}_2D2_spx_version.fits", 61000. - index, "SPHEREx-D2") for index in range(25)]
        with patch("backend.metadata.requests.get", return_value=votable_response(rows)):
            items, limited = metadata.query_archive(1, 2)
            self.assertTrue(limited)
            self.assertEqual(len(items), 24)
        with tempfile.TemporaryDirectory() as directory, patch("backend.metadata.query_archive", return_value=([observation()], False)), \
                patch("backend.metadata.atomic_json", side_effect=PermissionError("locked")):
            result = metadata.lookup(1, 2, Path(directory))
            self.assertEqual(len(result["observations"]), 1)
            self.assertIn("could not be saved", result["cache_warning"])

    def test_api_validates_inputs_and_enforces_offline_without_preparing_sequences(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory), offline=True)) as client, \
                patch("backend.metadata.query_archive", side_effect=AssertionError("Offline cannot fetch")), \
                patch("backend.main.prepare", side_effect=AssertionError("Metadata must never prepare frames")):
            for params in [{"ra": "nan", "dec": 0}, {"ra": 0, "dec": 91}, {"ra": "inf", "dec": 0}]:
                self.assertEqual(client.get("/api/observation-metadata", params=params).status_code, 422)
            result = client.get("/api/observation-metadata", params={"ra": 370, "dec": -20})
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["ra"], 10)
            self.assertIn("Reconnect", result.json()["message"])
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(Path(directory))) as client:
            with patch("backend.metadata.query_archive", side_effect=metadata.MetadataUnavailable("Please retry")):
                self.assertEqual(client.get("/api/observation-metadata?ra=1&dec=2").status_code, 503)
                self.assertEqual(client.get("/api/observation-metadata?ra=1&dec=2&cache_only=true").status_code, 200)


if __name__ == "__main__":
    unittest.main()
