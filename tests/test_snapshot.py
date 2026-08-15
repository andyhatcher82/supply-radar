"""Checks on the published snapshot, which is what the console actually serves.

The rest of the suite tests the code. This file tests the artefact, because the
worst defect found in this build was not a code fault: enrichment sampled from
all 167 operators with no net-new filter, so 14 of the 40 published leads were
businesses Viator already had. Every unit test passed throughout.
"""

import json
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).resolve().parent.parent / "snapshot" / "snapshot.json"


@pytest.fixture(scope="module")
def snap() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_every_published_lead_is_net_new(snap):
    """The defect this file exists for."""
    wrong = [
        lead["name"]
        for lead in snap["leads"]
        if lead.get("match_verdict") != "net_new"
    ]
    assert not wrong, f"{len(wrong)} leads are not net-new: {wrong[:5]}"


def test_lead_count_matches_the_funnel(snap):
    """The console shows both numbers on adjacent pages.

    A gap between them is not necessarily a bug, but it is always something a
    reader has to be told about, so it must not appear silently.
    """
    counts = snap["counts"]
    assert counts["leads_scored"] == len(snap["leads"])
    assert counts["leads_scored"] <= counts["net_new"]


def test_funnel_shares_one_denominator(snap):
    c = snap["counts"]
    assert c["already_on_file"] + c["needs_review"] + c["net_new"] == c["operators"]
    assert c["not_relevant"] + c["operators"] == c["places_discovered"]


def test_leads_without_a_website_are_marked_as_such(snap):
    """Low readiness on absent evidence must be distinguishable from low
    readiness on bad evidence, or the score reads as a judgement it is not."""
    for lead in snap["leads"]:
        assert lead["no_website"] is (not lead.get("website"))


def test_multi_category_operators_are_scored_on_what_they_sell(snap):
    """Agencies and charters have no single category, which is why the catch-all
    query found them. Their website says what they sell, so gap fit uses it
    rather than falling to the country default and discarding paid-for evidence.
    """
    multi = [l for l in snap["leads"] if l.get("sells_categories")]
    assert multi, "expected at least one multi-category operator in this snapshot"
    for lead in multi:
        assert not lead.get("category"), lead["name"]
        assert lead["category_source"] == "website"
        # One gap-fit component per category, so the axis score is their mean.
        names = [c["name"] for c in lead["gap_fit"]["components"]]
        assert names == lead["sells_categories"], lead["name"]
        assert "none" not in names and "other" not in names


def test_viator_claims_are_surfaced_not_acted_on(snap):
    """Some leads say on their own websites that they already sell on Viator.

    The synthetic supplier list decided net-new by seed, so the matcher is not
    wrong, but the contradiction is visible to an audience that can look these
    operators up. The flag must be present so the console can own it, and the
    match verdict must be untouched so the measured metrics stay honest.
    """
    flagged = [l for l in snap["leads"] if l.get("claims_viator")]
    for lead in flagged:
        assert "viator" in " ".join(lead["marketplaces"])
        # Surfaced, never acted on: acting would contradict the answer key.
        assert lead["match_verdict"] == "net_new"


def test_the_lead_count_reconciles_against_the_answer_key(snap):
    """The console shows what the matcher decided AND what was true.

    "105 net-new leads" reads as 105 businesses Viator does not have. Five of
    them are businesses Viator does have. The two numbers must stay consistent
    with each other or the reconciliation on the Overview is decoration.
    """
    c = snap["counts"]
    assert c["net_new_correct_in_leads"] + c["existing_wrongly_in_leads"] == c["net_new"]
    assert (
        c["net_new_correct_in_leads"] + c["net_new_held_in_review"]
        == c["net_new_actual"]
    ), "true net-new must be the ones we published plus the ones we held back"
    assert c["net_new_actual"] <= c["operators"]
    # The wasted calls the QA page reports are exactly the wrong ones we shipped.
    assert c["existing_wrongly_in_leads"] == snap["metrics"]["matching"]["wasted_call"]


def test_band_cutoffs_are_derived_from_the_ceiling(snap):
    bands = snap["bands"]
    assert 0 < bands["band_b"] < bands["band_a"] < bands["ceiling"] <= 1.0
    for lead in snap["leads"]:
        expected = (
            "A" if lead["composite"] >= bands["band_a"]
            else "B" if lead["composite"] >= bands["band_b"]
            else "C"
        )
        assert lead["band"] == expected, lead["name"]
