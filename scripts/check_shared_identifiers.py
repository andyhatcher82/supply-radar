"""Do different real operators genuinely share phones and domains?

This decides whether the 22 missed opportunities are a generator artefact or a
real property of the market. If real operators share identifiers, then "exact
phone match implies same business" is simply false here, and treating it as a
hard key is a design error rather than a tuning problem.

Uses ONLY the real discovered places. No synthetic data involved.

    python scripts/check_shared_identifiers.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.models import DiscoveredPlace  # noqa: E402
from supply_radar.normalise import normalise_phone, registrable_domain  # noqa: E402


def main() -> None:
    places = [
        DiscoveredPlace(**p)
        for p in json.loads(
            Path("data/split_places.json").read_text(encoding="utf-8")
        )
    ]
    print(f"{len(places)} real discovered places in Split\n")

    by_phone: dict[str, list[DiscoveredPlace]] = {}
    by_domain: dict[str, list[DiscoveredPlace]] = {}

    for p in places:
        if p.phone:
            key = normalise_phone(p.phone, "HR")
            if key:
                by_phone.setdefault(key, []).append(p)
        if p.website:
            key = registrable_domain(p.website)
            if key:
                by_domain.setdefault(key, []).append(p)

    shared_phone = {k: v for k, v in by_phone.items() if len(v) > 1}
    shared_domain = {k: v for k, v in by_domain.items() if len(v) > 1}

    with_phone = sum(len(v) for v in by_phone.values())
    with_domain = sum(len(v) for v in by_domain.values())
    affected_phone = sum(len(v) for v in shared_phone.values())
    affected_domain = sum(len(v) for v in shared_domain.values())

    print("PHONE")
    print(f"  places with a phone            {with_phone}")
    print(f"  distinct phone numbers         {len(by_phone)}")
    print(f"  numbers used by 2+ businesses  {len(shared_phone)}")
    print(f"  places affected                {affected_phone} "
          f"({affected_phone / max(1, with_phone):.1%})")
    print()

    print("DOMAIN")
    print(f"  places with a website          {with_domain}")
    print(f"  distinct registrable domains   {len(by_domain)}")
    print(f"  domains used by 2+ businesses  {len(shared_domain)}")
    print(f"  places affected                {affected_domain} "
          f"({affected_domain / max(1, with_domain):.1%})")
    print()

    print("examples of a shared phone across DIFFERENT businesses")
    for key, group in list(shared_phone.items())[:6]:
        print(f"  {key}")
        for p in group:
            print(f"      {p.name[:52]:<54} {p.website or '-'}")
        print()

    print("examples of a shared domain across DIFFERENT businesses")
    for key, group in list(shared_domain.items())[:6]:
        print(f"  {key}")
        for p in group:
            print(f"      {p.name[:52]:<54} {p.phone or '-'}")
        print()

    verdict = (
        "REAL market property: hard keys are not safe here"
        if shared_phone or shared_domain
        else "no sharing found; the misses are a generator artefact"
    )
    print(f"VERDICT: {verdict}")


if __name__ == "__main__":
    main()
