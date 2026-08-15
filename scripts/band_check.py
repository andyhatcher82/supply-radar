"""Are the lead band cut-offs actually reachable?

Bands were set at A>=0.65 / B>=0.45 by judgement rather than from the data.
The composite is a weighted sum of three axes, and gap fit is legitimately 0.00
in a saturated category, which caps the achievable composite well below 1.0.
If A is unreachable, the band carries no information.

    python scripts/band_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    leads = json.loads(
        Path("snapshot/snapshot.json").read_text(encoding="utf-8")
    )["leads"]
    comps = sorted(l["composite"] for l in leads)
    n = len(comps)

    def q(p: float) -> float:
        return comps[min(n - 1, int(p * n))]

    print(f"{n} scored leads")
    print(f"  min      {comps[0]:.3f}")
    print(f"  p25      {q(0.25):.3f}")
    print(f"  median   {q(0.50):.3f}")
    print(f"  p75      {q(0.75):.3f}")
    print(f"  p90      {q(0.90):.3f}")
    print(f"  max      {comps[-1]:.3f}")
    print()

    max_axes = {
        "quality": max(l["quality"]["score"] for l in leads),
        "readiness": max(l["readiness"]["score"] for l in leads),
        "gap_fit": max(l["gap_fit"]["score"] for l in leads),
    }
    print("best observed per axis")
    for k, v in max_axes.items():
        print(f"  {k:<12} {v:.3f}")
    ceiling = (max_axes["quality"] * 0.35 + max_axes["readiness"] * 0.35
               + max_axes["gap_fit"] * 0.30)
    print(f"  implied ceiling for this destination: {ceiling:.3f}")
    print()

    for a, b in ((0.65, 0.45), (0.55, 0.42), (0.52, 0.40)):
        bands = {"A": 0, "B": 0, "C": 0}
        for c in comps:
            bands["A" if c >= a else "B" if c >= b else "C"] += 1
        print(f"  A>={a} B>={b}  ->  {bands}")


if __name__ == "__main__":
    main()
