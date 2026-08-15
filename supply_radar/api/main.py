"""FastAPI application.

Serves both the JSON API and the single-page front end from one container, so
there is one deployable, no CORS and no build step.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from supply_radar import __version__
from supply_radar.api.routes import router
from supply_radar.config import STATIC_DIR, get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("supply_radar")

settings = get_settings()

app = FastAPI(
    title="Supply Radar",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get("/api/healthz")
def healthz() -> dict:
    """Liveness probe. Deliberately reveals no secrets, only whether each
    dependency has been configured at all.

    Lives under /api rather than at /healthz because Google Front End
    intercepts the bare /healthz path on Cloud Run and returns its own 404
    before the request ever reaches the container. Found by deploying early;
    it would have been an unpleasant discovery on Sunday night.
    """
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
        # Only capabilities that actually do something. "bigquery" used to be
        # reported here, set from whether an environment variable was present,
        # which reads as "the BigQuery path is wired up" when nothing in the
        # codebase imports the client. A health endpoint that overstates the
        # system is worse than one that omits a field.
        "capabilities": {
            "places": settings.places_enabled,
            "llm": settings.llm_enabled,
        },
    }


@app.get("/api/meta")
def meta() -> dict:
    return {
        "name": "Supply Radar",
        "version": __version__,
        "guards": {
            "max_cells_per_run": settings.max_cells_per_run,
            "max_subdivision_depth": settings.max_subdivision_depth,
            "daily_spend_cap_gbp": settings.daily_spend_cap_gbp,
        },
    }


app.include_router(router)

class RevalidatingStatic(StaticFiles):
    """Static files that must be revalidated before reuse.

    Starlette sets last-modified and an etag but no Cache-Control, which leaves
    freshness to browser heuristics. Measured on the deployed service: after a
    deploy the browser kept serving the previous app.css, so a CSS fix was live
    on the server and invisible in the page — the class was applied, the rule
    was in the file, and the rule was not in the loaded stylesheet.

    For a prototype that gets redeployed repeatedly and then demonstrated, a
    stale asset is a far worse outcome than a revalidation request. "no-cache"
    still allows the 304, so the cost is one conditional request per asset.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStatic(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
    )
