"""Cell subdivision tests.

The property that actually matters is coverage: every point of the requested
area must fall inside at least one query circle. If that fails, the sweep has a
hole in it and nobody would ever know, which is the exact failure this module
exists to prevent.
"""

import math

import pytest

from supply_radar.geometry import (
    Cell,
    SearchArea,
    cover,
    haversine_km,
)

SPLIT = (43.5081, 16.4402)


class TestHaversine:
    def test_zero_distance_for_identical_points(self):
        assert haversine_km(SPLIT, SPLIT) == pytest.approx(0.0, abs=1e-9)

    def test_known_distance_split_to_dubrovnik(self):
        # Straight-line Split to Dubrovnik is about 157 km.
        d = haversine_km(SPLIT, (42.6507, 18.0944))
        assert 150 < d < 165


class TestCell:
    def test_query_radius_covers_the_whole_square(self):
        """A cell is a square but the API only takes circles, so the circle must
        reach the corners or the corners go unsearched."""
        cell = Cell(lat=SPLIT[0], lng=SPLIT[1], half_side_km=5.0, depth=0)
        for corner in cell.corners():
            assert haversine_km((cell.lat, cell.lng), corner) <= cell.radius_km + 1e-6

    def test_radius_is_not_wastefully_larger_than_needed(self):
        cell = Cell(lat=SPLIT[0], lng=SPLIT[1], half_side_km=5.0, depth=0)
        furthest = max(
            haversine_km((cell.lat, cell.lng), c) for c in cell.corners()
        )
        # Within 2% of the tightest circle that still covers the square.
        assert cell.radius_km == pytest.approx(furthest, rel=0.02)

    def test_subdivide_yields_four_children_at_half_the_size(self):
        parent = Cell(lat=SPLIT[0], lng=SPLIT[1], half_side_km=8.0, depth=1)
        children = parent.subdivide()
        assert len(children) == 4
        assert all(c.half_side_km == 4.0 for c in children)
        assert all(c.depth == 2 for c in children)

    def test_children_cover_the_parent_square(self):
        parent = Cell(lat=SPLIT[0], lng=SPLIT[1], half_side_km=8.0, depth=0)
        children = parent.subdivide()
        # Every corner of the parent must sit inside some child's circle.
        for corner in parent.corners():
            assert any(
                haversine_km((c.lat, c.lng), corner) <= c.radius_km + 1e-6
                for c in children
            )


class TestCover:
    def test_circle_area_is_fully_covered_by_its_cells(self):
        area = SearchArea.from_circle(SPLIT[0], SPLIT[1], radius_km=10)
        cells = cover(area, initial_half_side_km=3.0)
        assert cells

        # Sample points across the requested circle; each must be reachable.
        for bearing in range(0, 360, 30):
            for frac in (0.2, 0.6, 0.99):
                point = _offset(SPLIT, 10 * frac, bearing)
                assert any(
                    haversine_km((c.lat, c.lng), point) <= c.radius_km + 1e-6
                    for c in cells
                ), f"gap at bearing {bearing} fraction {frac}"

    def test_cells_outside_the_area_are_discarded(self):
        area = SearchArea.from_circle(SPLIT[0], SPLIT[1], radius_km=5)
        cells = cover(area, initial_half_side_km=2.0)
        # No cell should be so far away that it cannot possibly intersect.
        for c in cells:
            assert haversine_km((c.lat, c.lng), SPLIT) < 5 + c.radius_km + 1

    def test_smaller_cells_produce_more_of_them(self):
        area = SearchArea.from_circle(SPLIT[0], SPLIT[1], radius_km=10)
        coarse = cover(area, initial_half_side_km=5.0)
        fine = cover(area, initial_half_side_km=2.5)
        assert len(fine) > len(coarse)

    def test_polygon_area_is_supported(self):
        # A rough triangle around Split.
        poly = [(43.60, 16.35), (43.60, 16.55), (43.45, 16.45)]
        area = SearchArea.from_polygon(poly)
        cells = cover(area, initial_half_side_km=2.0)
        assert cells
        assert area.contains(43.55, 16.45)
        assert not area.contains(43.20, 16.45)

    def test_area_km2_is_sane_for_a_known_circle(self):
        area = SearchArea.from_circle(SPLIT[0], SPLIT[1], radius_km=10)
        # pi * 10^2 = 314 km2
        assert area.area_km2 == pytest.approx(314, rel=0.05)


def _offset(origin, distance_km, bearing_deg):
    """Move a lat/lng by a distance and bearing, flat-earth approximation."""
    lat, lng = origin
    rad = math.radians(bearing_deg)
    dlat = (distance_km * math.cos(rad)) / 111.32
    dlng = (distance_km * math.sin(rad)) / (111.32 * math.cos(math.radians(lat)))
    return (lat + dlat, lng + dlng)
