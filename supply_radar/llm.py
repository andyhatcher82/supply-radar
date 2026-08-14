"""Shared Claude layer.

Every model call in the pipeline goes through here, for three reasons: token
usage is recorded centrally so the economics page reports real spend, the
prompt-cache prefix is constructed the same way every time, and refusals and
transport errors are handled once rather than at each call site.

Two design points are worth knowing.

**Prompt caching is a prefix match.** The instructions and taxonomy are
identical for every candidate and only the operator's details change, so the
stable part goes in the system prompt behind a cache breakpoint and the
volatile part goes in the user turn. Cache reads cost a tenth of input tokens,
which is what makes per-candidate classification affordable at national scale.

**Concurrent requests cannot share a cache they are all still writing.** Fan
out N identical-prefix calls at once and every one of them pays full price.
So the first call is made alone and awaited; only then do the rest fan out, by
which time there is a cache for them to read. That single ordering decision is
worth roughly an order of magnitude on the prompt tokens.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence, TypeVar

import anthropic
from pydantic import BaseModel

from supply_radar.costs import CostLedger

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Sonnet is the right tier here: the work is classification and extraction over
# short text, not deep reasoning. Opus would cost several times more for no
# measurable gain on these tasks.
DEFAULT_MODEL = "claude-sonnet-5"

# Anthropic sustains far more than this; the cap is politeness and keeping a
# demo's wall-clock predictable.
DEFAULT_CONCURRENCY = 8


class Refusal(Exception):
    """The model declined the request. Rare here, but it must not be silently
    read as an empty result."""

    def __init__(self, category: str | None, explanation: str | None):
        self.category = category
        self.explanation = explanation
        super().__init__(f"model refused ({category}): {explanation}")


class LLMClient:
    def __init__(
        self,
        api_key: str,
        ledger: CostLedger | None = None,
        model: str = DEFAULT_MODEL,
    ):
        if not api_key:
            raise ValueError("Anthropic API key is required")
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3)
        self.model = model
        self.ledger = ledger or CostLedger()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ core

    def _record(self, usage) -> None:
        with self._lock:
            self.ledger.record_llm(
                self.model,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            )

    def structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 512,
        think: bool = False,
    ) -> T:
        """One call returning a validated Pydantic object.

        `system` is cached; `user` is not. Keep everything that varies per item
        in `user` or the cache never hits.

        Thinking is off by default. Sonnet 5 runs adaptive thinking unless told
        otherwise, which is the right call for judgement work and pure waste for
        deciding whether a business is a tour operator. Callers that genuinely
        need reasoning pass think=True.
        """
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            # A list of blocks rather than a bare string, because only blocks
            # can carry a cache breakpoint.
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
        }
        if think:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": "low"}
        else:
            kwargs["thinking"] = {"type": "disabled"}
            kwargs["output_config"] = {"effort": "low"}

        response = self.client.messages.parse(**kwargs)
        self._record(response.usage)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise Refusal(
                getattr(details, "category", None),
                getattr(details, "explanation", None),
            )

        parsed = response.parsed_output
        if parsed is None:
            raise ValueError("model returned no parseable output")
        return parsed

    # ------------------------------------------------------------ concurrency

    def structured_many(
        self,
        system: str,
        users: Sequence[str],
        schema: type[T],
        max_tokens: int = 512,
        think: bool = False,
        concurrency: int = DEFAULT_CONCURRENCY,
        on_error: Callable[[int, Exception], None] | None = None,
    ) -> list[T | None]:
        """Run the same prompt over many items, warming the cache first.

        Returns one entry per input, with None where a call failed. A failure on
        one candidate must not abandon the other several hundred.
        """
        if not users:
            return []

        results: list[T | None] = [None] * len(users)

        def run(index: int) -> None:
            try:
                results[index] = self.structured(
                    system, users[index], schema, max_tokens, think
                )
            except Exception as exc:  # noqa: BLE001 - one bad item must not stop the run
                log.warning("item %d failed: %s", index, exc)
                if on_error:
                    on_error(index, exc)

        # Deliberately sequential: this call writes the cache entry that every
        # later call reads. Firing all of them together would have each pay the
        # full uncached prompt price.
        run(0)

        if len(users) > 1:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                list(pool.map(run, range(1, len(users))))

        return results

    @property
    def cache_hit_rate(self) -> float:
        usage = self.ledger.llm.get(self.model)
        return usage.cache_hit_rate if usage else 0.0
