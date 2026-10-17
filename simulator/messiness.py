"""Turn the clean simulated tables into realistic, messy export files.

Real brands don't get one tidy table. Each ad platform exports its own file with its own column names,
date conventions and quirks. This module produces those files from the clean tables, and adds data
defects one by one, each behind a flag in `config.yaml -> messiness`.

With every flag off, the exports contain exactly the clean data (only renamed and split by platform).
Each defect uses its own random stream, so turning one on never changes another file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# channel -> (file name, column renames from the canonical ad_spend table)
PLATFORM_EXPORTS = {
    "paid_search": (
        "google_ads_daily.csv",
        {
            "date": "Day",
            "campaign_id": "Campaign ID",
            "campaign_name": "Campaign",
            "region": "Region",
            "spend": "Cost",
            "impressions": "Impressions",
            "clicks": "Clicks",
            "platform_conversions": "Conversions",
            "platform_conversion_value": "Conv. value",
        },
    ),
    "paid_social": (
        "meta_ads_daily.csv",
        {
            "date": "date_start",
            "platform_conversions": "purchases",
            "platform_conversion_value": "purchase_value",
        },
    ),
    "tiktok": (
        "tiktok_ads_daily.csv",
        {
            "date": "stat_time_day",
            "platform_conversions": "complete_payment",
            "platform_conversion_value": "complete_payment_value",
        },
    ),
    "display": (
        "dsp_display_daily.csv",
        {
            "date": "report_date",
            "region": "geo",
            "spend": "media_cost",
            "platform_conversions": "conversions",
            "platform_conversion_value": "conversion_revenue",
        },
    ),
    "email": (
        "esp_email_daily.csv",
        {
            "date": "send_date",
            "spend": "cost",
            "impressions": "emails_sent",
            "platform_conversions": "attributed_orders",
            "platform_conversion_value": "attributed_revenue",
        },
    ),
}

SHOP_EXPORTS = {
    "orders": "shop_orders.csv",
    "customers": "shop_customers.csv",
    "products": "shop_products.csv",
    "price_history": "shop_price_history.csv",
    "campaign_log": "campaign_settings_log.csv",
    "regions": "regions.csv",
    "geo_experiments": "geo_experiments.csv",
}

# Fixed index per defect, so each gets its own reproducible random stream.
DEFECTS = [
    "duplicate_spend_rows",
    "tiktok_spend_as_text",
    "display_utc_timestamps",
    "missing_display_days",
    "search_name_inconsistency",
    "null_impressions",
    "late_arriving_orders",
]


def _rng(seed: int, defect: str) -> np.random.Generator:
    return np.random.default_rng([seed, DEFECTS.index(defect)])


def build_exports(tables: dict[str, pd.DataFrame], flags: dict, seed: int) -> dict[str, pd.DataFrame]:
    on = {d: bool(flags.get(d, False)) for d in DEFECTS}
    exports: dict[str, pd.DataFrame] = {}

    ad = tables["ad_spend"].copy()
    ad["date"] = ad["date"].dt.date
    per_channel = {ch: df.drop(columns="channel").reset_index(drop=True) for ch, df in ad.groupby("channel")}

    # --- Meta: some rows exported twice, a few impressions missing
    meta = per_channel["paid_social"]
    if on["duplicate_spend_rows"]:
        rng = _rng(seed, "duplicate_spend_rows")
        dupes = meta.sample(frac=0.01, random_state=int(rng.integers(1 << 31)))
        meta = pd.concat([meta, dupes]).sort_values(["date", "campaign_id", "region"], kind="stable")
    if on["null_impressions"]:
        rng = _rng(seed, "null_impressions")
        meta = meta.copy()
        meta["impressions"] = meta["impressions"].astype("Int64")
        meta.loc[rng.random(len(meta)) < 0.005, "impressions"] = pd.NA
    per_channel["paid_social"] = meta.reset_index(drop=True)

    # --- TikTok: spend written as currency text
    if on["tiktok_spend_as_text"]:
        tt = per_channel["tiktok"].copy()
        tt["spend"] = tt["spend"].map(lambda x: f"${x:,.2f}")
        per_channel["tiktok"] = tt

    # --- Display: dates reported as UTC end-of-day timestamps; some days missing
    disp = per_channel["display"]
    if on["missing_display_days"]:
        rng = _rng(seed, "missing_display_days")
        days = np.array(sorted(disp["date"].unique()))
        drop = set(rng.choice(days, size=4, replace=False))
        disp = disp[~disp["date"].isin(drop)]
    disp = disp.rename(columns=PLATFORM_EXPORTS["display"][1])
    if on["display_utc_timestamps"]:
        # local day D (America/Phoenix, UTC-7) ends at D+1 06:59:59 UTC; casting this to a date gives D+1
        ts = pd.to_datetime(disp["report_date"]) + pd.Timedelta(days=1, hours=6, minutes=59, seconds=59)
        disp.insert(0, "timestamp_utc", ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
        disp = disp.drop(columns="report_date")
    exports[PLATFORM_EXPORTS["display"][0]] = disp.reset_index(drop=True)

    # --- Google Ads: campaign names with stray casing and spaces
    if on["search_name_inconsistency"]:
        rng = _rng(seed, "search_name_inconsistency")
        gs = per_channel["paid_search"].copy()
        pick = rng.random(len(gs)) < 0.3
        variant = rng.integers(0, 4, len(gs))
        variants = [str.upper, str.lower, lambda s: s + "  ", lambda s: " " + s]
        gs.loc[pick, "campaign_name"] = [variants[v](s) for v, s in zip(variant[pick], gs.loc[pick, "campaign_name"])]
        per_channel["paid_search"] = gs

    for ch, (fname, renames) in PLATFORM_EXPORTS.items():
        if ch == "display":
            continue
        exports[fname] = per_channel[ch].rename(columns=renames)

    # --- Shop exports; some orders arrive in a later file
    orders = tables["orders"].copy()
    if on["late_arriving_orders"]:
        rng = _rng(seed, "late_arriving_orders")
        recent = orders["order_ts"] >= orders["order_ts"].max() - pd.Timedelta(days=60)
        late = recent & (rng.random(len(orders)) < 0.03)
        late_df = orders[late].copy()
        late_df["exported_at"] = late_df["order_ts"].dt.normalize() + pd.to_timedelta(
            rng.integers(2, 11, int(late.sum())), unit="D"
        ) + pd.Timedelta(hours=2)
        exports["shop_orders_late.csv"] = late_df.reset_index(drop=True)
        orders = orders[~late]
    exports[SHOP_EXPORTS["orders"]] = orders.reset_index(drop=True)
    for name, fname in SHOP_EXPORTS.items():
        if name != "orders":
            exports[fname] = tables[name]

    return exports
