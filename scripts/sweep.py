"""Run a real Places sweep over a destination.

    python scripts/sweep.py --lat 43.5081 --lng 16.4402 --radius 5 \
        --queries "boat tour,kayak tour" --cell 3 --depth 2

Prints the cost before doing anything, because a sweep that spends money
without telling you first is a defect.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.config import get_settings  # noqa: E402
from supply_radar.costs import CostLedger, estimate_sweep  # noqa: E402
from supply_radar.discovery.google_places import GooglePlacesSource  # noqa: E402
from supply_radar.geometry import SearchArea, cover  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, default=43.5081)
    ap.add_argument("--lng", type=float, default=16.4402)
    ap.add_argument("--radius", type=float, default=5.0, help="km")
    ap.add_argument("--queries", default="boat tour,kayak tour")
    ap.add_argument("--cell", type=float, default=3.0, help="cell half-side km")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--destination", default="split")
    ap.add_argument("--out", default=None)
    ap.add_argument("--yes", action="store_true", help="skip the confirmation")
    args = ap.parse_args()

    queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    area = SearchArea.from_circle(args.lat, args.lng, args.radius)
    cells = cover(area, args.cell)

    est = estimate_sweep(len(cells), len(queries))
    print("estimate before running")
    for k, v in est.items():
        print(f"  {k:<32} {v}")
    print()

    if not args.yes:
        print("re-run with --yes to execute")
        return

    settings = get_settings()
    ledger = CostLedger()

    def progress(done: int, remaining: int, found: int) -> None:
        print(f"\r  cells {done} done, {remaining} queued, {found} places",
              end="", flush=True)

    with GooglePlacesSource(settings.google_maps_api_key, ledger=ledger) as src:
        result = src.sweep(
            area,
            queries=queries,
            initial_half_side_km=args.cell,
            max_depth=args.depth,
            destination_id=args.destination,
            on_progress=progress,
        )

    print("\n")
    print("sweep result")
    for k, v in result.summary().items():
        print(f"  {k:<22} {v}")
    print()
    print("actual cost")
    for k, v in ledger.summary().items():
        print(f"  {k:<26} {v}")
    print()

    if result.unresolved_cells:
        print(f"WARNING {len(result.unresolved_cells)} cells still truncated at "
              f"max depth. Coverage is incomplete in those areas.")
        print()

    print("first 12 operators found")
    for p in result.places[:12]:
        stars = f"{p.rating}({p.review_count})" if p.rating else "unrated"
        print(f"  {p.name[:44]:<46} {stars:<12} {p.website or '-'}")

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([p.model_dump(mode="json") for p in result.places],
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nwrote {len(result.places)} places to {path}")


if __name__ == "__main__":
    main()
