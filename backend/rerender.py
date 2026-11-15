"""Rebuild saved previews from local FITS only: python -m backend.rerender."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from backend.archive import atomic_json
from backend.processing import RENDER_VERSION
from backend.series import read_saved, rerender_saved

ROOT = Path(__file__).resolve().parent.parent


def run(root: Path, *, force: bool = False) -> dict:
    report = {"render_version": RENDER_VERSION, "started_at": datetime.now(timezone.utc).isoformat(),
              "updated": 0, "skipped": 0, "failed": 0, "frames": 0, "previously_hidden_pixels": 0,
              "unavailable_pixels": 0, "results": []}
    for path in sorted((root / "series").glob("*/manifest.json")):
        identity = path.parent.name
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if not force and old.get("render_version", 1) >= RENDER_VERSION and read_saved(root, identity):
                report["skipped"] += 1
                continue
            height, width = old["image_shape"]
            total = height * width
            before = sum(round((1 - frame.get("available_fraction", frame["valid_fraction"])) * total) for frame in old["frames"])
            updated = rerender_saved(root, identity)
            missing = sum(round((1 - frame["available_fraction"]) * total) for frame in updated["frames"])
            report["updated"] += 1
            report["frames"] += updated["frame_count"]
            report["previously_hidden_pixels"] += before
            report["unavailable_pixels"] += missing
            result = {"id": identity, "state": "updated", "frames": updated["frame_count"],
                      "previously_hidden_pixels": before, "unavailable_pixels": missing}
            print(f"{identity}: rebuilt {updated['frame_count']} frames; {before} hidden pixels -> {missing} genuinely unavailable", flush=True)
        except Exception as error:
            report["failed"] += 1
            result = {"id": identity, "state": "error", "message": str(error)}
            print(f"{identity}: FAILED: {error}; previous manifest preserved", flush=True)
        report["results"].append(result)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(root / "rerender-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=Path(os.environ.get("SPHEREX_CACHE_DIR", ROOT / "data/cache")))
    parser.add_argument("--force", action="store_true", help="Rebuild current previews too; reuse local FITS only")
    args = parser.parse_args()
    report = run(args.cache_dir.resolve(), force=args.force)
    print(f"Updated {report['updated']}, skipped {report['skipped']}, failed {report['failed']}; {report['frames']} frames. No downloads.")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
