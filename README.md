# Atlas - Sky Viewer

A SPHEREx sky explorer and chronological image player for NASA Space Apps 2026. Explore the sky, inspect catalog information at a clicked position, and watch real dated observations change over time. Downloaded sequences stay in local files. No database or simulated observations.

## Setup

Install dependencies **before** the demo. Tested with Node 24.18, Python 3.14.6, and Chrome on Windows. The complete tested Python environment is pinned in `backend/requirements-lock.txt`; the other requirements files describe direct dependencies.

From the repository root:

~~~powershell
python -m venv .venv                  # skip if it already exists
.venv/Scripts/python.exe -m pip install -r backend/requirements-lock.txt
cd frontend
npm install
~~~

For development, start the backend from the repository root:

~~~powershell
.venv/Scripts/python.exe -m backend.main
~~~

In another terminal:

~~~powershell
cd frontend
npm run dev
~~~

Open **http://127.0.0.1:5173/**. Vite forwards /api to the backend on port 8000. The stack is React, TypeScript, Vite, Tailwind, Lucide, Aladin Lite, FastAPI, Astropy, NumPy, SciPy, Requests, and Pillow.

## Use the application

1. **Sky view** is the landing tab, starting at 320°. Drag to pan, scroll or use +/− to zoom, and reset as needed. The coordinate readout follows the pointer; field of view reports the camera's angular width. The accent color is muted orange, and there is no grid control, promotional heading, cutout tray, or footer text.
2. Click a preset under **Start exploring**, or enter RA/Dec in decimal degrees and choose **Go to location**. The map centers on the position and opens its information popup. **Sky selections never start observation playback, fetch FITS cutouts, or preload observation frames.** Only catalog information and a small observation-metadata response are requested.
3. Click the map to inspect a position. A popup attached to that point contains selected coordinates, copying, all catalog properties, nearby-match selection, and source links. Close it using its top-right **×** or Escape; the selected marker stays. Pan/zoom keeps it attached, hiding it while the point is off-screen. **View observations** loads that position and opens the player.
4. Explicitly choose **Observation playback** or **View observations** in the popup to load images. New playback requests use a fixed six-arcminute field; D2 is preferred, with another saved band used if D2 is unavailable locally. Scrub the chronological slider, skip backward/forward, or play at 1×/2×/4×. Playback and skip buttons wrap around indefinitely, without a loop indicator. Switching to the sky pauses playback and preserves the map camera.
5. **Saved objects** has one expandable entry per object. Open it to choose an available wavelength band in the right panel. Observation details are displayed above the playback image and update with its frame. Downloads and failures appear as progress/retry messages; no separate fetch/save action is needed.

The sky tab contains map navigation and object information. Playback controls and the saved collection are on the second tab. Tabs support arrow keys/Home/End, transitions respect reduced-motion preferences, and the layout adapts to mobile screens.

## Prepare and run the offline demo

1. While connected, select a target and explicitly open **Observation playback**, or prepare targets using the batch commands below. Wait until each appears under **Saved objects** and its playback controls are ready. Detector D2 and a six-arcminute field are the defaults. Different detector/size combinations have separate file caches; the collection groups them by object and wavelength, preferring the standard field.
2. Scrub and play saved sequences before disconnecting. Offline mode is controlled by the server's `--offline` flag; there is no UI checkbox. Coordinates, presets, cached catalog information, and saved observation playback remain usable.
3. Build the frontend once during preparation:

   ~~~powershell
   cd frontend
   npm run build
   cd ..
   ~~~

4. Stop the development backend if it occupies port 8000, then start the single-server demo from the repository root:

   ~~~powershell
   .venv/Scripts/python.exe -m backend.main --offline
   ~~~

5. Open **http://127.0.0.1:8000/** and choose a saved sequence. This server serves the compiled app, JSON, and images. The offline flag forbids archive retrieval, including after a server/browser restart. **Do not run npm/pip installation during an offline demo.** The local Python environment and built frontend must already exist.

You can also prepare data from the command line while connected:

~~~powershell
.venv/Scripts/python.exe -m backend.prepare_demo --ra 269.446268 --dec 4.768204 --detector D2
.venv/Scripts/python.exe -m backend.prepare_demo --ra 76.956587 --dec 30.401291 --detector D2
~~~

These are the Barnard's Star and RW Aurigae navigation positions. Repeating a completed request reads its cache without contacting IRSA. Initial retrieval can take several minutes. Fewer than two compatible frames cannot form a playable time series.

Examples prepared locally on 2026-11-14: **Barnard's Star, D2: 6 frames, 25 August 2025–22 September 2026; RW Aurigae, D2: 4 frames, 8 September 2025–15 September 2026**. Both use six-arcminute fields. Failed archive cutouts are recorded in each manifest and were excluded. Check **Saved objects** for the current inventory of prepared targets and bands.

## Batch download every preset and detector

No frontend or API server needs to be running. From the repository root, using the existing Python environment:

~~~powershell
.venv/Scripts/python.exe -m backend.prefetch_all
~~~

This reads the eight targets directly from `frontend/src/config/targets.ts` and runs all 48 target/detector combinations (D1-D6). The default patch is **6 arcmin wide**, centered at each preset's fixed coordinates; it does not cover the full extent of large objects such as Orion or follow a moving target's trajectory. Discovery pages through every matching DR1/QR3 spectral FITS product, including available processing versions, without the interactive search's 1,000-row cap or 48-exposure sampling. Every returned spectral cutout is retained, preserving the wavelengths actually observed. This is a snapshot of the searchable archive, not a guarantee of observations at every wavelength/date or a download of the whole-sky map.

The script then builds normal cached playback sequences using only saved files. **The current UI still selects one closely matched wavelength group per detector, samples at most 48 unique exposures, and displays at most 16 frames.** All other downloaded spectral data remain in the FITS cache for later processing; the UI does not yet expose all individual spectral channels. Files with insufficient usable coverage or fewer than two compatible frames may not produce playable sequences.

Useful commands:

~~~powershell
# List the available target slugs.
.venv/Scripts/python.exe -m backend.prefetch_all --list-targets

# Discover/count all matching products, without downloading images.
.venv/Scripts/python.exe -m backend.prefetch_all --dry-run

# Restrict a run, or use fewer concurrent downloads on an unreliable connection.
.venv/Scripts/python.exe -m backend.prefetch_all --targets barnards-star rw-aurigae --detectors D2 D6 --workers 2

# Rediscover newly published archive products while reusing verified files.
.venv/Scripts/python.exe -m backend.prefetch_all --refresh-catalog
~~~

**Resume by rerunning the same command.** Completed catalog pages are checkpointed, existing FITS files are structurally validated and SHA-256 checked, and failed/missing files are retried with bounded backoff. Files that finished downloading before a checkpoint failure are also recovered and validated. Invalid cached files are preserved with a `.corrupt-*` suffix and replaced. One failed file or target does not discard successful downloads or stop other targets. Checkpoint replacement retries short Windows file locks; the old checkpoint remains intact if all attempts fail. Each job has its own cancellation flag, so a failed checkpoint cannot leave the rest of the batch permanently canceled. Temporary downloads are published atomically. Ctrl+C preserves completed files; active requests may take up to their timeout to stop. A process lock prevents concurrent batch runs against the same cache and releases automatically after exit/crash. Avoid fetching the same patch in the UI during bulk preparation.

Progress appears in the terminal and `data/cache/prefetch.log`. `data/cache/batch/report.json` describes the latest run; each target/detector also has a folder named by its normal series ID containing `catalog.json`, `downloads.json`, and `summary.json`. Catalog completion, successful downloads, failures, and playback readiness are reported separately. Exit code **0** means the requested run finished (including an explicitly requested dry-run, or combinations with no archive data); **1** means failures or a disk-space stop; **130** means interrupted. A complete download does not imply every combination has useful playback images. Reruns retry failures; a completed catalog is reused unless `--refresh-catalog` is supplied.

Full downloads may take a long time and occupy multiple GB. The script keeps 2 GB free by default, plus a per-file allowance; `--min-free-gb` changes that reserve. `--size 0.05`, `0.1`, or `0.2` selects 3/6/12-arcminute fields; the simplified UI uses 0.1 degrees for new requests. Preparing other sizes requires separate runs. `--cache-dir` or `SPHEREX_CACHE_DIR` selects another destination; the app must use the same cache directory. Existing dependencies are sufficient. Run `--help` for all options.

After downloading, start the app normally and select a target or a wavelength in **Saved objects**. Its saved playback sequence loads locally. The sky map still needs internet.

## File cache

~~~text
data/cache/
  batch/report.json                   Latest bulk-run completion/failure report
  batch/<query-hash>/*.json            Catalog, download checksums, job summary
  prefetch.log                        Bulk retrieval progress and errors
  objects/<position-hash>.json         Cached SIMBAD candidates and source metadata
  observation-metadata/<hash>.json     Small IRSA observation records; no image data
  cutouts/<url-hash>.fits              Original downloaded FITS cutouts
  series/<query-hash>/
    query.json                        Parameters, archive rows, retrieval time
    manifest.json                     Sorted frames, UTC dates, wavelengths,
                                      provenance, quality, WCS, display settings
    frames/<index>-<content-hash>.png  Aligned previews
~~~

JSON stores metadata; images remain in practical image formats rather than bloated text encodings. Cache keys include normalized coordinates, detector, patch size, and pipeline version. Files do not automatically expire. A manifest is published only after its images exist, and checksums detect incomplete/corrupt copies.

**The cache is ignored by Git: keep or back it up separately.** For playback on another prepared machine, copy data/cache/series/ with all JSON/PNG files, the built frontend, and source. Install that machine's Python dependencies beforehand. FITS originals are useful for reprocessing but are not required for existing playback; copy all of data/cache/ if you want both. SPHEREX_CACHE_DIR selects another storage folder. New coordinates/detectors/patch sizes require internet unless that exact sequence is saved.

The full-sky HiPS map is an online feature. Offline mode shows an explicit placeholder while chronological playback works locally. This does not download the whole sky or create an installable PWA. Changing positions cancels stale frontend requests; a server preparation already running may finish and save its data.

## Scientific handling

- Slider steps follow available observations in chronological order. Play/pause, previous/next, and 1/2/4 frames per second are supported. Actual UTC times remain visible; date gaps can be unequal and no intermediate frames are invented.
- The backend queries individual **DR1/QR3 spectral images**, deduplicates exposure versions, and samples at most 48 exposures across the returned dates. It keeps at most 16 frames with a **total center-wavelength spread ≤2%** and overlapping center passbands. The metadata search is capped at 1,000 rows, so this is a bounded quick-look sample rather than an exhaustive archive inventory.
- Wavelength/bandwidth come from each cutout's spectral WCS. Detector identity alone is not treated as wavelength equivalence. Timestamps use IRSA time_bounds_lower (MJD exposure start), not product-processing dates.
- Images share one fixed ICRS TAN grid, north up/east left, at about the native 6.15-arcsecond scale. Alignment is to the sky, never to a moving target. Bilinear interpolation rejects invalid contributors. One shared asinh stretch is used for every frame; there is no individual auto-exposure.
- Nonfinite pixels, nonpositive variance, and flagged pixels are masked. SOURCE and FULLSAMPLE are treated as benign; other flag bits are conservatively masked. Teal marks missing/flagged pixels, distinct from dark sky. At least half of a patch must have usable coverage.
- This is visual exploration, not quantitative change detection. PSF/background/calibration differences and wavelength variation across a patch can mimic change. Bright targets such as Barnard's Star can have masked/saturated cores. Presets do not guarantee visible motion or variability.

## Presets and online map

The five requested targets appear above Galactic Center, Orion Nebula, and M101. Barnard's Star, Epsilon Indi B, RW Aurigae, and R Draconis use CDS/SIMBAD coordinates propagated with proper motion to epoch 2026.0 for navigation, without parallax correction. WISE 0855 uses the brief's supplied coordinates; its epoch was unspecified. Values and sources are in frontend/src/config/targets.ts.

The map retains pan/zoom/reset, direct click markers, manual coordinate entry, and copying. Selected coordinates are in the map popup; the live readout follows the pointer rather than the camera center. RA wraps into [0,360); Dec must be within [-90,90]. Its background is the **SPHEREx DR1 HiPS cube, frame 62, nominal 3.293 μm**; that mosaic combines observations and is not itself a time sequence. See [HiPS compatibility findings](docs/hips-compatibility.md).

## Object information on selection

Click the sky map, enter coordinates, or select a preset. **Object information** appears in a popup attached to the selected position and searches SIMBAD through the Python backend. It shows possible catalog matches within **30 arcseconds** of the selection: names, human-readable types, spectral types, V magnitudes, parallaxes, proper motions, J2000 catalog positions, and SIMBAD links when available. Unknown properties show **Not cataloged**. When several sources are nearby, choose one with **Nearby catalog matches**; entries that share a position are not claims that SPHEREx resolved each component. Zoom in before selecting a small source. The popup scrolls within the map to keep all properties accessible on small screens.

The backend uses the official Harvard SIMBAD mirror, with Strasbourg as a fallback. A 10-arcminute discovery cone includes fast-moving sources whose J2000 coordinates differ from their 2026 location. It compares the original catalog position and an approximate position propagated with proper motion to 2026.0 (without parallax/radial-velocity correction). A mosaic has no single observation epoch, so every result remains a candidate association. Discovery is capped at 501 rows; at most eight nearby matches are displayed. The panel explicitly flags crowded-field truncation, no matches, unavailable services, and failures saving metadata. A missing catalog match does not mean a source is absent from the image.

Only the backend contacts SIMBAD. Lookups are debounced; changing positions or closing the popup aborts stale frontend requests. Successful results, including no-match results, are cached as JSON under `data/cache/objects/`. These are dated snapshots without automatic expiration. Cached information works with server `--offline`; an uncached offline position displays a clear message without making an external request. The spectral-image batch does not prefetch metadata for arbitrary future map clicks.

To check or pre-cache known positions without starting either server:

~~~powershell
.venv/Scripts/python.exe -m backend.objects --ra 269.446268 --dec 4.768204
.venv/Scripts/python.exe -m backend.objects --ra 101.287155 --dec -16.716116
.venv/Scripts/python.exe -m backend.objects --ra 101.287155 --dec -16.716116 --cache-only
~~~

The second position is Sirius's catalog coordinate. SIMBAD identifies it as `* alf CMa`; the nearby white dwarf `* alf CMa B` can appear as another candidate. For a quick manual check: start both servers, open port 5173, enter those Sirius coordinates, zoom in, close the popup and click near the center of the map. Verify the popup, switch candidate, then click elsewhere or close it.

## API and implementation

`GET /api/observation-metadata?ra=...&dec=...` supplies the observation fields in the sky popup independently of playback. It first reads JSON manifests for saved fields containing the point, conservatively using an inscribed circle. Wavelength/bandwidth describe the saved field's center, and valid pixels describes that cutout; its center and field width are shown explicitly. It never reads or requests image files.

For uncached online positions, a bounded IRSA TAP query retrieves up to 24 recent spectral product records containing the position. It returns observation IDs, UTC dates, releases, and detector bands. Exact wavelength/bandwidth at the click and usable-pixel fractions stay unknown because those require spectral WCS and image/quality data; nominal detector coverage is labeled separately. These are observations of a field, not claims that a particular catalog source was detected. Results (including empty results) are cached as JSON under `data/cache/observation-metadata/`. Requests have bounded timeouts/concurrency; failures have an independent retry action and are never cached as empty results. `cache_only=true` and server `--offline` forbid external retrieval. No metadata lookup starts a preparation job or requests FITS/PNG files. Newly prepared field metadata takes precedence over an older archive-only JSON cache.

- GET /api/health — status and forced-offline mode.
- GET /api/series — valid saved sequences.
- GET /api/objects?ra=...&dec=... — cached or live SIMBAD candidates; cache_only=true forbids uncached external lookups (also enforced by server offline mode).
- GET /api/observation-metadata?ra=...&dec=... — image-free observation metadata, with local JSON reuse and optional cache_only=true.
- POST /api/series — JSON with ra, dec, detector (default D2), size_deg (default 0.1). A cache hit returns immediately; otherwise HTTP 202 provides a preparation ID. The query parameter cache_only=true forbids a cache-miss download.
- GET /api/series/{id} — poll preparation or load a saved manifest.
- GET /api/series/{id}/frames/{filename} — a local PNG.
- GET /api/series/{id}/manifest — downloadable metadata JSON.

backend/archive.py retrieves IRSA data; processing.py matches wavelengths, masks, aligns, and renders; series.py manages manifests; main.py provides FastAPI and static serving. One in-memory worker runs preparations, with four bounded cutout downloads and at most three queued preparations. Unfinished jobs do not survive restart; downloaded originals do.

`useObservations.ts` handles explicit playback requests, progress, cancellation, and saved manifests. `ObservationTimeline.tsx` owns looping playback and image preloading. `App.tsx` keeps selection independent of map availability and preserves the mounted map across tabs. `SkyViewer.tsx` handles Aladin, pointer coordinates, and world-to-screen popup anchoring. `SkyObservationInfo.tsx` requests small metadata responses separately, and `ObservationDetails.tsx` renders the five shared observation fields above the player and within the popup.

`backend/objects.py` validates, matches, and caches catalog information; `ObjectInfo.tsx` handles loading/errors, multiple candidates, and request cancellation inside `ObjectPopover.tsx`. `config/bands.ts` groups saved sequences into one object with ordered wavelength choices without deleting any cache files.

## Tests and troubleshooting

~~~powershell
.venv/Scripts/python.exe -m unittest discover -s backend/tests -v
cd frontend
npm test
npm run build
npm run test:browser
~~~

Backend tests cover wavelength grouping, WCS alignment, masked interpolation, shared display scaling, cache reuse/restarts, corruption, offline misses, and request validation. Batch tests also cover catalog checkpoints, server URI collation, TAP overflow, retries, corrupt FITS replacement, partial failures, free-space checks, local-only previews, and process locking. Browser tests use real downloaded sequences with external requests blocked, verify slider/skip/play/reload/mobile behavior and stale-response handling, test the built app on a fresh offline server, and check the map interactions. Real timeline tests require a saved Barnard's Star D2 sequence with at least three frames and the default 0.1-degree field; prepare it first. The runner needs Chrome (or PLAYWRIGHT_CHANNEL=msedge), .venv, and a built frontend. It uses separate test ports 5273, 8100, and 8101 so development servers on 5173/8000 can stay running. Run one suite at a time; `$env:CI='1'; npm run test:browser` requires fresh test servers. Vite's `ATLAS_API_URL` override is used only by the test server; normal development still proxies to port 8000.

Object tests cover API validation, service failure/failover, unknown fields, corrupt cache recovery, offline enforcement, approximate proper-motion matching, and multiple co-located entries. The map-click and forced-offline metadata browser checks require the real Sirius result cached by the command above; two UI failure/stale-response tests use isolated mocked transport responses. Batch regressions simulate temporary/persistent checkpoint locks and verify that a failed job does not cancel the next job. Tests use temporary files; they do not retry or restart the full real batch.

The current changes passed the production build, all six coordinate/band-grouping tests, all 36 backend tests, and all 14 browser checks. The browser suite verifies popup positioning and preserved catalog selection while resizing, multiple catalog candidates, closing and retries, stale requests, pointer coordinates, preserved map state across tabs, metadata-only sky selections with zero frame/preparation requests, early preset selection during startup, service reconnection, grouped wavelength choices, infinite playback, observation details above the image, desktop/mobile layouts, and a compiled app with external requests blocked. Screenshots are under `frontend/artifacts/atlas-*.png`. Uncached IRSA metadata requests are stubbed in browser tests; the live uncached query verification command was declined, so that upstream query was not verified live in this session. Backend tests cover parsing, limits, cache reuse, offline enforcement, and failures using isolated metadata responses. NASA HiPS can intermittently return HTTP/2 transport errors; the map reports imagery failures and offers retry. Backend tooling emits a non-failing Starlette/httpx deprecation warning in the pinned environment.

If the API is unavailable, start Python and refresh the saved list. For an offline miss, choose a listed sequence or reconnect and fetch that exact combination. Missing/corrupt previews are not advertised as ready: restore the cache or fetch the patch again online. IRSA HTTP 503/timeouts are retried, and excluded cutouts/reasons are recorded in JSON. A fully failed preparation can be retried. Cached sequences do not automatically refresh when the archive changes.

Vite's large-chunk warning is expected for the separate Aladin/WASM bundle (~2.6 MB uncompressed). Physical touch devices and other GPUs remain untested. Synthetic arrays exist only in isolated backend unit tests; application/demo images are actual SPHEREx downloads.

References: [dated cutouts](https://caltech-ipac.github.io/irsa-tutorials/spherex-cutouts/), [DR1 overview](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/overview_dr1.html), [spectral images/WCS](https://caltech-ipac.github.io/irsa-tutorials/spherex-intro/), [quality flags](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/sp.html). Next work: validate particularly useful target sequences, improve PSF/background comparisons, and add explicit archive refresh.
