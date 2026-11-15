"""Read one real cutout before developing processing assumptions."""
import argparse
from pathlib import Path

from astropy.io import fits
from astropy.wcs import WCS

from backend.archive import atomic_json, cutout_url, download_cutout, search

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ra", type=float, default=210.80227)
    parser.add_argument("--dec", type=float, default=54.34895)
    parser.add_argument("--detector", default="D2")
    args = parser.parse_args()
    rows = search(args.ra, args.dec, args.detector)
    atomic_json(Path("data/probe/query.json"), rows)
    print(f"Found {len(rows)} real observations", flush=True)
    if rows:
        row = rows[len(rows) // 2]
        path = download_cutout(cutout_url(row["uri"], args.ra, args.dec, 0.1), Path("data/cache"))
        with fits.open(path) as hdus:
            hdus.info()
            image = hdus["IMAGE"]
            print({key: value for key, value in image.header.items() if any(term in key for term in ("MJD", "DATE", "BUNIT", "DETECT", "EXPTIME", "WCSAXES"))})
            spatial = WCS(image.header).celestial
            x, y = spatial.world_to_pixel_values(args.ra, args.dec)
            spectral = WCS(image.header, fobj=hdus, key="W")
            spectral.sip = None
            print("Center pixel", x, y, "wavelength/bandwidth", spectral.pixel_to_world(x, y), flush=True)
            print("Raw primary metadata", repr(hdus[0].header), flush=True)
