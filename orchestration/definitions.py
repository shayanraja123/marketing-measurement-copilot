"""Dagster pipeline: raw exports -> dbt warehouse (daily), and an on-demand BigQuery publish.

Start the web UI from the project root with:   dagster dev
Then open http://localhost:3000

What Dagster runs:
  1. raw_exports         Writes the 13 raw export files (here: the simulator; in a real company,
                         pulls from the ad platforms' and store's APIs).
  2. dbt models          Every dbt model is its own asset, so the UI shows the full lineage, and
                         every dbt test shows up as a check on its table.
  3. daily_refresh       Runs 1 and 2 on the local DuckDB warehouse, scheduled every morning.
  4. publish_to_bigquery Uploads the raw files and rebuilds the warehouse in BigQuery. Started by
                         hand (before a demo, for example), because the free BigQuery sandbox has a
                         10 GiB lifetime storage limit that daily reloads would slowly use up.
"""


import os
import subprocess
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "warehouse"
RAW_DIR = ROOT / "data" / "raw"

# The 13 tables declared as dbt sources (warehouse/models/staging/_sources.yml).
# dagster-dbt names a source ["raw", <table>], so producing these keys links them to dbt's lineage.
RAW_TABLES = [
    "google_ads_daily",
    "meta_ads_daily",
    "tiktok_ads_daily",
    "dsp_display_daily",
    "esp_email_daily",
    "shop_orders",
    "shop_orders_late",
    "shop_customers",
    "shop_products",
    "shop_price_history",
    "campaign_settings_log",
    "regions",
    "geo_experiments",
]

warehouse_project = DbtProject(project_dir=DBT_DIR, profiles_dir=DBT_DIR)
warehouse_project.prepare_if_dev()  # under `dagster dev`, parses dbt so the manifest is current


# --------------------------------------------------------------------------- 1. raw exports


@dg.multi_asset(
    specs=[dg.AssetSpec(["raw", name], group_name="raw_exports") for name in RAW_TABLES],
    description="Writes the brand's raw export files to data/raw/.",
)
def raw_exports(context: dg.AssetExecutionContext):
    from simulator.generate import load_config, simulate, write_outputs

    cfg = load_config()
    result = simulate(cfg)
    written = write_outputs(result, cfg, ROOT / "data")
    context.log.info(f"Wrote {len(written)} files to {RAW_DIR}")

    for name in RAW_TABLES:
        path = RAW_DIR / f"{name}.csv"
        with open(path) as f:
            rows = sum(1 for _ in f) - 1  # minus the header line
        yield dg.MaterializeResult(
            asset_key=["raw", name],
            metadata={"rows": rows, "path": dg.MetadataValue.path(str(path))},
        )


# --------------------------------------------------------------------------- 2. dbt warehouse


@dbt_assets(manifest=warehouse_project.manifest_path)
def warehouse_models(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    """Builds and tests every dbt model on the local DuckDB warehouse (target: dev)."""
    yield from dbt.cli(["build"], context=context).stream()


# --------------------------------------------------------------------------- 3. daily schedule

daily_refresh = dg.define_asset_job(
    name="daily_refresh",
    selection=dg.AssetSelection.all(),
    description="Refresh the raw exports, then build and test the whole warehouse on DuckDB.",
)

daily_refresh_schedule = dg.ScheduleDefinition(
    job=daily_refresh,
    cron_schedule="0 6 * * *",              # every day at 6:00 am
    execution_timezone="America/Phoenix",
    default_status=dg.DefaultScheduleStatus.STOPPED,  # switch it on in the UI when you want it
)


# --------------------------------------------------------------------------- 4. BigQuery publish


@dg.op(description="Uploads data/raw/*.csv to the BigQuery `raw` dataset.")
def load_raw_to_bigquery(context: dg.OpExecutionContext) -> int:
    from pipeline.load_raw_bigquery import DEFAULT_PROJECT, load_all

    project = os.environ.get("GCP_PROJECT", DEFAULT_PROJECT)
    load_all(project, RAW_DIR)
    context.log.info(f"Loaded raw files into {project}.raw")
    return len(list(RAW_DIR.glob("*.csv")))


@dg.op(description="Runs `dbt build --target prod`: builds and tests every model in BigQuery.")
def build_warehouse_in_bigquery(context: dg.OpExecutionContext, files_loaded: int) -> None:
    context.log.info(f"{files_loaded} raw tables in BigQuery; building the warehouse there")
    run = subprocess.run(
        ["dbt", "--no-use-colors", "build", "--target", "prod"],  # plain text logs for the UI
        cwd=DBT_DIR,
        capture_output=True,
        text=True,
    )
    context.log.info(run.stdout[-5000:])  # dbt's summary is at the end
    if run.returncode != 0:
        raise dg.Failure(description="dbt build failed in BigQuery", metadata={"stderr": run.stderr[-2000:]})


@dg.job(description="Upload raw files and rebuild the warehouse in BigQuery. Run by hand.")
def publish_to_bigquery():
    build_warehouse_in_bigquery(load_raw_to_bigquery())


# --------------------------------------------------------------------------- definitions

defs = dg.Definitions(
    assets=[raw_exports, warehouse_models],
    jobs=[daily_refresh, publish_to_bigquery],
    schedules=[daily_refresh_schedule],
    resources={"dbt": DbtCliResource(project_dir=warehouse_project)},
)
