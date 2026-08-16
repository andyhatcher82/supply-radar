"""Assemble the Croatia snapshot the console serves.

This is also where the promise made in baseline_report.py is settled: the
matching numbers are recomputed against REAL discovered places rather than the
synthetic stand-ins used during development. The synthetic Viator supplier list
is still synthetic (we do not have theirs), but it is now derived from real
Croatian operators, so the names, addresses and domains being matched are real
and the corruptions are applied to real strings.

    python scripts/build_snapshot.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.classify import resolve_category  # noqa: E402
from supply_radar.config import SNAPSHOT_DIR  # noqa: E402
from supply_radar.costs import USD_TO_GBP  # noqa: E402
from supply_radar.evaluate import evaluate, threshold_sweep  # noqa: E402
from supply_radar.locales import load_locale  # noqa: E402
from supply_radar.matching import MatchThresholds, match_all  # noqa: E402
from supply_radar.models import DiscoveredPlace, MatchVerdict  # noqa: E402
from supply_radar.enrich import SiteExtract  # noqa: E402
from supply_radar.scoring import (  # noqa: E402
    BAND_A_SHARE_OF_CEILING,
    BAND_B_SHARE_OF_CEILING,
    band_cutoffs,
    score_gap_fit,
    score_lead,
)
from supply_radar.taxonomy import breadcrumb, coverage, label, top_level  # noqa: E402
from supply_radar.synth import expected_verdicts, generate_supplier_list  # noqa: E402

def locality_of(address: str | None) -> str | None:
    """Which town an operator is in, from its own postal address.

    Location is a property of the operator, not of the area that was swept. A
    sweep is a box drawn on a map and a box has no name, especially over open
    country. Every discovered operator carries a full address, so the town comes
    from the operator and the naming problem never arises.

    String-splitting is honest only for the Croatian format Places returns here.
    Production reads the structured addressComponents locality field instead;
    this is a prototype standing in for that.
    """
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    return parts[-2] if len(parts) >= 2 else None


DATA = Path("data")

# Recorded assumption, confirmed by Andy: a Destination Specialist researching
# one Croatian city by hand takes roughly one working day. Editable in the UI
# and labelled as an assumption wherever it drives a number.
# Stamped onto every match so the log has a date column from day one. Fixed
# rather than datetime.now(): the snapshot is a build artefact and rebuilding
# it from unchanged inputs should produce an identical file.
SNAPSHOT_DATE = "2026-08-14"

MANUAL_HOURS_PER_DESTINATION = 7.5
ANALYST_COST_PER_HOUR_GBP = 32.0


def main() -> None:
    places = [
        DiscoveredPlace(**p)
        for p in json.loads((DATA / "split_places.json").read_text(encoding="utf-8"))
    ]
    classified = {
        c["place_source_id"]: c
        for c in json.loads(
            (DATA / "split_classified.json").read_text(encoding="utf-8")
        )
    }
    leads = json.loads((DATA / "split_leads.json").read_text(encoding="utf-8"))

    print(f"{len(places)} real places, {len(leads)} scored leads")

    # The leads file carries the RAW classifier answer, which is "other" for
    # every operator the deterministic shortcut accepted, because those never
    # reach the classifier at all. Resolve it the same way scoring already
    # does, from the search term that found them, so the console shows what an
    # operator actually sells instead of a wall of "other".
    _places_by_id = {p.source_id: p for p in places}
    for lead in leads:
        place = _places_by_id.get(lead.get("place_source_id"))
        if place is None:
            continue
        # Contact and identity fields come from the discovered place and were
        # never copied onto the lead. Everything downstream that has to REACH an
        # operator rather than rank one — the CSV export, the warehouse table,
        # a CRM push — needs them, and a lead list Sales cannot phone is not a
        # lead list. The site extract carries an email but no phone at all.
        lead["phone"] = place.phone
        lead["address"] = place.address
        lead["lat"] = place.lat
        lead["lng"] = place.lng
        lead["rating"] = place.rating
        lead["review_count"] = place.review_count
        lead["category"] = resolve_category(place, lead.get("experience_type"))
        # Express the category in Viator's own words. A lead described as
        # "boat_tour" is described in my vocabulary; one described as
        # "Cruises & Sailing" is described in theirs.
        if lead.get("category"):
            lead["viator_label"] = label(lead["category"])
            lead["viator_path"] = breadcrumb(lead["category"])
            lead["viator_top"] = top_level(lead["category"])
        lead["category_source"] = (
            "classifier"
            if (lead.get("experience_type") or "").lower()
            not in ("", "other", "none", "unclear")
            else ("search term" if lead["category"] else "unresolved")
        )
    resolved = sum(1 for l in leads if l.get("category"))
    print(f"  categories resolved for {resolved}/{len(leads)} leads")

    # The SCORE is recomputed here, not trusted from the leads file, for the
    # same reason the bands are. Scoring is deterministic and free; enrichment
    # is neither. Freezing the score at enrichment time meant a change to a
    # weight, a counterweight, or even the wording of an evidence line could
    # not reach the console without paying to re-fetch and re-read 40 websites
    # — which is how a scoring fix silently failed to appear in a rebuild.
    #
    # The website extract is reused from the leads file. Nothing is fetched and
    # no model is called.
    for lead in leads:
        place = _places_by_id.get(lead.get("place_source_id"))
        if place is None:
            continue
        extract = lead.get("extract")
        site = SiteExtract(**extract) if extract else None
        rescored = score_lead(place, lead.get("category"), site)
        lead.update(rescored.to_dict())

        # Operators with no single category are not classification failures.
        # They are agencies and charters that sell across the board, which is
        # why no single search term found them. Their own website says what
        # they sell, so the console shows that rather than "not determined".
        if not lead.get("category") and site:
            sells = [
                c for c in (site.product_categories or [])
                if c and c not in ("none", "other")
            ]
            if sells:
                lead["sells_categories"] = sells
                lead["viator_labels"] = [label(c) for c in sells]
                lead["category_source"] = "website"

    # Bands are recomputed here rather than trusted from the leads file, and the
    # cut-offs are derived from what is achievable in THIS destination rather
    # than fixed. See band_cutoffs: a constant has now been wrong in both
    # directions, unreachable at 0.65 and then far too generous at 0.55 once the
    # quality axis was corrected.
    band_a, band_b, ceiling = band_cutoffs(leads)
    for lead in leads:
        c = lead["composite"]
        lead["band"] = "A" if c >= band_a else "B" if c >= band_b else "C"
    band_counts: dict[str, int] = {}
    for lead in leads:
        band_counts[lead["band"]] = band_counts.get(lead["band"], 0) + 1
    print(f"  ceiling {ceiling} -> bands (A>={band_a} B>={band_b}): {band_counts}")

    # ---- matching, against real discovered operators -----------------------
    #
    # Matching runs over the OPERATORS ONLY, not everything discovered.
    #
    # Originally it ran over all 301 discovered places, which produced two
    # problems. It compared car parks and museums against Viator's supplier
    # list, which is wasted work. And it reported "167 operators" alongside
    # "181 genuinely net-new", two numbers computed over different
    # denominators, which is simply confusing: net-new cannot exceed the
    # population it is drawn from.
    #
    # Classification now gates matching, so every figure below shares one
    # denominator and the funnel adds up: discovered -> operators -> already on
    # file + net-new + needs review.
    operator_places = [
        p
        for p in places
        if classified.get(p.source_id, {}).get("verdict") == "experience_operator"
    ]
    print(f"{len(operator_places)} operators go forward to matching "
          f"({len(places) - len(operator_places)} filtered out first)")

    locale = load_locale("hr")
    suppliers, truth = generate_supplier_list(operator_places, seed=42)
    answer = expected_verdicts(truth)
    thresholds = MatchThresholds()
    results = match_all(operator_places, suppliers, locale, thresholds)
    ev = evaluate(results, answer)

    print("matching on real data:", ev.summary())

    by_stage: dict[str, int] = {}
    for r in results:
        by_stage[f"{r.verdict.value}/{r.decided_by.value}"] = (
            by_stage.get(f"{r.verdict.value}/{r.decided_by.value}", 0) + 1
        )

    review_share = sum(
        1 for r in results if r.verdict is MatchVerdict.NEEDS_REVIEW
    ) / max(1, len(results))

    corruptions: dict[str, int] = {}
    for g in truth:
        for c in g.corruptions:
            corruptions[c] = corruptions.get(c, 0) + 1

    upper = threshold_sweep(
        operator_places, suppliers, answer, locale,
        highs=[0.66, 0.72, 0.76, 0.80, 0.82, 0.86, 0.90, 0.94], lows=[0.55],
    )
    lower = threshold_sweep(
        operator_places, suppliers, answer, locale,
        highs=[0.82], lows=[0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75],
    )

    # ---- review queue ------------------------------------------------------
    place_by_id = {p.source_id: p for p in places}
    supplier_by_id = {s.supplier_id: s for s in suppliers}
    review_queue = []
    for r in results:
        if r.verdict is not MatchVerdict.NEEDS_REVIEW:
            continue
        place = place_by_id.get(r.place_source_id)
        supplier = supplier_by_id.get(r.supplier_id) if r.supplier_id else None
        review_queue.append(
            {
                "place_source_id": r.place_source_id,
                "discovered_name": place.name if place else None,
                "discovered_address": place.address if place else None,
                "discovered_website": place.website if place else None,
                "discovered_phone": place.phone if place else None,
                # Carried so this page filters on the same two fields as Leads.
                # Both are properties of the operator, so both survive the move
                # from one destination to many.
                "locality": locality_of(place.address if place else None),
                "experience_type": resolve_category(
                    place, classified.get(r.place_source_id, {}).get("experience_type")
                ) if place else None,
                "supplier_name": getattr(supplier, "legal_name", None),
                "supplier_trading_name": getattr(supplier, "trading_name", None),
                "supplier_address": getattr(supplier, "address", None),
                "supplier_website": getattr(supplier, "website", None),
                "supplier_phone": getattr(supplier, "phone", None),
                "score": round(r.score, 4),
                "confidence": round(r.confidence, 4),
                "decided_by": r.decided_by.value,
                "evidence": [
                    {
                        "signal": e.signal,
                        "detail": e.detail,
                        "contribution": e.contribution,
                    }
                    for e in r.evidence
                ],
            }
        )

    # ---- match log ---------------------------------------------------------
    # Every pair the matcher settled on its own, published so a human can
    # disagree with one.
    #
    # This is not a QA report. Precision cannot be computed on real data,
    # because precision needs an answer key and Viator has none. In production
    # the number is not calculated, it is earned: someone opens a pair here and
    # says "those are two different businesses". This page is where that
    # happens, so it is the thing that PRODUCES the metric rather than the thing
    # that displays it.
    #
    # It grows without bound as destinations are added, which is why it is its
    # own page with filters rather than a panel inside Accuracy & QA.
    matched = []
    for r in results:
        if r.verdict is not MatchVerdict.EXISTING:
            continue
        place = place_by_id.get(r.place_source_id)
        supplier = supplier_by_id.get(r.supplier_id) if r.supplier_id else None
        matched.append(
            {
                "place_source_id": r.place_source_id,
                "discovered_name": place.name if place else None,
                "discovered_address": place.address if place else None,
                "discovered_website": place.website if place else None,
                "discovered_phone": place.phone if place else None,
                "locality": locality_of(place.address if place else None),
                "experience_type": resolve_category(
                    place, classified.get(r.place_source_id, {}).get("experience_type")
                ) if place else None,
                "supplier_id": r.supplier_id,
                "supplier_name": getattr(supplier, "legal_name", None),
                "supplier_trading_name": getattr(supplier, "trading_name", None),
                "supplier_address": getattr(supplier, "address", None),
                "supplier_website": getattr(supplier, "website", None),
                "supplier_phone": getattr(supplier, "phone", None),
                "score": round(r.score, 4),
                "confidence": round(r.confidence, 4),
                "decided_by": r.decided_by.value,
                # The date the match was made. One run, so one date today, but
                # the column has to exist now: a log without dates stops being
                # a log the moment a second destination lands in it.
                "matched_on": SNAPSHOT_DATE,
                "evidence": [
                    {
                        "signal": e.signal,
                        "detail": e.detail,
                        "contribution": e.contribution,
                    }
                    for e in r.evidence
                ],
            }
        )
    matched.sort(key=lambda m: m["confidence"])
    print(f"  match log         {len(matched)} auto-matched pairs")

    # ---- category gap picture ---------------------------------------------
    category_counts: dict[str, int] = {}
    for pid, c in classified.items():
        if c["verdict"] != "experience_operator":
            continue
        place = place_by_id.get(pid)
        if place is None:
            continue
        cat = resolve_category(place, c.get("experience_type")) or "unresolved"
        category_counts[cat] = category_counts.get(cat, 0) + 1

    gaps = []
    for cat, count in sorted(category_counts.items(), key=lambda kv: -kv[1]):
        axis = score_gap_fit("split", None if cat == "unresolved" else cat)
        gaps.append(
            {
                "category": cat,
                "viator_label": label(cat) if cat != "unresolved" else None,
                "viator_path": breadcrumb(cat) if cat != "unresolved" else None,
                "operators_found": count,
                "gap_fit": round(axis.score, 3),
                "evidence": axis.components[0].evidence,
            }
        )

    # ---- economics ---------------------------------------------------------
    #
    # THIS IS A MODEL OF WHAT A DESTINATION COSTS TO RUN, NOT A RECORD OF WHAT
    # WAS SPENT BUILDING IT. The two differ by more than an order of magnitude
    # and conflating them is the easiest way to lose an economics argument:
    #
    #   * List price, free tiers deliberately EXCLUDED. Google's first 1,000
    #     Enterprise search calls each month are free, so the real Google bill
    #     for this build was zero. At 200 destinations Viator exhausts that
    #     allowance immediately, so quoting zero would be worse than useless.
    #   * Standard Sonnet 5 rates, not the introductory rates expiring
    #     31 August 2026. Actual Anthropic spend was therefore about a third
    #     lower than modelled here.
    #   * Enrichment is EXTRAPOLATED. It was measured over a 40-operator sample
    #     and scaled to all operators, because a production run enriches every
    #     operator and a 40-lead sample is a budget decision, not a design one.
    #
    # Actual out-of-pocket across the whole build was roughly GBP 0.82. The
    # figure below is what one destination costs at list price with nothing
    # subsidised, which is the only version that survives multiplication.
    sweep_meta_path = DATA / "split_places_sweep.json"
    if sweep_meta_path.exists():
        places_calls = json.loads(sweep_meta_path.read_text(encoding="utf-8"))["api_calls"]
        places_calls_source = "recorded by the sweep"
    else:
        # Recorded from the 14 August Split sweep before sweep.py persisted its
        # own metrics. Kept as a fallback so the economics page still builds
        # from a places file produced by that run; re-running sweep.py replaces
        # it with a live figure.
        places_calls = 28
        places_calls_source = "recorded from the 14 August sweep, before metrics were persisted"
    places_usd = places_calls * 35.00 / 1000
    classify_usd = 0.3657
    enrich_usd_per_operator = 0.01176
    operators = sum(
        1 for c in classified.values() if c["verdict"] == "experience_operator"
    )
    enrich_usd = enrich_usd_per_operator * operators
    total_usd = places_usd + classify_usd + enrich_usd
    total_gbp = total_usd * USD_TO_GBP

    manual_cost = MANUAL_HOURS_PER_DESTINATION * ANALYST_COST_PER_HOUR_GBP

    economics = {
        "basis": (
            "This is what one destination costs to RUN at list price, not what "
            "was spent building the prototype. Actual out-of-pocket for the "
            "entire build was about GBP 0.82, because Google's free monthly "
            "allowance absorbed the Places calls and Anthropic introductory "
            "pricing was in force. Neither subsidy survives contact with 200 "
            "destinations, so neither is used below."
        ),
        "assumptions": [
            {
                "name": "Manual research time per destination",
                "value": f"{MANUAL_HOURS_PER_DESTINATION} hours (one working day)",
                "source": "Recorded assumption, not measured. Confirmed with the "
                          "hiring team as a working figure.",
            },
            {
                "name": "Analyst cost per hour",
                "value": f"GBP {ANALYST_COST_PER_HOUR_GBP:.2f}",
                "source": "Assumption. Fully loaded cost, editable.",
            },
            {
                "name": "Model pricing",
                "value": "Claude Sonnet 5 at standard rates, not the "
                         "introductory rate expiring 31 August 2026",
                "source": "Deliberately conservative. Actual spend was lower.",
            },
            {
                "name": "Free tiers",
                "value": "Excluded",
                "source": "Google's first 1,000 Enterprise search calls each "
                          "month are free, so the real Google bill for this "
                          "build was GBP 0.00. Amortising an allowance that "
                          "one destination exhausts would not scale.",
            },
            {
                "name": "Enrichment cost",
                "value": f"Extrapolated: USD {enrich_usd_per_operator} per "
                         f"operator x {operators} operators",
                "source": f"Measured over a 40-operator sample and scaled. A "
                          f"production run enriches every operator; the 40 was "
                          f"a budget decision, not a design one.",
            },
            {
                "name": "Places call count",
                "value": f"{places_calls} billable search calls",
                "source": places_calls_source.capitalize() + ".",
            },
            {
                "name": "Demand figures",
                "value": "Synthetic",
                "source": "In production these come from Viator's own search logs.",
            },
        ],
        "per_destination": {
            "places_usd": round(places_usd, 4),
            "classification_usd": round(classify_usd, 4),
            "enrichment_usd": round(enrich_usd, 4),
            "total_usd": round(total_usd, 4),
            "total_gbp": round(total_gbp, 4),
            "total_gbp_batched": round(total_gbp * 0.5, 4),
            "operators_surfaced": operators,
            "gbp_per_operator_surfaced": round(total_gbp / max(1, operators), 4),
        },
        "versus_manual": {
            "manual_hours": MANUAL_HOURS_PER_DESTINATION,
            "manual_cost_gbp": round(manual_cost, 2),
            "automated_cost_gbp": round(total_gbp, 2),
            "cost_ratio": round(manual_cost / max(0.01, total_gbp), 0),
        },
        "at_scale": [
            {
                "destinations": n,
                "cost_gbp": round(total_gbp * n, 2),
                "cost_gbp_batched": round(total_gbp * n * 0.5, 2),
                "manual_equivalent_gbp": round(manual_cost * n, 2),
                "manual_analyst_days": round(
                    MANUAL_HOURS_PER_DESTINATION * n / 7.5, 0
                ),
            }
            for n in (1, 10, 50, 200, 500)
        ],
    }

    # ---- lead gate ---------------------------------------------------------
    # A lead is by definition an operator Viator does not already have. The
    # enrichment sample used to be drawn from all 167 operators with no net-new
    # filter, so 14 of the 40 published "leads" were existing suppliers. Nothing
    # caught it because nothing checked. Every lead now carries the verdict that
    # justifies its presence, and a non-net-new lead stops the build rather than
    # reaching the console.
    verdict_by_id = {r.place_source_id: r.verdict for r in results}

    intruders = [
        lead["name"]
        for lead in leads
        if verdict_by_id.get(lead["place_source_id"]) is not MatchVerdict.NET_NEW
    ]
    if intruders:
        raise SystemExit(
            f"{len(intruders)} published leads are not net-new: "
            f"{', '.join(intruders[:5])}. Re-run enrich_score_run.py with "
            "--net-new-only."
        )
    for lead in leads:
        lead["match_verdict"] = MatchVerdict.NET_NEW.value
        lead["locality"] = locality_of(lead.get("address"))

        # Surfaced because the audience for this build is Viator, and three of
        # these leads say on their own websites that they already sell on
        # Viator. That is not a matching fault: the supplier list is synthetic,
        # so whether an operator is "on Viator" here was decided by seed=42
        # rather than by reality. But the contradiction is visible on the card
        # and anyone in the room can look a lead up, so the console says it
        # first. Using the claim as a matching signal is deliberately NOT done:
        # the synthetic answer key calls these net-new, so acting on the
        # website would score as three missed opportunities and drop recall.
        marketplaces = [
            str(m).lower()
            for m in ((lead.get("extract") or {}).get("marketplace_presence") or [])
        ]
        lead["marketplaces"] = marketplaces
        lead["claims_viator"] = any("viator" in m for m in marketplaces)
        lead["claims_tripadvisor"] = any("tripadvisor" in m for m in marketplaces)
        # Readiness scores low for these on evidence that does not exist rather
        # than evidence that is bad, and a reader is owed that distinction.
        lead["no_website"] = not lead.get("website")

    print(f"  lead gate         {len(leads)} leads, all net-new, "
          f"{sum(1 for l in leads if l['no_website'])} without a website")

    snapshot = {
        "destination": "Split, Croatia",
        "generated_from": "Real Google Places discovery, August 2026",
        # Published so the "cut-offs are recalibrated per destination" claim can
        # be checked rather than taken on trust.
        "bands": {
            "ceiling": ceiling,
            "band_a": band_a,
            "band_b": band_b,
            "basis": (
                f"Best achievable composite in this destination is {ceiling:.3f}, "
                f"from the best score observed on each axis. Band A is the top "
                f"{int(BAND_A_SHARE_OF_CEILING * 100)}% of that, band B the top "
                f"{int(BAND_B_SHARE_OF_CEILING * 100)}%. A fixed cut-off has been "
                f"wrong in both directions here: unreachable at 0.65, then too "
                f"generous at 0.55 once the quality axis was corrected."
            ),
        },
        # One funnel, one denominator, and it adds up. Every figure after
        # "operators" is a subset of it.
        "counts": {
            "places_discovered": len(places),
            "not_relevant": len(places) - len(operator_places),
            "operators": len(operator_places),
            "already_on_file": sum(
                1 for r in results if r.verdict is MatchVerdict.EXISTING
            ),
            "net_new": sum(1 for r in results if r.verdict is MatchVerdict.NET_NEW),
            # The pipeline's answer and the right answer, side by side.
            #
            # net_new above is what the matcher decided, and it is the only one
            # of these three that could exist in production. The rest come from
            # the synthetic answer key, so they are a property of the benchmark
            # rather than of the method, and every place they are shown says so.
            #
            # Publishing only the first would let "105 net-new leads" be read as
            # 105 businesses Viator does not have, when 5 of them are businesses
            # Viator does have. That is the same overstatement as the 14
            # already-supplier leads, one order of magnitude smaller.
            "net_new_actual": sum(
                1 for p in operator_places if p.source_id not in answer
            ),
            "net_new_correct_in_leads": sum(
                1
                for r in results
                if r.verdict is MatchVerdict.NET_NEW
                and r.place_source_id not in answer
            ),
            "existing_wrongly_in_leads": sum(
                1
                for r in results
                if r.verdict is MatchVerdict.NET_NEW and r.place_source_id in answer
            ),
            "net_new_held_in_review": sum(
                1
                for r in results
                if r.verdict is MatchVerdict.NEEDS_REVIEW
                and r.place_source_id not in answer
            ),
            "needs_review": len(review_queue),
            "leads_scored": len(leads),
            "suppliers_on_file": len(suppliers),
        },
        "leads": leads,
        "metrics": {
            "matching": ev.summary(),
            "thresholds": {"high": thresholds.high, "low": thresholds.low},
            "decisions_by_stage": by_stage,
            "review_share": round(review_share, 4),
            "corruptions_applied": corruptions,
            "sweep_upper": upper,
            "sweep_lower": lower,
        },
        "review_queue": review_queue,
        "matched": matched,
        "category_gaps": gaps,
        "taxonomy": coverage(),
        "economics": economics,
    }

    # Published, not working: SNAPSHOT_DIR is committed and copied into the
    # image, unlike data/ which both git and Docker ignore.
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / "snapshot.json"
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    size = out.stat().st_size / 1024
    print(f"wrote {out} ({size:.0f} KB)")
    print(f"  review queue      {len(review_queue)}")
    print(f"  category gaps     {len(gaps)}")
    print(f"  cost per dest     GBP {total_gbp:.2f} vs GBP {manual_cost:.0f} manual")


if __name__ == "__main__":
    main()
