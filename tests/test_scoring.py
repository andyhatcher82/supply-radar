"""Scoring tests.

The two counterweights are the point of these tests. Both exist because raw
rating and raw review count systematically favour operators who are already
good at digital, which is the opposite of what a supply team hunting the long
tail needs.
"""

import pytest

from supply_radar.models import DiscoveredPlace, Source
from supply_radar.scoring import (
    destination_from_address,
    score_gap_fit,
    score_lead,
    score_quality,
    score_readiness,
)


class FakeExtract:
    def __init__(self, booking="online_booking", languages=("en", "hr"),
                 contact_email="hi@example.hr", marketplace_presence=()):
        self.booking = booking
        self.languages = list(languages)
        self.contact_email = contact_email
        self.marketplace_presence = list(marketplace_presence)


def place(**kw) -> DiscoveredPlace:
    base = dict(
        source=Source.GOOGLE_PLACES,
        source_id="p1",
        name="Adriatic Boat Tours",
        lat=43.5,
        lng=16.4,
        destination_id="split",
        rating=4.7,
        review_count=180,
        website="https://www.example.hr",
        phone="+38521345678",
    )
    base.update(kw)
    return DiscoveredPlace(**base)


class TestQualityCounterweights:
    def test_a_perfect_rating_from_three_reviews_loses_to_a_strong_one_from_many(self):
        """A 5.0 backed by three reviews is not evidence. Shrinking toward the
        prior in proportion to sample size is what stops it topping the queue."""
        thin = score_quality(5.0, 3)
        solid = score_quality(4.8, 400)
        assert solid.score > thin.score

    def test_review_volume_is_capped_so_big_operators_cannot_bury_small_ones(self):
        """The volume component itself hard-caps. The overall score still moves
        a hair, because a rating backed by more reviews is genuinely better
        evidence, but the difference between 400 and 8,000 reviews must be
        negligible rather than dominant."""
        at_cap = score_quality(4.6, 400)
        far_above = score_quality(4.6, 8000)

        vol_at_cap = next(c for c in at_cap.components if c.name == "review volume")
        vol_above = next(c for c in far_above.components if c.name == "review volume")
        assert vol_at_cap.value == pytest.approx(vol_above.value, abs=1e-9) == 1.0

        assert far_above.score - at_cap.score < 0.02

    def test_an_excellent_small_operator_still_scores_respectably(self):
        """The long tail is the target. A genuinely good operator with modest
        review volume must not be scored near zero."""
        small = score_quality(4.9, 60)
        assert small.score > 0.55

    def test_a_poor_rating_scores_low_regardless_of_volume(self):
        assert score_quality(3.4, 2000).score < 0.5

    def test_a_missing_rating_is_flagged_rather_than_scored_as_zero(self):
        axis = score_quality(None, 0)
        assert axis.note is not None
        assert 0 < axis.score < 0.5

    def test_every_component_carries_readable_evidence(self):
        for c in score_quality(4.5, 100).components:
            assert c.evidence and not c.evidence.startswith("<")


class TestReadiness:
    def test_online_booking_beats_an_enquiry_form_beats_phone_only(self):
        online = score_readiness("https://x.hr", "+385", FakeExtract("online_booking"))
        form = score_readiness("https://x.hr", "+385", FakeExtract("enquiry_form"))
        phone = score_readiness("https://x.hr", "+385", FakeExtract("phone_or_email_only"))
        assert online.score > form.score > phone.score

    def test_without_a_site_read_readiness_is_low_and_says_why(self):
        axis = score_readiness("https://x.hr", "+385", None)
        assert axis.note is not None
        assert axis.score < 0.5

    def test_no_website_and_no_phone_scores_zero(self):
        assert score_readiness(None, None, None).score == 0.0

    def test_marketplace_presence_raises_readiness(self):
        without = score_readiness("https://x.hr", "+385", FakeExtract())
        with_mp = score_readiness(
            "https://x.hr", "+385", FakeExtract(marketplace_presence=["getyourguide"])
        )
        assert with_mp.score > without.score


class TestGapFit:
    def test_an_underserved_category_beats_a_well_served_one(self):
        """Split food and drink has high demand and almost no bookable supply;
        Split boat tours are heavily served already."""
        underserved = score_gap_fit("split", "food_drink")
        saturated = score_gap_fit("split", "boat_tour")
        assert underserved.score > saturated.score

    def test_a_saturated_category_scores_at_or_near_zero(self):
        assert score_gap_fit("dubrovnik", "walking_tour").score < 0.15

    def test_unknown_destination_falls_back_and_says_so(self):
        axis = score_gap_fit("narnia", "boat_tour")
        assert axis.note is not None or "default" in axis.components[0].evidence

    def test_missing_category_is_noted_not_silently_zeroed(self):
        axis = score_gap_fit("split", None)
        assert axis.note is not None

    def test_synthetic_provenance_is_always_disclosed(self):
        """The demand table is invented. Every score derived from it has to say
        so, or a reader will take it as a Viator figure.

        Asserts the disclosure, not one particular word for it. This test used
        to require the literal string "synthetic" and failed when the notes were
        rewritten in plain English for non-technical readers, which is a test
        objecting to better wording rather than to a real regression.
        """
        axis = score_gap_fit("split", "food_drink")
        note = (axis.note or "").lower()
        assert any(phrase in note for phrase in ("synthetic", "made up", "invented"))
        assert "viator" in note, "must say where the real figures would come from"


class TestComposite:
    def test_axes_are_reported_separately_not_just_blended(self):
        score = score_lead(place(), "food_drink", FakeExtract())
        d = score.to_dict()
        assert set(d) >= {"quality", "readiness", "gap_fit", "composite", "band"}
        assert d["quality"]["components"]

    def test_weights_change_the_ranking(self):
        p = place(rating=4.9, review_count=900)
        quality_led = score_lead(
            p, "boat_tour", None, {"quality": 0.8, "readiness": 0.1, "gap_fit": 0.1}
        )
        gap_led = score_lead(
            p, "boat_tour", None, {"quality": 0.1, "readiness": 0.1, "gap_fit": 0.8}
        )
        assert quality_led.composite != gap_led.composite

    def test_band_tracks_the_composite(self):
        strong = score_lead(
            place(rating=4.9, review_count=350), "food_drink", FakeExtract()
        )
        weak = score_lead(
            place(rating=3.6, review_count=4, website=None, phone=None), "boat_tour", None
        )
        assert strong.band < weak.band  # "A" sorts before "C"
        assert strong.composite > weak.composite


class TestUnenrichedSweep:
    """A live sweep cannot read websites, so it scores a different axis.

    Removing the four website components was only safe because the axis is
    renamed. Scored as "readiness" with them omitted, an operator with a website
    and a phone would report a perfect 1.0 readiness having proved nothing about
    whether it can transact. That mistake has been made three times here.
    """

    def test_unenriched_drops_website_components_and_renames_the_axis(self):
        axis = score_readiness("https://x.hr", "+385 1 234", None, unenriched=True)
        assert axis.name == "contactability"
        assert [c.name for c in axis.components] == ["website", "phone"]
        assert axis.score == 1.0

    def test_enriched_path_still_scores_missing_evidence_at_zero(self):
        axis = score_readiness("https://x.hr", "+385 1 234", None)
        assert axis.name == "readiness"
        assert len(axis.components) == 6
        assert axis.score < 0.35, "absent evidence must stay in the denominator"

    def test_gap_fit_uses_the_town_in_the_address_when_no_destination_is_set(self):
        """A sweep sets no destination_id, so every operator it found scored the
        country default even in destinations the demand table covers."""
        assert destination_from_address("Biserova ul. 16, 21000, Split, Croatia") == "split"
        assert destination_from_address("Ilica 1, 10000, Zagreb, Croatia") == "zagreb"
        # A town the table does not know falls back rather than guessing.
        assert destination_from_address("1, Kastel Sucurac, Croatia") is None
        assert destination_from_address(None) is None

    def test_the_fallback_actually_changes_the_score(self):
        default = score_gap_fit(None, "food_drink").score
        real = score_gap_fit(
            destination_from_address("A 1, 21000, Split, Croatia"), "food_drink"
        ).score
        assert real != default
