"""Upload the raw export files (data/raw/*.csv) into BigQuery, one table per file.

This is the "load" step: files land in the `raw` dataset untouched, with every column stored as
text (STRING), exactly like the local DuckDB setup reads them. dbt then cleans and types them.

Each run replaces the tables (WRITE_TRUNCATE), so it is safe to run again. It uses BigQuery
load jobs, which work in the free sandbox (the sandbox does not allow INSERT/UPDATE/DELETE).

Usage:
    python -m pipeline.load_raw_bigquery                       # project from GCP_PROJECT or default
    python -m pipeline.load_raw_bigquery --project my-project-id
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = "marketing-measurement-515620"
DATASET = "raw"
LOCATION = "US"


def bigquery_column_name(header: str) -> str:
    """BigQuery column names can't contain spaces or dots: "Conv. value" -> "conv_value".

    The dbt macro raw_col() in warehouse/macros/cross_db.sql applies the same rule,
    so the two must stay in sync.
    """
    return re.sub(r"[^0-9a-zA-Z]+", "_", header).strip("_").lower()


def load_all(project: str, raw_dir: Path) -> None:
    from google.cloud import bigquery  # imported here so --help works without the package

    client = bigquery.Client(project=project, location=LOCATION)
    dataset = bigquery.Dataset(f"{project}.{DATASET}")
    dataset.location = LOCATION
    client.create_dataset(dataset, exists_ok=True)

    files = sorted(raw_dir.glob("*.csv"))
    if not files:
        raise SystemExit(f"No CSV files in {raw_dir}. Run `python -m simulator.generate` first.")

    for path in files:
        with open(path, newline="") as f:
            headers = next(csv.reader(f))
        schema = [bigquery.SchemaField(bigquery_column_name(h), "STRING") for h in headers]

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        table_id = f"{project}.{DATASET}.{path.stem}"
        with open(path, "rb") as f:
            job = client.load_table_from_file(f, table_id, job_config=job_config)
        job.result()  # wait for the load to finish; raises if it failed
        table = client.get_table(table_id)
        print(f"  {path.stem:<24} {table.num_rows:>9,} rows")

    print(f"\nLoaded {len(files)} tables into {project}.{DATASET}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT", DEFAULT_PROJECT))
    parser.add_argument("--raw-dir", default=str(ROOT / "data" / "raw"))
    args = parser.parse_args()
    print(f"Loading raw files into BigQuery project {args.project}, dataset `{DATASET}`:\n")
    load_all(args.project, Path(args.raw_dir))


if __name__ == "__main__":
    main()
