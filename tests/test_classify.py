"""Prefilter and cost tests.

The prefilter is pure and free, so it gets direct coverage. The model-backed
path is exercised against real data by scripts/classify_run.py rather than by
mocking a model response, which would only test the mock.
"""

from supply_radar.classify import (
    AMBIGUOUS_TYPES,
    REJECT_TYPES,
    Verdict,
    prefilter,
)
from supply_radar.costs import CostLedger
from supply_radar.models import DiscoveredPlace, Source


def place(**kw) -> DiscoveredPlace:
    base = dict(
        source=Source.GOOGLE_PLACES,
        source_id="p1",
        name="Test Operator",
        lat=43.5,
        lng=16.4,
        categories=[],
    )
    base.update(kw)
    return DiscoveredPlace(**base)


class TestPrefilter:
    def test_tour_agency_is_accepted_without_a_model_call(self):
        res = prefilter(place(categories=["tour_agency", "travel_agency"]))
        assert res is not None
        assert res.verdict is Verdict.OPERATOR
        assert res.decided_by == "deterministic"

    def test_obvious_junk_is_rejected_without_a_model_call(self):
        res = prefilter(place(categories=["parking", "establishment"]))
        assert res is not None
        assert res.verdict is Verdict.NOT_RELEVANT

    def test_unknown_types_go_to_the_model(self):
        assert prefilter(place(categories=["point_of_interest"])) is None

    def test_no_categories_goes_to_the_model(self):
        assert prefilter(place(categories=[])) is None

    def test_ambiguous_types_go_to_the_model_even_alongside_a_reject(self):
        """A boat-party operator in Split is tagged night_club by Google. The
        original reject list discarded it silently; ambiguity must win."""
        res = prefilter(place(categories=["night_club", "bar"]))
        assert res is None

    def test_ambiguity_beats_a_reject_when_both_are_present(self):
        res = prefilter(place(categories=["night_club", "parking"]))
        assert res is None, "an ambiguous type must send the record to the model"

    def test_travel_agency_alone_is_not_auto_accepted(self):
        """travel_agency also covers flight and package sellers, which are not
        experience operators, so it must not shortcut to an accept."""
        assert prefilter(place(categories=["travel_agency"])) is None

    def test_reject_and_ambiguous_lists_do_not_overlap(self):
        assert not (REJECT_TYPES & AMBIGUOUS_TYPES)


class TestCostLedger:
    def test_cache_reads_are_far_cheaper_than_fresh_input(self):
        cached = CostLedger()
        cached.record_llm("claude-sonnet-5", cache_read_tokens=1_000_000)
        fresh = CostLedger()
        fresh.record_llm("claude-sonnet-5", input_tokens=1_000_000)
        assert cached.llm_usd() < fresh.llm_usd() / 5

    def test_cache_writes_cost_more_than_fresh_input(self):
        written = CostLedger()
        written.record_llm("claude-sonnet-5", cache_write_tokens=1_000_000)
        fresh = CostLedger()
        fresh.record_llm("claude-sonnet-5", input_tokens=1_000_000)
        assert written.llm_usd() > fresh.llm_usd()

    def test_batching_halves_model_cost(self):
        plain = CostLedger()
        plain.record_llm("claude-sonnet-5", input_tokens=100_000)
        batched = CostLedger()
        batched.record_llm("claude-sonnet-5", input_tokens=100_000, batched=True)
        assert batched.llm_usd() == plain.llm_usd() / 2

    def test_standard_pricing_is_the_default_not_the_intro_rate(self):
        """Intro pricing expires 31 August 2026. Quoting it in a business case
        would flatter a number that changes in a fortnight."""
        ledger = CostLedger()
        ledger.record_llm("claude-sonnet-5", input_tokens=1_000_000)
        assert ledger.llm_usd() == 3.0
        assert ledger.llm_usd(use_intro_pricing=True) == 2.0

    def test_cache_hit_rate_reflects_the_share_served_from_cache(self):
        ledger = CostLedger()
        ledger.record_llm(
            "claude-sonnet-5", input_tokens=100, cache_read_tokens=900
        )
        assert ledger.llm["claude-sonnet-5"].cache_hit_rate == 0.9

    def test_api_and_model_costs_both_land_in_the_total(self):
        ledger = CostLedger()
        ledger.record("places.text_search.enterprise", 1000)
        ledger.record_llm("claude-sonnet-5", input_tokens=1_000_000)
        assert ledger.usd() == round(35.0 + 3.0, 6)
