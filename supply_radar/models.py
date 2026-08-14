"""Core records.

Every record carries provenance. Which source produced it, when, and which
stage decided what, because a lead that Sales cannot trace back is a lead Sales
will not trust.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Source(str, Enum):
    GOOGLE_PLACES = "google_places"
    WEB_SEARCH = "web_search"
    DMO_REGISTRY = "dmo_registry"
    SYNTHETIC = "synthetic"


class DiscoveredPlace(BaseModel):
    """A candidate found by a discovery source, before any judgement is applied."""

    source: Source
    source_id: str
    name: str
    lat: float
    lng: float
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int | None = None
    categories: list[str] = Field(default_factory=list)
    destination_id: str | None = None
    discovered_at: datetime = Field(default_factory=_now)
    raw: dict = Field(default_factory=dict)

    @property
    def coords(self) -> tuple[float, float]:
        return (self.lat, self.lng)


class SupplierRecord(BaseModel):
    """An operator already on the marketplace, as the CRM holds them.

    Deliberately shaped like a real CRM row, which means partly stale, partly
    abbreviated, and inconsistently populated.
    """

    supplier_id: str
    legal_name: str
    trading_name: str | None = None
    address: str | None = None
    city: str | None = None
    # Held by the CRM but imprecise: geocoded from a postal address, sometimes
    # years out of date. Useful as a discriminator, never as proof on its own.
    lat: float | None = None
    lng: float | None = None
    phone: str | None = None
    website: str | None = None
    active: bool = True
    onboarded_at: datetime | None = None

    @property
    def display_name(self) -> str:
        return self.trading_name or self.legal_name


class MatchVerdict(str, Enum):
    EXISTING = "existing"          # already a supplier
    NET_NEW = "net_new"            # genuinely not on the marketplace
    NEEDS_REVIEW = "needs_review"  # ambiguous, a human decides


class DecidedBy(str, Enum):
    HARD_KEY = "hard_key"
    FUZZY_SCORE = "fuzzy_score"
    LLM = "llm"
    HUMAN = "human"


class MatchEvidence(BaseModel):
    """Why a decision was reached. Rendered directly in the review UI, so it is
    written to be read by a Destination Specialist, not by an engineer."""

    signal: str
    detail: str
    contribution: float | None = None


class MatchResult(BaseModel):
    place_source_id: str
    supplier_id: str | None = None
    verdict: MatchVerdict
    score: float = 0.0
    confidence: float = 0.0
    decided_by: DecidedBy
    evidence: list[MatchEvidence] = Field(default_factory=list)
    decided_at: datetime = Field(default_factory=_now)


class GroundTruth(BaseModel):
    """Hidden answer key for the synthetic supplier list.

    Never read by the matcher. Exists only so that precision and recall are
    measured rather than asserted.
    """

    supplier_id: str
    place_source_id: str | None  # None means the supplier was never discovered
    corruptions: list[str] = Field(default_factory=list)
    note: str | None = None
