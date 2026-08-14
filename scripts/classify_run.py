"""Classify a saved sweep.

Reads places from JSON so classification can be re-run and re-tuned without
paying for Places again.

    python scripts/classify_run.py --in data/split_places.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.classify import Verdict, classify  # noqa: E402
from supply_radar.config import get_settings  # noqa: E402
from supply_radar.costs import CostLedger  # noqa: E402
from supply_radar.llm import LLMClient  # noqa: E402
from supply_radar.models import DiscoveredPlace  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="infile", default="data/split_places.json")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--audit", type=float, default=0.1)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    raw = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    places = [DiscoveredPlace(**p) for p in raw]
    if args.limit:
        places = places[: args.limit]

    settings = get_settings()
    ledger = CostLedger()
    llm = LLMClient(settings.anthropic_api_key, ledger=ledger)

    print(f"classifying {len(places)} places\n")
    started = time.time()
    run = classify(places, llm, audit_accepts=args.audit, audit_rejects=min(1.0, args.audit * 4))
    elapsed = time.time() - started

    print("result")
    for k, v in run.summary().items():
        print(f"  {k:<24} {v}")
    print(f"  {'elapsed_seconds':<24} {elapsed:.1f}")
    print()

    usage = ledger.llm.get(llm.model)
    if usage:
        print("token usage")
        print(f"  calls              {usage.calls}")
        print(f"  input (uncached)   {usage.input_tokens}")
        print(f"  cache writes       {usage.cache_write_tokens}")
        print(f"  cache reads        {usage.cache_read_tokens}")
        print(f"  output             {usage.output_tokens}")
        print(f"  cache hit rate     {usage.cache_hit_rate:.1%}")
        print()

    print("cost")
    print(f"  model USD          {ledger.llm_usd():.4f}")
    print(f"  model USD batched  {ledger.llm_usd() * 0.5:.4f}")
    if usage and usage.calls:
        print(f"  USD per candidate  {ledger.llm_usd() / usage.calls:.6f}")
    print()

    by_verdict: dict[str, list] = {}
    for r in run.results:
        by_verdict.setdefault(r.verdict.value, []).append(r)

    names = {p.source_id: p.name for p in places}
    for verdict, items in sorted(by_verdict.items()):
        print(f"{verdict}  ({len(items)})")
        for r in items[:6]:
            flag = " [REVIEW]" if r.needs_review else ""
            print(f"    {names.get(r.place_source_id, '?')[:38]:<40} "
                  f"{r.confidence:.2f} {r.decided_by[:4]}{flag}")
            print(f"      {r.reason[:110]}")
        print()

    if run.audit_disagreements:
        print(f"AUDIT DISAGREEMENTS ({len(run.audit_disagreements)} of "
              f"{run.audit_checked} checked)")
        for d in run.audit_disagreements:
            print(f"  {d[:200]}")
        print()
    elif run.audit_checked:
        print(f"audit: model agreed with the deterministic shortcut on all "
              f"{run.audit_checked} sampled records\n")

    if run.errors:
        print(f"errors ({len(run.errors)})")
        for e in run.errors[:5]:
            print(f"  {e[:160]}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(
                [
                    {
                        "place_source_id": r.place_source_id,
                        "name": names.get(r.place_source_id),
                        "verdict": r.verdict.value,
                        "experience_type": r.experience_type,
                        "confidence": r.confidence,
                        "reason": r.reason,
                        "decided_by": r.decided_by,
                        "needs_review": r.needs_review,
                    }
                    for r in run.results
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {len(run.results)} classifications to {args.out}")


if __name__ == "__main__":
    main()
