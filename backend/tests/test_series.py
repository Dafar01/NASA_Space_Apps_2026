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
from astropy.io import fits

from backend.archive import atomic_json
from backend.main import create_app
from backend.processing import Frame, RENDER_VERSION, align_pixels, compatible_frames, output_grid, read_frame, save_previews
from backend.series import PIPELINE_VERSION, SeriesRequest, prepare, read_saved, rerender_saved, series_id
from backend.rerender import run as rerender_all


def write_test_fits(root: Path, request: SeriesRequest, pixels=None, flags=None, variance=None):
    target, shape = output_grid(request.ra, request.dec, request.size_deg)
    header = target.to_header()
    header['BUNIT'] = 'MJy/sr'
    # A simple linear alternate spectral WCS, isolated to these test files.
    for key, value in {'WCSAXESW': 2, 'CTYPE1W': 'WAVE', 'CTYPE2W': 'BAND',
                       'CUNIT1W': 'm', 'CUNIT2W': 'm', 'CRPIX1W': 1, 'CRPIX2W': 1,
                       'CRVAL1W': 1.5e-6, 'CRVAL2W': 3e-8, 'CDELT1W': 1e-12, 'CDELT2W': 1e-15}.items():
        header[key] = value
    pixels = np.arange(np.prod(shape), dtype=float).reshape(shape) if pixels is None else pixels
    flags = np.zeros(shape, dtype=np.int32) if flags is None else flags
    variance = np.ones(shape) if variance is None else variance
    path = root / 'cutouts' / ('a' * 24 + '.fits')
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(pixels, header, name='IMAGE'),
                  fits.ImageHDU(flags, name='FLAGS'), fits.ImageHDU(variance, name='VARIANCE')]).writeto(path)
    row = {'uri': 'ibe/data/spherex/dr1/level2/test/level2_test_2D2_spx_version.fits',
           'mjd': 60676., 'release': 'DR1', 'detector': request.detector, 'source_url': 'https://irsa.ipac.caltech.edu/test'}
    return path, row, target, shape


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

    def test_absent_pixels_are_not_filled_from_neighbours(self):
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
            np.testing.assert_array_equal(first[-1, 0], [38, 49, 61])

    def test_finite_flagged_values_and_bad_variance_are_visible_but_missing_values_are_not(self):
        request = SeriesRequest(ra=20, dec=-10)
        target, shape = output_grid(request.ra, request.dec, request.size_deg)
        pixels = np.ones(shape)
        pixels[25, 25] = 800  # A flagged bright pixel must remain visible.
        pixels[25, 26] = np.nan
        pixels[30, 30] = np.inf
        flags = np.zeros(shape, dtype=np.int32)
        flags[25, 25] = 1 << 15  # NONLINEAR, but a measurement exists.
        flags[10, 10] = 1 << 9  # Explicitly missing, even if a finite filler exists.
        variance = np.ones(shape)
        variance[15, 15] = 0
        variance[16, 16] = np.nan
        with tempfile.TemporaryDirectory() as temporary:
            path, row, _, _ = write_test_fits(Path(temporary), request, pixels, flags, variance)
            frame = read_frame(path, row, request.ra, request.dec, target, shape)
        self.assertEqual(frame.pixels[25, 25], 800)
        self.assertEqual(frame.pixels[15, 15], 1)
        self.assertEqual(frame.pixels[16, 16], 1)
        self.assertEqual(frame.pixels[25, 27], 1)  # Missing neighbours do not erode this cell.
        self.assertEqual(np.isnan(frame.pixels).sum(), 3)
        self.assertGreater(frame.metadata['available_fraction'], frame.metadata['valid_fraction'])

    def test_low_quality_does_not_discard_available_image_values_or_crop_edges(self):
        request = SeriesRequest(ra=359.999, dec=45)
        target, shape = output_grid(request.ra, request.dec, request.size_deg)
        pixels = np.arange(np.prod(shape), dtype=float).reshape(shape)
        with tempfile.TemporaryDirectory() as temporary:
            path, row, _, _ = write_test_fits(Path(temporary), request, pixels,
                np.full(shape, 1 << 17, dtype=np.int32), np.zeros(shape))
            frame = read_frame(path, row, request.ra, request.dec, target, shape)
        np.testing.assert_array_equal(frame.pixels, pixels)
        self.assertEqual(frame.metadata['available_fraction'], 1)
        self.assertEqual(frame.metadata['valid_fraction'], 0)

    def test_checkerboard_changes_only_missing_cells_and_empty_images_are_rejected(self):
        values = np.arange(16, dtype=float).reshape(4, 4)
        values[0, :2] = np.nan
        with tempfile.TemporaryDirectory() as temporary:
            frame = Frame({}, values)
            display = save_previews([frame], Path(temporary))
            rgb = np.asarray(Image.open(Path(temporary) / frame.metadata['image_file']))
            np.testing.assert_array_equal(rgb[-1, 0], [38, 49, 61])
            np.testing.assert_array_equal(rgb[-1, 1], [58, 70, 82])
            visible = np.flipud(np.isfinite(values))
            np.testing.assert_array_equal(rgb[visible, 0], rgb[visible, 1])
            np.testing.assert_array_equal(rgb[visible, 1], rgb[visible, 2])
            self.assertEqual(display['missing_pixel_style'], 'checkerboard')
            with self.assertRaisesRegex(ValueError, 'without available'):
                save_previews([Frame({}, np.full((4, 4), np.nan))], Path(temporary))


def save_test_manifest(root: Path, request: SeriesRequest) -> dict:
    """An isolated cache fixture, never written into data/cache."""
    identity = series_id(request)
    folder = root / "series" / identity
    frames = [Frame({"timestamp": "2025-01-01T00:00:00Z", "mjd": 60676}, np.arange(16, dtype=float).reshape((4, 4)))]
    save_previews(frames, folder / "frames")
    manifest = {"id": identity, "pipeline_version": PIPELINE_VERSION, "render_version": RENDER_VERSION, "state": "ready", "query": request.model_dump(),
                "created_at": "2025-01-01T00:00:00Z", "frame_count": 1, "date_start": "2025-01-01T00:00:00Z",
                "date_end": "2025-01-01T00:00:00Z", "wavelength_min_um": 1.2, "wavelength_max_um": 1.2,
                "frames": [frame.metadata for frame in frames], "notes": []}
    atomic_json(folder / "manifest.json", manifest)
    return manifest


def save_legacy_manifest(root: Path, request: SeriesRequest):
    path, row, target, shape = write_test_fits(root, request)
    frame = read_frame(path, row, request.ra, request.dec, target, shape)
    frame.pixels[25:30, 25:30] = np.nan  # Model the previous over-masking.
    frame.metadata.pop('available_fraction')
    frame.metadata['valid_fraction'] = float(np.isfinite(frame.pixels).mean())
    identity = series_id(request)
    directory = root / 'series' / identity
    save_previews([frame], directory / 'frames')
    manifest = {'id': identity, 'pipeline_version': PIPELINE_VERSION, 'render_version': 1,
                'query': request.model_dump(), 'created_at': '2025-01-01T00:00:00Z', 'state': 'ready',
                'frames': [frame.metadata], 'frame_count': 1, 'date_start': frame.metadata['timestamp'],
                'date_end': frame.metadata['timestamp'], 'wavelength_min_um': 1.5, 'wavelength_max_um': 1.5,
                'wcs': dict(target.to_header()), 'image_shape': list(shape), 'notes': ['Teal pixels are masked or lack coverage.']}
    atomic_json(directory / 'manifest.json', manifest)
    return manifest


class RerenderTests(unittest.TestCase):
    def test_local_migration_preserves_identity_dates_and_old_files_and_skips_completed_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = save_legacy_manifest(root, SeriesRequest(ra=10, dec=-20))
            directory = root / 'series' / old['id']
            old_image = directory / 'frames' / old['frames'][0]['image_file']
            with (patch('backend.archive.fetch', side_effect=AssertionError('Never fetch')),
                  patch('requests.sessions.Session.request', side_effect=AssertionError('Never use the network'))):
                result = rerender_all(root)
                again = rerender_all(root)
            self.assertEqual(result['updated'], 1)
            self.assertEqual(result['failed'], 0)
            self.assertEqual(again['skipped'], 1)
            updated = read_saved(root, old['id'])
            self.assertEqual(updated['id'], old['id'])
            self.assertEqual(updated['date_start'], old['date_start'])
            self.assertEqual(updated['frame_count'], 1)
            self.assertEqual(updated['render_version'], RENDER_VERSION)
            self.assertEqual(updated['frames'][0]['available_fraction'], 1)
            self.assertNotEqual(updated['frames'][0]['image_file'], old_image.name)
            self.assertTrue(old_image.is_file())
            self.assertEqual(json.loads((directory / 'manifest.render-1.json').read_text()), old)

    def test_missing_original_and_publication_failure_preserve_the_previous_sequence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = save_legacy_manifest(root, SeriesRequest(ra=10, dec=-20))
            path = root / 'series' / old['id'] / 'manifest.json'
            previous = path.read_bytes()
            with patch('backend.processing.read_frame', side_effect=FileNotFoundError('Original missing')):
                with self.assertRaises(FileNotFoundError):
                    rerender_saved(root, old['id'])
            self.assertEqual(path.read_bytes(), previous)
            with patch('backend.series.archive.atomic_json', side_effect=PermissionError('Locked')):
                with self.assertRaises(PermissionError):
                    rerender_saved(root, old['id'])
            self.assertEqual(path.read_bytes(), previous)
            self.assertIsNotNone(read_saved(root, old['id']))

    def test_prepare_upgrades_a_legacy_cache_hit_using_local_originals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = SeriesRequest(ra=10, dec=-20)
            save_legacy_manifest(root, request)
            with patch('backend.archive.search', side_effect=AssertionError('Do not discover')):
                result = prepare(request, root)
            self.assertEqual(result['render_version'], RENDER_VERSION)


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
