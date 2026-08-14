"""Matching tests.

Two things are being protected here. First, that a decisive signal decides,
regardless of how bad the other fields look. Second, that blocking never loses
a match that a brute force pass would have found, because a blocking bug is
silent and would quietly depress recall forever.
"""

import pytest

from supply_radar.locales import load_locale
from supply_radar.matching import (
    MatchIndex,
    MatchThresholds,
    brute_force_best,
    match_all,
    match_place,
    score_pair,
)
from supply_radar.models import DecidedBy, DiscoveredPlace, MatchVerdict, Source, SupplierRecord
from supply_radar.synth import expected_verdicts, generate_supplier_list

HR = load_locale("hr")


def place(**kw) -> DiscoveredPlace:
    base = dict(
        source=Source.GOOGLE_PLACES,
        source_id="place_x",
        name="Adriatic Kayak Adventures",
        lat=43.5081,
        lng=16.4402,
        address="Ulica kneza Domagoja 12, Split",
        phone="+38521345678",
        website="https://www.adriatickayak.hr",
    )
    base.update(kw)
    return DiscoveredPlace(**base)


def supplier(**kw) -> SupplierRecord:
    base = dict(
        supplier_id="VS-00001",
        legal_name="Adriatic Kayak Adventures d.o.o.",
        address="ul. kneza Domagoja 12, Split",
        city="split",
        lat=43.5082,
        lng=16.4403,
        phone="+38521345678",
        website="https://www.adriatickayak.hr",
    )
    base.update(kw)
    return SupplierRecord(**base)


def _index(suppliers):
    return MatchIndex(suppliers, HR)


class TestHardKeys:
    def test_matching_domain_decides_despite_a_completely_different_name(self):
        s = supplier(legal_name="Marić d.o.o.", phone=None)
        res = match_place(place(), _index([s]), HR)
        assert res.verdict is MatchVerdict.EXISTING
        assert res.decided_by is DecidedBy.HARD_KEY
        assert "domain" in res.evidence[0].signal

    def test_matching_phone_decides_when_domains_are_absent(self):
        s = supplier(legal_name="Kovačević obrt", website=None)
        res = match_place(place(website=None), _index([s]), HR)
        assert res.verdict is MatchVerdict.EXISTING
        assert res.decided_by is DecidedBy.HARD_KEY

    def test_phone_matches_across_formatting_differences(self):
        s = supplier(legal_name="Something Else", website=None, phone="021/345-678")
        res = match_place(place(website=None), _index([s]), HR)
        assert res.verdict is MatchVerdict.EXISTING

    def test_identical_name_at_same_premises_decides(self):
        s = supplier(website=None, phone=None, legal_name="Adriatic Kayak Adventures")
        res = match_place(place(website=None, phone=None), _index([s]), HR)
        assert res.verdict is MatchVerdict.EXISTING
        assert res.decided_by is DecidedBy.HARD_KEY

    def test_identical_name_far_away_is_not_a_hard_key_match(self):
        """Two operators can legitimately share a name in different towns."""
        s = supplier(
            website=None, phone=None,
            legal_name="Adriatic Kayak Adventures",
            lat=45.8150, lng=15.9819,  # Zagreb
        )
        res = match_place(place(website=None, phone=None), _index([s]), HR)
        assert res.decided_by is not DecidedBy.HARD_KEY

    def test_a_name_that_normalises_to_nothing_never_hard_key_matches(self):
        s = supplier(website=None, phone=None, legal_name="Tours Travel Agency")
        p = place(website=None, phone=None, name="Travel Agency Tours")
        res = match_place(p, _index([s]), HR)
        assert res.decided_by is not DecidedBy.HARD_KEY


class TestNetNew:
    def test_no_candidates_at_all_is_confidently_net_new(self):
        s = supplier(
            supplier_id="VS-09999",
            legal_name="Bura Diving Centre",
            website="https://www.buradiving.hr",
            phone="+38520111222",
            lat=42.65, lng=18.09,
            address="Obala 1, Dubrovnik",
        )
        p = place(name="Zagreb Street Food Walk", lat=45.815, lng=15.982,
                  website="https://www.zgfood.hr", phone="+3851999888",
                  address="Trg Republike 4, Zagreb")
        res = match_place(p, _index([s]), HR)
        assert res.verdict is MatchVerdict.NET_NEW
        assert res.confidence > 0.9

    def test_a_confusable_but_different_business_is_not_matched(self):
        """Similar name, same town, but different domain and phone. This is the
        precision case the hard negatives in the synthetic set represent."""
        s = supplier(
            legal_name="Adriatic Kayak Rent d.o.o.",
            website="https://www.adriatickayakrent.hr",
            phone="+38521999111",
            address="Poljička cesta 40, Split",
            lat=43.5120, lng=16.4450,
        )
        res = match_place(place(), _index([s]), HR)
        assert res.verdict is not MatchVerdict.EXISTING


class TestScoring:
    def test_conflicting_domains_reduce_the_score(self):
        agreeing = supplier(website="https://www.adriatickayak.hr", phone=None)
        conflicting = supplier(website="https://www.totallyother.hr", phone=None)
        idx = _index([agreeing])
        score_a, _ = score_pair(place(), idx.keys["VS-00001"], HR)
        idx2 = _index([conflicting])
        score_b, _ = score_pair(place(), idx2.keys["VS-00001"], HR)
        assert score_b < score_a

    def test_evidence_is_populated_for_a_human_to_read(self):
        s = supplier(website=None, phone=None, legal_name="Adriatik Kayak Adventure")
        res = match_place(place(website=None, phone=None), _index([s]), HR)
        signals = {e.signal for e in res.evidence}
        assert "name" in signals
        assert "location" in signals


class TestThresholds:
    def test_band_boundaries_behave_as_configured(self):
        t = MatchThresholds(high=0.8, low=0.4)
        assert t.band(0.9) is MatchVerdict.EXISTING
        assert t.band(0.8) is MatchVerdict.EXISTING
        assert t.band(0.6) is MatchVerdict.NEEDS_REVIEW
        assert t.band(0.4) is MatchVerdict.NET_NEW
        assert t.band(0.1) is MatchVerdict.NET_NEW

    def test_widening_the_review_band_moves_decisions_into_it(self, places):
        """The review band is the gap between low and high, so (0.95, 0.10)
        sends almost everything to a human and (0.70, 0.60) almost nothing.
        This is the dial that trades human effort against automated risk."""
        suppliers, _ = generate_supplier_list(places, seed=42)
        wide = match_all(places, suppliers, HR, MatchThresholds(high=0.95, low=0.10))
        narrow = match_all(places, suppliers, HR, MatchThresholds(high=0.70, low=0.60))
        n_wide = sum(1 for r in wide if r.verdict is MatchVerdict.NEEDS_REVIEW)
        n_narrow = sum(1 for r in narrow if r.verdict is MatchVerdict.NEEDS_REVIEW)
        assert n_wide >= n_narrow


class TestBlockingPreservesRecall:
    def test_blocking_finds_what_brute_force_finds(self, places):
        """A blocking bug is invisible: it just quietly lowers recall. So the
        blocked candidate set is checked against an exhaustive pass."""
        suppliers, _ = generate_supplier_list(places, seed=42)
        index = MatchIndex(suppliers, HR)

        for p in places:
            bf_score, bf_id = brute_force_best(p, suppliers, HR)
            if bf_score < 0.5:
                continue  # nothing worth finding
            candidate_ids = {k.supplier.supplier_id for k in index.candidates(p)}
            assert bf_id in candidate_ids, (
                f"blocking lost supplier {bf_id} for {p.source_id} "
                f"at brute force score {bf_score:.2f}"
            )


class TestAgainstGroundTruth:
    def test_recall_on_the_synthetic_set_is_credible(self, places):
        suppliers, truth = generate_supplier_list(places, seed=42)
        answer = expected_verdicts(truth)
        results = match_all(places, suppliers, HR)

        seeded = [r for r in results if r.place_source_id in answer]
        found = [
            r for r in seeded
            if r.verdict is MatchVerdict.EXISTING
            and r.supplier_id == answer[r.place_source_id]
        ]
        recall = len(found) / len(seeded)
        assert recall > 0.5, f"recall {recall:.0%} is too low to be credible"

    def test_no_net_new_lead_is_actually_an_existing_supplier(self, places):
        """The expensive error. A place wrongly declared net-new wastes a call;
        a place wrongly declared existing is never contacted again."""
        suppliers, truth = generate_supplier_list(places, seed=42)
        answer = expected_verdicts(truth)
        results = match_all(places, suppliers, HR)

        wrongly_existing = [
            r for r in results
            if r.verdict is MatchVerdict.EXISTING
            and r.place_source_id not in answer
        ]
        assert not wrongly_existing, (
            f"{len(wrongly_existing)} net-new operators were wrongly written off "
            f"as existing suppliers"
        )
