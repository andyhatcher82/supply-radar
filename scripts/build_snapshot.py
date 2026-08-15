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
from supply_radar.scoring import BAND_A, BAND_B, score_gap_fit  # noqa: E402
from supply_radar.synth import expected_verdicts, generate_supplier_list  # noqa: E402

DATA = Path("data")

# Recorded assumption, confirmed by Andy: a Destination Specialist researching
# one Croatian city by hand takes roughly one working day. Editable in the UI
# and labelled as an assumption wherever it drives a number.
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
        lead["category"] = resolve_category(place, lead.get("experience_type"))
        lead["category_source"] = (
            "classifier"
            if (lead.get("experience_type") or "").lower()
            not in ("", "other", "none", "unclear")
            else ("search term" if lead["category"] else "unresolved")
        )
    resolved = sum(1 for l in leads if l.get("category"))
    print(f"  categories resolved for {resolved}/{len(leads)} leads")

    # Bands are recomputed here rather than trusted from the leads file. That
    # file was written when the cut-offs were still 0.65/0.45, which measurement
    # later showed made band A unreachable. Deriving them from the composite at
    # build time means a threshold change takes effect without re-running the
    # expensive enrichment.
    for lead in leads:
        c = lead["composite"]
        lead["band"] = "A" if c >= BAND_A else "B" if c >= BAND_B else "C"
    band_counts: dict[str, int] = {}
    for lead in leads:
        band_counts[lead["band"]] = band_counts.get(lead["band"], 0) + 1
    print(f"  bands (A>={BAND_A} B>={BAND_B}): {band_counts}")

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
                "operators_found": count,
                "gap_fit": round(axis.score, 3),
                "evidence": axis.components[0].evidence,
            }
        )

    # ---- economics ---------------------------------------------------------
    # Measured from the real runs recorded during the build.
    places_calls = 28
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
                "source": "Deliberately conservative.",
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

    snapshot = {
        "destination": "Split, Croatia",
        "generated_from": "Real Google Places discovery, August 2026",
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
        "category_gaps": gaps,
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
