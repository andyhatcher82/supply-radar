"""Tests for the synthetic supplier list.

The generator underwrites every precision and recall number the project
reports, so its properties are tested directly. If the answer key is wrong, the
headline metric is wrong and nobody would be able to tell.
"""

from supply_radar.synth import expected_verdicts, generate_supplier_list


class TestDeterminism:
    def test_same_seed_produces_identical_output(self, places):
        a, ta = generate_supplier_list(places, seed=42)
        b, tb = generate_supplier_list(places, seed=42)
        assert [s.model_dump(exclude={"onboarded_at"}) for s in a] == [
            s.model_dump(exclude={"onboarded_at"}) for s in b
        ]
        assert [g.model_dump() for g in ta] == [g.model_dump() for g in tb]

    def test_different_seed_produces_different_output(self, places):
        a, _ = generate_supplier_list(places, seed=1)
        b, _ = generate_supplier_list(places, seed=2)
        assert [s.legal_name for s in a] != [s.legal_name for s in b]

    def test_input_order_does_not_change_output(self, places):
        a, _ = generate_supplier_list(places, seed=42)
        b, _ = generate_supplier_list(list(reversed(places)), seed=42)
        assert [s.legal_name for s in a] == [s.legal_name for s in b]


class TestComposition:
    def test_has_all_three_parts(self, places):
        _, truth = generate_supplier_list(places, seed=42)
        seeded = [g for g in truth if g.place_source_id is not None]
        phantoms = [g for g in truth if g.note and g.note.startswith("phantom")]
        hard = [g for g in truth if g.note and g.note.startswith("hard negative")]
        assert seeded and phantoms and hard

    def test_seeded_proportion_is_roughly_as_requested(self, places):
        _, truth = generate_supplier_list(places, seed=42, seeded_fraction=0.4)
        seeded = [g for g in truth if g.place_source_id is not None]
        assert len(seeded) == int(len(places) * 0.4)

    def test_every_seeded_entry_points_at_a_real_place(self, places):
        _, truth = generate_supplier_list(places, seed=42)
        ids = {p.source_id for p in places}
        for g in truth:
            if g.place_source_id is not None:
                assert g.place_source_id in ids

    def test_hard_negatives_are_never_drawn_from_seeded_places(self, places):
        """The precision test only means something if a hard negative cannot
        legitimately match anything. If one were derived from a seeded operator,
        matching it would be correct, not a false positive."""
        _, truth = generate_supplier_list(places, seed=42)
        seeded_place_ids = {
            g.place_source_id for g in truth if g.place_source_id is not None
        }
        for g in truth:
            if g.note and g.note.startswith("hard negative"):
                referenced = g.note.split("confusable with ")[1].split(",")[0]
                assert referenced not in seeded_place_ids

    def test_supplier_ids_are_unique(self, places):
        suppliers, _ = generate_supplier_list(places, seed=42)
        ids = [s.supplier_id for s in suppliers]
        assert len(ids) == len(set(ids))

    def test_every_supplier_has_a_truth_entry(self, places):
        suppliers, truth = generate_supplier_list(places, seed=42)
        assert {s.supplier_id for s in suppliers} == {g.supplier_id for g in truth}


class TestCorruption:
    def test_records_are_actually_degraded(self, places):
        _, truth = generate_supplier_list(places, seed=42)
        seeded = [g for g in truth if g.place_source_id is not None]
        assert any(g.corruptions for g in seeded)

    def test_a_broad_range_of_corruption_types_appears(self, places):
        _, truth = generate_supplier_list(places, seed=42)
        seen = {c for g in truth for c in g.corruptions}
        # Enough variety that the matcher is exercised across signal types
        # rather than only on names.
        assert len(seen) >= 6

    def test_seeded_names_are_not_all_identical_to_source(self, places):
        suppliers, truth = generate_supplier_list(places, seed=42)
        by_id = {s.supplier_id: s for s in suppliers}
        source_names = {p.source_id: p.name for p in places}
        changed = sum(
            1
            for g in truth
            if g.place_source_id
            and by_id[g.supplier_id].legal_name != source_names[g.place_source_id]
        )
        assert changed > 0


class TestExpectedVerdicts:
    def test_maps_only_the_seeded_records(self, places):
        _, truth = generate_supplier_list(places, seed=42)
        mapping = expected_verdicts(truth)
        seeded = [g for g in truth if g.place_source_id is not None]
        assert len(mapping) == len(seeded)
        assert all(v.startswith("VS-") for v in mapping.values())

    def test_unmapped_places_are_the_net_new_ones(self, places):
        _, truth = generate_supplier_list(places, seed=42)
        mapping = expected_verdicts(truth)
        net_new = [p for p in places if p.source_id not in mapping]
        assert net_new  # there must be something to find, or the demo is pointless
