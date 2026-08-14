import random

import pytest

from supply_radar.models import DiscoveredPlace, Source

_NAMES = [
    "Šibenik Boat Excursions", "Adriatic Adventures", "Blue Cave Tours",
    "Dubrovnik Kayak Company", "Split Free Walking Tours", "Hvar Sailing Club",
    "Krka Rafting Đakovo", "Zadar Sea Organ Trips", "Plitvice Day Trips",
    "Korčula Wine & Food", "Istria Truffle Hunting", "Pula Diving Centre",
    "Makarska Quad Safari", "Rovinj Sunset Cruises", "Zagreb Street Food Tour",
    "Omiš Zipline Adventure", "Trogir Private Guides", "Brač Island Hopping",
    "Vis Military Tour", "Mljet Bike Rental",
]


@pytest.fixture
def places() -> list[DiscoveredPlace]:
    """A deterministic stand-in for real discovery output.

    Real Places data replaces this in the pipeline; the fixture exists so the
    matching and synthesis logic can be tested without spending API calls.
    """
    rng = random.Random(7)
    out = []
    for i, name in enumerate(_NAMES):
        out.append(
            DiscoveredPlace(
                source=Source.GOOGLE_PLACES,
                source_id=f"place_{i:03d}",
                name=name,
                lat=43.5 + rng.uniform(-1.0, 1.0),
                lng=16.4 + rng.uniform(-1.5, 1.5),
                address=f"Ulica kneza Domagoja {rng.randint(1, 60)}, Split",
                phone=f"+38521{rng.randint(100000, 999999)}",
                website=f"https://www.operator{i:03d}.hr",
                rating=round(rng.uniform(3.5, 5.0), 1),
                review_count=rng.randint(3, 900),
                categories=["tourist_attraction"],
                destination_id="split",
            )
        )
    return out
