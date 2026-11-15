"""Persistent JSON manifests and bounded preparation of real sky sequences."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from astropy.wcs import WCS

from backend import archive, processing

LOGGER = logging.getLogger(__name__)
PIPELINE_VERSION = 1


class SeriesRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    ra: float
    dec: float = Field(ge=-90, le=90)
    size_deg: float = Field(default=0.1, ge=0.02, le=0.2)
    detector: Literal["D1", "D2", "D3", "D4", "D5", "D6"] = "D2"

    @field_validator("ra")
    @classmethod
    def wrap_ra(cls, value: float) -> float:
        return round(value % 360, 6) % 360

    @field_validator("dec", "size_deg")
    @classmethod
    def rounded(cls, value: float) -> float:
        return round(value, 6)


def series_id(request: SeriesRequest) -> str:
    return hashlib.sha256(json.dumps({**request.model_dump(), "pipeline_version": PIPELINE_VERSION}, sort_keys=True).encode()).hexdigest()[:24]


def valid_id(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{24}", value) is not None


def read_saved(root: Path, identity: str) -> dict | None:
    if not valid_id(identity):
        return None
    directory = root / "series" / identity
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("id") != identity or manifest.get("pipeline_version") != PIPELINE_VERSION:
            return None
        required = {"query", "created_at", "frame_count", "frames", "date_start", "date_end", "wavelength_min_um", "wavelength_max_um"}
        if not required.issubset(manifest) or not isinstance(manifest["frames"], list) or manifest["frame_count"] != len(manifest["frames"]):
            return None
        SeriesRequest.model_validate(manifest["query"])
        for frame in manifest["frames"]:
            filename = frame["image_file"]
            image = directory / "frames" / filename
            if not re.fullmatch(r"\d{3}-[0-9a-f]{12}\.png", filename) or not image.is_file():
                return None
            if hashlib.sha256(image.read_bytes()).hexdigest() != frame["image_sha256"]:
                return None
        return manifest
    except (OSError, ValueError, KeyError, TypeError):
        return None


def list_saved(root: Path) -> list[dict]:
    sequences = []
    for path in (root / "series").glob("*/manifest.json"):
        saved = read_saved(root, path.parent.name)
        if saved and saved["frames"]:
            sequences.append({key: saved[key] for key in ["id", "query", "created_at", "frame_count", "date_start", "date_end", "wavelength_min_um", "wavelength_max_um"]})
    return sorted(sequences, key=lambda s: s["created_at"], reverse=True)


def prepare(request: SeriesRequest, root: Path, progress: Callable[[str, int, int], None] | None = None,
            *, observations: list[dict] | None = None, local_only: bool = False, refresh: bool = False,
            interactive: bool = False) -> dict:
    identity = series_id(request)
    saved = read_saved(root, identity)
    if saved and not refresh:
        if saved.get("render_version", 1) < processing.RENDER_VERSION:
            return rerender_saved(root, identity)
        return saved
    directory = root / "series" / identity
    deadline = time.monotonic() + 5 * 60 if interactive else float("inf")
    update = progress or (lambda message, completed, total: None)
    update("Searching NASA IRSA for dated observations…", 0, 0)
    if local_only and observations is None:
        raise ValueError("Local-only preparation requires a supplied observation catalog")
    discovery = archive.search(request.ra, request.dec, request.detector, progress=lambda message: update(message, 0, 0)) if observations is None else None
    rows = discovery.rows if discovery is not None else observations
    assert rows is not None
    # Deduplicate processing versions of the same exposure, retaining DR1 first.
    unique: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: (r["release"] == "DR1", r["uri"])):
        exposure = Path(row["uri"]).name.split("_spx_")[0]
        unique[exposure] = row
    rows = sorted(unique.values(), key=lambda r: (r["mjd"], r["uri"]))
    archive.atomic_json(directory / "query.json", {"query": request.model_dump(), "queried_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": discovery.endpoint if discovery else "supplied observation catalog", "observations": rows})
    candidates = processing.evenly_sample(rows, processing.MAX_CANDIDATES)
    target, shape = processing.output_grid(request.ra, request.dec, request.size_deg)
    frames: list[processing.Frame] = []
    rejected: list[dict] = []

    def process(row: dict) -> processing.Frame:
        if time.monotonic() >= deadline:
            raise TimeoutError("Preparation time limit reached; retry to reuse cutouts already saved locally")
        url = archive.cutout_url(row["uri"], request.ra, request.dec, request.size_deg)
        if local_only:
            path = root / "cutouts" / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.fits"
            if not path.is_file():
                raise FileNotFoundError(f"Cutout is not saved locally: {row['uri']}")
        else:
            path = archive.download_cutout(url, root, interactive=True) if interactive else archive.download_cutout(url, root)
        return processing.read_frame(path, {**row, "source_url": url}, request.ra, request.dec, target, shape)

    update("Downloading and checking dated FITS cutouts…", 0, len(candidates))
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(process, row): row for row in candidates}
        for completed, future in enumerate(as_completed(tasks), 1):
            row = tasks[future]
            try:
                frames.append(future.result())
            except Exception as error:
                LOGGER.warning("Cutout excluded: %s: %s", row["uri"], error)
                rejected.append({"uri": row["uri"], "reason": str(error)})
            update("Downloading and checking dated FITS cutouts…", completed, len(candidates))
    if time.monotonic() >= deadline:
        raise TimeoutError("IRSA preparation exceeded five minutes. Retry to reuse downloaded cutouts, or open a saved object.")
    # Do not publish an empty permanent cache for an upstream outage or a failed
    # processing implementation: the next online request must be able to retry.
    if candidates and not frames:
        detail = rejected[0]["reason"] if rejected else "No usable cutouts"
        raise RuntimeError(f"No usable cutouts could be prepared. {detail}")
    selected = processing.compatible_frames(frames)
    update("Aligning the sequence and saving previews locally…", len(candidates), len(candidates))
    display = processing.save_previews(selected, directory / "frames")
    for frame in selected:
        frame.metadata["image_url"] = f"/api/series/{identity}/frames/{frame.metadata['image_file']}"
    dates = [frame.metadata["timestamp"] for frame in selected]
    waves = [frame.metadata["wavelength_um"] for frame in selected]
    notes = ["Wavelengths are matched at the selected coordinate (total spread ≤2%); they can vary across each patch.",
             processing.RENDER_NOTE,
             "Fixed sky coordinates and one shared brightness stretch. Displayed values can include saturation and other quality-flagged artifacts.",
             "Visual exploration only: PSF, background, and residual calibration differences can mimic changes."]
    if discovery and discovery.source == "NASA IRSA SIA":
        notes.append("Observation discovery used IRSA SIA; newly ingested data can lag TAP by approximately one day.")
    if len(rows) >= 1000:
        notes.append("The archive search is capped at 1,000 rows; this sequence is a bounded sample, not a complete archive search.")
    if len(candidates) < len(rows):
        notes.append(f"Sampled {len(candidates)} of {len(rows)} matching exposures across the available date range.")
    if rejected:
        notes.append(f"{len(rejected)} cutouts were excluded because of download, metadata, or image-quality problems.")
    if len(selected) < 2:
        notes.append("Fewer than two compatible frames are available. Try another detector or position; chronological playback is unavailable.")
    manifest = {"id": identity, "pipeline_version": PIPELINE_VERSION, "render_version": processing.RENDER_VERSION, "state": "ready" if selected else "empty",
                "query": request.model_dump(), "created_at": datetime.now(timezone.utc).isoformat(),
                "source": "NASA IRSA SPHEREx DR1 / QR3 individual spectral images", "frame_count": len(selected),
                "discovery_source": discovery.source if discovery else "supplied observation catalog",
                "discovery_endpoint": discovery.endpoint if discovery else None,
                "discovery_limited": discovery.limited if discovery else len(rows) >= 1000,
                "date_start": dates[0] if dates else None, "date_end": dates[-1] if dates else None,
                "wavelength_min_um": min(waves) if waves else None, "wavelength_max_um": max(waves) if waves else None,
                "matched_exposures": len(rows), "sampled_exposures": len(candidates), "usable_cutouts": len(frames),
                "frames": [frame.metadata for frame in selected], "excluded": rejected, "notes": notes, "display": display,
                "wcs": dict(target.to_header()), "image_shape": list(shape),
                "timestamp_definition": "UTC, from IRSA time_bounds_lower (MJD exposure start)",
                "mask_policy": processing.MASK_POLICY}
    # This file is the commit point: it is published only after all PNGs exist.
    archive.atomic_json(directory / "manifest.json", manifest)
    return manifest


def rerender_saved(root: Path, identity: str) -> dict:
    """Rebuild the SAME dated frames using local originals. Never fetch/reselect.

    New content-addressed PNGs and a backup are published before the manifest;
    old files remain available if a local original or a write fails.
    """
    if not valid_id(identity):
        raise ValueError("Invalid sequence ID")
    directory = root / "series" / identity
    path = directory / "manifest.json"
    original_text = path.read_text(encoding="utf-8")
    original = json.loads(original_text)
    if original.get("id") != identity or original.get("pipeline_version") != PIPELINE_VERSION:
        raise ValueError("Invalid saved sequence")
    request = SeriesRequest.model_validate(original["query"])
    if series_id(request) != identity:
        raise ValueError("Sequence ID does not match its coordinates and detector")
    shape = tuple(original["image_shape"])
    if len(shape) != 2 or not all(isinstance(size, int) and 1 <= size <= 4096 for size in shape):
        raise ValueError("Invalid image dimensions")
    target = WCS(original["wcs"])
    frames = []
    for row in original["frames"]:
        filename = row["fits_file"]
        if not re.fullmatch(r"[0-9a-f]{24}\.fits", filename):
            raise ValueError("Unexpected original FITS filename")
        fits_path = root / "cutouts" / filename
        if not fits_path.is_file():
            raise FileNotFoundError(f"Original FITS is not saved locally: {filename}")
        frames.append(processing.read_frame(fits_path, row, request.ra, request.dec, target, shape))
    display = processing.save_previews(frames, directory / "frames")
    for frame in frames:
        frame.metadata["image_url"] = f"/api/series/{identity}/frames/{frame.metadata['image_file']}"
    notes = [note for note in original.get("notes", []) if "Teal pixels" not in note and note != processing.RENDER_NOTE]
    notes.append(processing.RENDER_NOTE)
    updated = {**original, "render_version": processing.RENDER_VERSION, "frames": [frame.metadata for frame in frames],
               "display": display, "mask_policy": processing.MASK_POLICY, "notes": notes,
               "rerendered_at": datetime.now(timezone.utc).isoformat()}
    if path.read_text(encoding="utf-8") != original_text:
        raise RuntimeError("Sequence changed during rendering; retry after the other preparation finishes")
    backup = directory / f"manifest.render-{original.get('render_version', 1)}.json"
    if not backup.exists():
        archive.atomic_json(backup, original)
    archive.atomic_json(path, updated)
    return updated
