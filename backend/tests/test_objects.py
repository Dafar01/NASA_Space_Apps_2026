import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from astropy import units as u
from astropy.coordinates import SkyCoord
from fastapi.testclient import TestClient
import requests

from backend import objects
from backend.main import create_app


def catalog_row(**changes):
    return {"main_id": "Test star", "ra": 10.0, "dec": -20.0, "otype_txt": "*", "object_type": "Star",
            "pmra": None, "pmdec": None, "plx_value": None, "sp_type": None, "vmag": None, **changes}


class ObjectTests(unittest.TestCase):
    def test_proper_motion_finds_barnard_at_2026_and_keeps_original_catalog_position(self):
        row = catalog_row(main_id="NAME Barnard's star", ra=269.4520769586187, dec=4.69336496657667, pmra=-801.551, pmdec=10362.394)
        click = SkyCoord(269.446268 * u.deg, 4.768204 * u.deg)
        match = objects.candidate(row, click)
        self.assertIsNotNone(match)
        self.assertLess(match.separation_arcsec, 0.01)
        self.assertIn("2026", match.match_basis)
        self.assertEqual(match.ra, row["ra"])
        original = objects.candidate(row, SkyCoord(row["ra"] * u.deg, row["dec"] * u.deg))
        self.assertIn("J2000", original.match_basis)

    def test_outside_radius_is_not_identified_and_missing_fields_remain_unknown(self):
        click = SkyCoord(10 * u.deg, -20 * u.deg)
        self.assertIsNone(objects.candidate(catalog_row(ra=10.1), click))
        match = objects.candidate(catalog_row(vmag=float("nan")), click)
        self.assertIsNone(match.v_magnitude)
        self.assertIsNone(match.parallax_mas)

    def test_matches_and_no_matches_persist_and_never_fetch_in_offline_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("backend.objects.query_catalog", return_value=([catalog_row()], objects.ENDPOINTS[0])) as query:
                result = objects.lookup(10, -20, root)
                self.assertFalse(result["cached"])
                self.assertEqual(result["candidates"][0]["name"], "Test star")
                self.assertEqual(query.call_count, 1)
            with patch("backend.objects.query_catalog", side_effect=AssertionError("Offline must not fetch")):
                cached = objects.lookup(370, -20, root, offline=True)
                self.assertTrue(cached["cached"])
                with self.assertRaises(objects.CatalogNotSaved):
                    objects.lookup(11, -20, root, offline=True)
            with patch("backend.objects.query_catalog", return_value=([], objects.ENDPOINTS[0])):
                self.assertEqual(objects.lookup(12, -20, root)["candidates"], [])
            with patch("backend.objects.query_catalog", side_effect=AssertionError("Empty result should also be cached")):
                self.assertEqual(objects.lookup(12, -20, root, offline=True)["candidates"], [])

    def test_multiple_coincident_objects_are_returned_without_claiming_one_identity(self):
        rows = [catalog_row(main_id="Companion", otype_txt="Pl"), catalog_row(main_id="Parent star")]
        with tempfile.TemporaryDirectory() as temp, patch("backend.objects.query_catalog", return_value=(rows, objects.ENDPOINTS[0])):
            result = objects.lookup(10, -20, Path(temp))
            self.assertEqual(result["total_matches"], 2)
            self.assertEqual(result["candidates"][0]["name"], "Parent star")

    def test_cache_write_failure_returns_information_with_warning(self):
        with tempfile.TemporaryDirectory() as temp, patch("backend.objects.query_catalog", return_value=([catalog_row()], objects.ENDPOINTS[0])), \
                patch("backend.objects.atomic_json", side_effect=PermissionError("locked")):
            result = objects.lookup(10, -20, Path(temp))
            self.assertEqual(len(result["candidates"]), 1)
            self.assertIn("could not be saved", result["cache_warning"])

    def test_invalid_cached_metadata_is_refetched_and_catalog_failure_not_saved_as_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = objects.cache_path(root, 10, -20)
            path.parent.mkdir()
            path.write_text('{"broken": true}')
            with patch("backend.objects.query_catalog", side_effect=objects.CatalogUnavailable("down")):
                with self.assertRaises(objects.CatalogUnavailable):
                    objects.lookup(10, -20, root)
            self.assertEqual(json.loads(path.read_text()), {"broken": True})
            with patch("backend.objects.query_catalog", return_value=([catalog_row()], objects.ENDPOINTS[0])):
                self.assertEqual(objects.lookup(10, -20, root)["candidates"][0]["name"], "Test star")

    def test_bad_source_links_are_rejected(self):
        match = objects.candidate(catalog_row(), SkyCoord(10 * u.deg, -20 * u.deg)).model_dump()
        match["source_url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            objects.Candidate.model_validate(match)

    def test_catalog_failover_and_malformed_payload_do_not_create_false_matches(self):
        failed = requests.Response()
        failed.status_code = 503
        failed._content = b"down"
        failed._content_consumed = True
        valid = requests.Response()
        valid.status_code = 200
        valid._content = json.dumps({"metadata": [{"name": field} for field in catalog_row()], "data": [list(catalog_row().values())]}).encode()
        valid._content_consumed = True
        with patch("backend.objects.requests.get", side_effect=[failed, valid]) as fetch:
            rows, endpoint = objects.query_catalog(10, -20)
            self.assertEqual(endpoint, objects.ENDPOINTS[1])
            self.assertEqual(len(rows), 1)
            self.assertEqual(fetch.call_count, 2)
        for payload in [b"<html>Service down</html>", b'{"metadata": [], "data": []}']:
            response = requests.Response()
            response.status_code = 200
            response._content = payload
            response._content_consumed = True
            with patch("backend.objects.requests.get", return_value=response):
                with self.assertRaises(objects.CatalogUnavailable):
                    objects.query_catalog(10, -20)

    def test_api_validation_offline_and_service_errors(self):
        with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp))) as client:
            for params in [{"ra": "nan", "dec": 0}, {"ra": 1, "dec": 91}, {"ra": 0, "dec": "inf"}]:
                self.assertEqual(client.get("/api/objects", params=params).status_code, 422)
            with patch("backend.objects.query_catalog", side_effect=AssertionError("Do not fetch")):
                self.assertEqual(client.get("/api/objects", params={"ra": 1, "dec": 2, "cache_only": "true"}).status_code, 404)
            with patch("backend.objects.query_catalog", side_effect=objects.CatalogUnavailable("Catalog unavailable")):
                response = client.get("/api/objects", params={"ra": 1, "dec": 2})
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"], "Catalog unavailable")
        with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp), offline=True)) as client, \
                patch("backend.objects.query_catalog", side_effect=AssertionError("Do not fetch")):
            self.assertEqual(client.get("/api/objects", params={"ra": 1, "dec": 2}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
