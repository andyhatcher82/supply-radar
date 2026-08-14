"""Cost model and run ledger.

Every billable call is recorded as it happens, so the economics page reports
what a run actually cost rather than an estimate of what it might have.

Rates verified against Google's published pricing on 14 August 2026. They are
first-tier (lowest volume) rates, which is the conservative choice: at real
volume every one of these gets cheaper.

The one number worth understanding here is that Text Search is billed per CALL
returning up to 20 places, while Place Details is billed per PLACE. Pulling
website, phone and rating through Place Details rather than through the search
field mask would cost roughly five times more for identical data. Field mask
design is the single biggest cost lever in this pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# USD per 1000 requests, and the monthly allowance that costs nothing.
RATES: dict[str, tuple[float, int]] = {
    # Field mask includes rating, review count, website and phone, which puts
    # our search calls in the Enterprise tier.
    "places.text_search.enterprise": (35.00, 1000),
    "places.text_search.pro": (32.00, 5000),
    "places.text_search.ids_only": (0.00, 0),  # unlimited, no charge
    "places.details.enterprise": (20.00, 1000),
    "geocoding": (5.00, 10000),
}

# Assumption, stated rather than hidden. Editable in the economics page.
USD_TO_GBP = 0.78


@dataclass
class CostLedger:
    """Records billable activity for a single run."""

    counts: dict[str, int] = field(default_factory=dict)
    free_allowance_used: dict[str, int] = field(default_factory=dict)

    def record(self, sku: str, n: int = 1) -> None:
        if sku not in RATES:
            raise KeyError(f"Unknown SKU {sku!r}")
        self.counts[sku] = self.counts.get(sku, 0) + n

    @property
    def total_calls(self) -> int:
        return sum(self.counts.values())

    def usd(self, apply_free_tier: bool = False) -> float:
        """Cost of this run.

        Defaults to ignoring the free monthly allowance, because a run should
        report its true marginal cost. Amortising a shared monthly allowance
        across runs flatters whichever run happens to go first.
        """
        total = 0.0
        for sku, n in self.counts.items():
            rate, free = RATES[sku]
            billable = max(0, n - free) if apply_free_tier else n
            total += billable * rate / 1000
        return round(total, 4)

    def gbp(self, apply_free_tier: bool = False) -> float:
        return round(self.usd(apply_free_tier) * USD_TO_GBP, 4)

    def summary(self) -> dict:
        return {
            "calls": self.counts.copy(),
            "total_calls": self.total_calls,
            "usd": self.usd(),
            "gbp": self.gbp(),
            "usd_after_free_tier": self.usd(apply_free_tier=True),
            "gbp_after_free_tier": self.gbp(apply_free_tier=True),
        }


def estimate_sweep(n_cells: int, n_queries: int, avg_pages: float = 1.8) -> dict:
    """Predict a sweep's cost before running it.

    Shown in the UI as a confirmation gate, because a public button that spends
    money without telling you first is a design defect, not a feature.
    """
    calls = int(n_cells * n_queries * avg_pages)
    rate, free = RATES["places.text_search.enterprise"]
    usd = calls * rate / 1000
    return {
        "cells": n_cells,
        "queries_per_cell": n_queries,
        "estimated_calls": calls,
        "estimated_usd": round(usd, 2),
        "estimated_gbp": round(usd * USD_TO_GBP, 2),
        "free_allowance_remaining_note": (
            f"first {free} Enterprise search calls each month are not charged"
        ),
        # Places sustains well above this; the limit is politeness and the
        # wall-clock a demo can tolerate, not the API.
        "estimated_seconds": int(calls * 0.35),
    }
