"""Permitted search regions.

The browser greys out unpermitted areas, but that is a courtesy. These tests
protect the API-side gate, which is the actual boundary.
"""

from supply_radar.geometry import SearchArea
from supply_radar.regions import check_area, check_point, enabled_regions, load_regions


class TestConfig:
    def test_croatia_is_open_and_serbia_is_not(self):
        by_id = {r.id: r for r in load_regions()}
        assert by_id["croatia"].enabled
        assert not by_id["serbia"].enabled

    def test_only_enabled_regions_are_searchable(self):
        assert [r.id for r in enabled_regions()] == ["croatia"]


class TestPoints:
    def test_croatian_destinations_are_inside(self):
        for name, lat, lng in [
            ("Split", 43.5081, 16.4402),
            ("Dubrovnik", 42.6507, 18.0944),
            ("Zagreb", 45.8150, 15.9819),
            ("Pula", 44.8666, 13.8496),
            ("Rovinj", 45.0811, 13.6387),
            ("Poreč", 45.2269, 13.5959),
            ("Hvar town", 43.1729, 16.4413),
            ("Osijek", 45.5550, 18.6955),
            ("Rijeka", 45.3271, 14.4422),
            ("Zadar", 44.1194, 15.2314),
            ("Šibenik", 43.7350, 15.8952),
        ]:
            ok, region = check_point(lat, lng)
            assert ok, f"{name} should be inside Croatia"
            assert region.id == "croatia"

    def test_neighbouring_capitals_are_outside(self):
        """The boundary has to be tight enough on land that a user cannot
        quietly sweep a market nobody has opened."""
        for name, lat, lng in [
            ("Sarajevo", 43.8563, 18.4131),
            ("Belgrade", 44.7866, 20.4489),
            ("Ljubljana", 46.0569, 14.5058),
            ("Venice", 45.4408, 12.3155),
            ("Budapest", 47.4979, 19.0402),
        ]:
            ok, _ = check_point(lat, lng)
            assert not ok, f"{name} must not be searchable"


class TestAreas:
    def test_a_sweep_over_split_is_permitted(self):
        ok, region, _ = check_area(SearchArea.from_circle(43.5081, 16.4402, 8))
        assert ok and region.id == "croatia"

    def test_a_sweep_over_sarajevo_is_refused(self):
        ok, _, why = check_area(SearchArea.from_circle(43.8563, 18.4131, 8))
        assert not ok
        assert "Croatia" in why

    def test_an_area_straddling_the_border_is_refused(self):
        """Partial overlap must fail. A half-in sweep spends budget on a market
        nobody opened, and the operators it finds cannot be actioned."""
        ok, _, why = check_area(SearchArea.from_circle(43.05, 17.55, 45))
        assert not ok
        assert "entirely" in why

    def test_the_refusal_names_what_is_open(self):
        ok, _, why = check_area(SearchArea.from_circle(48.8566, 2.3522, 5))
        assert not ok
        assert "Croatia" in why

    def test_offshore_islands_are_inside_the_boundary(self):
        """The seaward edge is deliberately generous: excluding Vis or Lastovo
        would silently remove real operators from every sweep."""
        for name, lat, lng in [
            ("Vis", 43.0617, 16.1836),
            ("Lastovo", 42.7683, 16.9008),
            ("Korčula", 42.9601, 17.1358),
        ]:
            ok, _ = check_point(lat, lng)
            assert ok, f"{name} should be searchable"
