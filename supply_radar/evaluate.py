"""Measuring whether the matcher actually works.

A note on language, because the standard terms invert the business meaning and
that has caused real confusion.

In matching terms a "false positive" is a match asserted that is not real. In
business terms that is an operator wrongly written off as already being a
supplier, so nobody ever contacts them. It is invisible, permanent, and the
expensive error. This module calls it a MISSED OPPORTUNITY.

A matching "false negative" is a real match that was not spotted, so an
existing supplier is handed to Sales as a fresh lead. Somebody makes one
awkward call and the system self-corrects. This module calls it a WASTED CALL.

The asymmetry between those two is the whole argument for where human review
is pointed, so the metrics are named after the consequence, not the confusion
matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from supply_radar.locales import LocalePack
from supply_radar.matching import MatchThresholds, match_all
from supply_radar.models import DiscoveredPlace, MatchResult, MatchVerdict, SupplierRecord


@dataclass
class Evaluation:
    total: int = 0
    decided: int = 0
    sent_to_review: int = 0

    correct_existing: int = 0
    correct_net_new: int = 0
    missed_opportunity: int = 0   # net-new, wrongly called existing. Expensive.
    wasted_call: int = 0          # existing, wrongly called net-new. Cheap.
    wrong_supplier: int = 0       # matched, but to the wrong supplier record.

    examples: dict[str, list[str]] = field(default_factory=dict)

    @property
    def precision(self) -> float:
        """Of everything called existing, how much really was."""
        asserted = self.correct_existing + self.missed_opportunity + self.wrong_supplier
        return self.correct_existing / asserted if asserted else 1.0

    @property
    def recall(self) -> float:
        """Of everything that really was existing, how much we caught."""
        actual = self.correct_existing + self.wasted_call + self.wrong_supplier
        return self.correct_existing / actual if actual else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def review_rate(self) -> float:
        return self.sent_to_review / self.total if self.total else 0.0

    @property
    def automation_rate(self) -> float:
        """Share of decisions made without a human. The throughput number."""
        return self.decided / self.total if self.total else 0.0

    def summary(self) -> dict:
        return {
            "total": self.total,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "review_rate": round(self.review_rate, 4),
            "automation_rate": round(self.automation_rate, 4),
            "missed_opportunity": self.missed_opportunity,
            "wasted_call": self.wasted_call,
            "wrong_supplier": self.wrong_supplier,
            "correct_existing": self.correct_existing,
            "correct_net_new": self.correct_net_new,
        }


def evaluate(results: list[MatchResult], answer: dict[str, str]) -> Evaluation:
    """Score match results against the hidden answer key.

    `answer` maps place_source_id -> supplier_id for every place that genuinely
    corresponds to an existing supplier. Absence means genuinely net-new.
    """
    ev = Evaluation(total=len(results))
    ev.examples = {
        "missed_opportunity": [],
        "wasted_call": [],
        "wrong_supplier": [],
    }

    for r in results:
        truth_supplier = answer.get(r.place_source_id)

        if r.verdict is MatchVerdict.NEEDS_REVIEW:
            ev.sent_to_review += 1
            continue

        ev.decided += 1

        if r.verdict is MatchVerdict.EXISTING:
            if truth_supplier is None:
                ev.missed_opportunity += 1
                ev.examples["missed_opportunity"].append(r.place_source_id)
            elif r.supplier_id != truth_supplier:
                ev.wrong_supplier += 1
                ev.examples["wrong_supplier"].append(r.place_source_id)
            else:
                ev.correct_existing += 1
        else:  # NET_NEW
            if truth_supplier is None:
                ev.correct_net_new += 1
            else:
                ev.wasted_call += 1
                ev.examples["wasted_call"].append(r.place_source_id)

    return ev


def threshold_sweep(
    places: list[DiscoveredPlace],
    suppliers: list[SupplierRecord],
    answer: dict[str, str],
    locale: LocalePack,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> list[dict]:
    """Precision, recall and review load as the band boundaries move.

    This is what turns a chosen threshold from an arbitrary constant into an
    evidenced decision, and it makes the trade visible: every point of
    precision is bought with either recall or human review time.

    Both boundaries matter and they do different jobs. `high` controls how
    readily an operator is written off as an existing supplier, which governs
    the expensive error. `low` controls how readily one is declared net-new,
    which governs how much lands in the review queue.
    """
    highs = highs or [round(0.50 + i * 0.02, 2) for i in range(26)]
    lows = lows or [0.55]
    out = []
    for low in lows:
        for high in highs:
            if high <= low:
                continue
            results = match_all(
                places, suppliers, locale, MatchThresholds(high=high, low=low)
            )
            ev = evaluate(results, answer)
            row = ev.summary()
            row["high"] = high
            row["low"] = low
            out.append(row)
    return out
