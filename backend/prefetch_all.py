"""Resume bulk downloads for Start exploring targets across all six detectors.

Run from the repository root: .venv/Scripts/python.exe -m backend.prefetch_all
No UI or running backend server is required. See --help and README.md.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import re
import shutil
import threading
import unicodedata
import uuid
import warnings

from astropy.io import fits
import requests

from backend import archive
from backend.series import SeriesRequest, prepare, series_id

ROOT = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger("spherex.prefetch")
VERSION = 1


class InsufficientSpace(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_targets(path: Path = ROOT / "frontend/src/config/targets.ts") -> list[dict]:
    """Read the literal preset list, keeping Python and the UI in sync.

    Fail visibly if its format changes; never silently omit a target. No JS is
    evaluated. The current list uses single-quoted names and numeric coordinates.
    """
    source = path.read_text(encoding="utf-8")
    block = re.search(r"export const TARGETS\s*:\s*SkyTarget\[\]\s*=\s*\[(.*?)\n\];", source, re.S)
    if not block:
        raise ValueError("Cannot locate the Start exploring preset list")
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    pattern = rf"\{{\s*name:\s*'([^']+)'[^{{}}]*?\bra:\s*({number})\s*,\s*dec:\s*({number})"
    targets = []
    for name, ra, dec in re.findall(pattern, block[1]):
        plain = unicodedata.normalize("NFKD", name.replace("’", "").replace("'", "")).encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
        position = SeriesRequest(ra=float(ra), dec=float(dec))
        targets.append({"name": name, "slug": slug, "ra": position.ra, "dec": position.dec})
    if not targets or len(targets) != len(re.findall(r"\bname\s*:", block[1])) or len({t["slug"] for t in targets}) != len(targets):
        raise ValueError("Preset format changed; update load_targets before downloading")
    return targets


@contextmanager
def batch_lock(root: Path):
    """An OS lock releases on exit/crash; the small lock file can remain."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "prefetch.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another prefetch process is using this cache. Let it finish first.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value
    except (ValueError, UnicodeError) as error:
        quarantine = path.with_name(f"{path.name}.corrupt-{uuid.uuid4().hex[:8]}")
        archive.replace_with_retry(path, quarantine)
        LOGGER.warning("Preserved invalid checkpoint as %s: %s", quarantine, error)
        return None


def retry(operation, attempts: int, stop: threading.Event):
    for attempt in range(attempts):
        if stop.is_set():
            raise RuntimeError("Download interrupted")
        try:
            return operation()
        except InsufficientSpace:
            raise
        except Exception as error:
            # archive.fetch wraps HTTPError; do not repeatedly retry permanent
            # client errors such as 404. Stream failures are retried here too.
            cause = error
            while cause is not None:
                if isinstance(cause, requests.HTTPError) and cause.response is not None:
                    code = cause.response.status_code
                    if 400 <= code < 500 and code not in {408, 429}:
                        raise error
                cause = cause.__cause__
            if attempt + 1 == attempts:
                raise
            delay = min(30, 2 ** (attempt + 1)) + random.uniform(0, 1)
            LOGGER.warning("Attempt %s/%s failed; retrying in %.1fs: %s", attempt + 1, attempts, delay, error)
            if stop.wait(delay):
                raise RuntimeError("Download interrupted") from error


def discover(request: SeriesRequest, folder: Path, *, refresh: bool, page_size: int,
             attempts: int, stop: threading.Event) -> dict:
    path = folder / "catalog.json"
    catalog = None if refresh else read_json(path)
    if not catalog or catalog.get("version") != VERSION or catalog.get("query") != request.model_dump():
        catalog = {"version": VERSION, "query": request.model_dump(), "started_at": now(),
                   "endpoint": archive.TAP, "complete": False, "cursor": "", "observations": []}
    seen = {row["uri"] for row in catalog["observations"]}
    while not catalog["complete"]:
        rows, cursor, complete = retry(lambda: archive.search_page(
            request.ra, request.dec, request.detector, after=catalog["cursor"], page_size=page_size), attempts, stop)
        if any(row["uri"] in seen for row in rows):
            raise RuntimeError("TAP pagination repeated earlier products; catalog is incomplete")
        seen.update(row["uri"] for row in rows)
        catalog["observations"].extend(rows)
        catalog.update(cursor=cursor, complete=complete, updated_at=now())
        archive.atomic_json(path, catalog)
        LOGGER.info("  Catalog: %s products%s", len(catalog["observations"]), " (complete)" if complete else "")
    return catalog


def validate_fits(path: Path, expected_sha: str | None = None) -> dict:
    """Check structure/truncation and native checksums when present, then hash."""
    length = path.stat().st_size
    if length == 0 or length % 2880:
        raise ValueError("Truncated or incorrectly padded FITS file")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with fits.open(path, memmap=True, lazy_load_hdus=False) as hdus:
            hdus.verify("exception")
            for name in ("IMAGE", "FLAGS", "VARIANCE", "WCS-WAVE"):
                if name not in hdus:
                    raise ValueError(f"Missing required FITS extension: {name}")
            if hdus["IMAGE"].header.get("NAXIS") != 2 or min(hdus["IMAGE"].shape) <= 0:
                raise ValueError("FITS IMAGE is empty or not two dimensional")
            for hdu in hdus:
                info = hdu.fileinfo()
                if info and info["datLoc"] + info["datSpan"] > length:
                    raise ValueError("Truncated FITS data extension")
                if "CHECKSUM" in hdu.header and hdu.verify_checksum() != 1:
                    raise ValueError("FITS CHECKSUM mismatch")
                if "DATASUM" in hdu.header and hdu.verify_datasum() != 1:
                    raise ValueError("FITS DATASUM mismatch")
    with path.open("rb") as handle:
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()
    if expected_sha and checksum != expected_sha:
        raise ValueError("Cached FITS SHA-256 mismatch")
    return {"bytes": length, "sha256": checksum}


def fetch_product(row: dict, request: SeriesRequest, root: Path, previous: dict,
                  *, attempts: int, min_free_bytes: int, stop: threading.Event) -> dict:
    url = archive.cutout_url(row["uri"], request.ra, request.dec, request.size_deg)
    path = root / "cutouts" / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.fits"

    def validate_or_quarantine(expected: str | None = None) -> dict:
        try:
            return validate_fits(path, expected)
        except Exception:
            if path.exists():
                archive.replace_with_retry(path, path.with_name(f"{path.name}.corrupt-{uuid.uuid4().hex[:8]}"))
            raise

    if path.exists():
        try:
            valid = validate_or_quarantine(previous.get("sha256"))
            return {"status": "saved", "reused": True, "file": str(path.relative_to(root)), **valid}
        except (OSError, ValueError, Warning) as error:
            LOGGER.warning("  Replacing invalid cached cutout %s: %s", path.name, error)

    def download() -> dict:
        free = shutil.disk_usage(root).free
        if free < min_free_bytes + archive.MAX_DOWNLOAD_BYTES:
            raise InsufficientSpace("Not enough free disk space; free space or lower --min-free-gb, then rerun")
        archive.download_cutout(url, root)
        valid = validate_or_quarantine()
        return {"status": "saved", "reused": False, "file": str(path.relative_to(root)), **valid}

    return retry(download, attempts, stop)


def download_job(target: dict, request: SeriesRequest, root: Path, args, stop: threading.Event) -> dict:
    folder = root / "batch" / series_id(request)
    catalog = discover(request, folder, refresh=args.refresh_catalog, page_size=args.page_size,
                       attempts=args.attempts, stop=stop)
    rows = catalog["observations"]
    summary = {"target": target["name"], "slug": target["slug"], "query": request.model_dump(),
               "id": series_id(request), "catalog_products": len(rows), "catalog_complete": True,
               "data_complete": False, "playback_ready": False, "updated_at": now()}
    if args.dry_run:
        return {**summary, "state": "catalog_only"}
    path = folder / "downloads.json"
    state = read_json(path) or {}
    records = state.get("products", {}) if state.get("query") == request.model_dump() else {}
    state = {"version": VERSION, "query": request.model_dump(), "products": records}
    downloaded = []
    failures = []
    # A failed checkpoint stops this job's workers only. The batch stop flag
    # is reserved for a user interruption or an out-of-space stop.
    job_stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=args.workers)
    try:
        tasks = {pool.submit(fetch_product, row, request, root, records.get(row["uri"], {}),
                             attempts=args.attempts, min_free_bytes=int(args.min_free_gb * 1024 ** 3), stop=job_stop): row for row in rows}
        for count, future in enumerate(as_completed(tasks), 1):
            row = tasks[future]
            try:
                result = future.result()
                downloaded.append(row)
                LOGGER.info("  [%s/%s] %s %s", count, len(rows), "Verified cache" if result["reused"] else "Downloaded", Path(row["uri"]).name)
            except Exception as error:
                result = {"status": "failed", "error": str(error), "updated_at": now()}
                failures.append({"uri": row["uri"], "error": str(error)})
                LOGGER.error("  [%s/%s] FAILED %s: %s", count, len(rows), row["uri"], error)
                if isinstance(error, InsufficientSpace):
                    records[row["uri"]] = result
                    archive.atomic_json(path, state)
                    raise
            records[row["uri"]] = {**result, "updated_at": now()}
            archive.atomic_json(path, state)
    except BaseException as error:
        job_stop.set()
        if isinstance(error, (KeyboardInterrupt, InsufficientSpace)):
            stop.set()
        LOGGER.warning("Stopping this job: waiting for current requests (up to their timeout); completed files remain reusable.")
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    summary.update(saved_products=len(downloaded), failed_products=len(failures), failures=failures,
                   data_complete=not failures, state="partial" if failures else "complete")
    if not rows:
        summary["state"] = "no_data"
    if downloaded or not rows:
        try:
            # No archive/network calls during preview generation. Raw downloads
            # are exhaustive; the existing UI still uses a bounded quick-look.
            preview = prepare(request, root, observations=downloaded, local_only=True, refresh=True,
                              progress=lambda message, done, total: LOGGER.info("  Preview: %s %s/%s", message, done, total))
            summary.update(playback_frames=preview["frame_count"], playback_ready=preview["frame_count"] >= 2,
                           preview_excluded=len(preview["excluded"]))
        except Exception as error:
            summary.update(state="preview_failed", preview_error=str(error))
            LOGGER.exception("  Raw downloads retained, but preview preparation failed")
    archive.atomic_json(folder / "summary.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--list-targets", action="store_true", help="List target names/slugs, then exit")
    parser.add_argument("--targets", nargs="+", help="Target slugs from --list-targets; default: every preset")
    parser.add_argument("--detectors", nargs="+", choices=[f"D{i}" for i in range(1, 7)], default=[f"D{i}" for i in range(1, 7)])
    parser.add_argument("--size", type=float, choices=[0.05, 0.1, 0.2], default=0.1, help="Patch width in degrees (3/6/12 arcmin)")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=3, help="Concurrent cutout requests, one target/detector at a time")
    parser.add_argument("--attempts", type=int, choices=range(1, 6), default=3, help="Attempts per catalog page/file; HTTP connection retries also apply")
    parser.add_argument("--page-size", type=int, default=500, help="Metadata rows per TAP page (1-1000)")
    parser.add_argument("--min-free-gb", type=float, default=2, help="Keep this much disk space free, plus a cutout allowance")
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("SPHEREX_CACHE_DIR", ROOT / "data/cache")))
    parser.add_argument("--dry-run", action="store_true", help="Discover/save catalogs only; no images or previews")
    parser.add_argument("--refresh-catalog", action="store_true", help="Rediscover archive contents; still reuse verified downloads")
    args = parser.parse_args(argv)
    args.detectors = list(dict.fromkeys(args.detectors))
    if not 1 <= args.page_size <= 1000 or not 0 <= args.min_free_gb < float("inf"):
        parser.error("--page-size must be 1-1000 and --min-free-gb must be finite and nonnegative")
    try:
        targets = load_targets()
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.list_targets:
        for target in targets:
            print(f"{target['slug']}: {target['name']} ({target['ra']}, {target['dec']})")
        return 0
    if args.targets:
        unknown = set(args.targets) - {target["slug"] for target in targets}
        if unknown:
            parser.error(f"Unknown targets: {', '.join(sorted(unknown))}. Use --list-targets.")
        targets = [target for target in targets if target["slug"] in args.targets]
    if os.environ.get("SPHEREX_OFFLINE", "").lower() in {"1", "true", "yes"}:
        parser.error("Remove SPHEREX_OFFLINE before downloading online data")
    root = args.cache_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[
        logging.StreamHandler(), logging.FileHandler(root / "prefetch.log", encoding="utf-8")])
    stop = threading.Event()
    report = {"version": VERSION, "started_at": now(), "state": "running", "cache_dir": str(root),
              "size_deg": args.size, "dry_run": args.dry_run, "jobs": [],
              "scope": "All matching DR1/QR3 spectral FITS products at preset coordinates, across selected detectors. Not the whole sky or future data.",
              "preview_scope": "Current UI quick-look: at most 48 candidate exposures, one wavelength group, at most 16 playback frames per target/detector."}
    report_path = root / "batch/report.json"
    try:
        with batch_lock(root):
            archive.atomic_json(report_path, report)
            LOGGER.info("%s targets x %s detectors; patch %.2f degrees. Cache: %s", len(targets), len(args.detectors), args.size, root)
            LOGGER.info("Downloads are not sampled. This may take substantial time and disk space. Ctrl+C then rerun to resume.")
            try:
                total = len(targets) * len(args.detectors)
                for target in targets:
                    for detector in dict.fromkeys(args.detectors):
                        request = SeriesRequest(ra=target["ra"], dec=target["dec"], size_deg=args.size, detector=detector)
                        LOGGER.info("Job %s/%s: %s / %s", len(report["jobs"]) + 1, total, target["name"], detector)
                        try:
                            summary = download_job(target, request, root, args, stop)
                        except InsufficientSpace:
                            raise
                        except Exception as error:
                            summary = {"target": target["name"], "query": request.model_dump(), "state": "failed", "error": str(error)}
                            LOGGER.exception("Job failed; continuing with other targets/detectors")
                        report["jobs"].append(summary)
                        report["updated_at"] = now()
                        archive.atomic_json(report_path, report)
                incomplete = [job for job in report["jobs"] if job["state"] in {"failed", "partial", "preview_failed"}]
                report["state"] = "partial" if incomplete else ("catalog_only" if args.dry_run else "complete")
                report["finished_at"] = now()
                archive.atomic_json(report_path, report)
                LOGGER.info("Finished: %s; %s/%s jobs need retry. Playback-ready: %s. Report: %s",
                            report["state"], len(incomplete), len(report["jobs"]), sum(bool(j.get("playback_ready")) for j in report["jobs"]), report_path)
                return 1 if incomplete else 0
            except (KeyboardInterrupt, InsufficientSpace) as error:
                stop.set()
                report.update(state="interrupted" if isinstance(error, KeyboardInterrupt) else "disk_full", updated_at=now(), error=str(error))
                archive.atomic_json(report_path, report)
                LOGGER.warning("Stopped. Rerun the same command to resume. Report: %s", report_path)
                return 130 if isinstance(error, KeyboardInterrupt) else 1
    except (OSError, RuntimeError) as error:
        LOGGER.error("Cannot continue: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
