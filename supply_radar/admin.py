"""Runtime configuration an administrator can change without a deploy.

Four things sit here for v1: which markets are open, which search terms users
may pick from, which model runs the probabilistic stages, and the spend cap.

**Persistence is deliberately not built.** Overrides live in the container's
memory and are lost when Cloud Run scales to zero or replaces the instance.
Doing it properly needs a config store (Firestore or a GCS object) plus an
audit trail of who changed what, which is a day-2 item rather than something to
half-build now. The admin page says so on the page rather than letting anyone
discover it later.

Role-based access is also day 2. For v1 the page is gated by a separate admin
code, so it is a different key from the one that authorises spending.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from supply_radar.config import CONFIG_DIR
from supply_radar.regions import load_regions

# Models the probabilistic stages may run on. Deliberately a short list: this
# is a governance control, not a free-text field, and an operator should not be
# able to point production at an unverified model.
ALLOWED_MODELS = [
    {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5 (default)",
        "note": "Right tier for classification and extraction over short text.",
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "note": "Cheaper and faster. Worth testing for classification; likely "
                "weaker on website extraction.",
    },
    {
        "id": "claude-opus-5",
        "label": "Claude Opus 5",
        "note": "Several times the cost for no measured gain on these tasks. "
                "Available for comparison, not recommended.",
    },
]


@dataclass
class SearchTerm:
    term: str
    category: str
    enabled: bool
    default: bool = False
    note: str | None = None


@lru_cache
def _load_terms() -> tuple[tuple[SearchTerm, ...], int]:
    raw = yaml.safe_load(
        (CONFIG_DIR / "search_terms.yaml").read_text(encoding="utf-8")
    )
    terms = tuple(
        SearchTerm(
            term=t["term"],
            category=t["category"],
            enabled=bool(t.get("enabled", True)),
            default=bool(t.get("default", False)),
            note=(t.get("note") or "").strip() or None,
        )
        for t in raw["terms"]
    )
    return terms, int(raw.get("max_selectable", 3))


@dataclass
class AdminState:
    """In-memory overrides on top of the config files."""

    disabled_terms: set[str] = field(default_factory=set)
    enabled_terms: set[str] = field(default_factory=set)
    model: str | None = None
    daily_cap_gbp: float | None = None
    disabled_regions: set[str] = field(default_factory=set)

    def term_enabled(self, t: SearchTerm) -> bool:
        if t.term in self.enabled_terms:
            return True
        if t.term in self.disabled_terms:
            return False
        return t.enabled


STATE = AdminState()


def search_terms(include_disabled: bool = False) -> list[dict]:
    terms, _ = _load_terms()
    return [
        {
            "term": t.term,
            "category": t.category,
            "enabled": STATE.term_enabled(t),
            "default": t.default,
            "note": t.note,
        }
        for t in terms
        if include_disabled or STATE.term_enabled(t)
    ]


def max_selectable() -> int:
    return _load_terms()[1]


def term_categories() -> dict[str, str]:
    """Search term to category. The reason the list is curated at all: an
    unmapped term produces operators with no category, which silently zeroes
    the gap-fit axis."""
    terms, _ = _load_terms()
    return {t.term.lower(): t.category for t in terms}


def validate_terms(requested: list[str]) -> tuple[list[str], list[str]]:
    """Return (accepted, rejected). Anything not on the curated list is
    refused rather than quietly passed through to a paid API."""
    allowed = {t["term"].lower(): t["term"] for t in search_terms()}
    accepted, rejected = [], []
    for q in requested:
        key = q.strip().lower()
        if key in allowed:
            accepted.append(allowed[key])
        else:
            rejected.append(q)
    return accepted[: max_selectable()], rejected


def active_model() -> str:
    return STATE.model or ALLOWED_MODELS[0]["id"]


def snapshot(settings) -> dict:
    """Everything the admin page renders."""
    return {
        "persistence": {
            "persisted": False,
            "note": "Changes apply to this running instance only and are lost "
                    "when it restarts. Persisting them needs a config store and "
                    "an audit trail, which is a day-2 item.",
        },
        "regions": [
            {
                "id": r.id,
                "name": r.name,
                "enabled": r.enabled and r.id not in STATE.disabled_regions,
                "configured_enabled": r.enabled,
                "note": r.note,
            }
            for r in load_regions()
        ],
        "models": ALLOWED_MODELS,
        "active_model": active_model(),
        "spend": {
            "daily_cap_gbp": STATE.daily_cap_gbp
            if STATE.daily_cap_gbp is not None
            else settings.daily_spend_cap_gbp,
            "access_code_required": bool(settings.access_code),
            "note": "A live sweep needs the access code regardless of the cap. "
                    "The cap bounds what a code holder can spend in a day.",
        },
        "search_terms": search_terms(include_disabled=True),
        "max_selectable": max_selectable(),
    }


def apply(changes: dict) -> list[str]:
    """Apply admin changes, returning a human-readable list of what happened."""
    applied: list[str] = []

    model = changes.get("model")
    if model:
        if model not in {m["id"] for m in ALLOWED_MODELS}:
            raise ValueError(f"{model} is not an approved model")
        STATE.model = model
        applied.append(f"Model set to {model}")

    cap = changes.get("daily_cap_gbp")
    if cap is not None:
        cap = float(cap)
        if cap < 0 or cap > 500:
            raise ValueError("Daily cap must be between 0 and 500")
        STATE.daily_cap_gbp = cap
        applied.append(f"Daily spend cap set to £{cap:.2f}")

    for term in changes.get("enable_terms", []):
        STATE.enabled_terms.add(term)
        STATE.disabled_terms.discard(term)
        applied.append(f"Enabled search term '{term}'")

    for term in changes.get("disable_terms", []):
        STATE.disabled_terms.add(term)
        STATE.enabled_terms.discard(term)
        applied.append(f"Disabled search term '{term}'")

    for rid in changes.get("disable_regions", []):
        STATE.disabled_regions.add(rid)
        applied.append(f"Closed market '{rid}' for this instance")

    for rid in changes.get("enable_regions", []):
        STATE.disabled_regions.discard(rid)
        applied.append(f"Reopened market '{rid}'")

    return applied
