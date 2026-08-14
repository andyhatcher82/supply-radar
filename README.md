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

- **Discover** — Google Places across an area, plus web search, plus one
  non-Google source (a Croatian DMO / licensing register).
- **Classify** — is this actually an experience supplier, or a car park?
- **Normalise** — locale-aware: diacritics, legal forms, phones, domains.
- **Match** — deterministic hard keys, then deterministic fuzzy scoring, then an
  LLM adjudicating only the ambiguous middle band.
- **Enrich** — operator website into structured fields.
- **Score** — three separate axes: quality, readiness, gap fit.
- **Triage** — human review where it matters, which is not where most people
  put it. See the decision log.
- **Export** — versioned lead records with full provenance.

## Design notes

The reasoning, the rejected alternatives, the assumptions and the limitations
are in the decision log that accompanies this repo. The short version:

- The LLM touches roughly 10–15% of match decisions, by design. Deterministic
  rules handle the rest, because they are cheaper, faster, reproducible and
  explainable.
- Human review is deliberately pointed at the **auto-matched** decisions rather
  than the passes. A false positive costs one wasted Sales call. A false
  negative means an operator is silently never contacted again.
- The Places API silently truncates results at a per-query cap, so a naive
  city-wide sweep quietly loses suppliers. Cells that come back at the cap are
  subdivided and re-queried.
- Competitor marketplaces are deliberately **not** scraped.

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
