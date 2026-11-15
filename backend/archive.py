"""IRSA TAP discovery and original FITS cutouts, cached on disk."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import time
from urllib.parse import urlencode, urlparse
import uuid
import xml.etree.ElementTree as ET

from astropy.io.votable import parse_single_table
import requests

IRSA = "https://irsa.ipac.caltech.edu"
TAP = f"{IRSA}/TAP/sync"
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def replace_with_retry(source: Path, destination: Path, attempts: int = 6) -> None:
    """Allow short Windows/OneDrive/antivirus locks to release, then fail clearly.

    The destination remains intact until replacement succeeds. Do not retry
    unrelated errors such as a missing source or an exhausted disk.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            locked = isinstance(error, PermissionError) or getattr(error, "winerror", None) in {5, 32, 33}
            if not locked or attempt + 1 == attempts:
                raise
            time.sleep(min(0.1 * 2 ** attempt, 1.6))


def atomic_json(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
        replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("Temporary file remains locked: %s", temporary)


def fetch(url: str, *, params: dict | None = None) -> requests.Response:
    """Finite retries; TLS verification remains enabled."""
    last_error: Exception | None = None
    for attempt in range(3):
        response = None
        try:
            response = requests.get(url, params=params, timeout=(15, 120), stream=True)
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            if response is not None:
                response.close()
            last_error = error
            if isinstance(error, requests.HTTPError) and error.response is not None and error.response.status_code < 500 and error.response.status_code != 429:
                break
            if attempt < 2:
                time.sleep(1 + attempt)
    raise RuntimeError(f"IRSA request failed: {last_error}") from last_error


def search(ra: float, dec: float, detector: str) -> list[dict]:
    # User input is validated before interpolation. No arbitrary ADQL is accepted.
    if detector not in {f"D{i}" for i in range(1, 7)}:
        raise ValueError("Unknown SPHEREx detector")
    query = f"""
        SELECT TOP 1000 a.uri, p.time_bounds_lower, p.energy_bandpassname
        FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid
        WHERE 1 = CONTAINS(POINT('ICRS', {float(ra)}, {float(dec)}), p.poly)
          AND p.energy_bandpassname = 'SPHEREx-{detector}'
        ORDER BY p.time_bounds_lower
    """
    with fetch(TAP, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "QUERY": query}) as response:
        payload = response.content
    # Parsing a VOTable also detects TAP QUERY_STATUS errors.
    table = parse_single_table(io.BytesIO(payload)).to_table(use_names_over_ids=True)
    rows: list[dict] = []
    seen: set[str] = set()
    for row in table:
        uri = str(row["uri"])
        if not uri.startswith(("ibe/data/spherex/dr1/level2/", "ibe/data/spherex/qr3/level2/")) or not uri.endswith(".fits"):
            continue
        if uri in seen:
            continue
        seen.add(uri)
        rows.append({"uri": uri, "mjd": float(row["time_bounds_lower"]), "detector": detector,
                     "release": "DR1" if "/dr1/" in uri else "QR3"})
    return sorted(rows, key=lambda row: (row["mjd"], row["uri"]))


def search_page(ra: float, dec: float, detector: str, *, after: str = "", page_size: int = 500) -> tuple[list[dict], str, bool]:
    """Enumerate products by URI, without the interactive search's 1,000-row cap.

    A strict URI cursor avoids OFFSET and duplicate boundary rows. Refuse TAP
    overflow/error responses so a truncated catalog is never called complete.
    """
    import math

    if detector not in {f"D{i}" for i in range(1, 7)} or not 1 <= page_size <= 1000:
        raise ValueError("Invalid detector or page size")
    if not math.isfinite(ra) or not math.isfinite(dec) or not -90 <= dec <= 90:
        raise ValueError("Invalid coordinates")
    cursor = after.replace("'", "''")
    query = f"""
        SELECT TOP {page_size} a.uri, p.time_bounds_lower, p.energy_bandpassname
        FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid
        WHERE 1 = CONTAINS(POINT('ICRS', {float(ra)}, {float(dec)}), p.poly)
          AND p.energy_bandpassname = 'SPHEREx-{detector}'
          AND (a.uri LIKE 'ibe/data/spherex/dr1/level2/%.fits'
               OR a.uri LIKE 'ibe/data/spherex/qr3/level2/%.fits')
          AND a.uri > '{cursor}'
        ORDER BY a.uri
    """
    # Keep the protocol limit above the explicit TOP limit: IRSA otherwise
    # reports OVERFLOW whenever a full page exactly reaches MAXREC.
    with fetch(TAP, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "votable", "MAXREC": page_size + 1, "QUERY": query}) as response:
        payload = response.content
    for info in ET.fromstring(payload).iter():
        if info.tag.rsplit("}", 1)[-1] == "INFO" and info.get("name", "").upper() == "QUERY_STATUS":
            if info.get("value", "").upper() in {"ERROR", "OVERFLOW"}:
                raise RuntimeError(f"Incomplete TAP response: {info.get('value')}: {info.text or ''}")
    table = parse_single_table(io.BytesIO(payload)).to_table(use_names_over_ids=True)
    rows = []
    for row in table:
        uri, mjd = str(row["uri"]), float(row["time_bounds_lower"])
        cutout_url(uri, ra, dec, 0.1)  # Validate every returned product path.
        if not uri.endswith(".fits") or uri == after or not math.isfinite(mjd):
            raise RuntimeError("Invalid or non-advancing TAP page")
        rows.append({"uri": uri, "mjd": mjd, "detector": detector,
                     "release": "DR1" if "/dr1/" in uri else "QR3"})
    uris = [row["uri"] for row in rows]
    # IRSA's database collation orders punctuation differently from Python.
    # Keep its returned order and last URI; ORDER BY and > use that same
    # database collation. Python's max()/sorted() would skip some products.
    if len(uris) != len(set(uris)) or len(uris) > page_size:
        raise RuntimeError("TAP page contains duplicate products or exceeds the page size")
    return rows, uris[-1] if uris else after, len(table) < page_size


def cutout_url(uri: str, ra: float, dec: float, size: float) -> str:
    url = f"{IRSA}/{uri}"
    parsed = urlparse(url)
    if parsed.hostname != "irsa.ipac.caltech.edu" or not parsed.path.startswith(("/ibe/data/spherex/dr1/level2/", "/ibe/data/spherex/qr3/level2/")) or ".." in parsed.path:
        raise ValueError("Unexpected IRSA product URL")
    return f"{url}?{urlencode({'center': f'{ra},{dec}d', 'size': size})}"


def download_cutout(url: str, cache_root: Path) -> Path:
    identity = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = cache_root / "cutouts" / f"{identity}.fits"
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{identity}.{uuid.uuid4().hex}.part")
    try:
        with fetch(url) as response, temporary.open("wb") as output:
            total = 0
            for chunk in response.iter_content(128 * 1024):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise ValueError("IRSA cutout exceeds the 64 MB download limit")
                output.write(chunk)
        # Verify FITS framing before publishing the cached original.
        with temporary.open("rb") as check:
            if not check.read(80).startswith(b"SIMPLE  ="):
                raise ValueError("IRSA returned a non-FITS response")
        replace_with_retry(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
