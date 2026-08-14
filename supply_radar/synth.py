"""Synthetic Viator supplier list, with a hidden answer key.

We do not have Viator's supplier list, so this stands in for it. The point is
not realism for its own sake: it is that because we know exactly which records
were seeded from which discovered operator, match precision and recall become
measurable rather than asserted.

The corruption catalogue below was written from how CRM records actually drift
from reality, and deliberately written BEFORE the matcher. Designing the
corruptions after seeing what the matcher handles would rig the result and make
the precision figure worthless.

Three parts, each doing a different job:

  seeded          real discovered operators, degraded   -> tests recall
  phantoms        suppliers never discovered at all     -> tests we do not force a match
  hard negatives  confusable names, genuinely different -> tests precision
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta, timezone

from supply_radar.models import DiscoveredPlace, GroundTruth, SupplierRecord

# Every entry is a way a real CRM row ends up differing from what a discovery
# source sees today. Named so they can be reported per record in the UI.
CORRUPTIONS = [
    "diacritics_stripped",
    "legal_suffix_added",
    "legal_suffix_removed",
    "legal_name_substituted",
    "phone_reformatted",
    "phone_stale",
    "phone_missing",
    "website_www_variant",
    "website_subdomain",
    "website_stale_domain",
    "website_missing",
    "address_abbreviated",
    "address_moved",
    "ampersand_variant",
    "typo_transposition",
    "case_flattened",
    "name_truncated",
]

DIACRITIC_MAP = str.maketrans(
    {"č": "c", "ć": "c", "đ": "d", "š": "s", "ž": "z",
     "Č": "C", "Ć": "C", "Đ": "D", "Š": "S", "Ž": "Z"}
)

CROATIAN_SURNAMES = [
    "Horvat", "Kovačević", "Babić", "Marić", "Jurić", "Novak", "Kovačić",
    "Vuković", "Knežević", "Petrović", "Matić", "Tomić", "Perić", "Blažević",
]
CROATIAN_FORENAMES = [
    "Ivan", "Marko", "Ana", "Petar", "Josip", "Marija", "Luka", "Tomislav",
    "Ivana", "Nikola", "Katarina", "Damir",
]
LEGAL_FORMS = ["d.o.o.", "j.d.o.o.", "obrt", "d.d."]

STREET_STEMS = [
    "Ulica kneza Domagoja", "Obala hrvatskog narodnog preporoda",
    "Poljička cesta", "Ulica Ivana Gundulića", "Šetalište Bačvice",
    "Vukovarska ulica", "Trg Republike", "Ulica Petra Preradovića",
]

PHANTOM_NAME_PARTS = (
    ["Jadran", "Adriatic", "Dalmatia", "Kvarner", "Istra", "Blue Wave",
     "Sea Star", "Riva", "Bura", "Maestral", "Levanat", "Galeb"],
    ["Excursions", "Boat Tours", "Travel", "Adventures", "Charter",
     "Experiences", "Diving", "Sailing"],
)


def _strip_diacritics(text: str) -> str:
    return text.translate(DIACRITIC_MAP)


def _transpose_two_letters(text: str, rng: random.Random) -> str:
    letters = [i for i, ch in enumerate(text) if ch.isalpha()]
    if len(letters) < 4:
        return text
    i = rng.choice(letters[1:-2])
    chars = list(text)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def _reformat_phone(phone: str, rng: random.Random) -> str:
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("385"):
        national = digits[3:]
    else:
        national = digits.lstrip("0")
    style = rng.choice(["local", "spaced", "slashed", "intl_00"])
    if style == "local":
        return f"0{national}"
    if style == "spaced":
        return f"+385 {national[:2]} {national[2:5]} {national[5:]}"
    if style == "slashed":
        return f"0{national[:2]}/{national[2:5]}-{national[5:]}"
    return f"00385{national}"


def _make_phone(rng: random.Random) -> str:
    area = rng.choice(["21", "20", "23", "22", "52", "1", "51"])
    return f"+385{area}{rng.randint(100000, 999999)}"


def _make_website(name: str, rng: random.Random) -> str:
    slug = re.sub(r"[^a-z0-9]", "", _strip_diacritics(name).lower())[:18]
    tld = rng.choice([".hr", ".com", ".hr", ".eu"])
    return f"https://www.{slug or 'operator'}{tld}"


def _make_address(rng: random.Random, city: str | None = None) -> str:
    return f"{rng.choice(STREET_STEMS)} {rng.randint(1, 90)}, {city or 'Split'}"


def _corrupt_name(name: str, applied: list[str], rng: random.Random) -> str:
    out = name

    if rng.random() < 0.55:
        out = _strip_diacritics(out)
        applied.append("diacritics_stripped")

    if rng.random() < 0.35:
        out = f"{out} {rng.choice(LEGAL_FORMS)}"
        applied.append("legal_suffix_added")
    elif rng.random() < 0.20:
        stripped = re.sub(
            r"\s+(d\.?o\.?o\.?|j\.?d\.?o\.?o\.?|d\.?d\.?|obrt)\s*$",
            "",
            out,
            flags=re.IGNORECASE,
        )
        if stripped != out:
            out = stripped
            applied.append("legal_suffix_removed")

    if "&" in out and rng.random() < 0.5:
        out = out.replace("&", "and")
        applied.append("ampersand_variant")

    if rng.random() < 0.12:
        out = _transpose_two_letters(out, rng)
        applied.append("typo_transposition")

    if rng.random() < 0.10:
        out = out.upper() if rng.random() < 0.5 else out.lower()
        applied.append("case_flattened")

    if rng.random() < 0.08 and len(out) > 22:
        out = out[:22].rstrip()
        applied.append("name_truncated")

    return out


def _corrupt_phone(phone: str | None, applied: list[str], rng: random.Random):
    if phone is None:
        return None
    roll = rng.random()
    if roll < 0.15:
        applied.append("phone_missing")
        return None
    if roll < 0.28:
        # Number changed since onboarding. The hard-key match must fail here,
        # which is the point.
        applied.append("phone_stale")
        return _make_phone(rng)
    if roll < 0.75:
        applied.append("phone_reformatted")
        return _reformat_phone(phone, rng)
    return phone


def _corrupt_website(site: str | None, applied: list[str], rng: random.Random):
    if site is None:
        return None
    roll = rng.random()
    if roll < 0.14:
        applied.append("website_missing")
        return None
    if roll < 0.24:
        applied.append("website_stale_domain")
        return _make_website(f"old{rng.randint(1, 99)}", rng)
    if roll < 0.45:
        applied.append("website_www_variant")
        return site.replace("https://www.", "http://").replace("https://", "http://")
    if roll < 0.58:
        applied.append("website_subdomain")
        return site.replace("://www.", "://booking.")
    return site


def _corrupt_address(address: str | None, applied: list[str], rng: random.Random,
                     city: str | None):
    if address is None:
        address = _make_address(rng, city)
    roll = rng.random()
    if roll < 0.30:
        applied.append("address_abbreviated")
        return re.sub(r"\bUlica\b", "ul.", address, flags=re.IGNORECASE)
    if roll < 0.42:
        applied.append("address_moved")
        return _make_address(rng, city)
    return address


def _phantom_name(rng: random.Random) -> str:
    first, second = PHANTOM_NAME_PARTS
    return f"{rng.choice(first)} {rng.choice(second)}"


def _confusable_name(name: str, rng: random.Random) -> str:
    """Produce a name that a careless matcher would merge with `name`, but which
    belongs to a genuinely different business."""
    tokens = name.split()
    if len(tokens) >= 2:
        keep = " ".join(tokens[:-1])
    else:
        keep = name
    tail = rng.choice(
        ["Sport", "Charter", "Rent", "Group", "Marine", "Transfers", "Shuttle"]
    )
    return f"{keep} {tail}"


def generate_supplier_list(
    places: list[DiscoveredPlace],
    *,
    seed: int = 42,
    seeded_fraction: float = 0.40,
    phantom_fraction: float = 0.15,
    hard_negative_fraction: float = 0.10,
) -> tuple[list[SupplierRecord], list[GroundTruth]]:
    """Build a synthetic supplier list plus its hidden answer key.

    Deterministic for a given seed and input, so results are reproducible and a
    reported precision figure can be re-derived by anyone with the repo.
    """
    rng = random.Random(seed)
    ordered = sorted(places, key=lambda p: p.source_id)
    shuffled = ordered[:]
    rng.shuffle(shuffled)

    n_seeded = int(len(shuffled) * seeded_fraction)
    seeded = shuffled[:n_seeded]
    remainder = shuffled[n_seeded:]

    n_hard = int(len(shuffled) * hard_negative_fraction)
    hard_sources = remainder[:n_hard]

    n_phantom = max(1, int(len(shuffled) * phantom_fraction)) if shuffled else 0

    suppliers: list[SupplierRecord] = []
    truth: list[GroundTruth] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"VS-{counter:05d}"

    # 1. Seeded from real discovered operators, then degraded.
    for place in seeded:
        applied: list[str] = []
        name = _corrupt_name(place.name, applied, rng)

        trading_name = None
        if rng.random() < 0.18:
            # CRM holds the registered entity while the discovery source shows
            # the trading name. Only the phone, site or location can save this.
            applied.append("legal_name_substituted")
            legal_name = (
                f"{rng.choice(CROATIAN_SURNAMES)} {rng.choice(LEGAL_FORMS)}"
            )
        else:
            legal_name = name

        suppliers.append(
            SupplierRecord(
                supplier_id=(sid := next_id()),
                legal_name=legal_name,
                trading_name=trading_name,
                address=_corrupt_address(place.address, applied, rng,
                                         place.destination_id),
                city=place.destination_id,
                phone=_corrupt_phone(place.phone, applied, rng),
                website=_corrupt_website(place.website, applied, rng),
                active=rng.random() > 0.06,
                onboarded_at=datetime.now(timezone.utc)
                - timedelta(days=rng.randint(90, 2200)),
            )
        )
        truth.append(
            GroundTruth(
                supplier_id=sid,
                place_source_id=place.source_id,
                corruptions=sorted(set(applied)),
            )
        )

    # 2. Phantoms. On the marketplace, invisible to every discovery source.
    for _ in range(n_phantom):
        name = _phantom_name(rng)
        suppliers.append(
            SupplierRecord(
                supplier_id=(sid := next_id()),
                legal_name=f"{name} {rng.choice(LEGAL_FORMS)}",
                address=_make_address(rng),
                city=None,
                phone=_make_phone(rng),
                website=_make_website(name, rng) if rng.random() > 0.4 else None,
                active=rng.random() > 0.25,
                onboarded_at=datetime.now(timezone.utc)
                - timedelta(days=rng.randint(400, 3000)),
            )
        )
        truth.append(
            GroundTruth(
                supplier_id=sid,
                place_source_id=None,
                note="phantom: on the marketplace but not visible to discovery",
            )
        )

    # 3. Hard negatives. Confusable with a discovered operator, but a different
    #    business. Drawn only from operators NOT seeded, so that matching one is
    #    unambiguously a false positive.
    for place in hard_sources:
        name = _confusable_name(place.name, rng)
        suppliers.append(
            SupplierRecord(
                supplier_id=(sid := next_id()),
                legal_name=f"{name} {rng.choice(LEGAL_FORMS)}",
                address=_make_address(rng, place.destination_id),
                city=place.destination_id,
                phone=_make_phone(rng),
                website=_make_website(name, rng),
                active=True,
                onboarded_at=datetime.now(timezone.utc)
                - timedelta(days=rng.randint(200, 1800)),
            )
        )
        truth.append(
            GroundTruth(
                supplier_id=sid,
                place_source_id=None,
                note=f"hard negative: confusable with {place.source_id}, "
                     f"different business",
            )
        )

    return suppliers, truth


def expected_verdicts(truth: list[GroundTruth]) -> dict[str, str]:
    """Collapse the answer key into place_source_id -> supplier_id.

    Any discovered place absent from this mapping is genuinely net-new.
    """
    return {
        g.place_source_id: g.supplier_id
        for g in truth
        if g.place_source_id is not None
    }
