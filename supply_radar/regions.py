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

import math
from dataclasses import dataclass
from functools import lru_cache

import yaml
from shapely.geometry import Point, Polygon

from supply_radar.config import CONFIG_DIR
from supply_radar.geometry import SearchArea


KM_PER_DEG_LAT = 111.32


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    enabled: bool
    polygon_latlng: tuple[tuple[float, float], ...]
    tolerance_km: float = 0.0
    note: str | None = None

    @property
    def shape(self) -> Polygon:
        # Shapely works in (x, y), so longitude first.
        return Polygon([(lng, lat) for lat, lng in self.polygon_latlng])

    @property
    def tolerant_shape(self) -> Polygon:
        """The boundary with the tolerance applied outwards.

        Experiences do not stop at a border. A rafting operator on the Una or a
        wine tour in the hills behind Umag is legitimately Croatian supply, and
        losing them to a simplified outline is a worse error than occasionally
        catching one a few kilometres the wrong side.

        Buffering happens in a local kilometre plane rather than in degrees,
        because a degree of longitude is only about 78 km at this latitude
        against 111 km for a degree of latitude. Buffering in raw degrees would
        stretch the tolerance north-south and pinch it east-west.
        """
        if not self.tolerance_km:
            return self.shape

        lats = [lat for lat, _ in self.polygon_latlng]
        ref_lat = sum(lats) / len(lats)
        km_per_deg_lng = KM_PER_DEG_LAT * math.cos(math.radians(ref_lat))

        in_km = Polygon(
            [(lng * km_per_deg_lng, lat * KM_PER_DEG_LAT)
             for lat, lng in self.polygon_latlng]
        ).buffer(self.tolerance_km, quad_segs=8)

        return Polygon(
            [(x / km_per_deg_lng, y / KM_PER_DEG_LAT)
             for x, y in in_km.exterior.coords]
        )

    @property
    def display_polygon(self) -> list[list[float]]:
        """What the map shows: the tolerant boundary, because that is what is
        actually searchable. Showing the tight outline while accepting a wider
        one would be a UI that lies."""
        return [[lat, lng] for lng, lat in self.tolerant_shape.exterior.coords]


@lru_cache
def load_regions() -> tuple[Region, ...]:
    path = CONFIG_DIR / "regions" / "permitted.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    tolerance = float(raw.get("tolerance_km", 0))
    return tuple(
        Region(
            id=r["id"],
            name=r["name"],
            enabled=r.get("status") == "enabled",
            polygon_latlng=tuple((p[0], p[1]) for p in r["polygon"]),
            tolerance_km=tolerance,
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
        if region.tolerant_shape.contains(requested):
            return True, region, f"Inside {region.name}."

    # Give a useful message rather than a bare refusal.
    for region in regions:
        if region.tolerant_shape.intersects(requested):
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
        if region.tolerant_shape.contains(p):
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
                "polygon": r.display_polygon,
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

