"""SIMBAD candidate identification near a selected sky position; JSON disk cache."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import threading
from urllib.parse import urlencode, urlparse
import warnings

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time
from pydantic import BaseModel, ConfigDict, Field, field_validator
import requests

from backend.archive import atomic_json

# The official Harvard mirror is reachable while the Strasbourg service can
# be unavailable. Both serve SIMBAD; never substitute a different catalog.
ENDPOINTS = ("https://simbad.cfa.harvard.edu/simbad/sim-tap/sync",
             "https://simbad.cds.unistra.fr/simbad/sim-tap/sync")
VERSION = 1
RADIUS_ARCSEC = 30
DISCOVERY_DEG = 1 / 6  # Include high-proper-motion stars displaced since J2000.
MAX_ROWS = 500
MATCH_EPOCH = 2026.0
CATALOG_EPOCH = Time(2000.0, format="jyear")
IMAGE_EPOCH = Time(MATCH_EPOCH, format="jyear")
LOGGER = logging.getLogger(__name__)
SLOTS = threading.BoundedSemaphore(3)


class CatalogUnavailable(RuntimeError):
    pass


class CatalogNotSaved(RuntimeError):
    pass


class Candidate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    name: str = Field(min_length=1, max_length=500)
    object_type: str
    type_code: str
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    separation_arcsec: float = Field(ge=0, le=RADIUS_ARCSEC)
    match_basis: str
    matched_ra: float = Field(ge=0, lt=360)
    matched_dec: float = Field(ge=-90, le=90)
    spectral_type: str | None
    v_magnitude: float | None
    parallax_mas: float | None
    pm_ra_mas_yr: float | None
    pm_dec_mas_yr: float | None
    source_url: str

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        url = urlparse(value)
        if url.scheme != "https" or url.hostname not in {"simbad.cfa.harvard.edu", "simbad.cds.unistra.fr"} or url.path != "/simbad/sim-id":
            raise ValueError("Unexpected catalog source link")
        return value


class CatalogResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    version: int
    ra: float
    dec: float
    radius_arcsec: int
    retrieved_at: str
    source: str
    endpoint: str
    candidates: list[Candidate]
    total_matches: int
    search_limited: bool
    match_epoch: float


def cache_path(root: Path, ra: float, dec: float) -> Path:
    identity = hashlib.sha256(json.dumps([VERSION, ra, dec, RADIUS_ARCSEC, MATCH_EPOCH]).encode()).hexdigest()[:24]
    return root / "objects" / f"{identity}.json"


def number(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def candidate(row: dict, click: SkyCoord) -> Candidate | None:
    ra, dec = number(row.get("ra")), number(row.get("dec"))
    if ra is None or dec is None or not 0 <= ra < 360 or not -90 <= dec <= 90 or not row.get("main_id"):
        raise CatalogUnavailable("SIMBAD returned invalid object coordinates or identifiers")
    source = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
    nearest = source
    distance = float(click.separation(source).arcsec)
    basis = "Catalog position (J2000.0)"
    pmra, pmdec = number(row.get("pmra")), number(row.get("pmdec"))
    if pmra is not None and pmdec is not None:
        # Navigation approximation only: no observer/parallax correction or
        # radial-velocity perspective acceleration. Keep the original J2000
        # alternative because the mosaic/brief does not have one exact epoch.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            moving = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, pm_ra_cosdec=pmra * u.mas / u.yr,
                              pm_dec=pmdec * u.mas / u.yr, obstime=CATALOG_EPOCH).apply_space_motion(new_obstime=IMAGE_EPOCH)
        propagated_distance = float(click.separation(moving).arcsec)
        if propagated_distance < distance:
            nearest, distance = moving, propagated_distance
            basis = "Approximate proper-motion position (2026.0)"
    if distance > RADIUS_ARCSEC:
        return None
    name = str(row["main_id"]).strip()
    return Candidate(name=name, object_type=str(row.get("object_type") or row.get("otype_txt") or "Unspecified"),
                     type_code=str(row.get("otype_txt") or ""), ra=ra, dec=dec,
                     separation_arcsec=distance, match_basis=basis, matched_ra=float(nearest.ra.deg), matched_dec=float(nearest.dec.deg),
                     spectral_type=str(row["sp_type"]).strip() if row.get("sp_type") else None,
                     v_magnitude=number(row.get("vmag")), parallax_mas=number(row.get("plx_value")),
                     pm_ra_mas_yr=pmra, pm_dec_mas_yr=pmdec,
                     source_url="https://simbad.cfa.harvard.edu/simbad/sim-id?" + urlencode({"Ident": name}))


def query_catalog(ra: float, dec: float) -> tuple[list[dict], str]:
    query = f"""
        SELECT TOP {MAX_ROWS + 1} b.main_id, b.ra, b.dec, b.pmra, b.pmdec,
          b.plx_value, b.sp_type, b.otype_txt, t.description AS object_type,
          f.V AS vmag,
          DISTANCE(POINT('ICRS', b.ra, b.dec), POINT('ICRS', {ra}, {dec})) AS separation
        FROM basic AS b
        LEFT JOIN allfluxes AS f ON f.oidref = b.oid
        LEFT JOIN otypedef AS t ON t.otype = b.otype
        WHERE 1 = CONTAINS(POINT('ICRS', b.ra, b.dec), CIRCLE('ICRS', {ra}, {dec}, {DISCOVERY_DEG}))
        ORDER BY separation
    """
    errors = []
    for endpoint in ENDPOINTS:
        try:
            with requests.get(endpoint, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json",
                                               "MAXREC": MAX_ROWS + 2, "QUERY": query},
                              timeout=(5, 15), stream=True) as response:
                response.raise_for_status()
                payload = bytearray()
                for chunk in response.iter_content(65536):
                    payload.extend(chunk)
                    if len(payload) > 2 * 1024 * 1024:
                        raise CatalogUnavailable("SIMBAD response exceeded the size limit")
            body = json.loads(payload)
            fields = [field["name"].lower() for field in body["metadata"]]
            if not {"main_id", "ra", "dec", "object_type", "otype_txt"}.issubset(fields) or not isinstance(body["data"], list):
                raise ValueError("Missing SIMBAD result columns")
            if len(body["data"]) > MAX_ROWS + 1 or any(not isinstance(row, list) or len(row) != len(fields) for row in body["data"]):
                raise ValueError("Invalid SIMBAD result rows")
            return [dict(zip(fields, row)) for row in body["data"]], endpoint
        except (requests.RequestException, ValueError, KeyError, TypeError, CatalogUnavailable) as error:
            errors.append(f"{endpoint}: {error}")
    LOGGER.warning("SIMBAD lookup failed: %s", "; ".join(errors))
    raise CatalogUnavailable("The SIMBAD object catalog could not be reached or returned an invalid response. Try again shortly.")


def lookup(ra: float, dec: float, root: Path, *, offline: bool = False) -> dict:
    if not math.isfinite(ra) or not math.isfinite(dec) or not -90 <= dec <= 90:
        raise ValueError("Invalid object lookup coordinates")
    ra, dec = round(ra % 360, 6) % 360, round(dec, 6)
    path = cache_path(root, ra, dec)
    try:
        saved = CatalogResult.model_validate_json(path.read_text(encoding="utf-8"))
        if saved.version == VERSION and saved.ra == ra and saved.dec == dec and saved.radius_arcsec == RADIUS_ARCSEC and saved.match_epoch == MATCH_EPOCH:
            return {**saved.model_dump(), "cached": True, "cache_warning": None}
    except (OSError, ValueError):
        pass
    if offline:
        raise CatalogNotSaved("Object information for this coordinate is not saved locally. Connect to look it up first.")
    if not SLOTS.acquire(blocking=False):
        raise CatalogUnavailable("Object lookup is busy. Please retry shortly.")
    try:
        rows, endpoint = query_catalog(ra, dec)
        click = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
        try:
            matches = [match for row in rows if (match := candidate(row, click)) is not None]
        except ValueError as error:
            raise CatalogUnavailable("SIMBAD returned invalid object information. Please retry.") from error
        # Co-located companion entries are not distinct resolved detections.
        matches.sort(key=lambda item: (item.separation_arcsec, item.type_code == "Pl", item.name))
        result = CatalogResult(version=VERSION, ra=ra, dec=dec, radius_arcsec=RADIUS_ARCSEC,
                               retrieved_at=datetime.now(timezone.utc).isoformat(), source="SIMBAD (CDS)", endpoint=endpoint,
                               candidates=matches[:8], total_matches=len(matches), search_limited=len(rows) > MAX_ROWS,
                               match_epoch=MATCH_EPOCH).model_dump()
        warning = None
        try:
            atomic_json(path, result)
        except OSError:
            LOGGER.exception("Object information retrieved but could not be cached")
            warning = "Object information loaded, but could not be saved on this computer."
        return {**result, "cached": False, "cache_warning": warning}
    finally:
        SLOTS.release()


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Look up and cache nearby SIMBAD objects without starting the UI.")
    parser.add_argument("--ra", type=float, required=True, help="ICRS right ascension in decimal degrees")
    parser.add_argument("--dec", type=float, required=True, help="ICRS declination in decimal degrees")
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ.get("SPHEREX_CACHE_DIR", Path(__file__).resolve().parent.parent / "data/cache"))
    try:
        result = lookup(args.ra, args.dec, root, offline=args.cache_only or os.environ.get("SPHEREX_OFFLINE", "").lower() in {"1", "true", "yes"})
        print(json.dumps(result, indent=2, ensure_ascii=True))
    except (CatalogUnavailable, CatalogNotSaved, ValueError, OSError) as error:
        parser.exit(1, f"Object lookup failed: {error}\n")
