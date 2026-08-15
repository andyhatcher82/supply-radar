# Supply Radar

Finds experience suppliers in a destination that are **not already on Viator**,
determines which are genuinely net-new, enriches and ranks them for Sales, and
does it as configuration rather than code so it repeats across destinations.

Built as a case-study prototype. Not production software.

---

## What it does

```
discover  →  classify  →  normalise  →  match  →  net-new  →  enrich  →  score  →  triage  →  export
```

- **Discover** — Google Places across an area, with adaptive subdivision so a
  silently truncated query does not lose the tail. Google Places is the only
  discovery source that runs: `web_search` appears in the destination pack and
  the `Source` enum but is **not implemented**, and the Croatian licensing
  register turned out not to be a discovery source at all (see below).
- **Classify** — is this actually an experience supplier, or a car park?
- **Normalise** — locale-aware: diacritics, legal forms, phones, domains.
- **Match** — deterministic hard keys, then deterministic fuzzy scoring, then
  banding. The ambiguous middle goes to a human, not to a model: see below.
- **Enrich** — operator website into structured fields.
- **Score** — three separate axes: quality, readiness, gap fit.
- **Triage** — human review where it matters, which is not where most people
  put it. See the decision log.
- **Export** — versioned lead records with full provenance.

## Design notes

The reasoning, the rejected alternatives, the assumptions and the limitations
are in the decision log that accompanies this repo. The short version:

- **No LLM touches a match decision.** It was designed to adjudicate the
  ambiguous middle band, and then the deterministic layer got good enough that
  the band collapsed to 2.4% — four pairs per destination. A model call is not
  worth the non-determinism to save a specialist twelve minutes, so the middle
  band goes to a human. `DecidedBy.LLM` is in the enum and is used nowhere.
- The LLM **is** used, for real and with real spend, in classification (is this
  an experience operator?) and in website enrichment.
- Human review is deliberately pointed at the **auto-matched** decisions rather
  than the passes. A false positive costs one wasted Sales call. A false
  negative means an operator is silently never contacted again.
- The Places API silently truncates results at a per-query cap, so a naive
  city-wide sweep quietly loses suppliers. Cells that come back at the cap are
  subdivided and re-queried.
- Competitor marketplaces are deliberately **not** scraped.
- An exact signal is not an exact signal in a dense tourist market. 13.2% of
  Split operators share a phone number with a different business, eight share
  one Wix subdomain, and one waterfront address covers five businesses. Phone,
  domain and address all require name corroboration before they may decide.

## The licensing register

The Ministry of Tourism publishes every licensed Croatian travel agency as a
weekly XLSX under an open licence. `supply_radar/registry.py` downloads and
parses it, and `scripts/registry_check.py` measures what it is worth.

The answer is: less than expected, and the measurement is the interesting part.
It joins to **5 of 167** discovered operators and **0 of the 40 published
leads**. "Turistička agencija" is a legal category rather than a trade, so it
contains villa rental firms and freight agencies while missing the skipper
regulated as maritime transport. The operators it can vouch for are established
agencies, which are exactly the ones already on the marketplace.

So it is not wired into scoring and not attached to the lead record. It stays
because it is the evidence behind that claim, and because it makes the
multi-source architecture a demonstrated thing rather than a Protocol with one
implementation.

## Output: the lead table in BigQuery

`supply_radar/warehouse.py` appends each run's leads to
`supply-radar-croatia.supply_radar.leads`, partitioned by run date and clustered
on destination and band, with the per-axis evidence carried alongside so a lead
in the warehouse can still be traced back to why it ranked where it did.

```bash
python scripts/build_snapshot.py
python scripts/publish_to_bigquery.py --dry-run   # inspect rows, write nothing
python scripts/publish_to_bigquery.py
```

Append rather than replace, because the question a supply team asks three months
in is not "what are the leads" but "what changed, and did the ones we contacted
convert". A replaced table cannot answer that afterwards.

It is deliberately **not** the console's read path. The console serves the
committed JSON snapshot, which needs no credentials and cold-starts instantly.

## What is not built

Stated here rather than discovered by a reader:

- **`web_search` and `dmo_registry` are `Source` enum values with no
  implementation.** Google Places is the only discovery source that runs. The
  licensing register does exist as a module and was measured, but deliberately
  is not wired as a discovery source — see "The licensing register" above.
- **`DecidedBy.LLM` is referenced nowhere.** LLM adjudication of the ambiguous
  match band was designed and never built; that band goes to a human.
- **DuckDB is not used.** `duckdb>=1.1` is in `requirements.txt` and nothing
  imports it. It was intended as the local pipeline working set.
- No supplier contact is automated. The pipeline ends at a qualified lead.
- Nothing a user does is persisted. Review decisions and lead removals are
  captured in the browser session only, and a live sweep's results are lost on
  navigation.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env   # then fill it in
uvicorn supply_radar.api.main:app --reload --port 8080
```

## Deploying

```bash
gcloud run deploy supply-radar --source . --region europe-west1
```

No local Docker required; Cloud Build builds the image remotely.

## Tests

```bash
pytest
```

## Data

The Viator supplier list used here is **synthetic**, generated with hidden
ground truth so that match precision and recall are measurable rather than
asserted. Discovery data is real.
