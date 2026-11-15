"""Matched-wavelength previews on a fixed ICRS grid, never aligned to a mover."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import re
from pathlib import Path
import uuid
import warnings

from astropy import units as u
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates
from backend.archive import replace_with_retry

# Quality checks remain metadata; they no longer hide measured image values.
BENIGN_FLAGS = (1 << 21) | (1 << 12)
MISSING_DATA_FLAG = 1 << 9
RENDER_VERSION = 2
RENDER_NOTE = "All available image values are displayed, including quality-flagged measurements. Checkerboard cells have no data; no missing values are filled."
MASK_POLICY = "Display finite IMAGE values regardless of quality flags/variance; omit only nonfinite values, explicit MISSING_DATA, and positions outside image coverage."
WAVELENGTH_RATIO = 1.02
MAX_CANDIDATES = 48
MAX_FRAMES = 16


@dataclass
class Frame:
    metadata: dict
    pixels: np.ndarray


def output_grid(ra: float, dec: float, size: float) -> tuple[WCS, tuple[int, int]]:
    # About the native SPHEREx scale, avoiding invented fine spatial detail.
    width = max(16, int(np.ceil(size * 3600 / 6.15)))
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.crpix = [(width + 1) / 2, (width + 1) / 2]
    wcs.wcs.cdelt = [-size / width, size / width]
    wcs.wcs.radesys = "ICRS"
    return wcs, (width, width)


def align_pixels(pixels: np.ndarray, valid: np.ndarray, spatial: WCS, target: WCS, shape: tuple[int, int]) -> np.ndarray:
    yy, xx = np.indices(shape)
    sky_ra, sky_dec = target.pixel_to_world_values(xx, yy)
    source_x, source_y = spatial.world_to_pixel_values(sky_ra, sky_dec)
    locations = np.array([source_y, source_x])
    # Keep the actual nearest detector sample, without mixing it with masked
    # neighbours or filling a missing sample from nearby sky. grid-constant
    # respects pixel-cell edges (-0.5 .. n-0.5), including WCS roundoff at 0.
    return map_coordinates(np.where(valid & np.isfinite(pixels), pixels, np.nan), locations,
                           order=0, mode="grid-constant", cval=np.nan, prefilter=False)


def read_frame(path: Path, row: dict, ra: float, dec: float, target: WCS, shape: tuple[int, int]) -> Frame:
    with fits.open(path, memmap=False) as hdus:
        image = hdus["IMAGE"]
        pixels = np.asarray(image.data, dtype=np.float64)
        if pixels.ndim != 2 or pixels.size == 0:
            raise ValueError("Cutout contains no two-dimensional IMAGE")
        units = image.header.get("BUNIT", "").replace(" ", "").lower()
        if units not in {"mjy/sr", "mjy/steradian"}:
            raise ValueError(f"Unexpected surface-brightness units: {units}")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # FITS DATE-OBS normalization only; validation is explicit below.
            spatial = WCS(image.header, fobj=hdus).celestial
            x, y = spatial.world_to_pixel_values(ra, dec)
            # SIP belongs to the spatial WCS, not the spectral alternate WCS.
            spectral_header = image.header.copy()
            for key in list(spectral_header):
                if re.fullmatch(r"(?:A|B|AP|BP)_(?:ORDER|\d+_\d+)", key):
                    del spectral_header[key]
            spectral = WCS(spectral_header, fobj=hdus, key="W")
            spectral.sip = None
            wavelength, bandwidth = spectral.pixel_to_world(x, y)
        wavelength = float(wavelength.to_value(u.um))
        bandwidth = float(bandwidth.to_value(u.um))
        if not np.isfinite([x, y, wavelength, bandwidth]).all() or wavelength <= 0 or bandwidth <= 0:
            raise ValueError("Missing or invalid wavelength WCS at the requested position")
        if not (-0.5 <= x < pixels.shape[1] - 0.5 and -0.5 <= y < pixels.shape[0] - 0.5):
            raise ValueError("Selected coordinate lies outside the downloaded cutout")
        flags = np.asarray(hdus["FLAGS"].data, dtype=np.int64)
        variance = np.asarray(hdus["VARIANCE"].data)
        if flags.shape != pixels.shape or variance.shape != pixels.shape:
            raise ValueError("Quality arrays do not match IMAGE dimensions")
        available = np.isfinite(pixels) & ((flags & MISSING_DATA_FLAG) == 0)
        quality = available & np.isfinite(variance) & (variance > 0) & ((flags & ~BENIGN_FLAGS) == 0)
        sampled = align_pixels(pixels, available, spatial, target, shape)
        available_fraction = float(np.isfinite(sampled).mean())
        if available_fraction == 0:
            raise ValueError("This patch contains no available image values")
        quality_sampled = align_pixels(pixels, quality, spatial, target, shape)
        fraction = float(np.isfinite(quality_sampled).mean())
        mjd = float(row["mjd"])
        date = Time(mjd, format="mjd", scale="utc").isot + "Z"
        metadata = {**row, "timestamp": date, "wavelength_um": wavelength, "bandwidth_um": bandwidth,
                    "valid_fraction": fraction, "available_fraction": available_fraction,
                    "render_version": RENDER_VERSION, "processing_version": str(hdus[0].header.get("VERSION", "unknown")),
                    "observation_id": Path(row["uri"]).name.split("_spx_")[0],
                    "fits_file": path.name}
        return Frame(metadata, sampled)


def evenly_sample(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    return [items[int(index)] for index in np.linspace(0, len(items) - 1, limit).round().astype(int)]


def compatible_frames(frames: list[Frame]) -> list[Frame]:
    """Find a group whose *total* center-wavelength spread is <=2%.

    Prefer distinct observation days, then time baseline, then frame count.
    All passbands must also overlap at the selected position.
    """
    best: list[Frame] = []
    best_score = (0, 0.0, 0)
    for anchor in frames:
        low = anchor.metadata["wavelength_um"]
        group = [frame for frame in frames if low <= frame.metadata["wavelength_um"] <= low * WAVELENGTH_RATIO]
        if not group:
            continue
        lower = max(f.metadata["wavelength_um"] - f.metadata["bandwidth_um"] / 2 for f in group)
        upper = min(f.metadata["wavelength_um"] + f.metadata["bandwidth_um"] / 2 for f in group)
        if lower >= upper:
            continue
        dates = [f.metadata["mjd"] for f in group]
        score = (len({int(date) for date in dates}), max(dates) - min(dates), len(group))
        if score > best_score:
            best, best_score = group, score
    return evenly_sample(sorted(best, key=lambda f: f.metadata["mjd"]), MAX_FRAMES)


def save_previews(frames: list[Frame], directory: Path) -> dict:
    """One fixed stretch for the complete series. No per-frame auto exposure."""
    if not frames:
        return {}
    values = np.concatenate([f.pixels[np.isfinite(f.pixels)] for f in frames])
    if not values.size:
        raise ValueError("Cannot render a sequence without available image values")
    low, high = np.percentile(values, [2, 99.7])
    if high <= low:
        high = low + 1
    directory.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames):
        pixels = frame.pixels
        scaled = np.clip((np.nan_to_num(pixels, nan=low) - low) / (high - low), 0, 1)
        scaled = np.arcsinh(8 * scaled) / np.arcsinh(8)
        rgb = np.repeat((scaled[..., None] * 255).astype(np.uint8), 3, axis=2)
        # A pixel-sized checkerboard is a missing-data marker, not invented sky.
        yy, xx = np.indices(pixels.shape)
        missing = ~np.isfinite(pixels)
        rgb[missing & ((xx + yy) % 2 == 0)] = [38, 49, 61]
        rgb[missing & ((xx + yy) % 2 == 1)] = [58, 70, 82]
        output = io.BytesIO()
        Image.fromarray(np.flipud(rgb)).save(output, format="PNG")
        payload = output.getvalue()
        checksum = hashlib.sha256(payload).hexdigest()
        filename = f"{index:03d}-{checksum[:12]}.png"
        destination = directory / filename
        if not destination.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != checksum:
            temporary = directory / f"{filename}.{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_bytes(payload)
                replace_with_retry(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        frame.metadata["image_file"] = filename
        frame.metadata["image_sha256"] = checksum
    return {"stretch": "asinh", "low": float(low), "high": float(high), "units": "MJy/sr",
            "shared_across_frames": True, "north_up": True, "east_left": True,
            "render_version": RENDER_VERSION, "resampling": "nearest detector sample",
            "pixel_policy": MASK_POLICY, "missing_pixel_style": "checkerboard",
            "missing_pixel_colors": ["#26313d", "#3a4652"]}
