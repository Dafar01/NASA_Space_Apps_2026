"""Synthetic arrays stay inside unit tests; demo playback uses real IRSA files."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from astropy.wcs import WCS
from fastapi.testclient import TestClient
import numpy as np
from PIL import Image

from backend.archive import atomic_json
from backend.main import create_app
from backend.processing import Frame, align_pixels, compatible_frames, output_grid, save_previews
from backend.series import PIPELINE_VERSION, SeriesRequest, prepare, read_saved, series_id


class ProcessingTests(unittest.TestCase):
    def test_stationary_star_stays_fixed_across_shifted_wcs(self):
        target, shape = output_grid(359.999, 45, 0.1)
        y, x = np.indices(shape)
        peaks = []
        for shift in (0, 6):
            source = WCS(target.to_header())
            source.wcs.crpix[0] += shift
            sx, sy = source.world_to_pixel_values(359.999, 45)
            pixels = np.exp(-((x - sx) ** 2 + (y - sy) ** 2) / 2)
            aligned = align_pixels(pixels, np.ones(shape, dtype=bool), source, target, shape)
            peaks.append(np.unravel_index(np.nanargmax(aligned), shape))
        self.assertEqual(peaks[0], peaks[1])

    def test_flagged_pixels_cannot_leak_through_interpolation(self):
        target, shape = output_grid(20, -10, 0.1)
        pixels = np.ones(shape)
        valid = np.ones(shape, dtype=bool)
        valid[25:30, 25:30] = False
        pixels[~valid] = 1000000
        aligned = align_pixels(pixels, valid, target, target, shape)
        self.assertTrue(np.isnan(aligned[27, 27]))
        self.assertLessEqual(np.nanmax(aligned), 1.000001)

    def test_wavelength_group_has_bounded_total_spread_not_chained_matches(self):
        frames = [Frame({"wavelength_um": wave, "bandwidth_um": 0.2, "mjd": mjd}, np.ones((2, 2)))
                  for wave, mjd in [(1, 60000), (1.019, 60001), (1.038, 60100)]]
        selected = compatible_frames(frames)
        self.assertEqual(len(selected), 2)
        waves = [frame.metadata["wavelength_um"] for frame in selected]
        self.assertLessEqual(max(waves) / min(waves), 1.02)
        self.assertEqual([f.metadata["mjd"] for f in selected], [60001, 60100])

    def test_shared_stretch_preserves_brightness_order_and_explicit_masks(self):
        with tempfile.TemporaryDirectory() as temp:
            faint = np.arange(64, dtype=float).reshape((8, 8))
            faint[0, 0] = np.nan
            bright = faint * 2
            frames = [Frame({}, faint), Frame({}, bright)]
            display = save_previews(frames, Path(temp))
            self.assertTrue(display["shared_across_frames"])
            first = np.array(Image.open(Path(temp) / frames[0].metadata["image_file"]))
            second = np.array(Image.open(Path(temp) / frames[1].metadata["image_file"]))
            self.assertGreater(second[3, 3, 0], first[3, 3, 0])
            np.testing.assert_array_equal(first[-1, 0], [28, 43, 58])


def save_test_manifest(root: Path, request: SeriesRequest) -> dict:
    """An isolated cache fixture, never written into data/cache."""
    identity = series_id(request)
    folder = root / "series" / identity
    frames = [Frame({"timestamp": "2025-01-01T00:00:00Z", "mjd": 60676}, np.arange(16, dtype=float).reshape((4, 4)))]
    save_previews(frames, folder / "frames")
    manifest = {"id": identity, "pipeline_version": PIPELINE_VERSION, "state": "ready", "query": request.model_dump(),
                "created_at": "2025-01-01T00:00:00Z", "frame_count": 1, "date_start": "2025-01-01T00:00:00Z",
                "date_end": "2025-01-01T00:00:00Z", "wavelength_min_um": 1.2, "wavelength_max_um": 1.2,
                "frames": [frame.metadata for frame in frames], "notes": []}
    atomic_json(folder / "manifest.json", manifest)
    return manifest


class CacheTests(unittest.TestCase):
    def test_cache_identity_uses_position_detector_and_patch_size(self):
        self.assertEqual(series_id(SeriesRequest(ra=360, dec=1)), series_id(SeriesRequest(ra=0, dec=1)))
        self.assertNotEqual(series_id(SeriesRequest(ra=0, dec=1)), series_id(SeriesRequest(ra=0, dec=1, detector="D6")))
        self.assertNotEqual(series_id(SeriesRequest(ra=0, dec=1)), series_id(SeriesRequest(ra=0, dec=1, size_deg=0.2)))

    def test_saved_sequence_survives_new_app_and_forbids_network_when_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = SeriesRequest(ra=10, dec=-20)
            manifest = save_test_manifest(root, request)
            for _restart in range(2):
                with patch("backend.archive.fetch", side_effect=AssertionError("No internet permitted")), TestClient(create_app(root, offline=True)) as client:
                    self.assertTrue(client.get("/api/health").json()["offline"])
                    saved = client.post("/api/series", json=request.model_dump())
                    self.assertEqual(saved.status_code, 200)
                    self.assertTrue(saved.json()["cached"])
                    self.assertEqual(len(client.get("/api/series").json()["series"]), 1)
                    filename = manifest["frames"][0]["image_file"]
                    image = client.get(f"/api/series/{manifest['id']}/frames/{filename}")
                    self.assertEqual(image.status_code, 200)
                    self.assertEqual(hashlib.sha256(image.content).hexdigest(), manifest["frames"][0]["image_sha256"])
                    missing = client.post("/api/series", json={"ra": 11, "dec": -20})
                    self.assertEqual(missing.status_code, 404)
                    self.assertIn("not saved locally", missing.json()["detail"])

    def test_local_cache_is_reused_before_archive_search(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            request = SeriesRequest(ra=10, dec=-20)
            save_test_manifest(root, request)
            with patch("backend.archive.search", side_effect=AssertionError("Cache hit must not query IRSA")):
                self.assertEqual(prepare(request, root)["frame_count"], 1)

    def test_corrupt_or_incomplete_cache_is_not_advertised_as_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = save_test_manifest(root, SeriesRequest(ra=10, dec=-20))
            image = root / "series" / manifest["id"] / "frames" / manifest["frames"][0]["image_file"]
            image.write_bytes(b"corrupted")
            self.assertIsNone(read_saved(root, manifest["id"]))
            with TestClient(create_app(root, offline=True)) as client:
                self.assertEqual(client.get("/api/series").json()["series"], [])
                self.assertEqual(client.get(f"/api/series/{manifest['id']}").status_code, 404)

    def test_invalid_coordinates_detector_and_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp), offline=True)) as client:
            for query in [{"ra": 10, "dec": 91}, {"ra": "NaN", "dec": 0}, {"ra": 1, "dec": 2, "detector": "D2' OR 1=1"}, {"ra": 1, "dec": 2, "size_deg": 100}]:
                self.assertEqual(client.post("/api/series", json=query).status_code, 422)
            self.assertEqual(client.get("/api/series/not-a-hash").status_code, 404)


if __name__ == "__main__":
    unittest.main()
