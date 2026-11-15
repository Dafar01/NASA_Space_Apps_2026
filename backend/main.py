"""Run: python -m backend.main [--offline]. No database or external credentials."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import re
import threading

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.series import SeriesRequest, list_saved, prepare, read_saved, series_id, valid_id
from backend import objects, metadata

ROOT = Path(__file__).resolve().parent.parent


def create_app(cache_root: Path | None = None, offline: bool | None = None) -> FastAPI:
    cache = cache_root or Path(os.environ.get("SPHEREX_CACHE_DIR", ROOT / "data" / "cache"))
    cache = cache.resolve()
    is_offline = offline if offline is not None else os.environ.get("SPHEREX_OFFLINE", "").lower() in {"1", "true", "yes"}
    jobs: dict[str, dict] = {}
    lock = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="spherex-series")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Atlas - Sky Viewer", version="0.2.0", lifespan=lifespan)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "offline": is_offline, "storage": "local files", "saved_sequences": len(list_saved(cache))}

    @app.get("/api/series")
    def saved_sequences():
        return {"series": list_saved(cache), "offline": is_offline}

    @app.get("/api/objects")
    def identify_objects(ra: float = Query(allow_inf_nan=False),
                         dec: float = Query(ge=-90, le=90, allow_inf_nan=False),
                         cache_only: bool = False):
        try:
            return objects.lookup(ra, dec, cache, offline=is_offline or cache_only)
        except objects.CatalogNotSaved as error:
            raise HTTPException(404, str(error)) from error
        except objects.CatalogUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/api/observation-metadata")
    def observation_metadata(ra: float = Query(allow_inf_nan=False),
                             dec: float = Query(ge=-90, le=90, allow_inf_nan=False),
                             cache_only: bool = False):
        try:
            return metadata.lookup(ra, dec, cache, offline=is_offline or cache_only)
        except metadata.MetadataUnavailable as error:
            raise HTTPException(503, str(error)) from error

    def response_for(manifest: dict) -> dict:
        return {**manifest, "cached": True, "offline": is_offline, "message": "Loaded from local files; internet is not required for these frames."}

    @app.get("/api/series/{identity}")
    def get_sequence(identity: str):
        if not valid_id(identity):
            raise HTTPException(404, "Sequence not found")
        with lock:
            job = jobs.get(identity)
            if job:
                return dict(job)
        manifest = read_saved(cache, identity)
        if manifest is None:
            raise HTTPException(404, "This sequence is not saved locally, or its saved images are incomplete.")
        return response_for(manifest)

    @app.post("/api/series")
    def create_sequence(request: SeriesRequest, cache_only: bool = Query(default=False)):
        identity = series_id(request)
        saved = read_saved(cache, identity)
        if saved:
            return response_for(saved)
        if is_offline or cache_only:
            raise HTTPException(404, "This patch and detector are not saved locally. Connect to the internet and fetch them before the demo, or open a saved sequence.")
        with lock:
            if identity in jobs and jobs[identity]["state"] == "preparing":
                return JSONResponse(dict(jobs[identity]), status_code=202)
            if sum(job["state"] == "preparing" for job in jobs.values()) >= 3:
                raise HTTPException(429, "Three sequences are already being prepared. Wait for one to finish.")
            jobs[identity] = {"id": identity, "state": "preparing", "query": request.model_dump(),
                              "message": "Queued for NASA IRSA retrieval…", "completed": 0, "total": 0}

        def progress(message: str, completed: int, total: int):
            with lock:
                jobs[identity].update(message=message, completed=completed, total=total)

        def work():
            try:
                manifest = prepare(request, cache, progress)
                with lock:
                    jobs[identity] = {**manifest, "cached": False, "offline": False,
                                      "message": "Sequence saved locally and ready for offline playback."}
            except Exception as error:
                logging.getLogger(__name__).exception("Sequence preparation failed")
                with lock:
                    jobs[identity].update(state="error", message=f"Unable to prepare this sequence: {error}")
            finally:
                # Completed manifests live on disk; keep only a bounded number of job records.
                with lock:
                    for old in list(jobs):
                        if len(jobs) <= 24:
                            break
                        if old != identity and jobs[old]["state"] != "preparing":
                            del jobs[old]

        executor.submit(work)
        return JSONResponse({"id": identity, "state": "preparing", "query": request.model_dump(),
                             "message": "Searching NASA IRSA…", "completed": 0, "total": 0}, status_code=202)

    @app.get("/api/series/{identity}/frames/{filename}")
    def frame_image(identity: str, filename: str):
        if not valid_id(identity) or not re.fullmatch(r"\d{3}-[0-9a-f]{12}\.png", filename):
            raise HTTPException(404, "Frame not found")
        manifest = read_saved(cache, identity)
        if not manifest or not any(frame["image_file"] == filename for frame in manifest["frames"]):
            raise HTTPException(404, "This frame is not saved locally")
        return FileResponse(cache / "series" / identity / "frames" / filename, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/series/{identity}/manifest")
    def manifest_file(identity: str):
        if read_saved(cache, identity) is None:
            raise HTTPException(404, "Saved manifest not found")
        return FileResponse(cache / "series" / identity / "manifest.json", media_type="application/json", filename=f"spherex-{identity}.json")

    # A single local process can serve the compiled frontend during the demo.
    # Vite proxies /api to port 8000 during development instead.
    distribution = ROOT / "frontend" / "dist"
    if (distribution / "index.html").is_file():
        app.mount("/", StaticFiles(directory=distribution, html=True), name="frontend")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Serve saved observations and object information without external requests")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(create_app(offline=args.offline or os.environ.get("SPHEREX_OFFLINE", "").lower() in {"1", "true", "yes"}), host="127.0.0.1", port=args.port)
