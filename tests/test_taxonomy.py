"""Viator taxonomy loading and mapping."""

from supply_radar.taxonomy import (
    CATEGORY_MAP,
    breadcrumb,
    coverage,
    is_valid,
    label,
    load_taxonomy,
    tier1,
    top_level,
    unmapped_categories,
)


class TestLoading:
    def test_the_eight_real_top_level_categories_load(self):
        names = {n.name for n in tier1()}
        assert names == {
            "Art & Culture",
            "Classes & Workshops",
            "Food & Drink",
            "Outdoor Activities",
            "Seasonal & Special Occasions",
            "Tickets & Passes",
            "Tours, Sightseeing & Cruises",
            "Travel & Transportation Services",
        }

    def test_the_merchandising_badge_is_not_a_category(self):
        """'Likely To Sell Out' sits alongside these in the filter UI, but it
        describes how a product is selling rather than what it is. As a category
        it would appear in the gap analysis and mean nothing."""
        assert not is_valid("Likely To Sell Out")

    def test_all_three_tiers_are_present(self):
        nodes = load_taxonomy()
        assert nodes["Outdoor Activities"].tier == 1
        assert nodes["Outdoor Activities/On the Water"].tier == 2
        assert nodes["Outdoor Activities/On the Water/Sailing"].tier == 3

    def test_children_are_linked_to_their_parent(self):
        water = load_taxonomy()["Outdoor Activities/On the Water"]
        assert "Outdoor Activities/On the Water/Kayaking Tours" in water.children
        assert len(water.children) > 20

    def test_a_node_repeated_under_two_parents_stays_distinct(self):
        """Viator lists Museums under both Arts & Design and Culture. That is
        their structure and it is preserved rather than deduplicated away."""
        assert is_valid("Art & Culture/Arts & Design/Museums")
        assert is_valid("Art & Culture/Culture/Museums")


class TestMapping:
    def test_every_mapped_path_exists_in_the_taxonomy(self):
        """A typo here would silently produce leads filed under a category
        Viator does not have."""
        for category, paths in CATEGORY_MAP.items():
            for path in paths:
                assert is_valid(path), f"{category} maps to unknown path {path!r}"

    def test_internal_categories_render_in_viator_wording(self):
        assert label("boat_tour") == "Cruises & Sailing"
        assert label("walking_tour") == "Walking Tours"
        assert label("day_trip") == "Day Trips"

    def test_breadcrumb_is_what_a_specialist_would_recognise(self):
        assert breadcrumb("water_sports") == "Outdoor Activities / On the Water"
        assert top_level("food_drink") == "Food & Drink"

    def test_nothing_is_left_unmapped(self):
        assert unmapped_categories() == []

    def test_coverage_is_reported_honestly(self):
        c = coverage()
        assert c["tier1"] == 8
        assert c["total_nodes"] > 150
        # The build targets a slice of the catalogue, and says which slice.
        assert c["tier1_not_covered"]
