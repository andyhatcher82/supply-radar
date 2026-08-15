"""FastAPI application.

Serves both the JSON API and the single-page front end from one container, so
there is one deployable, no CORS and no build step.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from supply_radar import __version__
from supply_radar.api import gate
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


@app.middleware("http")
async def require_access_code(request: Request, call_next):
    """One shared code in front of everything, including the API.

    Gating only the front end would be theatre: /api/snapshot returns the whole
    published lead list, and the page source names the endpoint. So the check
    runs here, before routing, and the few paths the entry page itself needs are
    the only exceptions.

    With no code configured the site is open, which keeps local development
    frictionless and matches how access_code already behaved.
    """
    if not settings.access_code or gate.is_open(request.url.path):
        return await call_next(request)
    if not gate.has_valid_cookie(request, settings.access_code, settings.gate_secret):
        return gate.deny(request)
    return await call_next(request)


@app.get("/enter")
def enter_page():
    return gate.entry_page()


@app.post("/api/enter")
async def enter(request: Request):
    """Exchange the code for a signed cookie.

    Rate-limited per client because a 5-digit code is 100,000 combinations,
    which is nothing to a script. This does not make it strong, it makes
    guessing slow enough to be pointless for the week this is deployed.
    """
    if gate.rate_limited(request):
        return JSONResponse(
            {"detail": "Too many attempts. Wait a few minutes and try again."},
            status_code=429,
        )
    body = await request.json()
    if str(body.get("code", "")).strip() != settings.access_code:
        gate.record_failure(request)
        return JSONResponse({"detail": "That code was not recognised."}, status_code=401)
    response = JSONResponse({"ok": True})
    gate.grant(
        response, settings.access_code, settings.gate_secret, gate.is_https(request)
    )
    return response


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
