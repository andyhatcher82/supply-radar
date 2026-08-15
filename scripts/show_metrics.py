"""Print the matching metrics from the built snapshot."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def table(rows: list[dict]) -> None:
    print(f"  {'high':<7}{'low':<7}{'prec':<8}{'recall':<8}{'f1':<8}"
          f"{'review':<9}{'missed':<8}{'wasted'}")
    for r in rows:
        print(f"  {r['high']:<7}{r['low']:<7}{r['precision']:<8.3f}"
              f"{r['recall']:<8.3f}{r['f1']:<8.3f}{r['review_rate']:<9.1%}"
              f"{r['missed_opportunity']:<8}{r['wasted_call']}")


def main() -> None:
    d = json.loads(Path("data/snapshot.json").read_text(encoding="utf-8"))
    m = d["metrics"]

    print("counts")
    for k, v in d["counts"].items():
        print(f"  {k:<24} {v}")
    print()

    print(f"matching at high={m['thresholds']['high']} low={m['thresholds']['low']}")
    for k, v in m["matching"].items():
        print(f"  {k:<24} {v}")
    print()

    print("decisions by stage")
    for k, v in sorted(m["decisions_by_stage"].items()):
        print(f"  {k:<30} {v}")
    print()

    print("corruptions applied to the synthetic supplier list")
    for k, v in sorted(m["corruptions_applied"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<28} {v}")
    print()

    print("sweep: upper boundary (governs the expensive error)")
    table(m["sweep_upper"])
    print()
    print("sweep: lower boundary (governs review load)")
    table(m["sweep_lower"])


if __name__ == "__main__":
    main()
