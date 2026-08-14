"""Does the gap-fit axis actually discriminate, or is it dead?

Scores every classified operator without enrichment (free, no API calls) and
reports gap fit by resolved category. A gap axis that returns the same number
for everything is worse than no axis, because it looks like a signal.

    python scripts/gap_check.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.classify import resolve_category  # noqa: E402
from supply_radar.models import DiscoveredPlace  # noqa: E402
from supply_radar.scoring import score_gap_fit  # noqa: E402


def main() -> None:
    places = {
        p["source_id"]: DiscoveredPlace(**p)
        for p in json.loads(Path("data/split_places.json").read_text(encoding="utf-8"))
    }
    classified = json.loads(
        Path("data/split_classified.json").read_text(encoding="utf-8")
    )
    operators = [c for c in classified if c["verdict"] == "experience_operator"]

    by_category: dict[str, list[float]] = {}
    unresolved = 0

    for c in operators:
        place = places[c["place_source_id"]]
        category = resolve_category(place, c.get("experience_type"))
        if category is None:
            unresolved += 1
            key = "(unresolved)"
        else:
            key = category
        by_category.setdefault(key, []).append(
            score_gap_fit(place.destination_id, category).score
        )

    print(f"{len(operators)} operators, {unresolved} with no resolvable category\n")
    print(f"  {'category':<18}{'count':<8}{'gap fit'}")
    for cat, scores in sorted(by_category.items(), key=lambda kv: -kv[1][0]):
        print(f"  {cat:<18}{len(scores):<8}{scores[0]:.3f}")

    distinct = {round(s[0], 3) for s in by_category.values()}
    print()
    print(f"distinct gap-fit values across categories: {len(distinct)}")
    if len(distinct) == 1:
        print("AXIS IS DEAD - every category scores the same")
    else:
        print("axis discriminates")

    print()
    print("what the axis says about Split, across all categories in the table")
    for cat in (
        "food_drink", "private_guide", "adventure", "water_sports",
        "day_trip", "walking_tour", "cultural", "boat_tour",
    ):
        axis = score_gap_fit("split", cat)
        print(f"  {cat:<16}{axis.score:.3f}   {axis.components[0].evidence[:74]}")


if __name__ == "__main__":
    main()
