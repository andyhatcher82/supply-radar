"""Search-area geometry and adaptive cell subdivision.

The Places API takes a circle and returns at most a fixed number of results per
query. It does not tell you when it truncated. So a single circle over a dense
city centre silently loses suppliers, and a naive sweep looks like it worked.

This module covers a requested area with square cells, each queried as the
circle that circumscribes it. Any cell that comes back at the result cap is
subdivided into four and re-queried, on the assumption that a full page means
there was more behind it.

All internal maths happens in a local kilometre plane centred on the area,
using an equirectangular approximation. Over a single country that is accurate
to well under a percent, and it keeps the code readable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from shapely.geometry import Point, Polygon, box

KM_PER_DEG_LAT = 111.32
EARTH_RADIUS_KM = 6371.0088

# The circumscribing circle is enlarged very slightly. The cell corners are
# derived in a flat plane while the API works on a sphere, and without a margin
# that mismatch leaves metre-scale slivers of a cell unsearched.
COVERAGE_MARGIN = 1.002


class AreaTooLargeError(Exception):
    """Raised when a requested sweep would exceed the configured cell budget."""


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def _km_per_deg_lng(lat: float) -> float:
    return KM_PER_DEG_LAT * math.cos(math.radians(lat))


@dataclass(frozen=True)
class Cell:
    """A square patch of the search area, queried as its circumscribing circle."""

    lat: float
    lng: float
    half_side_km: float
    depth: int

    @property
    def radius_km(self) -> float:
        return self.half_side_km * math.sqrt(2) * COVERAGE_MARGIN

    def corners(self) -> list[tuple[float, float]]:
        dlat = self.half_side_km / KM_PER_DEG_LAT
        dlng = self.half_side_km / _km_per_deg_lng(self.lat)
        return [
            (self.lat + dlat, self.lng + dlng),
            (self.lat + dlat, self.lng - dlng),
            (self.lat - dlat, self.lng + dlng),
            (self.lat - dlat, self.lng - dlng),
        ]

    def subdivide(self) -> list[Cell]:
        """Split into four quadrants. Used when a query came back at the cap."""
        child_half = self.half_side_km / 2
        dlat = child_half / KM_PER_DEG_LAT
        dlng = child_half / _km_per_deg_lng(self.lat)
        return [
            Cell(self.lat + dlat, self.lng + dlng, child_half, self.depth + 1),
            Cell(self.lat + dlat, self.lng - dlng, child_half, self.depth + 1),
            Cell(self.lat - dlat, self.lng + dlng, child_half, self.depth + 1),
            Cell(self.lat - dlat, self.lng - dlng, child_half, self.depth + 1),
        ]


class SearchArea:
    """A requested search area, held as a polygon in a local kilometre plane.

    Circle and polygon requests collapse to the same representation here, which
    is why supporting both costs almost nothing.
    """

    def __init__(self, polygon_km: Polygon, ref_lat: float, ref_lng: float):
        self.polygon_km = polygon_km
        self.ref_lat = ref_lat
        self.ref_lng = ref_lng

    @classmethod
    def from_circle(cls, lat: float, lng: float, radius_km: float) -> SearchArea:
        return cls(Point(0.0, 0.0).buffer(radius_km, quad_segs=64), lat, lng)

    @classmethod
    def from_polygon(cls, points: list[tuple[float, float]]) -> SearchArea:
        if len(points) < 3:
            raise ValueError("A polygon needs at least three points")
        ref_lat = sum(p[0] for p in points) / len(points)
        ref_lng = sum(p[1] for p in points) / len(points)
        area = cls(Polygon([(0, 0), (1, 0), (0, 1)]), ref_lat, ref_lng)
        area.polygon_km = Polygon([area.to_km(lat, lng) for lat, lng in points])
        return area

    def to_km(self, lat: float, lng: float) -> tuple[float, float]:
        x = (lng - self.ref_lng) * _km_per_deg_lng(self.ref_lat)
        y = (lat - self.ref_lat) * KM_PER_DEG_LAT
        return x, y

    def from_km(self, x: float, y: float) -> tuple[float, float]:
        lat = self.ref_lat + y / KM_PER_DEG_LAT
        lng = self.ref_lng + x / _km_per_deg_lng(self.ref_lat)
        return lat, lng

    def contains(self, lat: float, lng: float) -> bool:
        return self.polygon_km.contains(Point(*self.to_km(lat, lng)))

    @property
    def area_km2(self) -> float:
        return self.polygon_km.area


def cover(
    area: SearchArea,
    initial_half_side_km: float,
    max_cells: int | None = None,
) -> list[Cell]:
    """Tile the area with cells, discarding any that do not touch it.

    Cells are contiguous by construction, so every point of the area falls
    inside at least one cell and therefore inside at least one query circle.
    """
    if initial_half_side_km <= 0:
        raise ValueError("Cell size must be positive")

    minx, miny, maxx, maxy = area.polygon_km.bounds
    step = 2 * initial_half_side_km

    cells: list[Cell] = []
    y = miny + initial_half_side_km
    while y - initial_half_side_km < maxy:
        x = minx + initial_half_side_km
        while x - initial_half_side_km < maxx:
            square = box(
                x - initial_half_side_km,
                y - initial_half_side_km,
                x + initial_half_side_km,
                y + initial_half_side_km,
            )
            if square.intersects(area.polygon_km):
                lat, lng = area.from_km(x, y)
                cells.append(Cell(lat, lng, initial_half_side_km, depth=0))
                if max_cells is not None and len(cells) > max_cells:
                    raise AreaTooLargeError(
                        f"Area needs more than {max_cells} cells at "
                        f"{initial_half_side_km} km. Draw a smaller area or "
                        f"use a coarser sweep."
                    )
            x += step
        y += step

    return cells
