"""Small observation-metadata lookups. Never request FITS files or prepare images."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import threading
from typing import Literal

from astropy.time import Time
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from backend.archive import atomic_json
from backend.discovery import DiscoveryUnavailable, discover
from backend.series import PIPELINE_VERSION, SeriesRequest, valid_id

LIMIT = 24
SLOTS = threading.BoundedSemaphore(3)


class MetadataUnavailable(RuntimeError):
    pass


class Observation(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    observation_id: str = Field(min_length=1, max_length=200)
    detector: Literal["D1", "D2", "D3", "D4", "D5", "D6"]
    release: Literal["DR1", "QR3"]
    timestamp: AwareDatetime
    wavelength_um: float | None = Field(default=None, gt=0)
    bandwidth_um: float | None = Field(default=None, gt=0)
    valid_fraction: float | None = Field(default=None, ge=0, le=1)
    available_fraction: float | None = Field(default=None, ge=0, le=1)
    center_ra: float | None = Field(default=None, ge=0, lt=360)
    center_dec: float | None = Field(default=None, ge=-90, le=90)
    size_deg: float | None = Field(default=None, gt=0)


class Metadata(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    version: Literal[1] = 1
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    source: Literal["saved_cutouts", "NASA IRSA SIA", "NASA IRSA TAP"]
    retrieved_at: datetime
    observations: list[Observation] = Field(max_length=LIMIT)
    limited: bool = False


def separation(ra: float, dec: float, other_ra: float, other_dec: float) -> float:
    a, b = math.radians(dec), math.radians(other_dec)
    value = math.sin((a - b) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(ra - other_ra) / 2) ** 2
    return math.degrees(2 * math.asin(math.sqrt(min(1, max(0, value)))))


def saved_observations(root: Path, ra: float, dec: float) -> tuple[list[Observation], bool]:
    # Read JSON only. No PNG checksums, FITS I/O, or image preparations.
    matches = []
    for path in (root / "series").glob("*/manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not valid_id(path.parent.name) or data["id"] != path.parent.name or data["pipeline_version"] != PIPELINE_VERSION:
                continue
            query = SeriesRequest.model_validate(data["query"])
            offset = separation(ra, dec, query.ra, query.dec)
            # A conservative inscribed circle stays inside the square cutout.
            if offset > query.size_deg / 2:
                continue
            for frame in data["frames"]:
                try:
                    item = Observation.model_validate({**frame, "detector": query.detector,
                        "center_ra": query.ra, "center_dec": query.dec, "size_deg": query.size_deg})
                    matches.append((offset, query.size_deg, item))
                except (ValueError, TypeError):
                    continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
    # Prefer the closest field and avoid repeating an exposure saved at two sizes.
    unique = {}
    for _, _, item in sorted(matches, key=lambda match: (match[0], match[1])):
        unique.setdefault((item.observation_id, item.release), item)
    items = sorted(unique.values(), key=lambda item: item.timestamp, reverse=True)
    return items[:LIMIT], len(items) > LIMIT


def query_archive(ra: float, dec: float) -> tuple[list[Observation], bool, str]:
    if not SLOTS.acquire(blocking=False):
        raise MetadataUnavailable("Observation metadata service is busy. Please retry.")
    try:
        result = discover(ra, dec, metadata_only=True)
        records = []
        for row in reversed(result.rows):
            uri = str(row["uri"])
            records.append(Observation(observation_id=Path(uri).name.split("_spx_")[0],
                detector=row["detector"], release=row["release"],
                timestamp=Time(row["mjd"], format="mjd", scale="utc").to_datetime(timezone=timezone.utc)))
        unique = {(item.observation_id, item.release): item for item in records}
        return list(unique.values())[:LIMIT], result.limited or len(unique) > LIMIT, result.source
    except (DiscoveryUnavailable, ValueError, KeyError, TypeError) as error:
        raise MetadataUnavailable(f"IRSA observation metadata is unavailable: {error}. No images were downloaded.") from error
    finally:
        SLOTS.release()


def lookup(ra: float, dec: float, root: Path, *, offline: bool = False) -> dict:
    query = SeriesRequest(ra=ra, dec=dec)
    ra, dec = query.ra, query.dec
    observations, limited = saved_observations(root, ra, dec)
    now = datetime.now(timezone.utc)
    if observations:
        return {**Metadata(ra=ra, dec=dec, source="saved_cutouts", retrieved_at=now,
                          observations=observations, limited=limited).model_dump(mode="json"), "cached": True, "offline": offline}
    identity = hashlib.sha256(json.dumps([1, ra, dec]).encode()).hexdigest()[:24]
    path = root / "observation-metadata" / f"{identity}.json"
    try:
        cached = Metadata.model_validate_json(path.read_text(encoding="utf-8"))
        if cached.ra == ra and cached.dec == dec:
            return {**cached.model_dump(mode="json"), "cached": True, "offline": offline}
    except (OSError, ValueError):
        pass
    if offline:
        return {**Metadata(ra=ra, dec=dec, source="NASA IRSA SIA", retrieved_at=now, observations=[]).model_dump(mode="json"),
                "cached": False, "offline": True, "message": "No observation metadata saved at this position. Reconnect to look it up."}
    observations, limited, source = query_archive(ra, dec)
    result = Metadata(ra=ra, dec=dec, source=source, retrieved_at=now, observations=observations, limited=limited).model_dump(mode="json")
    warning = None
    try:
        atomic_json(path, result)
    except OSError:
        warning = "Metadata could not be saved locally."
        logging.getLogger(__name__).warning("Could not save observation metadata: %s", path)
    return {**result, "cached": False, "offline": False, "cache_warning": warning}
