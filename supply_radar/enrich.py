"""Website enrichment.

Google Places tells you an operator exists and roughly how well regarded they
are. It does not tell you whether they can actually transact: what languages
they sell in, whether a traveller can book online or only send an email,
whether they run year-round or only in August. That is the READINESS axis of
the scoring model, and without it the axis is empty.

So each operator's own website is fetched and read. Deliberately politely:

  - robots.txt is honoured, not consulted and ignored
  - an identifiable user agent, so an operator can see who called
  - a hard cap of a few pages per operator
  - rate limited, and cached on disk so a re-run costs nothing

The extraction itself is a model call, because turning arbitrary marketing HTML
into a fixed set of fields is judgement over unstructured text. That is the
third and last justified model use in the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from supply_radar.config import DATA_DIR
from supply_radar.llm import LLMClient

log = logging.getLogger(__name__)

USER_AGENT = (
    "SupplyRadarBot/0.1 (+experience supplier discovery prototype; "
    "contact via the operator of this tool)"
)

# Pages worth trying beyond the homepage. Ordered by how likely they are to
# carry booking and contact detail.
CANDIDATE_PATHS = [
    "/booking", "/book", "/contact", "/about", "/tours", "/en",
]

MAX_PAGES_PER_SITE = 3
MAX_CHARS_PER_SITE = 12_000
REQUEST_TIMEOUT = 12.0
POLITENESS_DELAY = 0.5


class SiteExtract(BaseModel):
    """Fields pulled from an operator's own website."""

    languages: list[str] = Field(
        description=(
            "Languages the operator appears to SELL in, as ISO codes like "
            "en, hr, de, it. Base this on language switchers or translated "
            "pages, not on a single stray phrase. Empty list if unclear."
        )
    )
    booking: Literal[
        "online_booking", "enquiry_form", "phone_or_email_only", "unclear"
    ] = Field(
        description=(
            "online_booking: a traveller can pick a date and pay or reserve "
            "on the site. enquiry_form: a form that sends a request, with no "
            "instant confirmation. phone_or_email_only: contact details only. "
            "unclear: not determinable from what you were given."
        )
    )
    product_categories: list[str] = Field(
        description=(
            "Types of experience sold, from: boat_tour, walking_tour, "
            "food_drink, adventure, water_sports, cultural, day_trip, "
            "transfer, private_guide, other."
        )
    )
    contact_email: str = Field(
        description="Best contact email address, or an empty string if none is shown."
    )
    seasonality: Literal["year_round", "seasonal", "unclear"] = Field(
        description="Whether the operator appears to run all year or only in season."
    )
    group_type: Literal["private", "shared", "both", "unclear"] = Field(
        description="Whether experiences are sold as private, shared, or both."
    )
    marketplace_presence: list[str] = Field(
        description=(
            "Third-party marketplaces the SITE ITSELF links to or mentions "
            "selling through, such as getyourguide, viator, tripadvisor, "
            "airbnb. Empty list if none. Do not guess."
        )
    )
    confidence: float = Field(
        description="0.0 to 1.0. How well the page content supported these answers."
    )


SYSTEM_PROMPT = """\
You read the website of a small travel experience operator and extract a fixed \
set of facts about how they sell.

The text you are given is stripped from one to three pages of the operator's \
own site. It is often messy: navigation menus, cookie banners, and marketing \
copy mixed together, sometimes in more than one language. Work with what is \
there.

# What you are deciding

The purpose is to judge whether this operator could transact on a \
tours-and-activities marketplace today, so focus on evidence of how a \
traveller actually books:

- **Languages**: look for a language switcher, translated navigation, or \
  clearly multilingual page content. A single foreign phrase in an address is \
  not evidence of selling in that language. Croatian operators very commonly \
  sell in English as well as Croatian.
- **Booking**: the single most useful field. A live calendar, a "Book now" \
  that leads to date and traveller selection, or a payment step means \
  online_booking. A "Contact us" or "Request a quote" form means enquiry_form. \
  A page that only lists a phone number or email means phone_or_email_only.
- **Marketplace presence**: only report a marketplace if the site itself links \
  to it or names it. Do not infer it from the operator being well known. This \
  field is used to judge whether they are already comfortable selling through \
  a third party, so a false positive is actively misleading.
- **Seasonality**: many Adriatic operators run May to October only. Look for \
  stated season dates or "closed for winter" style copy.

# How to answer

Return the structured extract only. Where the page content genuinely does not \
support a field, use the "unclear" option or an empty list rather than \
guessing. Set confidence low when you are working from very thin content: a \
cookie banner and a phone number is not a basis for confident answers, and \
saying so is more useful than inventing detail.
"""


@dataclass
class EnrichmentResult:
    place_source_id: str
    website: str | None = None
    fetched_pages: int = 0
    extract: SiteExtract | None = None
    error: str | None = None
    from_cache: bool = False


@dataclass
class EnrichmentRun:
    results: dict[str, EnrichmentResult] = field(default_factory=dict)
    skipped_no_site: int = 0
    blocked_by_robots: int = 0
    fetch_failures: int = 0
    cache_hits: int = 0
    pages_fetched: int = 0

    def summary(self) -> dict:
        extracted = [r for r in self.results.values() if r.extract]
        booking: dict[str, int] = {}
        for r in extracted:
            booking[r.extract.booking] = booking.get(r.extract.booking, 0) + 1
        return {
            "operators": len(self.results),
            "extracted": len(extracted),
            "skipped_no_site": self.skipped_no_site,
            "blocked_by_robots": self.blocked_by_robots,
            "fetch_failures": self.fetch_failures,
            "pages_fetched": self.pages_fetched,
            "cache_hits": self.cache_hits,
            "booking_capability": booking,
        }


class SiteFetcher:
    """Polite, cached fetcher for operator websites."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or (DATA_DIR / "sitecache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.client = httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{digest}.json"

    def _allowed(self, url: str) -> bool:
        """Check robots.txt. A site we cannot read robots for is treated as
        allowed, which is the conventional reading, but a site that explicitly
        disallows is respected."""
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                res = self.client.get(urljoin(origin, "/robots.txt"))
                if res.status_code == 200:
                    parser.parse(res.text.splitlines())
                else:
                    parser = None
            except Exception:  # noqa: BLE001 - unreachable robots is not fatal
                parser = None
            self._robots[origin] = parser

        parser = self._robots[origin]
        if parser is None:
            return True
        return parser.can_fetch(USER_AGENT, url)

    def fetch(self, base_url: str) -> tuple[str, int, str | None]:
        """Return (text, pages_fetched, error). Cached on disk."""
        cache_file = self._cache_path(base_url)
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            return cached["text"], cached["pages"], cached.get("error")

        text, pages, error = self._fetch_live(base_url)
        cache_file.write_text(
            json.dumps({"text": text, "pages": pages, "error": error}),
            encoding="utf-8",
        )
        return text, pages, error

    def is_cached(self, base_url: str) -> bool:
        return self._cache_path(base_url).exists()

    def _fetch_live(self, base_url: str) -> tuple[str, int, str | None]:
        if not self._allowed(base_url):
            return "", 0, "blocked by robots.txt"

        chunks: list[str] = []
        pages = 0
        urls = [base_url] + [urljoin(base_url, p) for p in CANDIDATE_PATHS]

        for url in urls:
            if pages >= MAX_PAGES_PER_SITE:
                break
            if url != base_url and not self._allowed(url):
                continue
            try:
                res = self.client.get(url)
            except Exception as exc:  # noqa: BLE001 - a dead page is not fatal
                if url == base_url:
                    return "", 0, f"{type(exc).__name__}: {exc}"
                continue

            if res.status_code != 200 or "html" not in res.headers.get(
                "content-type", ""
            ):
                if url == base_url:
                    return "", 0, f"HTTP {res.status_code}"
                continue

            chunks.append(f"--- {url} ---\n{html_to_text(res.text)}")
            pages += 1
            time.sleep(POLITENESS_DELAY)

        if not chunks:
            return "", 0, "no readable pages"

        return "\n\n".join(chunks)[:MAX_CHARS_PER_SITE], pages, None


def html_to_text(html: str) -> str:
    """Strip HTML to readable text, dropping script, style and navigation noise."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def enrich(
    operators: list[tuple[str, str | None]],
    llm: LLMClient,
    fetcher: SiteFetcher | None = None,
    on_progress=None,
) -> EnrichmentRun:
    """Enrich operators given as (place_source_id, website) pairs."""
    run = EnrichmentRun()
    own_fetcher = fetcher is None
    fetcher = fetcher or SiteFetcher()

    try:
        fetched: list[tuple[str, str, str]] = []  # (id, website, text)

        for source_id, website in operators:
            if not website:
                run.skipped_no_site += 1
                run.results[source_id] = EnrichmentResult(
                    place_source_id=source_id, error="no website"
                )
                continue

            cached = fetcher.is_cached(website)
            text, pages, error = fetcher.fetch(website)

            if cached:
                run.cache_hits += 1
            run.pages_fetched += pages

            if error or not text:
                if error == "blocked by robots.txt":
                    run.blocked_by_robots += 1
                else:
                    run.fetch_failures += 1
                run.results[source_id] = EnrichmentResult(
                    place_source_id=source_id,
                    website=website,
                    error=error or "empty",
                    from_cache=cached,
                )
                continue

            run.results[source_id] = EnrichmentResult(
                place_source_id=source_id,
                website=website,
                fetched_pages=pages,
                from_cache=cached,
            )
            fetched.append((source_id, website, text))

            if on_progress:
                on_progress(len(run.results), len(operators))

        if fetched:
            prompts = [
                f"Operator website: {site}\n\nPage content:\n{text}"
                for _, site, text in fetched
            ]
            outputs = llm.structured_many(
                SYSTEM_PROMPT, prompts, SiteExtract, max_tokens=700
            )
            for (source_id, _, _), out in zip(fetched, outputs):
                run.results[source_id].extract = out

    finally:
        if own_fetcher:
            fetcher.close()

    return run
