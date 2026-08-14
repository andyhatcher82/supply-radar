"""Stand-in discovery output for development and testing.

Exists so the matching, scoring and evaluation layers can be built and measured
without spending Places API calls on every run. Real discovery replaces this
entirely; any figure computed from these places is a development baseline, not
a result, and is labelled as such wherever it is shown.
"""

from __future__ import annotations

import random
import re

from supply_radar.models import DiscoveredPlace, Source

TOWNS = [
    ("Split", 43.5081, 16.4402), ("Dubrovnik", 42.6507, 18.0944),
    ("Zadar", 44.1194, 15.2314), ("Šibenik", 43.7350, 15.8952),
    ("Hvar", 43.1729, 16.4412), ("Rovinj", 45.0811, 13.6387),
    ("Pula", 44.8666, 13.8496), ("Zagreb", 45.8150, 15.9819),
    ("Korčula", 42.9600, 17.1350), ("Omiš", 43.4447, 16.6892),
    ("Trogir", 43.5125, 16.2517), ("Makarska", 43.2969, 17.0178),
]

QUALIFIERS = [
    "Adriatic", "Blue", "Jadran", "Dalmatia", "Bura", "Maestral", "Galeb",
    "Riva", "Sea Star", "Old Town", "Sunset", "Island", "Coral", "Delfin",
]

ACTIVITIES = [
    "Boat Tours", "Kayak Adventures", "Diving Centre", "Wine Tasting",
    "Food Walks", "Sailing Charter", "Quad Safari", "Rafting", "Private Guides",
    "Island Hopping", "Sunset Cruises", "E-Bike Tours", "Truffle Hunting",
    "Sea Kayaking", "Zipline Adventure", "Fishing Trips",
]

STREETS = [
    "Ulica kneza Domagoja", "Obala hrvatskog narodnog preporoda",
    "Poljička cesta", "Ulica Ivana Gundulića", "Šetalište Bačvice",
    "Vukovarska ulica", "Trg Republike", "Ulica Petra Preradovića",
]

# Types Google actually returns for this kind of business, including the
# unhelpful ones. Classification has to cope with these being wrong.
CATEGORY_POOL = [
    ["travel_agency", "point_of_interest"],
    ["tourist_attraction", "point_of_interest"],
    ["point_of_interest", "establishment"],
    ["tourist_attraction", "establishment"],
]

AREA_CODES = {
    "Split": "21", "Dubrovnik": "20", "Zadar": "23", "Šibenik": "22",
    "Hvar": "21", "Rovinj": "52", "Pula": "52", "Zagreb": "1",
    "Korčula": "20", "Omiš": "21", "Trogir": "21", "Makarska": "21",
}


def _slug(text: str) -> str:
    ascii_text = (
        text.replace("č", "c").replace("ć", "c").replace("š", "s")
        .replace("ž", "z").replace("đ", "d")
    )
    return re.sub(r"[^a-z0-9]", "", ascii_text.lower())[:22]


def synthetic_places(count: int = 200, seed: int = 11) -> list[DiscoveredPlace]:
    rng = random.Random(seed)
    seen: set[str] = set()
    out: list[DiscoveredPlace] = []

    while len(out) < count:
        town, tlat, tlng = rng.choice(TOWNS)
        name = f"{rng.choice(QUALIFIERS)} {rng.choice(ACTIVITIES)}"
        if rng.random() < 0.35:
            name = f"{town} {name}"
        if name in seen:
            continue
        seen.add(name)

        i = len(out)
        has_site = rng.random() > 0.22
        has_phone = rng.random() > 0.12

        out.append(
            DiscoveredPlace(
                source=Source.GOOGLE_PLACES,
                source_id=f"place_{i:04d}",
                name=name,
                lat=round(tlat + rng.uniform(-0.05, 0.05), 6),
                lng=round(tlng + rng.uniform(-0.05, 0.05), 6),
                address=f"{rng.choice(STREETS)} {rng.randint(1, 90)}, {town}",
                phone=(
                    f"+385{AREA_CODES[town]}{rng.randint(100000, 999999)}"
                    if has_phone else None
                ),
                website=(
                    f"https://www.{_slug(name)}.hr" if has_site else None
                ),
                rating=round(rng.uniform(3.2, 5.0), 1),
                review_count=int(rng.lognormvariate(3.4, 1.2)),
                categories=rng.choice(CATEGORY_POOL),
                destination_id=town.lower(),
            )
        )

    return out
