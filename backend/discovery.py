"""Bounded, metadata-only SPHEREx discovery: SIA first, TAP as a fallback."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import io
import logging
import math
import re
import time
from typing import Callable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from astropy.io.votable import parse_single_table
from astropy.table import Table
import requests

from backend.archive import IRSA, TAP

SIA = f"{IRSA}/SIA"
COLLECTIONS = ("spherex_dr1", "spherex_qr3", "spherex_dr1_deep", "spherex_qr3_deep")
MAX_RECORDS = 1000
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
LOGGER = logging.getLogger(__name__)


class DiscoveryUnavailable(RuntimeError):
    pass


@dataclass
class DiscoveryResult:
    rows: list[dict]
    source: str
    endpoint: str
    limited: bool = False


def retry_delay(header: str | None, attempt: int) -> float:
    """Respect both numeric and HTTP-date Retry-After headers."""
    if header:
        try:
            return max(0., float(header))
        except ValueError:
            try:
                return max(0., (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return float(2 ** attempt)


def _table(endpoint: str, params: dict | list, deadline: float, *, metadata_only: bool,
           progress: Callable[[str], None]) -> Table:
    label = "SIA" if endpoint == SIA else "TAP"
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= .1:
            raise DiscoveryUnavailable(f"IRSA {label} discovery time limit reached")
        # Connection + read timeouts fit the remaining budget. A read timeout
        # applies to inactivity; also check elapsed time while streaming.
        timeout = (min(3., remaining / 3), min(8. if metadata_only else 25., remaining * 2 / 3))
        retry_after = None
        try:
            with requests.get(endpoint, params=params, timeout=timeout, stream=True) as response:
                retry_after = response.headers.get("Retry-After")
                response.raise_for_status()
                payload = bytearray()
                for chunk in response.iter_content(16384):
                    if time.monotonic() >= deadline:
                        raise DiscoveryUnavailable(f"IRSA {label} discovery time limit reached")
                    payload.extend(chunk)
                    if len(payload) > MAX_RESPONSE_BYTES:
                        raise DiscoveryUnavailable(f"IRSA {label} metadata exceeds the 4 MB limit")
            for info in ET.fromstring(payload).iter():
                if info.tag.rsplit("}", 1)[-1] == "INFO" and info.get("name", "").upper() == "QUERY_STATUS":
                    state = info.get("value", "").upper()
                    if state in {"ERROR", "OVERFLOW"}:
                        raise DiscoveryUnavailable(f"IRSA {label} returned {state}; the metadata response is incomplete")
            return parse_single_table(io.BytesIO(payload)).to_table(use_names_over_ids=True)
        except requests.RequestException as error:
            status = error.response.status_code if error.response is not None else None
            message = f"IRSA {label} returned HTTP {status}" if status is not None else f"IRSA {label} connection failed or timed out"
            transient = status in {429, 500, 502, 503, 504} or isinstance(error, (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError))
            delay = retry_delay(retry_after, attempt)
            if not transient or attempt == 1 or delay + .1 >= deadline - time.monotonic():
                raise DiscoveryUnavailable(message) from error
            progress(f"{message}. Retrying {label} discovery…")
            time.sleep(delay)
        except (ValueError, TypeError, ET.ParseError) as error:
            raise DiscoveryUnavailable(f"IRSA {label} returned invalid observation metadata") from error
    raise DiscoveryUnavailable(f"IRSA {label} discovery failed")


def product_uri(value: str, *, absolute: bool) -> str:
    """Only allow direct on-premises DR1/QR3 spectral images, never arbitrary URLs."""
    if absolute:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != "irsa.ipac.caltech.edu" or parsed.query or parsed.fragment or parsed.params:
            raise DiscoveryUnavailable("IRSA returned an unexpected image access URL")
        value = parsed.path.removeprefix("/")
    if not re.fullmatch(r"ibe/data/spherex/(?:dr1|qr3)/level2/[A-Za-z0-9_./-]+\.fits", value) or any(part in {"", ".", ".."} for part in value.split("/")):
        raise DiscoveryUnavailable("IRSA returned an unexpected spectral image path")
    return value


def _rows(table: Table, *, sia: bool, detector: str | None) -> list[dict]:
    uri_column, time_column = ("access_url", "t_min") if sia else ("uri", "time_bounds_lower")
    if not {uri_column, time_column, "energy_bandpassname"}.issubset(table.colnames):
        raise DiscoveryUnavailable("IRSA response is missing required observation columns")
    unique = {}
    for row in table:
        band = str(row["energy_bandpassname"])
        if band not in {f"SPHEREx-D{i}" for i in range(1, 7)}:
            raise DiscoveryUnavailable("IRSA returned an unknown SPHEREx detector")
        if detector and band != f"SPHEREx-{detector}":
            continue
        uri = product_uri(str(row[uri_column]), absolute=sia)
        try:
            value = row[time_column]
            if getattr(value, 'mask', False):
                raise ValueError("Missing timestamp")
            mjd = float(value)
        except (ValueError, TypeError) as error:
            raise DiscoveryUnavailable("IRSA returned an invalid observation time") from error
        if not math.isfinite(mjd):
            raise DiscoveryUnavailable("IRSA returned an invalid observation time")
        unique[uri] = {"uri": uri, "mjd": mjd, "detector": band.removeprefix("SPHEREx-"),
                       "release": "DR1" if "/dr1/" in uri else "QR3"}
    return sorted(unique.values(), key=lambda row: (row["mjd"], row["uri"]))


def discover(ra: float, dec: float, detector: str | None = None, *, metadata_only: bool = False,
             progress: Callable[[str], None] | None = None) -> DiscoveryResult:
    if not math.isfinite(ra) or not math.isfinite(dec) or not -90 <= dec <= 90:
        raise ValueError("Invalid sky coordinates")
    if detector is not None and detector not in {f"D{i}" for i in range(1, 7)}:
        raise ValueError("Unknown SPHEREx detector")
    update = progress or (lambda message: None)
    # Reserve time for TAP fallback within the popup's 25-second client limit.
    deadline = time.monotonic() + (20 if metadata_only else 90)
    sia_deadline = min(deadline, time.monotonic() + (12 if metadata_only else 55))
    params = [("COLLECTION", value) for value in COLLECTIONS]
    params += [("POS", f"CIRCLE {ra % 360} {dec} 0"), ("MAXREC", str(MAX_RECORDS + 1))]
    update("Searching NASA IRSA SIA for dated observations…")
    try:
        table = _table(SIA, params, sia_deadline, metadata_only=metadata_only, progress=update)
        if len(table) > MAX_RECORDS:
            raise DiscoveryUnavailable("IRSA SIA result exceeds the bounded discovery limit")
        rows = _rows(table, sia=True, detector=detector)
        if rows:
            return DiscoveryResult(rows, "NASA IRSA SIA", SIA)
        sia_error = "SIA returned no matching observations; checking TAP for recently ingested data"
    except DiscoveryUnavailable as error:
        sia_error = str(error)
        LOGGER.warning("SIA discovery unavailable: %s", error)

    update(f"{sia_error}. Checking NASA IRSA TAP fallback…")
    band_filter = f"AND p.energy_bandpassname = 'SPHEREx-{detector}'" if detector else ""
    query = f"""SELECT TOP {MAX_RECORDS} a.uri, p.time_bounds_lower, p.energy_bandpassname
        FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid
        WHERE 1 = CONTAINS(POINT('ICRS', {float(ra % 360)}, {float(dec)}), p.poly)
          {band_filter}
          AND (a.uri LIKE 'ibe/data/spherex/dr1/level2/%.fits'
               OR a.uri LIKE 'ibe/data/spherex/qr3/level2/%.fits')
        ORDER BY p.time_bounds_lower {'DESC' if metadata_only else 'ASC'}"""
    try:
        table = _table(TAP, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "MAXREC": MAX_RECORDS + 1, "QUERY": query},
                       deadline, metadata_only=metadata_only, progress=update)
        return DiscoveryResult(_rows(table, sia=False, detector=detector), "NASA IRSA TAP", TAP, len(table) >= MAX_RECORDS)
    except DiscoveryUnavailable as error:
        raise DiscoveryUnavailable(f"Observation discovery unavailable: {sia_error}; {error}. Retry, or open a saved object.") from error
