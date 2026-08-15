"""Append the current snapshot's leads to BigQuery.

Run after build_snapshot.py. Costs effectively nothing: 40 rows is far inside
the free tier, and the table is partitioned so it stays that way as runs
accumulate.

    python scripts/publish_to_bigquery.py            # uses BQ_PROJECT / BQ_DATASET
    python scripts/publish_to_bigquery.py --dry-run  # print rows, write nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supply_radar.config import SNAPSHOT_DIR, get_settings  # noqa: E402
from supply_radar.warehouse import _rows_from_snapshot, publish_snapshot  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    settings = get_settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=settings.bq_project)
    parser.add_argument("--dataset", default=settings.bq_dataset)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    snapshot_path = SNAPSHOT_DIR / "snapshot.json"

    if args.dry_run:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        from datetime import datetime, timezone

        rows = _rows_from_snapshot(snapshot, "dryrun", datetime.now(timezone.utc))
        print(f"{len(rows)} rows would be appended. First row:")
        print(json.dumps(rows[0], indent=2, ensure_ascii=False))
        return

    if not args.project:
        raise SystemExit(
            "No BigQuery project. Set BQ_PROJECT in .env or pass --project.\n"
            "This is the one part of the pipeline that needs a real GCP project."
        )

    result = publish_snapshot(snapshot_path, args.project, args.dataset)
    print(result)
    print()
    print("Query it:")
    print(f"  SELECT band, COUNT(*) FROM `{result.table}`")
    print(f"  WHERE run_id = '{result.run_id}' GROUP BY band ORDER BY band")


if __name__ == "__main__":
    main()
