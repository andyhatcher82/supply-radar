"""API routes.

Two classes of endpoint, deliberately separated:

  READ    the precomputed Croatia snapshot, metrics and economics. Open, fast,
          cannot fail in front of an audience, costs nothing to serve.
  RUN     a genuinely live discovery sweep over an area drawn on the map.
          Gated by an access code and hard-capped, because it spends money.

Every run endpoint answers "what will this cost" before it will spend anything.
A public button that spends money without saying so first is a defect.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from supply_radar import admin
from supply_radar.api import gate
from supply_radar.classify import classify, resolve_category
from supply_radar.config import SNAPSHOT_DIR, get_settings
from supply_radar.costs import CostLedger, estimate_sweep
from supply_radar.discovery.google_places import GooglePlacesSource
from supply_radar.geometry import AreaTooLargeError, SearchArea, cover
from supply_radar.llm import LLMClient
from supply_radar.regions import as_geojson, check_area
from supply_radar.scoring import score_lead

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
settings = get_settings()

# A live run is a demonstration, not a production sweep. These caps keep a
# public button from becoming an expensive surprise.
MAX_LIVE_CELLS = 24
MAX_LIVE_QUERIES = 3
MAX_LIVE_DEPTH = 2

DEFAULT_QUERIES = ["boat tour", "walking tour", "wine tasting"]


class AreaRequest(BaseModel):
    shape: Literal["circle", "polygon"] = "circle"
    center_lat: float | None = None
    center_lng: float | None = None
    radius_km: float | None = None
    points: list[list[float]] | None = Field(
        default=None, description="[[lat, lng], ...] for a drawn polygon"
    )
    cell_km: float = 3.0
    queries: list[str] = Field(default_factory=lambda: list(DEFAULT_QUERIES))

    def to_area(self) -> SearchArea:
        if self.shape == "circle":
            if self.center_lat is None or self.center_lng is None or not self.radius_km:
                raise HTTPException(400, "A circle needs a centre and a radius")
            if self.radius_km > 25:
                raise HTTPException(400, "Radius is capped at 25 km for a live run")
            return SearchArea.from_circle(
                self.center_lat, self.center_lng, self.radius_km
            )

        if not self.points or len(self.points) < 3:
            raise HTTPException(400, "A polygon needs at least three points")
        return SearchArea.from_polygon([(p[0], p[1]) for p in self.points])

    def clean_queries(self) -> list[str]:
        """Only curated terms are accepted. Anything else is refused rather
        than quietly passed through to a paid API with no category mapping."""
        accepted, rejected = admin.validate_terms(self.queries)
        if rejected and not accepted:
            raise HTTPException(
                400,
                f"Not on the approved search-term list: {', '.join(rejected)}. "
                "Pick from the list, or ask an admin to add them.",
            )
        return accepted or [t["term"] for t in admin.search_terms() if t["default"]][:1]


def _load(name: str) -> dict | list:
    path = SNAPSHOT_DIR / name
    if not path.exists():
        raise HTTPException(404, f"{name} has not been generated yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_code(supplied: str | None, request: Request | None = None) -> None:
    """Gate the endpoints that spend money.

    Browsing used to be open and spending gated separately. The whole site now
    sits behind the same code, so anyone who can reach this endpoint has
    already presented it, and demanding it a second time is friction placed
    exactly where the demo can least afford it. The session cookie is therefore
    accepted as proof.

    The header is still honoured, because a script or a curl call has no cookie
    and there is no reason to force one through the browser flow to use the API.
    """
    if not settings.access_code:
        return
    if supplied == settings.access_code:
        return
    if request is not None and gate.has_valid_cookie(
        request, settings.access_code, settings.gate_secret
    ):
        return
    raise HTTPException(401, "A valid access code is required to run a live sweep")


# --------------------------------------------------------------------- read


@router.get("/snapshot")
def snapshot() -> dict:
    """The precomputed Croatia snapshot the console loads by default."""
    return _load("snapshot.json")


@router.get("/leads")
def leads(limit: int = 200) -> dict:
    data = _load("snapshot.json")
    return {"leads": data.get("leads", [])[:limit]}


@router.get("/metrics")
def metrics() -> dict:
    data = _load("snapshot.json")
    return data.get("metrics", {})


@router.get("/economics")
def economics() -> dict:
    data = _load("snapshot.json")
    return data.get("economics", {})


@router.get("/regions")
def regions() -> dict:
    """Markets the business has opened, so the browser can grey out the rest."""
    data = as_geojson()
    # Honour any market an admin has closed on this instance.
    data["enabled"] = [
        r for r in data["enabled"] if r["id"] not in admin.STATE.disabled_regions
    ]
    return data


@router.get("/search-terms")
def search_terms() -> dict:
    """The curated list users pick from.

    Free text was the wrong control here. A user's own wording can spend money
    on a query that returns nothing, and the term a result was found by is what
    feeds gap-fit scoring — an unmapped term produces operators with no
    category and silently zeroes an entire axis.
    """
    return {
        "terms": admin.search_terms(),
        "max_selectable": admin.max_selectable(),
    }


def _require_admin(supplied: str | None) -> None:
    if not settings.admin_code:
        raise HTTPException(503, "No admin code is configured on this deployment")
    if supplied != settings.admin_code:
        raise HTTPException(401, "A valid admin code is required")


@router.get("/admin/config")
def admin_config(x_admin_code: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_code)
    return admin.snapshot(settings)


@router.post("/admin/config")
def admin_update(
    changes: dict, x_admin_code: str | None = Header(default=None)
) -> dict:
    _require_admin(x_admin_code)
    try:
        applied = admin.apply(changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"applied": applied, "config": admin.snapshot(settings)}


# ---------------------------------------------------------------- estimate


def _sweep_score(score) -> dict:
    """A live sweep's score, with the band removed.

    Bands are calibrated against the enriched lead list, so the same letter on a
    sweep would mean something different from the same letter on the Leads page.
    An operator shown as A here and B once enriched is exactly the kind of
    inconsistency this build keeps having to correct, so the letter is withheld
    rather than qualified.

    The composite stays, because a sweep still needs a sort order, and it is
    labelled provisional wherever it appears.
    """
    out = score.to_dict()
    out.pop("band", None)
    out["enriched"] = False
    return out


@router.post("/estimate")
def estimate(req: AreaRequest) -> dict:
    """What a sweep over this area would cost, before spending anything."""
    area = req.to_area()
    queries = req.clean_queries()

    permitted, region, why = check_area(area)
    if not permitted:
        raise HTTPException(403, why)

    try:
        cells = cover(area, req.cell_km, max_cells=settings.max_cells_per_run)
    except AreaTooLargeError as exc:
        raise HTTPException(400, str(exc)) from exc

    est = estimate_sweep(len(cells), len(queries))
    too_big = len(cells) > MAX_LIVE_CELLS

    return {
        **est,
        "area_km2": round(area.area_km2, 1),
        "region": region.name if region else None,
        "queries": queries,
        "within_live_run_limit": not too_big,
        "live_run_cell_limit": MAX_LIVE_CELLS,
        "message": (
            f"{len(cells)} cells x {len(queries)} queries. "
            + (
                f"That is over the {MAX_LIVE_CELLS}-cell live limit; "
                "draw a smaller area or use a coarser cell size."
                if too_big
                else "Within the live run limit."
            )
        ),
    }


# --------------------------------------------------------------------- run


@router.post("/run")
def run(
    req: AreaRequest,
    request: Request,
    x_access_code: str | None = Header(default=None),
) -> dict:
    """A genuinely live sweep: discover, classify, score.

    Enrichment is skipped here because fetching operator websites takes several
    seconds each and would make this unusable interactively. Readiness
    therefore scores on contactability alone, and the response says so rather
    than letting a reader assume the axis is complete.
    """
    _require_code(x_access_code, request)

    if not settings.places_enabled:
        raise HTTPException(503, "Places API is not configured on this deployment")

    area = req.to_area()
    queries = req.clean_queries()

    # Re-checked here and not only in /estimate. The browser gate is a
    # courtesy; this is the boundary.
    permitted, _region, why = check_area(area)
    if not permitted:
        raise HTTPException(403, why)

    try:
        cells = cover(area, req.cell_km, max_cells=settings.max_cells_per_run)
    except AreaTooLargeError as exc:
        raise HTTPException(400, str(exc)) from exc

    if len(cells) > MAX_LIVE_CELLS:
        raise HTTPException(
            400,
            f"{len(cells)} cells exceeds the {MAX_LIVE_CELLS}-cell live run limit. "
            "Draw a smaller area or increase the cell size.",
        )

    ledger = CostLedger()
    started = time.time()

    with GooglePlacesSource(settings.google_maps_api_key, ledger=ledger) as source:
        sweep = source.sweep(
            area,
            queries=queries,
            initial_half_side_km=req.cell_km,
            max_depth=MAX_LIVE_DEPTH,
            max_cells=settings.max_cells_per_run,
            destination_id=None,
        )

    classification = None
    results_by_id: dict = {}
    if settings.llm_enabled and sweep.places:
        llm = LLMClient(
            settings.anthropic_api_key, ledger=ledger, model=admin.active_model()
        )
        classification = classify(sweep.places, llm)
        results_by_id = {r.place_source_id: r for r in classification.results}

    leads = []
    for place in sweep.places:
        result = results_by_id.get(place.source_id)
        if result is not None and result.verdict.value != "experience_operator":
            continue
        category = resolve_category(
            place, result.experience_type if result else None
        )
        score = score_lead(place, category, None, unenriched=True)
        leads.append(
            {
                "name": place.name,
                "website": place.website,
                "phone": place.phone,
                "address": place.address,
                "lat": place.lat,
                "lng": place.lng,
                "rating": place.rating,
                "review_count": place.review_count,
                "category": category,
                "classification": {
                    "verdict": result.verdict.value if result else "unclassified",
                    "confidence": result.confidence if result else None,
                    "reason": result.reason if result else None,
                    "decided_by": result.decided_by if result else None,
                    "needs_review": result.needs_review if result else False,
                }
                if result
                else None,
                **_sweep_score(score),
            }
        )

    leads.sort(key=lambda lead: -lead["composite"])

    return {
        "elapsed_seconds": round(time.time() - started, 1),
        "discovery": sweep.summary(),
        "classification": classification.summary() if classification else None,
        "leads": leads,
        "cost": ledger.summary(),
        # Carried in the payload rather than written as UI copy, because it is a
        # statement about what this endpoint did and did not compute. Anyone
        # reading the API gets it too.
        "scope": {
            "net_new_determined": False,
            "headline": (
                "These are discovered operators, not net-new leads. "
                "No net-new determination has been made."
            ),
            "detail": (
                "Net-new is not a property of an operator, it is a relation "
                "between an operator and Viator's supply list, so it cannot be "
                "computed with one side missing. This sweep discovers, "
                "classifies and scores anywhere, live. The net-new "
                "determination is demonstrated on the Split benchmark, where an "
                "answer key exists. In production it runs everywhere, because "
                "the supplier list is a lookup that exists for every "
                "destination."
            ),
        },
        # Incomplete coverage is NOT repeated here. discovery.unresolved_cells
        # carries the number, and the console renders it as a warning in its own
        # right. Stating it in both places put the same sentence on screen twice,
        # in two different wordings, which reads as two separate problems.
        "caveats": [
            "We did not read operator websites, so readiness only reflects "
            "whether we can contact them. The published lead list is enriched.",
            "Demand data here is made up. In production it comes from Viator's "
            "own search logs.",
        ],
    }
