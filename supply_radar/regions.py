"""Permitted search regions.

Supply expansion is a commercial decision before it is a technical one, so a
user can only sweep a market the business has actually opened. Enabling one is
a config change, not a code change.

Enforced in two places deliberately. The browser greys out everything outside a
permitted region so the control is visible rather than a surprise, and the API
re-checks every request because a client-side gate is a courtesy, not a
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import yaml
from shapely.geometry import Point, Polygon

from supply_radar.config import CONFIG_DIR
from supply_radar.geometry import SearchArea


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    enabled: bool
    polygon_latlng: tuple[tuple[float, float], ...]
    note: str | None = None

    @property
    def shape(self) -> Polygon:
        # Shapely works in (x, y), so longitude first.
        return Polygon([(lng, lat) for lat, lng in self.polygon_latlng])


@lru_cache
def load_regions() -> tuple[Region, ...]:
    path = CONFIG_DIR / "regions" / "permitted.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return tuple(
        Region(
            id=r["id"],
            name=r["name"],
            enabled=r.get("status") == "enabled",
            polygon_latlng=tuple((p[0], p[1]) for p in r["polygon"]),
            note=(r.get("note") or "").strip() or None,
        )
        for r in raw["regions"]
    )


def enabled_regions() -> list[Region]:
    return [r for r in load_regions() if r.enabled]


def check_area(area: SearchArea) -> tuple[bool, Region | None, str]:
    """Is this search area inside a permitted region?

    Requires the area to be FULLY contained, not merely to overlap. A sweep
    half in Bosnia would otherwise spend budget on a market nobody has opened,
    and the operators it found could not be actioned by anyone.
    """
    regions = enabled_regions()
    if not regions:
        return False, None, "No search regions are currently enabled."

    # Convert the area's local kilometre polygon back to lat/lng.
    coords = [area.from_km(x, y) for x, y in area.polygon_km.exterior.coords]
    requested = Polygon([(lng, lat) for lat, lng in coords])

    for region in regions:
        if region.shape.contains(requested):
            return True, region, f"Inside {region.name}."

    # Give a useful message rather than a bare refusal.
    for region in regions:
        if region.shape.intersects(requested):
            return (
                False,
                region,
                f"This area extends beyond {region.name}. Searches must sit "
                f"entirely within an enabled market. Move or shrink the area.",
            )

    names = ", ".join(r.name for r in regions)
    return (
        False,
        None,
        f"This area is outside every enabled market. Currently enabled: {names}.",
    )


def check_point(lat: float, lng: float) -> tuple[bool, Region | None]:
    p = Point(lng, lat)
    for region in enabled_regions():
        if region.shape.contains(p):
            return True, region
    return False, None


def as_geojson() -> dict:
    """Permitted regions for the browser, so it can grey out everywhere else."""
    return {
        "enabled": [
            {
                "id": r.id,
                "name": r.name,
                "note": r.note,
                # Leaflet wants [lat, lng] rings.
                "polygon": [[lat, lng] for lat, lng in r.polygon_latlng],
            }
            for r in load_regions()
            if r.enabled
        ],
        "disabled": [
            {"id": r.id, "name": r.name, "note": r.note}
            for r in load_regions()
            if not r.enabled
        ],
    }
