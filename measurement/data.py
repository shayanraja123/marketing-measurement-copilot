"""Load the media mix model's input table and prepare it for modeling.

The models read ONLY `mart_mmm_daily` (built by dbt). They never read the ground-truth file:
that is done by measurement/grade.py, after a model has made its estimates.
"""

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "warehouse.duckdb"

CHANNELS = ["paid_search", "paid_social", "tiktok", "display", "email"]
SPEND_COLUMNS = [f"spend_{c}" for c in CHANNELS]


def load_mmm_daily(db_path: Path = DEFAULT_DB) -> pd.DataFrame:
    """Read mart_mmm_daily (one row per day and region) from the local DuckDB warehouse."""
    import duckdb  # installed with dbt-duckdb

    with duckdb.connect(str(db_path), read_only=True) as con:
        df = con.execute("select * from main.mart_mmm_daily order by date_day, region").df()
    df["date_day"] = pd.to_datetime(df["date_day"])
    money = ["revenue", *SPEND_COLUMNS]
    df[money] = df[money].astype(float)
    return df


def national_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Sum regions into one row per day, and fill the days the display export skipped.

    On those days, display spend in the mart is 0 only because the file was missing, not because
    nothing was spent. Treating it as 0 would tell the model display spend dropped, so those days
    are filled by linear interpolation from the days on either side.
    """
    agg = {"revenue": "sum", "orders": "sum", **{c: "sum" for c in SPEND_COLUMNS}}
    flags = {"is_promo_day": "max", "display_export_missing": "max"}
    national = df.groupby("date_day").agg({**agg, **flags}).reset_index()

    gap = national["display_export_missing"].astype(bool)
    national.loc[gap, "spend_display"] = float("nan")
    national["spend_display"] = national["spend_display"].interpolate(limit_direction="both")
    national["is_promo_day"] = national["is_promo_day"].astype(int)
    return national
