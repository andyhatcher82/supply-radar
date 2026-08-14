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


@app.get("/healthz")
def healthz() -> dict:
    """Liveness probe. Deliberately reveals no secrets, only whether each
    dependency has been configured at all."""
    return {
        "status": "ok",
        "version": __version__,
        "environment": settings.environment,
        "capabilities": {
            "places": settings.places_enabled,
            "llm": settings.llm_enabled,
            "bigquery": bool(settings.bq_project),
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


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
