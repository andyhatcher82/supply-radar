"""Development baseline: run the matcher against the hidden answer key.

Numbers produced here use stand-in discovery data, so they measure the METHOD,
not the real-world result. They get recomputed against real Places output once
discovery is live.

    python scripts/baseline_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.evaluate import evaluate, threshold_sweep  # noqa: E402
from supply_radar.fixtures import synthetic_places  # noqa: E402
from supply_radar.locales import load_locale  # noqa: E402
from supply_radar.matching import MatchThresholds, match_all  # noqa: E402
from supply_radar.models import MatchVerdict  # noqa: E402
from supply_radar.synth import expected_verdicts, generate_supplier_list  # noqa: E402


def main() -> None:
    locale = load_locale("hr")
    places = synthetic_places(count=400, seed=11)
    suppliers, truth = generate_supplier_list(places, seed=42)
    answer = expected_verdicts(truth)

    print(f"places            {len(places)}")
    print(f"suppliers         {len(suppliers)}")
    print(f"genuinely on file {len(answer)}")
    print(f"genuinely net-new {len(places) - len(answer)}")
    print()

    corruption_counts: dict[str, int] = {}
    for g in truth:
        for c in g.corruptions:
            corruption_counts[c] = corruption_counts.get(c, 0) + 1
    print("corruptions applied")
    for name, n in sorted(corruption_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<26} {n}")
    print()

    thresholds = MatchThresholds()
    results = match_all(places, suppliers, locale, thresholds)
    ev = evaluate(results, answer)

    print(f"baseline at high={thresholds.high} low={thresholds.low}")
    for k, v in ev.summary().items():
        print(f"  {k:<20} {v}")
    print()

    by_stage: dict[str, int] = {}
    for r in results:
        key = f"{r.verdict.value}/{r.decided_by.value}"
        by_stage[key] = by_stage.get(key, 0) + 1
    print("how each decision was reached")
    for k, v in sorted(by_stage.items()):
        print(f"  {k:<28} {v}")
    print()

    llm_share = sum(
        1 for r in results if r.verdict is MatchVerdict.NEEDS_REVIEW
    ) / len(results)
    print(f"share needing adjudication  {llm_share:.1%}")
    print()

    def show(rows):
        print(f"  {'high':<6}{'low':<6}{'precision':<11}{'recall':<9}{'f1':<8}"
              f"{'review':<9}{'missed':<8}{'wasted'}")
        for row in rows:
            print(
                f"  {row['high']:<6}{row['low']:<6}"
                f"{row['precision']:<11.3f}{row['recall']:<9.3f}"
                f"{row['f1']:<8.3f}{row['review_rate']:<9.1%}"
                f"{row['missed_opportunity']:<8}{row['wasted_call']}"
            )

    print("sweep: upper boundary, governs the expensive error")
    show(threshold_sweep(places, suppliers, answer, locale,
                         highs=[0.66, 0.72, 0.76, 0.80, 0.82, 0.86, 0.90, 0.94],
                         lows=[0.55]))
    print()

    print("sweep: lower boundary, governs review load")
    show(threshold_sweep(places, suppliers, answer, locale,
                         highs=[0.82],
                         lows=[0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75]))


if __name__ == "__main__":
    main()
