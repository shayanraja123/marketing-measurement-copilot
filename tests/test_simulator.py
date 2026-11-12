"""Tests that the simulator's ground truth means what it claims.

Run with:  pytest -q
"""

from __future__ import annotations

import copy
from functools import lru_cache

import numpy as np
import pandas as pd

from simulator.generate import load_config, simulate
from simulator.messiness import DEFECTS, PLATFORM_EXPORTS, build_exports

SEED = 7


@lru_cache(maxsize=None)
def full_run():
    return simulate(load_config())


def small_config(n_days: int = 200) -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["n_days"] = n_days
    cfg["geo_experiments"] = []
    cfg["campaign_changes"] = [c for c in cfg["campaign_changes"] if c["day"] < n_days]
    cfg["products"]["price_increase_days"] = [d for d in cfg["products"]["price_increase_days"] if d < n_days]
    return cfg


@lru_cache(maxsize=None)
def small_run():
    return simulate(small_config())


def exports_with(flags_on: set[str]) -> dict[str, pd.DataFrame]:
    return build_exports(small_run().tables, {d: d in flags_on for d in DEFECTS}, SEED)


# ----------------------------------------------------------------- reproducibility


def test_same_seed_gives_identical_data():
    a, b = simulate(small_config(150)), simulate(small_config(150))
    for name in a.tables:
        pd.testing.assert_frame_equal(a.tables[name], b.tables[name])
    assert a.truth == b.truth


# ----------------------------------------------------------------- ground truth


def test_true_iroas_matches_config_target():
    for name, t in full_run().truth["channels"].items():
        assert abs(t["true_iroas"] / t["target_iroas"] - 1) < 0.02, name


def test_marginal_iroas_below_average_iroas():
    # diminishing returns: the next dollar earns less than the average dollar
    for name, t in full_run().truth["channels"].items():
        assert t["true_marginal_iroas"] < t["true_iroas"], name


def test_contributions_add_up_to_expected_revenue():
    r = full_run()
    assert np.isclose(r.contributions.expected_revenue.sum(), r.truth["total_expected_revenue"], rtol=1e-6)


def test_simulated_revenue_close_to_expected():
    r = full_run()
    actual = r.tables["orders"].revenue.sum()
    assert abs(actual / r.truth["total_expected_revenue"] - 1) < 0.02


def test_platforms_overclaim():
    for name, t in full_run().truth["channels"].items():
        assert t["expected_platform_roas"] > t["true_iroas"], name


# ----------------------------------------------------------------- spend logic


def test_tiktok_has_no_spend_before_launch():
    r = full_run()
    cfg = load_config()
    launch = r.meta["dates"][cfg["channels"]["tiktok"]["launch_day"]]
    tt = r.tables["ad_spend"].query("channel == 'tiktok'")
    assert tt.date.min() >= launch


def test_geo_holdout_switches_channel_off():
    r = full_run()
    exp = load_config()["geo_experiments"][0]
    dates = r.meta["dates"]
    ad = r.tables["ad_spend"]
    in_window = (
        (ad.channel == exp["channel"])
        & ad.region.isin(exp["treatment_regions"])
        & ad.date.between(dates[exp["start_day"]], dates[exp["end_day"]])
    )
    assert not in_window.any()
    # control regions kept spending during the test
    control = (ad.channel == exp["channel"]) & ~ad.region.isin(exp["treatment_regions"])
    assert (control & ad.date.between(dates[exp["start_day"]], dates[exp["end_day"]])).any()
    truth = r.truth["experiments"][exp["id"]]
    assert truth["true_revenue_lost"] > 0 and truth["spend_withheld"] > 0


# ----------------------------------------------------------------- messiness


def _ad_spend_from_exports(exports: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for channel, (fname, renames) in PLATFORM_EXPORTS.items():
        df = exports[fname].rename(columns={v: k for k, v in renames.items()})
        df.insert(0, "channel", channel)
        frames.append(df)
    out = pd.concat(frames)
    out["date"] = pd.to_datetime(out["date"])
    return out


def test_all_flags_off_reproduces_clean_data():
    clean = small_run().tables
    exp = exports_with(set())
    cols = list(clean["ad_spend"].columns)
    got = _ad_spend_from_exports(exp)[cols].sort_values(["date", "channel", "campaign_id", "region"])
    want = clean["ad_spend"].sort_values(["date", "channel", "campaign_id", "region"])
    pd.testing.assert_frame_equal(got.reset_index(drop=True), want.reset_index(drop=True), check_dtype=False)
    pd.testing.assert_frame_equal(exp["shop_orders.csv"], clean["orders"])
    assert "shop_orders_late.csv" not in exp


EXPECTED_TARGETS = {
    "duplicate_spend_rows": {"meta_ads_daily.csv"},
    "null_impressions": {"meta_ads_daily.csv"},
    "tiktok_spend_as_text": {"tiktok_ads_daily.csv"},
    "display_utc_timestamps": {"dsp_display_daily.csv"},
    "missing_display_days": {"dsp_display_daily.csv"},
    "search_name_inconsistency": {"google_ads_daily.csv"},
    "late_arriving_orders": {"shop_orders.csv", "shop_orders_late.csv"},
}


def test_each_defect_changes_only_its_own_files():
    base = exports_with(set())
    for defect in DEFECTS:
        messy = exports_with({defect})
        changed = {f for f in set(base) | set(messy) if f not in base or f not in messy or not base[f].equals(messy[f])}
        assert changed == EXPECTED_TARGETS[defect], defect


def test_late_orders_plus_main_export_equal_all_orders():
    exp = exports_with({"late_arriving_orders"})
    combined = pd.concat([exp["shop_orders.csv"], exp["shop_orders_late.csv"].drop(columns="exported_at")])
    assert sorted(combined.order_id) == sorted(small_run().tables["orders"].order_id)


def test_utc_timestamps_convert_back_to_local_dates():
    exp = exports_with({"display_utc_timestamps"})
    ts = pd.to_datetime(exp["dsp_display_daily.csv"]["timestamp_utc"], utc=True)
    local_dates = ts.dt.tz_convert("America/Phoenix").dt.date
    clean = small_run().tables["ad_spend"].query("channel == 'display'")
    assert sorted(local_dates) == sorted(clean.date.dt.date)
