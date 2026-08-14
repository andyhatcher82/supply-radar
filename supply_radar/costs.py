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

# Anthropic model rates, USD per million tokens. Verified 14 August 2026.
#
# Sonnet 5 carries introductory pricing of $2/$10 until 31 August 2026, after
# which it returns to $3/$15. The cost model deliberately uses the STANDARD
# rate: quoting a number that expires in a fortnight would flatter the business
# case, and the whole point of the economics page is that it holds up.
LLM_RATES: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {
        "input": 3.00,
        "output": 15.00,
        "input_intro": 2.00,
        "output_intro": 10.00,
    },
}

# Cache writes cost more than plain input; cache reads cost a tenth. This is
# the single biggest lever on classification at scale, because the taxonomy and
# instructions are identical across every candidate and only the operator's
# details change.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

# The Batch API halves token cost in exchange for asynchronous completion
# (typically under an hour). Discovery is a scheduled overnight job in
# production, so this is close to free money at scale.
BATCH_DISCOUNT = 0.50

# Assumption, stated rather than hidden. Editable in the economics page.
USD_TO_GBP = 0.78


@dataclass
class TokenUsage:
    """Token counts for one model, accumulated across a run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    calls: int = 0
    batched: bool = False

    @property
    def cache_hit_rate(self) -> float:
        """Share of prompt tokens served from cache.

        The headline efficiency number for classification at scale. A rate near
        zero across repeated runs means something is silently invalidating the
        cached prefix.
        """
        total = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return self.cache_read_tokens / total if total else 0.0


@dataclass
class CostLedger:
    """Records billable activity for a single run."""

    counts: dict[str, int] = field(default_factory=dict)
    free_allowance_used: dict[str, int] = field(default_factory=dict)
    llm: dict[str, TokenUsage] = field(default_factory=dict)

    def record(self, sku: str, n: int = 1) -> None:
        if sku not in RATES:
            raise KeyError(f"Unknown SKU {sku!r}")
        self.counts[sku] = self.counts.get(sku, 0) + n

    def record_llm(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
        batched: bool = False,
    ) -> None:
        usage = self.llm.setdefault(model, TokenUsage())
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.cache_write_tokens += cache_write_tokens
        usage.cache_read_tokens += cache_read_tokens
        usage.calls += 1
        usage.batched = usage.batched or batched

    def llm_usd(self, use_intro_pricing: bool = False) -> float:
        total = 0.0
        for model, usage in self.llm.items():
            rates = LLM_RATES.get(model)
            if rates is None:
                continue
            in_rate = rates["input_intro"] if use_intro_pricing else rates["input"]
            out_rate = rates["output_intro"] if use_intro_pricing else rates["output"]

            cost = (
                usage.input_tokens * in_rate
                + usage.cache_write_tokens * in_rate * CACHE_WRITE_MULTIPLIER
                + usage.cache_read_tokens * in_rate * CACHE_READ_MULTIPLIER
                + usage.output_tokens * out_rate
            ) / 1_000_000

            if usage.batched:
                cost *= BATCH_DISCOUNT
            total += cost
        return round(total, 6)

    @property
    def total_calls(self) -> int:
        return sum(self.counts.values()) + sum(u.calls for u in self.llm.values())

    def usd(self, apply_free_tier: bool = False) -> float:
        """Total cost of this run, APIs plus models.

        Defaults to ignoring the free monthly allowance, because a run should
        report its true marginal cost. Amortising a shared monthly allowance
        across runs flatters whichever run happens to go first.
        """
        total = 0.0
        for sku, n in self.counts.items():
            rate, free = RATES[sku]
            billable = max(0, n - free) if apply_free_tier else n
            total += billable * rate / 1000
        return round(total + self.llm_usd(), 6)

    def gbp(self, apply_free_tier: bool = False) -> float:
        return round(self.usd(apply_free_tier) * USD_TO_GBP, 6)

    def summary(self) -> dict:
        out = {
            "calls": self.counts.copy(),
            "total_calls": self.total_calls,
            "usd": self.usd(),
            "gbp": self.gbp(),
            "usd_after_free_tier": self.usd(apply_free_tier=True),
            "gbp_after_free_tier": self.gbp(apply_free_tier=True),
        }
        if self.llm:
            out["llm"] = {
                model: {
                    "calls": u.calls,
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_read_tokens": u.cache_read_tokens,
                    "cache_write_tokens": u.cache_write_tokens,
                    "cache_hit_rate": round(u.cache_hit_rate, 3),
                }
                for model, u in self.llm.items()
            }
            out["llm_usd"] = self.llm_usd()
            out["llm_usd_batched"] = round(self.llm_usd() * BATCH_DISCOUNT, 6)
        return out


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
