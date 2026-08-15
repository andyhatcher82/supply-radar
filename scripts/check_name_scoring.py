"""Does IDF weighting fix the shared-token problem?

The case that prompted it: "Boat Split" scored a PERFECT 1.00 name match
against "Hemingway Boat Split", because token_set_ratio treats a subset as
identical and every word counted the same. Both share only "boat" and "split",
which nearly every operator in this market has.

    python scripts/check_name_scoring.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rapidfuzz import fuzz  # noqa: E402

from supply_radar.locales import load_locale  # noqa: E402
from supply_radar.matching import MatchIndex, name_similarity  # noqa: E402
from supply_radar.models import DiscoveredPlace  # noqa: E402
from supply_radar.normalise import normalise_name  # noqa: E402
from supply_radar.synth import generate_supplier_list  # noqa: E402

PAIRS = [
    ("Boat Split", "Hemingway Boat Split", "different businesses"),
    ("Split Boat Tours", "Split Boat Trips", "different businesses"),
    ("Condor Yachting", "Hemingway Boat Split", "different businesses"),
    ("Tinel Boat tours", "Tinel Tours d.o.o.", "SAME business"),
    ("Blue Cave Express", "Blue Cave Express obrt", "SAME business"),
    ("Adriatik Kayak Adventures", "Adriatic Kayak Adventures", "SAME, typo"),
    ("Split Boat Tours", "Boat Tours Split", "ambiguous, both generic"),
]


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
    suppliers, _ = generate_supplier_list(operators, seed=42)
    index = MatchIndex(suppliers, locale, extra_name_corpus=[p.name for p in operators])
    idf = index.idf

    print(f"corpus: {idf.n_docs} name records")
    generic = sorted(idf.generic_names)
    print(f"words treated as generic in this market ({len(generic)}):")
    print(f"  {', '.join(generic)}")
    print()

    print("most distinctive words seen (highest IDF):")
    top = sorted(idf.name.items(), key=lambda kv: -kv[1])[:10]
    print(f"  {', '.join(t for t, _ in top)}")
    print()

    print(f"  {'place':<28}{'supplier':<28}{'before':<9}{'after':<9}{'truth'}")
    for a, b, truth in PAIRS:
        na, nb = normalise_name(a, locale), normalise_name(b, locale)
        before = fuzz.token_set_ratio(na, nb) / 100
        after, detail = name_similarity(na, nb, idf)
        flag = "  <-- was wrong" if before > 0.9 and "different" in truth else ""
        print(f"  {a[:26]:<28}{b[:26]:<28}{before:<9.2f}{after:<9.2f}{truth}{flag}")
        print(f"      {detail[:104]}")
    print()


if __name__ == "__main__":
    main()
