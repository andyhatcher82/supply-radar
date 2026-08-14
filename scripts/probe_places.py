"""One-off probe of the Places API.

Confirms the request shape, what a real Croatian result looks like, and above
all where the result cap actually bites, because the whole subdivision design
rests on that number.

    python scripts/probe_places.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.config import get_settings  # noqa: E402

SEARCH_TEXT = "https://places.googleapis.com/v1/places:searchText"

# Field masks drive the billing SKU on Places API (New), so asking for less
# genuinely costs less. This is the set the pipeline actually uses.
FIELDS = ",".join(
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


def search(client: httpx.Client, key: str, query: str, lat: float, lng: float,
           radius_m: float, page_token: str | None = None) -> dict:
    body: dict = {
        "textQuery": query,
        "pageSize": 20,
        "locationBias": {
            "circle": {
                "center": {"latitude": lat, "longitude": lng},
                "radius": radius_m,
            }
        },
    }
    if page_token:
        body["pageToken"] = page_token

    res = client.post(
        SEARCH_TEXT,
        headers={
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": FIELDS,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30.0,
    )
    if res.status_code != 200:
        print(f"HTTP {res.status_code}: {res.text[:600]}")
        res.raise_for_status()
    return res.json()


def main() -> None:
    settings = get_settings()
    if not settings.google_maps_api_key:
        raise SystemExit("GOOGLE_MAPS_API_KEY missing from .env")

    key = settings.google_maps_api_key
    calls = 0

    with httpx.Client() as client:
        data = search(client, key, "boat tour", 43.5081, 16.4402, 8000)
        calls += 1
        places = data.get("places", [])
        print(f"first page: {len(places)} results, "
              f"nextPageToken present: {bool(data.get('nextPageToken'))}")
        print()

        print("sample record")
        if places:
            print(json.dumps(places[0], indent=2, ensure_ascii=False))
        print()

        # How deep does pagination actually go? The subdivision trigger depends
        # on knowing the real ceiling rather than assuming it.
        total = len(places)
        token = data.get("nextPageToken")
        pages = 1
        while token and pages < 6:
            data = search(client, key, "boat tour", 43.5081, 16.4402, 8000,
                          page_token=token)
            calls += 1
            got = len(data.get("places", []))
            total += got
            pages += 1
            token = data.get("nextPageToken")
            print(f"page {pages}: {got} results, more: {bool(token)}")

        print()
        print(f"total across {pages} pages: {total}")
        print(f"api calls made: {calls}")

        # Field coverage matters for the readiness score, so measure how much
        # of it is actually populated in the wild rather than assuming.
        all_places = places
        have_site = sum(1 for p in all_places if p.get("websiteUri"))
        have_phone = sum(1 for p in all_places if p.get("internationalPhoneNumber"))
        have_rating = sum(1 for p in all_places if p.get("rating"))
        n = len(all_places) or 1
        print()
        print("field coverage on first page")
        print(f"  website  {have_site}/{n}")
        print(f"  phone    {have_phone}/{n}")
        print(f"  rating   {have_rating}/{n}")

        types: dict[str, int] = {}
        for p in all_places:
            for t in p.get("types", []):
                types[t] = types.get(t, 0) + 1
        print()
        print("types returned")
        for t, c in sorted(types.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {t:<34} {c}")


if __name__ == "__main__":
    main()
