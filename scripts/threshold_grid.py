"""Two-dimensional threshold sweep on the operator-only population.

The one-dimensional sweeps hold the other boundary fixed, which hides the real
trade: raising the upper boundary buys fewer missed opportunities but pushes
pairs into the review band, and the lower boundary then decides how many of
those a human actually sees.

Human attention is the scarce resource, so this scores each combination on a
stated cost model rather than eyeballing it.

    python scripts/threshold_grid.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.evaluate import evaluate  # noqa: E402
from supply_radar.locales import load_locale  # noqa: E402
from supply_radar.matching import MatchThresholds, match_all  # noqa: E402
from supply_radar.models import DiscoveredPlace, MatchVerdict  # noqa: E402
from supply_radar.synth import expected_verdicts, generate_supplier_list  # noqa: E402

# Stated cost model, in analyst-minutes. Everything here is arguable, which is
# exactly why it is written down rather than left implicit in a judgement call.
MIN_PER_REVIEW = 3        # a Destination Specialist adjudicating one pair
MIN_PER_WASTED_CALL = 20  # an awkward call to a supplier already on the books
MIN_PER_MISSED = 240      # an operator never contacted: a day of lost sourcing,
                          # and it never surfaces on its own


def main() -> None:
    places = [
        DiscoveredPlace(**p)
        for p in json.loads(
            Path("data/split_places.json").read_text(encoding="utf-8")
        )
    ]
    classified = {
        c["place_source_id"]: c
        for c in json.loads(
            Path("data/split_classified.json").read_text(encoding="utf-8")
        )
    }
    operators = [
        p for p in places
        if classified.get(p.source_id, {}).get("verdict") == "experience_operator"
    ]

    locale = load_locale("hr")
    suppliers, truth = generate_supplier_list(operators, seed=42)
    answer = expected_verdicts(truth)

    print(f"{len(operators)} operators, {len(suppliers)} supplier records\n")
    print(f"cost model: review {MIN_PER_REVIEW}min, wasted call "
          f"{MIN_PER_WASTED_CALL}min, missed operator {MIN_PER_MISSED}min\n")

    rows = []
    for high in (0.76, 0.80, 0.84, 0.88, 0.90, 0.94):
        for low in (0.55, 0.60, 0.65, 0.70, 0.75):
            if low >= high:
                continue
            results = match_all(
                operators, suppliers, locale, MatchThresholds(high=high, low=low)
            )
            ev = evaluate(results, answer)
            s = ev.summary()
            reviews = sum(
                1 for r in results if r.verdict is MatchVerdict.NEEDS_REVIEW
            )
            minutes = (
                reviews * MIN_PER_REVIEW
                + s["wasted_call"] * MIN_PER_WASTED_CALL
                + s["missed_opportunity"] * MIN_PER_MISSED
            )
            rows.append({
                "high": high, "low": low,
                "precision": s["precision"], "recall": s["recall"],
                "review_rate": s["review_rate"], "reviews": reviews,
                "missed": s["missed_opportunity"], "wasted": s["wasted_call"],
                "minutes": minutes,
            })

    rows.sort(key=lambda r: r["minutes"])

    print(f"  {'high':<7}{'low':<7}{'prec':<8}{'recall':<8}{'review':<9}"
          f"{'reviews':<9}{'missed':<8}{'wasted':<8}{'analyst hours'}")
    for r in rows[:18]:
        print(f"  {r['high']:<7}{r['low']:<7}{r['precision']:<8.3f}"
              f"{r['recall']:<8.3f}{r['review_rate']:<9.1%}{r['reviews']:<9}"
              f"{r['missed']:<8}{r['wasted']:<8}{r['minutes'] / 60:.1f}")

    best = rows[0]
    print()
    print(f"lowest total cost: high={best['high']} low={best['low']} "
          f"-> {best['minutes'] / 60:.1f} analyst hours per destination")
    print(f"  {best['reviews']} reviews, {best['missed']} missed, "
          f"{best['wasted']} wasted calls")

    # The cost of a missed operator is the softest number in the model, and it
    # dominates the total. So the choice is only trustworthy if it survives
    # being wrong about that number by an order of magnitude.
    print()
    print("sensitivity: does the winner change if a missed operator costs less?")
    print(f"  {'missed costs':<16}{'winner':<18}{'reviews':<10}{'missed'}")
    for missed_cost in (20, 60, 120, 240, 480):
        scored = sorted(
            rows,
            key=lambda r: r["reviews"] * MIN_PER_REVIEW
            + r["wasted"] * MIN_PER_WASTED_CALL
            + r["missed"] * missed_cost,
        )
        w = scored[0]
        label = f"{missed_cost}min"
        if missed_cost == MIN_PER_WASTED_CALL:
            label += " (= wasted call)"
        print(f"  {label:<16}high={w['high']} low={w['low']}"
              f"{'':<4}{w['reviews']:<10}{w['missed']}")


if __name__ == "__main__":
    main()
