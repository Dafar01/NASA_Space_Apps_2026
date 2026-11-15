"""Download a real, reproducible sequence before disconnecting from the internet."""
import argparse
import json
import os
from pathlib import Path

from backend.series import SeriesRequest, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ra", type=float, required=True)
    parser.add_argument("--dec", type=float, required=True)
    parser.add_argument("--detector", default="D2", choices=[f"D{i}" for i in range(1, 7)])
    parser.add_argument("--size", type=float, default=0.1, help="Patch width in degrees")
    args = parser.parse_args()
    if os.environ.get("SPHEREX_OFFLINE") == "1":
        parser.error("Remove SPHEREX_OFFLINE before preparing new online data")
    root = Path(os.environ.get("SPHEREX_CACHE_DIR", Path(__file__).resolve().parent.parent / "data" / "cache"))
    result = prepare(SeriesRequest(ra=args.ra, dec=args.dec, detector=args.detector, size_deg=args.size), root,
                     lambda message, complete, total: print(f"{message} {complete}/{total}" if total else message, flush=True))
    print(json.dumps({key: result[key] for key in ["id", "frame_count", "date_start", "date_end", "wavelength_min_um", "wavelength_max_um"]}, indent=2))
    print(f"Saved in {root / 'series' / result['id']}", flush=True)
    if result["frame_count"] < 2:
        raise SystemExit("Fewer than two compatible frames; this target/detector is not ready for playback.")


if __name__ == "__main__":
    main()
