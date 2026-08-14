"""Identity resolution: is this discovered operator already a supplier?

Three stages, in deliberate order of cost and certainty.

  1. Hard keys      exact agreement on a registrable domain, an E.164 phone, or
                    an identical normalised name at the same location. Certain
                    enough to decide alone. No model, no scoring.
  2. Fuzzy scoring  a transparent weighted combination of name, geography and
                    address, with explicit penalties where strong signals
                    actively disagree.
  3. Banding        high scores are matched, low scores are net-new, and the
                    middle is handed on for adjudication.

Nothing here calls an LLM. The model only ever sees the middle band, and it is
invoked by the caller, not by this module. That separation is the point: the
overwhelming majority of decisions are made by rules that are cheap, instant,
reproducible and explainable to a Destination Specialist.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from supply_radar.geometry import haversine_km
from supply_radar.locales import LocalePack, _depunctuate
from supply_radar.models import (
    DecidedBy,
    DiscoveredPlace,
    MatchEvidence,
    MatchResult,
    MatchVerdict,
    SupplierRecord,
)
from supply_radar.normalise import (
    fold_diacritics,
    normalise_name,
    normalise_phone,
    registrable_domain,
)

# Signal weights. Held here rather than buried in the scoring function so they
# can be surfaced in the UI and tuned per locale without touching logic.
W_NAME = 0.65
W_GEO = 0.20
W_ADDRESS = 0.15

# Disagreement is evidence too. Two businesses with different registered
# domains are usually different businesses, however similar the names look.
PENALTY_DOMAIN_CONFLICT = 0.20
PENALTY_PHONE_CONFLICT = 0.12

# Geography decays fast on purpose. In a dense tourist town every operator sits
# inside the same couple of square kilometres, so "500 m apart in Split" is not
# evidence of anything. A generous radius here quietly inflates the score of
# every unrelated pair in the same destination, which was measured pushing 39%
# of decisions into the review queue against a design target of 10-15%.
GEO_FULL_CREDIT_KM = 0.1
GEO_ZERO_CREDIT_KM = 2.0
SAME_PREMISES_KM = 0.2

# Identity requires name agreement. Location and address can corroborate a
# match but must never carry one on their own, or two nameless records at the
# same address would score a perfect match. Pairs failing the gate are capped
# below any sensible threshold rather than excluded, so they still appear in
# the evidence trail with a reason.
NAME_GATE = 0.45
NAME_GATE_CAP = 0.35

# Blocking grid, roughly 5 km of latitude. Coarse on purpose: neighbours are
# searched too, so the cost of a slightly loose grid is a few extra comparisons,
# whereas the cost of a tight one is a missed match.
GEO_CELL_DEG = 0.05


@dataclass(frozen=True)
class MatchThresholds:
    """Band boundaries. Deliberately configurable, because the right values are
    an empirical question answered by the threshold sweep, not a constant.

    The defaults are read straight off that sweep, and the two boundaries are
    chosen on different grounds because they control different costs.

    high=0.90 is where the expensive error disappears. Below it, operators that
    are genuinely absent from the marketplace start being written off as
    existing suppliers, which is silent and permanent. Raising it further buys
    nothing.

    low=0.65 is where the review queue becomes affordable. Dropping from 0.55
    to 0.65 cuts human review from roughly a third of all decisions to about a
    tenth, and costs one additional wasted Sales call. That is exactly the
    trade the asymmetry argument says to take: spend cheap, self-correcting
    errors to buy back human attention.
    """

    high: float = 0.90
    low: float = 0.65

    def band(self, score: float) -> MatchVerdict:
        if score >= self.high:
            return MatchVerdict.EXISTING
        if score <= self.low:
            return MatchVerdict.NET_NEW
        return MatchVerdict.NEEDS_REVIEW


@dataclass
class SupplierKeys:
    """Precomputed comparison keys for one supplier record."""

    supplier: SupplierRecord
    norm_name: str
    domain: str | None
    phone: str | None
    address_tokens: frozenset[str]


def _address_tokens(address: str | None, locale: LocalePack) -> frozenset[str]:
    if not address:
        return frozenset()
    folded = fold_diacritics(address.lower(), locale)
    tokens = {
        _depunctuate(t)
        for t in folded.replace(",", " ").split()
    }
    # Single characters and house numbers carry almost no discriminating power
    # and inflate overlap between unrelated addresses.
    return frozenset(t for t in tokens if len(t) > 2 and not t.isdigit())


def _geo_cell(lat: float, lng: float) -> tuple[int, int]:
    return (int(lat / GEO_CELL_DEG), int(lng / GEO_CELL_DEG))


def _geo_similarity(distance_km: float) -> float:
    if distance_km <= GEO_FULL_CREDIT_KM:
        return 1.0
    if distance_km >= GEO_ZERO_CREDIT_KM:
        return 0.0
    span = GEO_ZERO_CREDIT_KM - GEO_FULL_CREDIT_KM
    return 1.0 - (distance_km - GEO_FULL_CREDIT_KM) / span


class MatchIndex:
    """Blocking index over the supplier list.

    Comparing every place against every supplier is fine for one destination
    and hopeless for hundreds. Blocking keeps the comparison count roughly
    linear while preserving recall, which is tested directly against a brute
    force pass.
    """

    def __init__(self, suppliers: list[SupplierRecord], locale: LocalePack):
        self.locale = locale
        self.keys: dict[str, SupplierKeys] = {}
        self._by_domain: dict[str, list[str]] = defaultdict(list)
        self._by_phone: dict[str, list[str]] = defaultdict(list)
        self._by_token: dict[str, list[str]] = defaultdict(list)
        self._by_cell: dict[tuple[int, int], list[str]] = defaultdict(list)

        for s in suppliers:
            norm = normalise_name(s.display_name, locale)
            domain = registrable_domain(s.website) if s.website else None
            phone = normalise_phone(s.phone, locale.phone_region) if s.phone else None

            self.keys[s.supplier_id] = SupplierKeys(
                supplier=s,
                norm_name=norm,
                domain=domain,
                phone=phone,
                address_tokens=_address_tokens(s.address, locale),
            )

            if domain:
                self._by_domain[domain].append(s.supplier_id)
            if phone:
                self._by_phone[phone].append(s.supplier_id)
            for token in set(norm.split()):
                self._by_token[token].append(s.supplier_id)
            if s.lat is not None and s.lng is not None:
                self._by_cell[_geo_cell(s.lat, s.lng)].append(s.supplier_id)

    def candidates(self, place: DiscoveredPlace) -> list[SupplierKeys]:
        ids: set[str] = set()

        domain = registrable_domain(place.website) if place.website else None
        if domain:
            ids.update(self._by_domain.get(domain, ()))

        phone = normalise_phone(place.phone, self.locale.phone_region) if place.phone else None
        if phone:
            ids.update(self._by_phone.get(phone, ()))

        for token in set(normalise_name(place.name, self.locale).split()):
            ids.update(self._by_token.get(token, ()))

        cx, cy = _geo_cell(place.lat, place.lng)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                ids.update(self._by_cell.get((cx + dx, cy + dy), ()))

        return [self.keys[i] for i in ids]


def score_pair(
    place: DiscoveredPlace, keys: SupplierKeys, locale: LocalePack
) -> tuple[float, list[MatchEvidence]]:
    """Score one place against one supplier, returning the evidence as well.

    The evidence is not decoration. A Destination Specialist reviewing an
    ambiguous pair needs to see what drove the number, or they cannot overrule
    it with any confidence.
    """
    evidence: list[MatchEvidence] = []
    parts: list[tuple[float, float]] = []  # (weight, similarity)

    place_norm = normalise_name(place.name, locale)
    name_sim: float | None = None
    if place_norm and keys.norm_name:
        name_sim = fuzz.token_set_ratio(place_norm, keys.norm_name) / 100.0
        parts.append((W_NAME, name_sim))
        evidence.append(
            MatchEvidence(
                signal="name",
                detail=f"'{place_norm}' vs '{keys.norm_name}'",
                contribution=round(name_sim, 3),
            )
        )

    if keys.supplier.lat is not None and keys.supplier.lng is not None:
        distance = haversine_km(place.coords, (keys.supplier.lat, keys.supplier.lng))
        sim = _geo_similarity(distance)
        parts.append((W_GEO, sim))
        evidence.append(
            MatchEvidence(
                signal="location",
                detail=f"{distance:.2f} km apart",
                contribution=round(sim, 3),
            )
        )

    place_addr = _address_tokens(place.address, locale)
    if place_addr and keys.address_tokens:
        overlap = len(place_addr & keys.address_tokens)
        union = len(place_addr | keys.address_tokens)
        sim = overlap / union if union else 0.0
        parts.append((W_ADDRESS, sim))
        evidence.append(
            MatchEvidence(
                signal="address",
                detail=f"{overlap} of {union} tokens shared",
                contribution=round(sim, 3),
            )
        )

    if not parts:
        return 0.0, evidence

    total_weight = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / total_weight

    place_domain = registrable_domain(place.website) if place.website else None
    if place_domain and keys.domain and place_domain != keys.domain:
        score -= PENALTY_DOMAIN_CONFLICT
        evidence.append(
            MatchEvidence(
                signal="domain conflict",
                detail=f"{place_domain} vs {keys.domain}",
                contribution=-PENALTY_DOMAIN_CONFLICT,
            )
        )

    place_phone = normalise_phone(place.phone, locale.phone_region) if place.phone else None
    if place_phone and keys.phone and place_phone != keys.phone:
        score -= PENALTY_PHONE_CONFLICT
        evidence.append(
            MatchEvidence(
                signal="phone conflict",
                detail=f"{place_phone} vs {keys.phone}",
                contribution=-PENALTY_PHONE_CONFLICT,
            )
        )

    if name_sim is None or name_sim < NAME_GATE:
        if score > NAME_GATE_CAP:
            evidence.append(
                MatchEvidence(
                    signal="name gate",
                    detail=(
                        "no usable name agreement, so location and address "
                        "alone cannot establish identity"
                    ),
                    contribution=round(NAME_GATE_CAP - score, 3),
                )
            )
            score = NAME_GATE_CAP

    return max(0.0, min(1.0, score)), evidence


def _hard_key_match(
    place: DiscoveredPlace, keys: SupplierKeys, locale: LocalePack
) -> tuple[str, str] | None:
    """Return (signal, detail) when a single key is decisive on its own."""
    place_domain = registrable_domain(place.website) if place.website else None
    if place_domain and keys.domain and place_domain == keys.domain:
        return "domain", place_domain

    place_phone = normalise_phone(place.phone, locale.phone_region) if place.phone else None
    if place_phone and keys.phone and place_phone == keys.phone:
        return "phone", place_phone

    place_norm = normalise_name(place.name, locale)
    if (
        place_norm
        and keys.norm_name
        and place_norm == keys.norm_name
        and keys.supplier.lat is not None
        and keys.supplier.lng is not None
        and haversine_km(place.coords, (keys.supplier.lat, keys.supplier.lng))
        <= SAME_PREMISES_KM
    ):
        return "name and premises", place_norm

    return None


def match_place(
    place: DiscoveredPlace,
    index: MatchIndex,
    locale: LocalePack,
    thresholds: MatchThresholds | None = None,
) -> MatchResult:
    thresholds = thresholds or MatchThresholds()
    candidates = index.candidates(place)

    if not candidates:
        return MatchResult(
            place_source_id=place.source_id,
            verdict=MatchVerdict.NET_NEW,
            score=0.0,
            confidence=0.95,
            decided_by=DecidedBy.HARD_KEY,
            evidence=[
                MatchEvidence(
                    signal="no candidates",
                    detail="No supplier shares a name token, phone, domain or locality",
                )
            ],
        )

    for keys in candidates:
        hit = _hard_key_match(place, keys, locale)
        if hit:
            signal, detail = hit
            return MatchResult(
                place_source_id=place.source_id,
                supplier_id=keys.supplier.supplier_id,
                verdict=MatchVerdict.EXISTING,
                score=1.0,
                confidence=0.97,
                decided_by=DecidedBy.HARD_KEY,
                evidence=[
                    MatchEvidence(
                        signal=f"exact {signal}",
                        detail=f"{detail} matches {keys.supplier.display_name}",
                        contribution=1.0,
                    )
                ],
            )

    scored = [(score_pair(place, k, locale), k) for k in candidates]
    scored.sort(key=lambda item: item[0][0], reverse=True)
    (best_score, best_evidence), best = scored[0]

    verdict = thresholds.band(best_score)

    # Confidence reflects distance from the nearest band boundary, so a score
    # sitting right on a threshold is reported as uncertain even when the
    # verdict is technically decided.
    if verdict is MatchVerdict.EXISTING:
        confidence = min(1.0, 0.5 + (best_score - thresholds.high) * 2)
    elif verdict is MatchVerdict.NET_NEW:
        confidence = min(1.0, 0.5 + (thresholds.low - best_score) * 2)
    else:
        midpoint = (thresholds.high + thresholds.low) / 2
        half_span = max((thresholds.high - thresholds.low) / 2, 1e-9)
        confidence = 0.5 * (1 - abs(best_score - midpoint) / half_span)

    evidence = list(best_evidence)
    if len(scored) > 1:
        runner_score = scored[1][0][0]
        evidence.append(
            MatchEvidence(
                signal="runner-up",
                detail=(
                    f"next best {scored[1][1].supplier.display_name} "
                    f"at {runner_score:.2f}"
                ),
                contribution=round(runner_score, 3),
            )
        )

    return MatchResult(
        place_source_id=place.source_id,
        supplier_id=best.supplier.supplier_id
        if verdict is not MatchVerdict.NET_NEW
        else None,
        verdict=verdict,
        score=round(best_score, 4),
        confidence=round(max(0.0, min(1.0, confidence)), 3),
        decided_by=DecidedBy.FUZZY_SCORE,
        evidence=evidence,
    )


def match_all(
    places: list[DiscoveredPlace],
    suppliers: list[SupplierRecord],
    locale: LocalePack,
    thresholds: MatchThresholds | None = None,
) -> list[MatchResult]:
    index = MatchIndex(suppliers, locale)
    return [match_place(p, index, locale, thresholds) for p in places]


def brute_force_best(
    place: DiscoveredPlace, suppliers: list[SupplierRecord], locale: LocalePack
) -> tuple[float, str | None]:
    """Exhaustive comparison, used only to prove blocking loses nothing."""
    best_score, best_id = 0.0, None
    for s in suppliers:
        keys = SupplierKeys(
            supplier=s,
            norm_name=normalise_name(s.display_name, locale),
            domain=registrable_domain(s.website) if s.website else None,
            phone=normalise_phone(s.phone, locale.phone_region) if s.phone else None,
            address_tokens=_address_tokens(s.address, locale),
        )
        score, _ = score_pair(place, keys, locale)
        if score > best_score:
            best_score, best_id = score, s.supplier_id
    return best_score, best_id
