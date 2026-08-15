"""Publishing leads to BigQuery.

WHY BIGQUERY AND NOT SOMETHING SIMPLER
    Two reasons, and only one of them is technical. Per round-one intel Viator
    already run BigQuery and Looker, so a lead table landing there is output in
    their stack rather than output they have to import. And it is the same
    pattern already run in production for the Powder24 forecast-vs-actuals
    dashboard, which means it is a claim about something done rather than
    something read about.

WHAT IT WRITES
    One append-only table, one row per lead per run, stamped with a run id and
    a run timestamp. Append rather than replace, because the question a supply
    team asks three months in is not "what are the leads" but "what has changed
    since we last looked, and did the ones we contacted convert". A replaced
    table cannot answer that and cannot be recovered afterwards.

    Partitioned by run date and clustered by destination and band, so the
    Looker query a Destination Specialist actually runs — this destination,
    band A, latest run — scans one partition instead of the table.

WHAT IT DOES NOT DO
    It is not the console's read path. The console serves the JSON snapshot,
    which is committed, small, needs no credentials and cold-starts instantly
    on Cloud Run. Routing a page render through a warehouse query to prove a
    point would be worse engineering for a better-sounding sentence.

    Honest scope, then: BigQuery is where the output is published for
    downstream consumption, not where the app reads from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

LEADS_TABLE = "leads"

# Flattened deliberately. The three axes are nested dicts in the snapshot
# because the UI renders their components; a warehouse table that a Looker user
# has to UNNEST to filter on band or readiness is a table nobody uses.
SCHEMA = [
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("run_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("destination", "STRING"),
    bigquery.SchemaField("place_source_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("name", "STRING"),
    bigquery.SchemaField("website", "STRING"),
    # A lead table Sales cannot act on is a reporting table. Contact and
    # reputation fields travel with the score for that reason.
    bigquery.SchemaField("phone", "STRING"),
    bigquery.SchemaField("address", "STRING"),
    bigquery.SchemaField("rating", "FLOAT"),
    bigquery.SchemaField("review_count", "INTEGER"),
    bigquery.SchemaField("band", "STRING"),
    bigquery.SchemaField("composite", "FLOAT"),
    bigquery.SchemaField("quality", "FLOAT"),
    bigquery.SchemaField("readiness", "FLOAT"),
    bigquery.SchemaField("gap_fit", "FLOAT"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("category_source", "STRING"),
    bigquery.SchemaField("viator_top", "STRING"),
    bigquery.SchemaField("viator_label", "STRING"),
    bigquery.SchemaField("viator_path", "STRING"),
    bigquery.SchemaField("booking", "STRING"),
    bigquery.SchemaField("languages", "STRING", mode="REPEATED"),
    # The evidence travels with the lead. A lead Sales cannot trace back to why
    # it ranked where it did is a lead Sales will not trust, and that has to
    # survive the trip into the warehouse, not just render in the console.
    bigquery.SchemaField("evidence_json", "STRING"),
]


@dataclass
class PublishResult:
    table: str
    rows: int
    run_id: str
    created_dataset: bool
    created_table: bool

    def __str__(self) -> str:
        made = [
            what
            for what, did in (("dataset", self.created_dataset), ("table", self.created_table))
            if did
        ]
        suffix = f" (created {' and '.join(made)})" if made else ""
        return f"{self.rows} rows -> {self.table} as run {self.run_id}{suffix}"


def _rows_from_snapshot(snapshot: dict, run_id: str, run_at: datetime) -> list[dict]:
    destination = snapshot.get("destination")
    rows = []
    for lead in snapshot.get("leads", []):
        extract = lead.get("extract") or {}
        rows.append(
            {
                "run_id": run_id,
                "run_at": run_at.isoformat(),
                "run_date": run_at.date().isoformat(),
                "destination": lead.get("destination") or destination,
                "place_source_id": lead["place_source_id"],
                "name": lead.get("name"),
                "website": lead.get("website"),
                "phone": lead.get("phone"),
                "address": lead.get("address"),
                "rating": lead.get("rating"),
                "review_count": lead.get("review_count"),
                "band": lead.get("band"),
                "composite": lead.get("composite"),
                "quality": (lead.get("quality") or {}).get("score"),
                "readiness": (lead.get("readiness") or {}).get("score"),
                "gap_fit": (lead.get("gap_fit") or {}).get("score"),
                "category": lead.get("category"),
                "category_source": lead.get("category_source"),
                "viator_top": lead.get("viator_top"),
                "viator_label": lead.get("viator_label"),
                "viator_path": lead.get("viator_path"),
                "booking": extract.get("booking"),
                "languages": [x for x in (extract.get("languages") or []) if x],
                "evidence_json": json.dumps(
                    {
                        axis: (lead.get(axis) or {}).get("components", [])
                        for axis in ("quality", "readiness", "gap_fit")
                    },
                    ensure_ascii=False,
                ),
            }
        )
    return rows


def publish_snapshot(
    snapshot_path: Path,
    project: str,
    dataset: str,
    run_id: str | None = None,
    client: bigquery.Client | None = None,
) -> PublishResult:
    """Append one run's leads to the warehouse, creating the table if needed."""
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    run_at = datetime.now(timezone.utc)
    run_id = run_id or run_at.strftime("%Y%m%dT%H%M%SZ")

    client = client or bigquery.Client(project=project)
    dataset_ref = bigquery.DatasetReference(project, dataset)

    created_dataset = False
    try:
        client.get_dataset(dataset_ref)
    except NotFound:
        meta = bigquery.Dataset(dataset_ref)
        # Same region as the Cloud Run service. Cross-region reads are billable
        # and slow, and a warehouse in the wrong continent is a support ticket
        # nobody expects.
        meta.location = "europe-west1"
        client.create_dataset(meta)
        created_dataset = True

    table_ref = dataset_ref.table(LEADS_TABLE)
    created_table = False
    try:
        existing = client.get_table(table_ref)
        # Add any column the schema has gained since the table was created.
        # BigQuery allows appending NULLABLE fields in place, and without this
        # the first publish after a schema change fails on "no such field"
        # against a table nobody thought to migrate.
        known = {field.name for field in existing.schema}
        missing = [field for field in SCHEMA if field.name not in known]
        if missing:
            existing.schema = list(existing.schema) + missing
            client.update_table(existing, ["schema"])
    except NotFound:
        table = bigquery.Table(table_ref, schema=SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(field="run_date")
        table.clustering_fields = ["destination", "band"]
        client.create_table(table)
        created_table = True

    rows = _rows_from_snapshot(snapshot, run_id, run_at)
    if rows:
        errors = client.insert_rows_json(f"{project}.{dataset}.{LEADS_TABLE}", rows)
        if errors:
            raise RuntimeError(f"BigQuery rejected {len(errors)} row(s): {errors[:2]}")

    return PublishResult(
        table=f"{project}.{dataset}.{LEADS_TABLE}",
        rows=len(rows),
        run_id=run_id,
        created_dataset=created_dataset,
        created_table=created_table,
    )
