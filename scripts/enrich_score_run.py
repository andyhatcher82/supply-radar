"""Enrich operator websites and score the resulting leads.

    python scripts/enrich_score_run.py --limit 40

Reads the classified sweep, keeps the operators, fetches their sites (cached on
disk, so re-runs are free), extracts structured fields, and ranks them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.classify import resolve_category  # noqa: E402
from supply_radar.config import get_settings  # noqa: E402
from supply_radar.costs import CostLedger  # noqa: E402
from supply_radar.enrich import SiteFetcher, enrich  # noqa: E402
from supply_radar.llm import LLMClient  # noqa: E402
from supply_radar.models import DiscoveredPlace  # noqa: E402
from supply_radar.scoring import score_lead  # noqa: E402
from supply_radar.taxonomy import top_level  # noqa: E402


UNMAPPED = "(unmapped)"
UNMAPPED_CAP = 3


def _stratified_sample(operators, places, limit):
    """Sample across Viator tier-1 categories, scarcest first.

    Taking the first N operators with a website inherits whatever the discovery
    sweep found most of. In Split that is boat tours, and the result was a lead
    list of 39 boat tours and 1 walking tour in which every single lead scored
    gap_fit 0.00, because both categories are saturated. A 30%-weighted axis
    contributing nothing to any lead is not a scoring bug — the bands are
    calibrated for it — but it hides the argument the axis exists to make.

    So: take EVERY operator in the scarce categories, and let the dominant one
    absorb whatever is left. The dominant category's quota is therefore not
    chosen, it is the remainder, which is the same discipline the match
    thresholds follow. On the Split data that lands at 13 Food & Drink, 13
    Outdoor Activities, 2 Art & Culture, and 9 of the 118 Tours, Sightseeing &
    Cruises operators.

    That is the correct output rather than a flattering one: the composite ranks
    by opportunity, and adding a 40th boat tour to a saturated category is worth
    less than adding a first food tour. A lead list weighted by what discovery
    found most of would be ranking by volume, which is what a generic lead-gen
    tool does.
    """
    from collections import defaultdict

    def tier_of(c) -> str:
        place = places[c["place_source_id"]]
        category = resolve_category(place, c.get("experience_type"))
        try:
            return top_level(category) or UNMAPPED
        except Exception:
            return UNMAPPED

    by_tier: dict[str, list] = defaultdict(list)
    for c in operators:
        if not places[c["place_source_id"]].website:
            continue  # cannot be enriched, so it cannot be scored on readiness
        by_tier[tier_of(c)].append(c)

    if not by_tier:
        return operators[:limit]

    # Everything except the largest tier is taken whole, so the dominant tier's
    # quota is the remainder rather than a number anyone picked.
    dominant = max(by_tier, key=lambda t: len(by_tier[t]))

    chosen = []
    for tier, members in sorted(by_tier.items(), key=lambda kv: len(kv[1])):
        if tier == dominant:
            continue
        # Uncategorised operators are capped. A few are worth showing, because
        # pretending every operator resolves cleanly would be its own dishonesty,
        # but ten of them would crowd out the categories that carry gap fit.
        take = UNMAPPED_CAP if tier == UNMAPPED else len(members)
        chosen.extend(members[: min(take, max(0, limit - len(chosen)))])

    chosen.extend(by_tier[dominant][: max(0, limit - len(chosen))])

    print("stratified sample across Viator tier-1:")
    counts: dict[str, int] = defaultdict(int)
    for c in chosen:
        counts[tier_of(c)] += 1
    for tier, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        note = "  <- remainder" if tier == dominant else ""
        print(f"  {n:3d} of {len(by_tier[tier]):3d} available   {tier}{note}")
    print()
    return chosen[:limit]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--places", default="data/split_places.json")
    ap.add_argument("--classified", default="data/split_classified.json")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--out", default="data/split_leads.json")
    ap.add_argument(
        "--stratify",
        action="store_true",
        help="Sample across Viator tier-1 categories instead of taking the "
             "first N with a website. See _stratified_sample.",
    )
    args = ap.parse_args()

    places = {
        p["source_id"]: DiscoveredPlace(**p)
        for p in json.loads(Path(args.places).read_text(encoding="utf-8"))
    }
    classified = json.loads(Path(args.classified).read_text(encoding="utf-8"))

    operators = [c for c in classified if c["verdict"] == "experience_operator"]
    # Sites first: an operator with no website cannot be enriched, and putting
    # them last keeps the sample informative.
    operators.sort(key=lambda c: places[c["place_source_id"]].website is None)

    if args.stratify:
        operators = _stratified_sample(operators, places, args.limit)
    elif args.limit:
        operators = operators[: args.limit]

    print(f"{len(operators)} operators to enrich\n")

    settings = get_settings()
    ledger = CostLedger()
    llm = LLMClient(settings.anthropic_api_key, ledger=ledger)

    def progress(done: int, total: int) -> None:
        print(f"\r  fetching {done}/{total}", end="", flush=True)

    started = time.time()
    with SiteFetcher() as fetcher:
        run = enrich(
            [(c["place_source_id"], places[c["place_source_id"]].website)
             for c in operators],
            llm,
            fetcher=fetcher,
            on_progress=progress,
        )
    elapsed = time.time() - started

    print("\n")
    print("enrichment")
    for k, v in run.summary().items():
        print(f"  {k:<22} {v}")
    print(f"  {'elapsed_seconds':<22} {elapsed:.1f}")
    print()

    usage = ledger.llm.get(llm.model)
    if usage:
        print("cost")
        print(f"  model USD           {ledger.llm_usd():.4f}")
        print(f"  cache hit rate      {usage.cache_hit_rate:.1%}")
        print(f"  USD per operator    {ledger.llm_usd() / max(1, usage.calls):.5f}")
        print()

    scored = []
    for c in operators:
        pid = c["place_source_id"]
        result = run.results.get(pid)
        category = resolve_category(places[pid], c.get("experience_type"))
        score = score_lead(
            places[pid],
            category,
            result.extract if result else None,
        )
        c["resolved_category"] = category
        scored.append((score.composite, score, places[pid], c, result))

    scored.sort(key=lambda t: -t[0])

    bands: dict[str, int] = {}
    for _, s, _, _, _ in scored:
        bands[s.band] = bands.get(s.band, 0) + 1
    print(f"bands  {bands}\n")

    print("top 12 leads")
    print(f"  {'operator':<34}{'comp':<7}{'qual':<7}{'ready':<7}{'gap':<7}{'band'}")
    for composite, s, place, c, _ in scored[:12]:
        print(f"  {place.name[:32]:<34}{composite:<7.2f}"
              f"{s.quality.score:<7.2f}{s.readiness.score:<7.2f}"
              f"{s.gap_fit.score:<7.2f}{s.band}")
    print()

    print("bottom 4, for contrast")
    for composite, s, place, c, _ in scored[-4:]:
        print(f"  {place.name[:32]:<34}{composite:<7.2f}"
              f"{s.quality.score:<7.2f}{s.readiness.score:<7.2f}"
              f"{s.gap_fit.score:<7.2f}{s.band}")
    print()

    top = scored[0]
    print(f"evidence trail for the top lead: {top[2].name}")
    for axis in (top[1].quality, top[1].readiness, top[1].gap_fit):
        print(f"  {axis.name} = {axis.score:.2f}")
        for comp in axis.components:
            print(f"    {comp.name:<32} {comp.value:.2f}  {comp.evidence[:78]}")
    print()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(
            [
                {
                    "name": place.name,
                    "website": place.website,
                    "destination": place.destination_id,
                    "experience_type": c.get("experience_type"),
                    **s.to_dict(),
                    "extract": r.extract.model_dump() if r and r.extract else None,
                }
                for _, s, place, c, r in scored
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(scored)} scored leads to {args.out}")


if __name__ == "__main__":
    main()
