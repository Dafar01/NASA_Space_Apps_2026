"""Matched-wavelength previews on a fixed ICRS grid, never aligned to a mover."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import re
from pathlib import Path
import warnings

from astropy import units as u
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS
import numpy as np
from PIL import Image
from scipy.ndimage import map_coordinates

# SOURCE and FULLSAMPLE describe normal pixels; all other bits are masked.
BENIGN_FLAGS = (1 << 21) | (1 << 12)
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
    sampled = map_coordinates(np.where(valid, pixels, 0), locations, order=1, mode="constant", cval=0, prefilter=False)
    weight = map_coordinates(valid.astype(float), locations, order=1, mode="constant", cval=0, prefilter=False)
    sampled[weight < 0.999] = np.nan
    return sampled


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
        valid = np.isfinite(pixels) & np.isfinite(variance) & (variance > 0) & ((flags & ~BENIGN_FLAGS) == 0)
        sampled = align_pixels(pixels, valid, spatial, target, shape)
        fraction = float(np.isfinite(sampled).mean())
        if fraction < 0.5:
            raise ValueError("Less than half of this patch has usable, unflagged image coverage")
        mjd = float(row["mjd"])
        date = Time(mjd, format="mjd", scale="utc").isot + "Z"
        metadata = {**row, "timestamp": date, "wavelength_um": wavelength, "bandwidth_um": bandwidth,
                    "valid_fraction": fraction, "processing_version": str(hdus[0].header.get("VERSION", "unknown")),
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
    low, high = np.percentile(values, [2, 99.7])
    if high <= low:
        high = low + 1
    directory.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(frames):
        pixels = frame.pixels
        scaled = np.clip((np.nan_to_num(pixels, nan=low) - low) / (high - low), 0, 1)
        scaled = np.arcsinh(8 * scaled) / np.arcsinh(8)
        rgb = np.repeat((scaled[..., None] * 255).astype(np.uint8), 3, axis=2)
        # Invalid/flagged pixels have an explicit colored mask, distinct from dark sky.
        rgb[~np.isfinite(pixels)] = [28, 43, 58]
        output = io.BytesIO()
        Image.fromarray(np.flipud(rgb)).save(output, format="PNG")
        payload = output.getvalue()
        checksum = hashlib.sha256(payload).hexdigest()
        filename = f"{index:03d}-{checksum[:12]}.png"
        (directory / filename).write_bytes(payload)
        frame.metadata["image_file"] = filename
        frame.metadata["image_sha256"] = checksum
    return {"stretch": "asinh", "low": float(low), "high": float(high), "units": "MJy/sr",
            "shared_across_frames": True, "north_up": True, "east_left": True,
            "masked_pixel_color": "#1c2b3a"}
