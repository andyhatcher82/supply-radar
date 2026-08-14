"""Google Places discovery with adaptive subdivision.

The API returns at most 60 results per query, across four pages, and says
nothing when it truncates. Measured directly: a "boat tour" search over an 8 km
radius around Split returned 20, 20, 17, 3 and then stopped, which is exactly
the cap. There are certainly more than 60 boat tour operators reachable from
Split, so a single query silently loses the tail.

So any cell that comes back at the cap is treated as evidence of more behind
it, split into four, and re-queried. Cells still at the cap when maximum depth
is reached are reported as UNRESOLVED rather than quietly dropped, because a
sweep that knows it is incomplete should say so.
"""

from __future__ import annotations

import logging
import math
import time

import httpx

from supply_radar.costs import CostLedger
from supply_radar.discovery.base import SweepResult
from supply_radar.geometry import KM_PER_DEG_LAT, Cell, SearchArea, cover
from supply_radar.models import DiscoveredPlace, Source

log = logging.getLogger(__name__)

SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"

# Hard ceiling the API imposes, confirmed empirically rather than assumed.
RESULT_CAP = 60
PAGE_SIZE = 20

# Requesting these fields puts the call in the Enterprise SKU. Everything here
# is used downstream: dropping any of it would save money but cost a scoring
# signal, and pulling it later via Place Details would cost roughly five times
# more for the same data.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.rating",
        "places.userRatingCount",
        "places.websiteUri",
        "places.internationalPhoneNumber",
        "places.types",
        "places.businessStatus",
        "nextPageToken",
    ]
)

# Places that are never an experience supplier. A cheap deterministic filter
# applied before anything is paid for or sent to a model.
EXCLUDED_TYPES = {
    "parking", "gas_station", "atm", "bank", "hospital", "pharmacy",
    "supermarket", "convenience_store", "car_repair", "car_wash",
    "post_office", "school", "primary_school", "secondary_school",
    "university", "police", "fire_station", "cemetery", "storage",
    "real_estate_agency", "insurance_agency", "dentist", "doctor",
}


class GooglePlacesSource:
    name = "google_places"

    def __init__(
        self,
        api_key: str,
        ledger: CostLedger | None = None,
        client: httpx.Client | None = None,
        pause_seconds: float = 0.12,
    ):
        if not api_key:
            raise ValueError("Google Places API key is required")
        self.api_key = api_key
        self.ledger = ledger or CostLedger()
        self._client = client
        self._owns_client = client is None
        self.pause_seconds = pause_seconds

    def __enter__(self):
        if self._client is None:
            self._client = httpx.Client(timeout=30.0)
        return self

    def __exit__(self, *exc):
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30.0)
        return self._client

    # ------------------------------------------------------------------ API

    def _post(self, body: dict) -> dict:
        """One search call, with backoff on rate limiting and transient faults."""
        delay = 1.0
        for attempt in range(4):
            res = self.client.post(
                SEARCH_TEXT_URL,
                headers={
                    "X-Goog-Api-Key": self.api_key,
                    "X-Goog-FieldMask": FIELD_MASK,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            self.ledger.record("places.text_search.enterprise")

            if res.status_code == 200:
                return res.json()
            if res.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise httpx.HTTPStatusError(
                f"Places returned {res.status_code}: {res.text[:300]}",
                request=res.request,
                response=res,
            )
        raise RuntimeError("unreachable")

    @staticmethod
    def _rectangle(cell: Cell) -> dict:
        dlat = cell.half_side_km / KM_PER_DEG_LAT
        dlng = cell.half_side_km / (
            KM_PER_DEG_LAT * math.cos(math.radians(cell.lat))
        )
        return {
            "rectangle": {
                "low": {"latitude": cell.lat - dlat, "longitude": cell.lng - dlng},
                "high": {"latitude": cell.lat + dlat, "longitude": cell.lng + dlng},
            }
        }

    def query_cell(
        self, cell: Cell, query: str, destination_id: str | None = None
    ) -> tuple[list[DiscoveredPlace], bool, int]:
        """Query one cell, paginating to the cap.

        Returns the places, whether the result set was truncated, and the number
        of API calls made.
        """
        collected: list[DiscoveredPlace] = []
        token: str | None = None
        calls = 0

        while True:
            body: dict = {
                "textQuery": query,
                "pageSize": PAGE_SIZE,
                "locationRestriction": self._rectangle(cell),
            }
            if token:
                body["pageToken"] = token

            data = self._post(body)
            calls += 1

            for raw in data.get("places", []):
                place = self._to_place(raw, query, destination_id)
                if place is not None:
                    collected.append(place)

            token = data.get("nextPageToken")
            if not token or len(collected) >= RESULT_CAP:
                break
            if self.pause_seconds:
                time.sleep(self.pause_seconds)

        truncated = len(collected) >= RESULT_CAP
        return collected, truncated, calls

    @staticmethod
    def _to_place(
        raw: dict, query: str, destination_id: str | None
    ) -> DiscoveredPlace | None:
        location = raw.get("location") or {}
        lat, lng = location.get("latitude"), location.get("longitude")
        if lat is None or lng is None:
            return None

        types = raw.get("types", [])
        if any(t in EXCLUDED_TYPES for t in types):
            return None
        if raw.get("businessStatus") == "CLOSED_PERMANENTLY":
            return None

        return DiscoveredPlace(
            source=Source.GOOGLE_PLACES,
            source_id=raw["id"],
            name=(raw.get("displayName") or {}).get("text", "").strip(),
            lat=lat,
            lng=lng,
            address=raw.get("formattedAddress"),
            phone=raw.get("internationalPhoneNumber"),
            website=raw.get("websiteUri"),
            rating=raw.get("rating"),
            review_count=raw.get("userRatingCount"),
            categories=types,
            destination_id=destination_id,
            raw={"matched_query": query, "business_status": raw.get("businessStatus")},
        )

    # ---------------------------------------------------------------- sweep

    def sweep(
        self,
        area: SearchArea,
        queries: list[str],
        initial_half_side_km: float = 3.0,
        max_depth: int = 3,
        max_cells: int = 400,
        destination_id: str | None = None,
        on_progress=None,
    ) -> SweepResult:
        result = SweepResult()
        seen: dict[str, DiscoveredPlace] = {}

        base_cells = cover(area, initial_half_side_km, max_cells=max_cells)
        pending: list[tuple[Cell, str]] = [
            (cell, q) for cell in base_cells for q in queries
        ]

        processed = 0
        while pending:
            cell, query = pending.pop(0)

            try:
                places, truncated, calls = self.query_cell(cell, query, destination_id)
            except Exception as exc:  # noqa: BLE001 - a bad cell must not kill a sweep
                result.errors.append(f"{query} @ {cell.lat:.4f},{cell.lng:.4f}: {exc}")
                log.warning("cell failed: %s", exc)
                continue

            result.cells_queried += 1
            result.api_calls += calls
            processed += 1

            for place in places:
                # Deduplicate across overlapping cells and across query terms.
                # The same operator legitimately answers to "boat tour" and
                # "island hopping", and to two adjacent cells.
                if place.source_id not in seen:
                    seen[place.source_id] = place

            if truncated:
                result.truncated_cells += 1
                if cell.depth < max_depth:
                    result.cells_subdivided += 1
                    pending.extend((child, query) for child in cell.subdivide())
                else:
                    result.unresolved_cells.append(
                        {
                            "lat": cell.lat,
                            "lng": cell.lng,
                            "half_side_km": cell.half_side_km,
                            "query": query,
                            "depth": cell.depth,
                        }
                    )

            if on_progress:
                on_progress(processed, len(pending), len(seen))

            if self.pause_seconds:
                time.sleep(self.pause_seconds)

        # Cells overlap slightly and the API biases beyond hard bounds, so trim
        # anything that fell outside what was actually asked for.
        result.places = [p for p in seen.values() if area.contains(p.lat, p.lng)]
        return result
