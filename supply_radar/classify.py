"""Is this discovered place actually an experience supplier?

Google Places returns museums, viewpoints, hotels, restaurants and car parks
alongside tour operators, and its type taxonomy is unreliable for this
question: a kayak operator is frequently tagged `tourist_attraction`, and so is
a cathedral.

Three-way split, cheapest first:

  1. Deterministic reject   obvious non-suppliers, free
  2. Deterministic accept   unambiguous operator types, free
  3. Model                  everything genuinely ambiguous

The deterministic accept is not trusted blindly. A configurable sample of it is
sent to the model anyway and the two verdicts compared, so the shortcut's error
rate is measured rather than assumed. A free shortcut that is quietly wrong
2% of the time is worse than no shortcut at all, because nothing surfaces it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from supply_radar.config import CONFIG_DIR
from supply_radar.llm import LLMClient
from supply_radar.models import DiscoveredPlace

GENERIC_CATEGORIES = {"", "other", "none", "unclear"}


@lru_cache
def load_query_categories(country: str = "croatia") -> dict[str, str]:
    path = CONFIG_DIR / "destinations" / f"{country}.yaml"
    pack = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {k.lower(): v for k, v in (pack.get("query_categories") or {}).items()}


def resolve_category(
    place: DiscoveredPlace,
    classified_type: str | None,
    country: str = "croatia",
) -> str | None:
    """Best available experience category for a place.

    The classifier's answer wins when it gave a specific one. Otherwise fall
    back to the search term that found the place, which is free and known
    because we chose the queries.

    Without this fallback, every operator accepted by the deterministic
    shortcut carried the category "other" and scored zero on gap fit, killing
    the axis for a third of the pipeline without any error appearing.
    """
    if classified_type and classified_type.lower() not in GENERIC_CATEGORIES:
        return classified_type.lower()

    query = ((place.raw or {}).get("matched_query") or "").lower()
    mapped = load_query_categories(country).get(query)
    if mapped and mapped not in GENERIC_CATEGORIES:
        return mapped
    return None


class Verdict(str, Enum):
    """Category labels, worded for the model rather than for the business.

    These were originally `supplier` / `attraction` / `out_of_scope`, matching
    the language Viator and the brief use. Measured against real Split data,
    that wording was wrong six times out of seven: in travel, "supplier"
    commonly means a B2B wholesale provider, so the model read a boat-tour
    operator as NOT a supplier and filed it under "attraction" instead.

    Renaming the categories so they cannot be misread fixed it outright. The
    label you give a category is itself a prompt, and a self-describing label
    beats a paragraph of definition correcting a misleading one. The business
    term "supplier" is still what the UI shows; only the model-facing value
    changed.
    """

    OPERATOR = "experience_operator"
    ATTRACTION = "attraction_only"
    NOT_RELEVANT = "not_relevant"


# Types where no experience operator could plausibly hide. Rejecting on these
# is free and safe.
REJECT_TYPES = {
    "parking", "gas_station", "atm", "bank", "hospital", "pharmacy",
    "supermarket", "convenience_store", "car_repair", "car_wash",
    "post_office", "school", "primary_school", "secondary_school",
    "university", "police", "fire_station", "cemetery", "storage",
    "real_estate_agency", "insurance_agency", "dentist", "doctor",
    "shoe_store", "jewelry_store", "book_store", "hair_care",
    "beauty_salon", "shopping_mall",
}

# Types that LOOK like rejects but genuinely hide operators, so they go to the
# model instead of being discarded.
#
# Found by measurement, not by inspection: "X Party Boat Split" is a Split
# boat-party operator that Google tags `night_club`. The original reject list
# threw it away silently. Likewise `lodging` covers agriturismos selling
# cookery classes, and `bar` covers wineries selling scheduled tastings.
#
# This is the same asymmetry that governs the matching stage. A wrong accept
# costs one wasted classification. A wrong reject deletes a real operator from
# the pipeline with nothing left behind to notice it.
AMBIGUOUS_TYPES = {
    "night_club", "bar", "restaurant", "lodging", "hotel", "campground",
    "rv_park", "gym", "clothing_store", "store",
}

# `tour_agency` is Google's own label for a business that sells tours. On the
# Split sample every single result carried it, which makes it a far better
# signal than the design assumed. `travel_agency` is deliberately NOT here:
# it also covers flight and package sellers, which are not experience
# suppliers, so those go to the model.
ACCEPT_TYPES = {"tour_agency"}


class Classification(BaseModel):
    """Structured verdict. The reason is written to be read by a Destination
    Specialist reviewing the queue, not by an engineer."""

    verdict: Literal["experience_operator", "attraction_only", "not_relevant"] = Field(
        description=(
            "experience_operator: a business that RUNS or RESELLS bookable "
            "activities travellers pay to take part in. "
            "attraction_only: a place travellers visit that sells no activity "
            "of its own (a viewpoint, a church, a public beach). "
            "not_relevant: anything else."
        )
    )
    experience_type: str = Field(
        description=(
            "One of: boat_tour, walking_tour, food_drink, adventure, "
            "water_sports, cultural, day_trip, transfer, private_guide, "
            "other, none. Use 'none' unless the verdict is experience_operator."
        )
    )
    confidence: float = Field(
        description="0.0 to 1.0. Below 0.6 the record is escalated to a human."
    )
    reason: str = Field(
        description="One short sentence, in plain English, for a non-technical reviewer."
    )


SYSTEM_PROMPT = """\
You classify businesses discovered in a travel destination, deciding whether \
each one is an experience operator that a tours-and-activities marketplace \
could sell.

# What counts as an experience operator

An experience operator is a business that sells a bookable, scheduled activity \
to travellers. The traveller pays that business for a thing they DO, and the \
activity has a start time, a duration, and a person or vessel or vehicle \
providing it.

Note this is about who RUNS the activity, not about wholesale or trade supply. \
A company taking travellers out on a boat is an experience_operator. Ignore \
any other meaning of the word "supplier" you may be familiar with.

Examples that ARE experience operators:
- A company running boat trips to the Blue Cave
- A kayaking or rafting operator
- A licensed walking-tour guide or guiding company
- A winery or distillery selling scheduled tastings or tours
- A diving centre offering courses and guided dives
- A cooking school running classes for visitors
- A quad, buggy or jeep safari operator
- A sailing charter sold as a day experience
- A local agency packaging and reselling day trips

Examples that are NOT experience operators:
- A museum, gallery, fortress or church that only sells admission. Admission is \
  not an experience: nobody guides it and it has no start time. Classify these \
  as "attraction_only".
- A viewpoint, beach, park, waterfall or square. "attraction_only".
- A hotel, apartment or campsite, even one that mentions activities. "not_relevant".
- A restaurant or bar, unless it is specifically selling a scheduled tasting or \
  cooking class. "not_relevant".
- A car hire, scooter hire or taxi firm. Transport is not an experience unless \
  it is sold as a sightseeing trip. "not_relevant".
- A shop, including souvenir and dive shops that only sell equipment. "not_relevant".
- A travel agency selling flights, packages or accommodation rather than \
  activities it runs or resells. "not_relevant".

# Hard cases and how to treat them

- A business whose name suggests tours but whose reviews are all about \
  equipment hire is a hire business. "not_relevant".
- A national park or nature reserve is "attraction_only", but a guiding company \
  operating inside it is an "experience_operator".
- If a business both runs experiences and does something else, and the \
  experiences are genuine, classify it "experience_operator".
- If the evidence is thin, say so through a low confidence rather than \
  guessing. A confident wrong answer costs a Destination Specialist more time \
  than an honest uncertain one.

# How to answer

Return the structured verdict only. The reason must be one short sentence a \
non-technical reviewer can act on, referring to the actual evidence you were \
given, not to your process. Never say "based on the information provided".

Set confidence below 0.6 whenever the record could reasonably be classified \
another way. That band is routed to a human on purpose, and marking genuine \
ambiguity is the correct outcome, not a failure.
"""


def _describe(place: DiscoveredPlace) -> str:
    lines = [f"Name: {place.name}"]
    if place.categories:
        lines.append(f"Google categories: {', '.join(place.categories)}")
    if place.address:
        lines.append(f"Address: {place.address}")
    if place.website:
        lines.append(f"Website: {place.website}")
    if place.rating is not None:
        lines.append(f"Rating: {place.rating} from {place.review_count or 0} reviews")
    matched = (place.raw or {}).get("matched_query")
    if matched:
        lines.append(f"Found by searching: {matched}")
    return "\n".join(lines)


@dataclass
class ClassificationResult:
    place_source_id: str
    verdict: Verdict
    experience_type: str
    confidence: float
    reason: str
    decided_by: Literal["deterministic", "model"]
    needs_review: bool = False


@dataclass
class ClassificationRun:
    results: list[ClassificationResult] = field(default_factory=list)
    deterministic_rejects: int = 0
    deterministic_accepts: int = 0
    model_calls: int = 0
    audit_checked: int = 0
    audit_disagreements: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def model_share(self) -> float:
        return self.model_calls / len(self.results) if self.results else 0.0

    @property
    def audit_agreement(self) -> float:
        if not self.audit_checked:
            return 1.0
        return 1 - len(self.audit_disagreements) / self.audit_checked

    def summary(self) -> dict:
        by_verdict: dict[str, int] = {}
        for r in self.results:
            by_verdict[r.verdict.value] = by_verdict.get(r.verdict.value, 0) + 1
        return {
            "total": len(self.results),
            "by_verdict": by_verdict,
            "deterministic_rejects": self.deterministic_rejects,
            "deterministic_accepts": self.deterministic_accepts,
            "model_calls": self.model_calls,
            "model_share": round(self.model_share, 4),
            "needs_review": sum(1 for r in self.results if r.needs_review),
            "audit_checked": self.audit_checked,
            "audit_agreement": round(self.audit_agreement, 4),
            "errors": len(self.errors),
        }


def prefilter(place: DiscoveredPlace) -> ClassificationResult | None:
    """Decide for free where the types are unambiguous. None means ask the model."""
    types = set(place.categories or [])

    # Ambiguity wins over a reject: if any type could plausibly hide an
    # operator, the model decides rather than the type list.
    if types & AMBIGUOUS_TYPES:
        return None

    if types & REJECT_TYPES:
        hit = sorted(types & REJECT_TYPES)[0]
        return ClassificationResult(
            place_source_id=place.source_id,
            verdict=Verdict.NOT_RELEVANT,
            experience_type="none",
            confidence=0.95,
            reason=f"Google classifies this as {hit.replace('_', ' ')}.",
            decided_by="deterministic",
        )

    if types & ACCEPT_TYPES:
        return ClassificationResult(
            place_source_id=place.source_id,
            verdict=Verdict.OPERATOR,
            experience_type="other",
            confidence=0.9,
            reason="Google classifies this as a tour agency.",
            decided_by="deterministic",
        )

    return None


def classify(
    places: list[DiscoveredPlace],
    llm: LLMClient,
    review_threshold: float = 0.6,
    audit_accepts: float = 0.05,
    audit_rejects: float = 0.20,
    seed: int = 3,
) -> ClassificationRun:
    """Classify every place, using the model only where it is needed.

    A sample of the deterministic decisions is ALSO sent to the model and the
    two verdicts compared, so the shortcut's error rate is measured rather than
    assumed.

    **Rejects are sampled four times as heavily as accepts**, deliberately. A
    wrong accept surfaces immediately: the operator reaches the lead queue and
    someone notices it is a car park. A wrong reject deletes a real operator
    with nothing left behind to notice, so it can only be caught by looking on
    purpose. The audit budget goes where the invisible error is.
    """
    run = ClassificationRun()
    rng = random.Random(seed)

    needs_model: list[DiscoveredPlace] = []
    audited: list[DiscoveredPlace] = []
    decided: dict[str, ClassificationResult] = {}

    for place in places:
        pre = prefilter(place)
        if pre is None:
            needs_model.append(place)
            continue

        decided[place.source_id] = pre
        if pre.verdict is Verdict.OPERATOR:
            run.deterministic_accepts += 1
            rate = audit_accepts
        else:
            run.deterministic_rejects += 1
            rate = audit_rejects

        if rate and rng.random() < rate:
            audited.append(place)

    to_call = needs_model + audited
    if to_call:
        prompts = [_describe(p) for p in to_call]
        outputs = llm.structured_many(
            SYSTEM_PROMPT,
            prompts,
            Classification,
            max_tokens=400,
            on_error=lambda i, exc: run.errors.append(
                f"{to_call[i].source_id}: {exc}"
            ),
        )
        run.model_calls = len(to_call)

        for place, out in zip(to_call, outputs):
            if out is None:
                # A failed call must not silently drop an operator. Escalate.
                decided.setdefault(
                    place.source_id,
                    ClassificationResult(
                        place_source_id=place.source_id,
                        verdict=Verdict.OPERATOR,
                        experience_type="other",
                        confidence=0.0,
                        reason="Classification failed; sent for human review.",
                        decided_by="model",
                        needs_review=True,
                    ),
                )
                continue

            verdict = Verdict(out.verdict)

            if place.source_id in decided:
                # This was an audit of a deterministic accept.
                run.audit_checked += 1
                if verdict is not decided[place.source_id].verdict:
                    run.audit_disagreements.append(
                        f"{place.source_id} ({place.name}): shortcut said "
                        f"{decided[place.source_id].verdict.value}, model said "
                        f"{verdict.value} - {out.reason}"
                    )
                continue

            decided[place.source_id] = ClassificationResult(
                place_source_id=place.source_id,
                verdict=verdict,
                experience_type=out.experience_type,
                confidence=out.confidence,
                reason=out.reason,
                decided_by="model",
                needs_review=out.confidence < review_threshold,
            )

    run.results = [decided[p.source_id] for p in places if p.source_id in decided]
    return run
