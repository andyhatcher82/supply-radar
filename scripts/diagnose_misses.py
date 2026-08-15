"""Which signal is causing the missed opportunities?

A "missed opportunity" is the expensive error: a genuinely net-new operator
that the matcher declared already on file, so it never reaches Sales and
nothing surfaces the mistake.

The threshold sweep on real data showed these barely respond to the high
threshold, which means they are not coming from the fuzzy path at all. This
script attributes each one to the signal that actually decided it.

    python scripts/diagnose_misses.py
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


def main() -> None:
    places = [
        DiscoveredPlace(**p)
        for p in json.loads(
            Path("data/split_places.json").read_text(encoding="utf-8")
        )
    ]
    locale = load_locale("hr")
    suppliers, truth = generate_supplier_list(places, seed=42)
    answer = expected_verdicts(truth)
    results = match_all(places, suppliers, locale, MatchThresholds())

    place_by_id = {p.source_id: p for p in places}
    supplier_by_id = {s.supplier_id: s for s in suppliers}

    ev = evaluate(results, answer)
    print("headline")
    for k, v in ev.summary().items():
        print(f"  {k:<22} {v}")
    print()

    # A missed opportunity: we said EXISTING, truth says it was never on file.
    misses = [
        r
        for r in results
        if r.verdict is MatchVerdict.EXISTING and r.place_source_id not in answer
    ]
    print(f"{len(misses)} missed opportunities\n")

    by_signal: dict[str, int] = {}
    for r in misses:
        key = r.decided_by.value
        if r.evidence:
            key = f"{r.decided_by.value}:{r.evidence[0].signal}"
        by_signal[key] = by_signal.get(key, 0) + 1

    print("attributed to")
    for k, v in sorted(by_signal.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<34} {v}")
    print()

    print("worked examples")
    for r in misses[:8]:
        place = place_by_id.get(r.place_source_id)
        supplier = supplier_by_id.get(r.supplier_id) if r.supplier_id else None
        print(f"  DISCOVERED  {place.name if place else '?'}")
        print(f"              {place.address if place else ''}")
        print(f"              phone={place.phone if place else None} "
              f"site={place.website if place else None}")
        print(f"  MATCHED TO  {getattr(supplier, 'legal_name', '?')}")
        print(f"              {getattr(supplier, 'address', '')}")
        print(f"              phone={getattr(supplier, 'phone', None)} "
              f"site={getattr(supplier, 'website', None)}")
        print(f"  score={r.score:.3f} via {r.decided_by.value}")
        for e in r.evidence:
            print(f"    - {e.signal}: {e.detail}")
        print()

    # How much of the problem is name collision between DIFFERENT operators?
    names: dict[str, list[str]] = {}
    for p in places:
        key = p.name.strip().lower()
        names.setdefault(key, []).append(p.source_id)
    dupes = {k: v for k, v in names.items() if len(v) > 1}
    print(f"identical discovered names across different places: {len(dupes)}")
    for k, v in list(dupes.items())[:5]:
        print(f"  {k!r} x{len(v)}")


if __name__ == "__main__":
    main()
