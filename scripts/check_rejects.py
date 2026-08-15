"""What did classification actually throw away?

A museum that runs guided tours IS an experience operator; one that only sells
admission is not. Same for a restaurant running a cooking class versus one
where you turn up and eat. The prompt draws that line, but the line is only
worth anything if the real rejects respect it.

    python scripts/check_rejects.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.models import DiscoveredPlace  # noqa: E402

WATCH = ("museum", "restaurant", "cafe", "bar", "winery", "food", "art_gallery",
         "tourist_attraction", "lodging", "night_club")


def main() -> None:
    places = {
        p["source_id"]: DiscoveredPlace(**p)
        for p in json.loads(
            Path("data/split_places.json").read_text(encoding="utf-8")
        )
    }
    classified = json.loads(
        Path("data/split_classified.json").read_text(encoding="utf-8")
    )

    buckets: dict[str, list] = {}
    for c in classified:
        buckets.setdefault(c["verdict"], []).append(c)

    print("verdicts")
    for v, items in sorted(buckets.items()):
        by = {}
        for i in items:
            by[i["decided_by"]] = by.get(i["decided_by"], 0) + 1
        print(f"  {v:<22} {len(items):<6} {by}")
    print()

    print("records whose Google types include a watched category, and what happened")
    for verdict in ("not_relevant", "attraction_only"):
        rows = []
        for c in buckets.get(verdict, []):
            place = places.get(c["place_source_id"])
            if not place:
                continue
            hits = [t for t in (place.categories or []) if t in WATCH]
            if hits:
                rows.append((place, c, hits))
        print(f"\n  {verdict.upper()}  ({len(rows)} of {len(buckets.get(verdict, []))})")
        for place, c, hits in rows[:14]:
            print(f"    {place.name[:44]:<46} {','.join(hits)[:34]}")
            print(f"      {c['decided_by']}: {c['reason'][:96]}")


if __name__ == "__main__":
    main()
