"""How many discovered operators can the licensing register actually vouch for?

The register is only worth wiring into scoring if it can be joined to the
operators we found. This measures the join rather than assuming it, and reports
the failures as loudly as the successes.

    python scripts/registry_check.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.locales import load_locale  # noqa: E402
from supply_radar.matching import build_idf  # noqa: E402
from supply_radar.models import DiscoveredPlace  # noqa: E402
from supply_radar.normalise import normalise_name  # noqa: E402
from supply_radar.registry import (  # noqa: E402
    entries_for_town,
    fetch_register,
    find_licence,
    parse_register,
    register_name,
    street_key,
)

DATA = Path("data")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    locale = load_locale("hr")

    places = [
        DiscoveredPlace(**p)
        for p in json.loads((DATA / "split_places.json").read_text(encoding="utf-8"))
    ]
    classified = {
        c["place_source_id"]: c
        for c in json.loads((DATA / "split_classified.json").read_text(encoding="utf-8"))
    }
    operators = [
        p
        for p in places
        if classified.get(p.source_id, {}).get("verdict") == "experience_operator"
    ]

    path = fetch_register(DATA / "registry_cache")
    entries = entries_for_town(parse_register(path), "Split")
    print(f"register file .................. {path.name}")
    print(f"licensed agencies in Split ..... {len(entries)}")
    print(f"discovered operators in Split .. {len(operators)}")
    print()

    # One IDF table over both sides, so "split" and "tours" carry no weight in
    # the corroboration either. Without this the join re-runs Correction 14.
    idf = build_idf(
        [set(normalise_name(p.name, locale).split()) for p in operators]
        + [set(register_name(e.legal_name, locale).split()) for e in entries],
        [],
    )

    matched, unmatched = [], []
    for place in operators:
        hit = find_licence(place.name, place.address, entries, locale, idf)
        (matched if hit else unmatched).append((place, hit))

    rate = len(matched) / max(len(operators), 1)
    print(f"CORROBORATED LICENCE MATCHES ... {len(matched)}/{len(operators)} ({rate:.1%})")
    print()

    by_address = sum(1 for _, h in matched if h.address_agreement)
    print(f"  of which address also agreed . {by_address}")
    print(f"  name-only (different premises) {len(matched) - by_address}")
    print()

    protection = Counter()
    for _, hit in matched:
        e = hit.entry
        protection[
            "both declared"
            if e.is_fully_protected
            else "liability only"
            if e.liability_insurance
            else "insolvency only"
            if e.insolvency_protection
            else "neither declared"
            if e.liability_insurance is False or e.insolvency_protection is False
            else "no declaration held"
        ] += 1
    print("regulatory position of the matched operators")
    for k, v in protection.most_common():
        print(f"  {k:<24} {v}")
    print()

    print("--- matched, with what the register adds ---")
    for place, hit in sorted(matched, key=lambda m: -m[1].name_agreement)[:12]:
        e = hit.entry
        print(f"  {hit.name_agreement:.2f}  {place.name[:44]}")
        print(f"        -> {e.legal_name[:60]}")
        print(f"           liability={e.liability_insurance} "
              f"insolvency={e.insolvency_protection} "
              f"addr_agreed={hit.address_agreement}")

    # The honest half. An unmatched operator is not an unlicensed one, and the
    # difference matters if this ever reaches a scoring axis.
    print()
    print("--- a sample of the operators the register could NOT vouch for ---")
    for place, _ in unmatched[:8]:
        key = street_key(place.address, locale)
        same_address = [e for e in entries if street_key(e.premises, locale) == key]
        note = (
            f"{len(same_address)} agency(s) at this address, none name-agreeing"
            if same_address
            else "no licensed agency at this address"
        )
        print(f"  {place.name[:50]}")
        print(f"      {note}")


if __name__ == "__main__":
    main()
