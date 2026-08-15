"""Lead scoring on three separate axes.

Sales normally gets one number. That number hides why a lead ranks where it
does, cannot be tuned per destination, and quietly conflates three unrelated
questions:

  QUALITY    would we want this operator on the marketplace?
  READINESS  can they actually transact today?
  GAP FIT    does adding them fill a hole travellers are already looking for?

They are scored separately, each with its contributing evidence, and the
composite is presented as a SORT ORDER rather than a decision. A Destination
Specialist can see that a lead ranks high purely on gap fit despite thin
reviews, and act accordingly. One blended number cannot tell them that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from supply_radar.config import CONFIG_DIR

# Review counts reward operators who are already good at digital, which is
# exactly the opposite of what a supply team hunting the long tail wants. Two
# deliberate counterweights:
#
# 1. Volume is log-scaled and CAPPED at a modest number of reviews. Past the
#    cap, more reviews buy nothing. A 5,000-review operator should not bury a
#    genuinely excellent 60-review one.
# 2. Ratings are shrunk toward the destination mean in proportion to how few
#    reviews back them, so a 5.0 from three reviews does not outrank a 4.8
#    from four hundred.
REVIEW_VOLUME_CAP = 400
RATING_PRIOR_MEAN = 4.3
RATING_PRIOR_WEIGHT = 20

# Lead bands, expressed as what Sales should DO rather than as score ranges.
#
# Originally A>=0.65 / B>=0.45, set by judgement. Measured against the real
# Split leads, band A turned out to be UNREACHABLE: the best achievable
# composite in that destination is 0.651, and only for an operator that is
# simultaneously the strongest on quality and on readiness. No lead can be A,
# so the band carried no information.
#
# That is the same error the match thresholds were deliberately protected from,
# made in a different component: a cut-off chosen by intuition rather than read
# off the distribution.
#
# Recalibrated so the top band means something. Note the ceiling moves with the
# destination, because gap fit is legitimately 0.00 in a saturated category, so
# these are config and get recalibrated per destination pack.
BAND_A = 0.55
BAND_B = 0.42

BAND_MEANING = {
    "A": "Contact first. Strong on quality and readiness, and in a category "
         "with room for more supply.",
    "B": "Worth contacting. Solid on at least one axis, with a visible caveat "
         "on another. Check the evidence before calling.",
    "C": "Park for now. Either the evidence is thin, or the category is "
         "already well served and adding supply mostly cannibalises it.",
}

BOOKING_SCORES = {
    "online_booking": 1.0,
    "enquiry_form": 0.6,
    "phone_or_email_only": 0.3,
    "unclear": 0.15,
}


@dataclass
class Component:
    name: str
    value: float
    weight: float
    evidence: str

    @property
    def contribution(self) -> float:
        return self.value * self.weight


@dataclass
class Axis:
    name: str
    components: list[Component] = field(default_factory=list)
    note: str | None = None

    @property
    def score(self) -> float:
        total_weight = sum(c.weight for c in self.components)
        if not total_weight:
            return 0.0
        return sum(c.contribution for c in self.components) / total_weight

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "note": self.note,
            "components": [
                {
                    "name": c.name,
                    "value": round(c.value, 4),
                    "weight": c.weight,
                    "evidence": c.evidence,
                }
                for c in self.components
            ],
        }


@dataclass
class LeadScore:
    place_source_id: str
    quality: Axis
    readiness: Axis
    gap_fit: Axis
    weights: dict[str, float]

    @property
    def composite(self) -> float:
        return (
            self.quality.score * self.weights["quality"]
            + self.readiness.score * self.weights["readiness"]
            + self.gap_fit.score * self.weights["gap_fit"]
        )

    @property
    def band(self) -> str:
        c = self.composite
        if c >= BAND_A:
            return "A"
        if c >= BAND_B:
            return "B"
        return "C"

    def to_dict(self) -> dict:
        return {
            "place_source_id": self.place_source_id,
            "composite": round(self.composite, 4),
            "band": self.band,
            "quality": self.quality.to_dict(),
            "readiness": self.readiness.to_dict(),
            "gap_fit": self.gap_fit.to_dict(),
        }


@lru_cache
def load_demand(country: str = "croatia") -> dict:
    path = CONFIG_DIR / "demand" / f"{country}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the axes


def score_quality(rating: float | None, review_count: int | None) -> Axis:
    axis = Axis(name="quality")
    reviews = review_count or 0

    if rating is None:
        axis.note = "No rating available; quality cannot be assessed from discovery data."
        axis.components.append(
            Component("rating", 0.35, 0.6, "No rating on the listing")
        )
    else:
        # Shrink toward the prior in proportion to how thin the evidence is.
        adjusted = (
            reviews * rating + RATING_PRIOR_WEIGHT * RATING_PRIOR_MEAN
        ) / (reviews + RATING_PRIOR_WEIGHT)
        # 3.5 is roughly the floor for a functioning operator; 5.0 the ceiling.
        value = max(0.0, min(1.0, (adjusted - 3.5) / 1.5))
        axis.components.append(
            Component(
                "rating",
                value,
                0.6,
                f"{rating} from {reviews} reviews "
                f"(adjusted to {adjusted:.2f} for sample size)",
            )
        )

    volume = math.log10(1 + reviews) / math.log10(1 + REVIEW_VOLUME_CAP)
    axis.components.append(
        Component(
            "review volume",
            max(0.0, min(1.0, volume)),
            0.4,
            f"{reviews} reviews (capped at {REVIEW_VOLUME_CAP}; "
            f"more buys no further credit)",
        )
    )
    return axis


def score_readiness(
    website: str | None,
    phone: str | None,
    extract=None,
) -> Axis:
    axis = Axis(name="readiness")

    axis.components.append(
        Component(
            "website",
            1.0 if website else 0.0,
            0.2,
            website or "No website found",
        )
    )
    axis.components.append(
        Component(
            "phone",
            1.0 if phone else 0.0,
            0.1,
            phone or "No phone number listed",
        )
    )

    if extract is None:
        # The missing components are added explicitly at zero rather than
        # omitted. Omitting them would renormalise the axis over only the
        # signals that happen to be present, so an operator whose website could
        # not be read would score a PERFECT 1.0 on contactability alone.
        #
        # This is the third time this pipeline has been bitten by the same
        # thing: absent evidence must count as absent, never be excluded from
        # the denominator. Scoring it explicitly also means the UI shows a
        # reviewer exactly which evidence is missing.
        axis.note = (
            "Website not read, so booking capability, languages and email are "
            "unknown and score zero. Readiness here reflects contactability only."
        )
        for name, weight in (
            ("booking capability", 0.35),
            ("languages sold in", 0.15),
            ("contactable by email", 0.1),
            ("sells on a marketplace already", 0.1),
        ):
            axis.components.append(
                Component(name, 0.0, weight, "Not assessed: website not read")
            )
        return axis

    axis.components.append(
        Component(
            "booking capability",
            BOOKING_SCORES.get(extract.booking, 0.15),
            0.35,
            extract.booking.replace("_", " "),
        )
    )

    languages = [lang for lang in extract.languages if lang]
    axis.components.append(
        Component(
            "languages sold in",
            min(1.0, len(languages) / 3),
            0.15,
            ", ".join(languages) if languages else "None detected",
        )
    )
    axis.components.append(
        Component(
            "contactable by email",
            1.0 if extract.contact_email else 0.0,
            0.1,
            extract.contact_email or "No email published",
        )
    )
    axis.components.append(
        Component(
            "sells on a marketplace already",
            1.0 if extract.marketplace_presence else 0.0,
            0.1,
            ", ".join(extract.marketplace_presence)
            if extract.marketplace_presence
            else "No marketplace presence found on their site",
        )
    )
    return axis


def score_gap_fit(
    destination_id: str | None,
    category: str | None,
    country: str = "croatia",
) -> Axis:
    """How much unmet traveller demand this operator's destination and category
    represents, weighted by how much demand there is at all.

    A category with huge demand that is already well served scores low; a
    smaller category with almost no bookable supply scores high.
    """
    axis = Axis(name="gap_fit")
    table = load_demand(country)

    if not destination_id or not category or category in ("none", "other"):
        axis.note = "Destination or category unknown; scored at the country default."
        cell = table["default"]
        label = "country default"
    else:
        dest = table["destinations"].get(destination_id.lower(), {})
        cell = dest.get(category)
        if cell is None:
            cell = table["default"]
            label = f"{destination_id}/{category} not in the demand table, using default"
        else:
            label = f"{destination_id}/{category}"

    demand = cell["demand"]
    supply = cell["supply"]
    equilibrium = table["equilibrium_supply_ratio"] * demand

    unmet = max(0.0, 1 - (supply / equilibrium)) if equilibrium else 0.0
    demand_weight = demand / table["demand_scale_max"]
    value = max(0.0, min(1.0, unmet * demand_weight))

    axis.components.append(
        Component(
            "unmet demand",
            value,
            1.0,
            f"{label}: demand index {demand}, {supply} bookable operators "
            f"against {equilibrium:.0f} needed to serve it",
        )
    )
    if axis.note is None:
        axis.note = "Demand figures are synthetic; in production these come from Viator search logs."
    return axis


def score_lead(
    place,
    category: str | None = None,
    extract=None,
    weights: dict[str, float] | None = None,
    country: str = "croatia",
) -> LeadScore:
    weights = weights or {"quality": 0.35, "readiness": 0.35, "gap_fit": 0.30}
    return LeadScore(
        place_source_id=place.source_id,
        quality=score_quality(place.rating, place.review_count),
        readiness=score_readiness(place.website, place.phone, extract),
        gap_fit=score_gap_fit(place.destination_id, category, country),
        weights=weights,
    )
