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
from collections import Counter, defaultdict
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
    identity_domain,
    normalise_name,
    normalise_phone,
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

# Token weighting.
#
# The original name score used token_set_ratio over equally-weighted tokens,
# which is wrong twice over in a market like this. It returns a PERFECT score
# when one name's tokens are a subset of the other's, so "boat split" scored
# 1.00 against "hemingway boat split". And it treats every word as equally
# informative, when "boat", "split" and "tours" appear in most operator names
# in this corpus and carry no identity at all.
#
# Tokens are now weighted by inverse document frequency, computed over the
# actual corpus. A word appearing in many records tells you almost nothing; a
# rare one like "hemingway" tells you almost everything. The same applies to
# addresses, where half the boat operators in Split share the Riva.
#
# A token appearing in more than this share of records is treated as generic
# for this market. The absolute floor stops a tiny corpus declaring everything
# generic.
GENERIC_TOKEN_SHARE = 0.10
GENERIC_TOKEN_MIN_DOCS = 3

# Two tokens count as the same word above this character similarity, so
# "adriatik" still matches "adriatic".
TOKEN_FUZZ_FLOOR = 85

# When BOTH names reduce to nothing but market-generic words, the name cannot
# establish identity however similar it looks. Capped below the review band so
# corroborating signals have to carry it.
GENERIC_ONLY_CAP = 0.40

# How much the names must agree before a shared phone number is allowed to
# decide a match on its own. Set from the real Split data: the colliding pairs
# there ("Split Boat Trips" vs "semiSUBMARINE Split", "Condor Yachting" vs
# "Hemingway Boat Split") agree well below this, while genuine same-business
# pairs sit well above it.
PHONE_CORROBORATION_NAME_FLOOR = 0.70

# The same floor, applied to domains that are demonstrably shared.
#
# identity_domain already refuses to treat a bare builder domain as identity,
# keeping the full host so that two operators on wixsite.com do not collide.
# That was necessary and it was not sufficient: measured on the real Split
# sweep, EIGHT different operators list the same full host,
# tantulika28.wixsite.com, and five more share cro-hr.com. A shared booking
# agent's site is one host serving many businesses, and the subdomain does not
# separate them because there is only one subdomain.
#
# Left uncorroborated this was not a small residue. It was ALL of it: every one
# of the 7 remaining missed opportunities, the expensive error, came from this
# single host. Phone was demoted for exactly this reason in Correction 7 and
# domain simply never got the same treatment.
#
# Which hosts are shared is learned from the corpus rather than hardcoded, the
# same way generic name tokens are. A curated builder list cannot know that
# cro-hr.com is a Croatian agency portal, but counting can.
DOMAIN_CORROBORATION_NAME_FLOOR = 0.70

# Blocking grid, roughly 5 km of latitude. Coarse on purpose: neighbours are
# searched too, so the cost of a slightly loose grid is a few extra comparisons,
# whereas the cost of a tight one is a missed match.
GEO_CELL_DEG = 0.05


@dataclass(frozen=True)
class MatchThresholds:
    """Band boundaries. Deliberately configurable, because the right values are
    an empirical question answered by the threshold sweep, not a constant.

    The defaults are read straight off that sweep, run as a two-dimensional
    grid and scored against a stated cost model in analyst-minutes.

    They were originally 0.90 and 0.65, chosen from one-dimensional sweeps that
    held the other boundary fixed. That hid the real interaction, and it was
    calibrated on a population that still included car parks and museums. Once
    classification was moved ahead of matching, the remaining population was
    167 mutually-confusable Split tour operators, and the old settings cost
    52 human reviews to deliver MORE missed operators, not fewer.

    Re-read again after names and addresses moved to IDF weighting. That change
    shifted the whole score distribution: genuine matches still score high,
    while pairs sharing only market-generic words ("boat", "split") collapsed
    from near-perfect to near-zero. A more discriminating score needs tighter
    bands, not the same ones, so 0.94/0.75 became 0.80/0.65.

    Result on the real operator population: 7 missed, 4 reviews (2.4%), 4
    wasted calls. Robust: it stays cheapest even when a missed operator is
    valued no higher than a wasted call, the softest and most dominant number
    in the cost model.

    The review queue is small because the deterministic layer got better, not
    because humans were designed out. Worth saying plainly rather than quietly
    enjoying the number.

    Calibrated on Split. Per-locale recalibration is a stated day-2 item, and
    these are config rather than constants for exactly that reason.
    """

    high: float = 0.80
    low: float = 0.65

    def band(self, score: float) -> MatchVerdict:
        if score >= self.high:
            return MatchVerdict.EXISTING
        if score <= self.low:
            return MatchVerdict.NET_NEW
        return MatchVerdict.NEEDS_REVIEW


@dataclass
class TokenIdf:
    """How much identity each token actually carries, learned from the corpus.

    Built once per index. `generic_*` are the tokens so common in this market
    that sharing one is not evidence of anything.
    """

    name: dict[str, float]
    address: dict[str, float]
    n_docs: int
    generic_names: frozenset[str]
    generic_address: frozenset[str]

    @property
    def unseen_idf(self) -> float:
        """A token absent from the corpus is maximally distinctive."""
        return math.log((self.n_docs + 1) / 1)

    def name_weight(self, token: str) -> float:
        return self.name.get(token, self.unseen_idf)

    def address_weight(self, token: str) -> float:
        return self.address.get(token, self.unseen_idf)

    def explain_generic(self, tokens: set[str]) -> str:
        hits = sorted(tokens & self.generic_names)
        return ", ".join(hits)


def build_idf(name_docs: list[set[str]], address_docs: list[set[str]]) -> TokenIdf:
    n = max(1, len(name_docs))

    def table(docs: list[set[str]]) -> tuple[dict[str, float], frozenset[str]]:
        df: dict[str, int] = defaultdict(int)
        for doc in docs:
            for token in doc:
                df[token] += 1
        idf = {t: math.log((n + 1) / (1 + c)) for t, c in df.items()}
        generic = frozenset(
            t
            for t, c in df.items()
            if c >= GENERIC_TOKEN_MIN_DOCS and c / n > GENERIC_TOKEN_SHARE
        )
        return idf, generic

    name_idf, generic_names = table(name_docs)
    addr_idf, generic_addr = table(address_docs)
    return TokenIdf(
        name=name_idf,
        address=addr_idf,
        n_docs=n,
        generic_names=generic_names,
        generic_address=generic_addr,
    )


def _weighted_dice(a: set[str], b: set[str], weight) -> float:
    """Dice coefficient over IDF-weighted tokens, tolerant of typos.

    Symmetric, so a truncated name is not silently rewarded the way a subset
    match is under token_set_ratio.
    """
    total_a = sum(weight(t) for t in a)
    total_b = sum(weight(t) for t in b)
    if not total_a or not total_b:
        return 0.0

    matched = 0.0
    remaining = set(b)
    # Most distinctive first, so the rare token claims its partner before a
    # common one can consume it.
    for ta in sorted(a, key=lambda t: -weight(t)):
        best, best_ratio = None, 0
        for tb in remaining:
            r = fuzz.ratio(ta, tb)
            if r > best_ratio:
                best, best_ratio = tb, r
        if best is not None and best_ratio >= TOKEN_FUZZ_FLOOR:
            matched += min(weight(ta), weight(best)) * (best_ratio / 100)
            remaining.discard(best)

    return max(0.0, min(1.0, 2 * matched / (total_a + total_b)))


def name_similarity(
    place_norm: str, supplier_norm: str, idf: TokenIdf | None
) -> tuple[float, str]:
    """Name agreement, weighted by how distinctive the shared words are."""
    a, b = set(place_norm.split()), set(supplier_norm.split())
    if not a or not b:
        return 0.0, "no usable name on one side"

    if idf is None:
        return fuzz.token_set_ratio(place_norm, supplier_norm) / 100.0, (
            f"'{place_norm}' vs '{supplier_norm}'"
        )

    a_dist, b_dist = a - idf.generic_names, b - idf.generic_names

    if a_dist and b_dist:
        sim = _weighted_dice(a_dist, b_dist, idf.name_weight)
        shared = sorted(a_dist & b_dist)
        detail = (
            f"distinctive words {sorted(a_dist)} vs {sorted(b_dist)}"
            + (f", shared: {shared}" if shared else ", none in common")
        )
        generic = idf.explain_generic(a & b)
        if generic:
            detail += f" (ignoring '{generic}', common to this market)"
        return sim, detail

    if not a_dist and not b_dist:
        raw = fuzz.token_set_ratio(place_norm, supplier_norm) / 100.0
        return min(GENERIC_ONLY_CAP, raw), (
            f"both names are only market-generic words "
            f"('{idf.explain_generic(a | b)}'), so the name cannot establish identity"
        )

    lone = sorted(a_dist or b_dist)
    return GENERIC_ONLY_CAP * 0.5, (
        f"only one side carries a distinguishing word ({lone}); the rest is "
        f"generic to this market"
    )


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

    def __init__(
        self,
        suppliers: list[SupplierRecord],
        locale: LocalePack,
        extra_name_corpus: list[str] | None = None,
        extra_domain_corpus: list[str | None] | None = None,
    ):
        self.locale = locale
        self.keys: dict[str, SupplierKeys] = {}
        self._by_domain: dict[str, list[str]] = defaultdict(list)
        self._by_phone: dict[str, list[str]] = defaultdict(list)
        self._by_token: dict[str, list[str]] = defaultdict(list)
        self._by_cell: dict[tuple[int, int], list[str]] = defaultdict(list)

        for s in suppliers:
            norm = normalise_name(s.display_name, locale)
            domain = identity_domain(s.website) if s.website else None
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

        # Learn which words actually carry identity in THIS market. Extra
        # corpus (the discovered places) makes the estimate better, because a
        # supplier list alone under-counts how common "boat" really is.
        name_docs = [set(k.norm_name.split()) for k in self.keys.values()]
        addr_docs = [set(k.address_tokens) for k in self.keys.values()]
        for text in extra_name_corpus or ():
            name_docs.append(set(normalise_name(text, locale).split()))
        self.idf = build_idf(name_docs, addr_docs)

        # Learn which hosts serve more than one business, on either side of the
        # join. Two references to one host across the two sides is what a
        # genuine match looks like, so that is not evidence of sharing; two
        # businesses on the SAME side is.
        discovered_domains: Counter[str] = Counter()
        for website in extra_domain_corpus or ():
            if website:
                d = identity_domain(website)
                if d:
                    discovered_domains[d] += 1
        self.shared_domains: frozenset[str] = frozenset(
            {d for d, ids in self._by_domain.items() if len(set(ids)) > 1}
            | {d for d, n in discovered_domains.items() if n > 1}
        )

    def candidates(self, place: DiscoveredPlace) -> list[SupplierKeys]:
        ids: set[str] = set()

        domain = identity_domain(place.website) if place.website else None
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
    place: DiscoveredPlace,
    keys: SupplierKeys,
    locale: LocalePack,
    idf: TokenIdf | None = None,
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
        name_sim, name_detail = name_similarity(place_norm, keys.norm_name, idf)
        parts.append((W_NAME, name_sim))
        evidence.append(
            MatchEvidence(
                signal="name",
                detail=name_detail,
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
        if idf is None:
            overlap = len(place_addr & keys.address_tokens)
            union = len(place_addr | keys.address_tokens)
            sim = overlap / union if union else 0.0
            detail = f"{overlap} of {union} tokens shared"
        else:
            # Same problem as names, and worse: most boat operators in Split
            # list the Riva, so plain token overlap scores them all as
            # neighbours on the same premises.
            sim = _weighted_dice(
                place_addr, keys.address_tokens, idf.address_weight
            )
            shared = sorted(place_addr & keys.address_tokens)
            common = sorted(
                (place_addr & keys.address_tokens) & idf.generic_address
            )
            detail = f"shared: {shared}" if shared else "no address words in common"
            if common:
                detail += (
                    f" — but {common} appear on many operators here, so they "
                    f"count for little"
                )
        parts.append((W_ADDRESS, sim))
        evidence.append(
            MatchEvidence(
                signal="address",
                detail=detail,
                contribution=round(sim, 3),
            )
        )

    if not parts:
        return 0.0, evidence

    total_weight = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / total_weight

    place_domain = identity_domain(place.website) if place.website else None
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
    place: DiscoveredPlace,
    keys: SupplierKeys,
    locale: LocalePack,
    shared_domains: frozenset[str] = frozenset(),
) -> tuple[str, str] | None:
    """Return (signal, detail) when a single key is decisive on its own."""
    place_norm = normalise_name(place.name, locale)

    def name_agreement() -> float:
        if not place_norm or not keys.norm_name:
            return 0.0
        return fuzz.token_set_ratio(place_norm, keys.norm_name) / 100

    place_domain = identity_domain(place.website) if place.website else None
    if place_domain and keys.domain and place_domain == keys.domain:
        # A host used by only one business is identity. A host used by several
        # is an address, and needs the name to say which tenant this is.
        if place_domain not in shared_domains:
            return "domain", place_domain
        agreement = name_agreement()
        if agreement >= DOMAIN_CORROBORATION_NAME_FLOOR:
            return (
                "domain and name",
                f"{place_domain} with {agreement:.0%} name agreement",
            )

    # Phone alone is NOT identity in this market, and that is measured rather
    # than assumed: 13% of real Split operators share a number with a different
    # business. Small operators are fronted by shared agencies, and a booking
    # kiosk on the Riva sells for several boats off one line.
    #
    # So a phone match must be corroborated by the name before it can decide on
    # its own. Uncorroborated, it falls through to fuzzy scoring, where it is
    # still a strong positive signal but cannot single-handedly declare an
    # operator already-on-file.
    place_phone = normalise_phone(place.phone, locale.phone_region) if place.phone else None
    if place_phone and keys.phone and place_phone == keys.phone:
        agreement = name_agreement()
        if agreement >= PHONE_CORROBORATION_NAME_FLOOR:
            return (
                "phone and name",
                f"{place_phone} with {agreement:.0%} name agreement",
            )

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
        hit = _hard_key_match(place, keys, locale, index.shared_domains)
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

    scored = [(score_pair(place, k, locale, index.idf), k) for k in candidates]
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
    # The discovered names go into the corpus too. A supplier list alone
    # under-counts how ordinary a word like "boat" really is in this market.
    index = MatchIndex(
        suppliers,
        locale,
        extra_name_corpus=[p.name for p in places],
        extra_domain_corpus=[p.website for p in places],
    )
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
            domain=identity_domain(s.website) if s.website else None,
            phone=normalise_phone(s.phone, locale.phone_region) if s.phone else None,
            address_tokens=_address_tokens(s.address, locale),
        )
        score, _ = score_pair(place, keys, locale)
        if score > best_score:
            best_score, best_id = score, s.supplier_id
    return best_score, best_id
