"""Probe the Claude call shape before wiring it into the pipeline.

Specifically: does messages.parse() tolerate `output_config` (which carries
effort) alongside `output_format` (which it uses to build output_config
itself)? Guessing wrong here would fail on every call at scale.

    python scripts/probe_llm.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from supply_radar.config import get_settings  # noqa: E402

MODEL = "claude-sonnet-5"


class Verdict(BaseModel):
    verdict: Literal["supplier", "attraction", "out_of_scope"]
    confidence: float = Field(description="0.0 to 1.0")
    reason: str


SYSTEM = "You classify travel businesses. Answer with the structured verdict only."
USER = "Name: Tinel Boat tours\nGoogle categories: tour_agency, travel_agency\nRating: 4.9 from 604 reviews"


def attempt(label: str, **kwargs) -> None:
    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key)
    started = time.time()
    try:
        res = client.messages.parse(
            model=MODEL,
            max_tokens=400,
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": USER}],
            output_format=Verdict,
            **kwargs,
        )
        u = res.usage
        print(f"  {label:<44} OK   {time.time() - started:.1f}s  "
              f"in={u.input_tokens} out={u.output_tokens} "
              f"cw={getattr(u, 'cache_creation_input_tokens', 0)} "
              f"cr={getattr(u, 'cache_read_input_tokens', 0)}")
        print(f"       -> {res.parsed_output}")
    except Exception as exc:  # noqa: BLE001 - probing on purpose
        print(f"  {label:<44} FAIL {type(exc).__name__}: {str(exc)[:180]}")


def main() -> None:
    if not get_settings().anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY missing from .env")

    print("call shape probes")
    attempt("bare parse (adaptive thinking default)")
    attempt("thinking disabled", thinking={"type": "disabled"})
    attempt("thinking disabled + effort low",
            thinking={"type": "disabled"}, output_config={"effort": "low"})
    attempt("effort low only", output_config={"effort": "low"})
    attempt("adaptive + effort low",
            thinking={"type": "adaptive"}, output_config={"effort": "low"})

    print()
    print("cache behaviour: same system prompt three times in a row")
    for i in range(3):
        attempt(f"repeat {i + 1}", thinking={"type": "disabled"})


if __name__ == "__main__":
    main()
