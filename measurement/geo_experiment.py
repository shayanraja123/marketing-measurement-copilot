"""Measure the geo holdout test: how much revenue did turning paid social off actually cost?

The test (GEO-2025-01): paid social was switched off in 3 regions (treatment) for 4 weeks,
while the other 7 regions (control) kept spending.

Method: difference-in-differences with a scaled control.
  1. In the weeks BEFORE the test, measure how big treatment revenue is relative to control
     revenue (the ratio).
  2. During the test, "what treatment would have sold" = control revenue x that ratio.
     Seasonality, weekdays and anything else hitting all regions cancel out, because control
     regions experience them too.
  3. Lost revenue = expected - actual. The window includes 2 weeks after the test, because
     adstock means sales keep recovering after spend resumes.
  4. iROAS = lost revenue / spend withheld. Withheld spend is estimated the same way: what treatment
     regions would have spent = control spend x the pre-test spend ratio.

How sure are we?
  * Bootstrap: resample whole weeks (keeping weekday patterns intact) 2,000 times and recompute,
    giving a 95% confidence interval.
  * Placebo tests: run the same method on every 4-week window before the test, when NO test
    happened. Those "effects" are pure noise; their spread shows how big an effect noise alone can
    fake. The share of placebo effects at least as large as the real one works like a p-value.
    The bootstrap only captures noise inside the test; placebos also capture drift between regions,
    so they are the more honest check.

The analysis reads only the warehouse. The truth is compared at the very end, in a separate step.

Usage:
    python -m measurement.geo_experiment
"""

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from measurement.data import ROOT, load_mmm_daily

RESULTS_DIR = ROOT / "results" / "geo_experiment"
PRE_DAYS = 42           # 6 weeks before the test to learn the treatment/control ratio
POST_DAYS = 14          # 2 weeks after the test, for the adstock tail
N_BOOT = 2000
BLOCK_DAYS = 7          # bootstrap resamples whole weeks
SEED = 42


@dataclass
class Experiment:
    experiment_id: str
    channel: str
    treatment_regions: list[str]
    start: pd.Timestamp
    end: pd.Timestamp


def load_experiment(db_path=ROOT / "data" / "warehouse.duckdb") -> Experiment:
    """Read the test's design from the warehouse (what the brand logged when it ran the test)."""
    import duckdb

    with duckdb.connect(str(db_path), read_only=True) as con:
        rows = con.execute(
            "select experiment_id, channel, region, start_date, end_date from main.dim_geo_experiment_regions"
        ).df()
    first = rows.iloc[0]
    return Experiment(
        experiment_id=first["experiment_id"],
        channel=first["channel"],
        treatment_regions=sorted(rows["region"]),
        start=pd.Timestamp(first["start_date"]),
        end=pd.Timestamp(first["end_date"]),
    )


def group_daily(mart: pd.DataFrame, exp: Experiment) -> pd.DataFrame:
    """Daily revenue and test-channel spend, summed for treatment and for control regions."""
    spend_col = f"spend_{exp.channel}"
    group = np.where(mart["region"].isin(exp.treatment_regions), "treatment", "control")
    daily = (
        mart.assign(group=group)
        .groupby(["date_day", "group"])[["revenue", spend_col]]
        .sum()
        .unstack("group")
    )
    daily.columns = [f"{metric}_{g}" for metric, g in daily.columns]
    return daily.rename(columns=lambda c: c.replace(spend_col, "spend"))


def did_estimate(pre: pd.DataFrame, window: pd.DataFrame) -> dict:
    """Scaled-control difference-in-differences on one pre-period and one measurement window."""
    revenue_ratio = pre["revenue_treatment"].sum() / pre["revenue_control"].sum()
    spend_ratio = pre["spend_treatment"].sum() / pre["spend_control"].sum()
    expected_revenue = revenue_ratio * window["revenue_control"].sum()
    actual_revenue = window["revenue_treatment"].sum()
    expected_spend = spend_ratio * window["spend_control"].sum()
    spend_withheld = expected_spend - window["spend_treatment"].sum()
    lost = expected_revenue - actual_revenue
    return {
        "revenue_lost": lost,
        "lift_pct": lost / expected_revenue,
        "spend_withheld": spend_withheld,
        "iroas": lost / spend_withheld if spend_withheld > 0 else np.nan,
    }


def windows(daily: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp):
    pre = daily.loc[start - pd.Timedelta(days=PRE_DAYS) : start - pd.Timedelta(days=1)]
    window = daily.loc[start : end + pd.Timedelta(days=POST_DAYS)]
    return pre, window


def block_bootstrap(pre: pd.DataFrame, window: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Resample whole weeks within each period, with replacement, and re-estimate each time."""

    def resample(df: pd.DataFrame) -> pd.DataFrame:
        blocks = [df.iloc[i : i + BLOCK_DAYS] for i in range(0, len(df), BLOCK_DAYS)]
        picks = rng.integers(0, len(blocks), len(blocks))
        return pd.concat([blocks[i] for i in picks])

    return pd.DataFrame([did_estimate(resample(pre), resample(window)) for _ in range(N_BOOT)])


def placebo_tests(daily: pd.DataFrame, exp: Experiment) -> pd.DataFrame:
    """The same method on every weekly start date with no test, ending before the real test begins.

    Windows overlap, so they are not independent, but together they show how big an "effect"
    ordinary noise can produce in this data.
    """
    length = (exp.end - exp.start).days
    last_start = exp.start - pd.Timedelta(days=length + POST_DAYS + 1)
    first_start = daily.index.min() + pd.Timedelta(days=PRE_DAYS)
    rows = []
    for s in pd.date_range(first_start, last_start, freq="7D"):
        pre, window = windows(daily, s, s + pd.Timedelta(days=length))
        est = did_estimate(pre, window)
        rows.append({"placebo_start": s.date(), "lift_pct": est["lift_pct"], "revenue_lost": est["revenue_lost"]})
    return pd.DataFrame(rows)


def analyze(mart: pd.DataFrame, exp: Experiment) -> dict:
    daily = group_daily(mart, exp)
    pre, window = windows(daily, exp.start, exp.end)
    point = did_estimate(pre, window)
    boot = block_bootstrap(pre, window, np.random.default_rng(SEED))
    placebos = placebo_tests(daily, exp)
    ci = {k: boot[k].quantile([0.025, 0.975]).tolist() for k in ["revenue_lost", "lift_pct", "iroas"]}
    return {
        "experiment_id": exp.experiment_id,
        "channel": exp.channel,
        "treatment_regions": exp.treatment_regions,
        "test_period": [str(exp.start.date()), str(exp.end.date())],
        "measurement_window": [str(window.index.min().date()), str(window.index.max().date())],
        "pre_period_days": len(pre),
        "estimate": point,
        "ci95": ci,
        "placebo_windows": len(placebos),
        "placebo_lift_sd": float(placebos["lift_pct"].std()),
        # share of no-test windows whose "effect" is at least as large as the real one
        "placebo_p_value": float((placebos["lift_pct"].abs() >= abs(point["lift_pct"])).mean()),
    }


def main() -> None:
    exp = load_experiment()
    result = analyze(load_mmm_daily(), exp)
    est, ci = result["estimate"], result["ci95"]

    print(f"Geo holdout {result['experiment_id']}: {exp.channel} off in {', '.join(exp.treatment_regions)}")
    print(f"Test {result['test_period'][0]} to {result['test_period'][1]}; "
          f"measured through {result['measurement_window'][1]} (includes the {POST_DAYS}-day adstock tail)\n")
    print(f"Revenue lost:    ${est['revenue_lost']:>10,.0f}   95% CI ${ci['revenue_lost'][0]:,.0f} to ${ci['revenue_lost'][1]:,.0f}")
    print(f"Lift:            {est['lift_pct']:>10.1%}   95% CI {ci['lift_pct'][0]:.1%} to {ci['lift_pct'][1]:.1%}")
    print(f"Spend withheld:  ${est['spend_withheld']:>10,.0f}")
    print(f"iROAS:           {est['iroas']:>10.2f}   95% CI {ci['iroas'][0]:.2f} to {ci['iroas'][1]:.2f}")
    print(f"\nPlacebo check: the same method on {result['placebo_windows']} windows with no test")
    print(f"  typical 'effect' from noise alone: +/-{result['placebo_lift_sd']:.1%}")
    print(f"  share of no-test windows with an effect at least as large as the real one: "
          f"{result['placebo_p_value']:.0%}  (lower is stronger evidence)")

    # ---- grading: compare with the truth (read only here)
    from measurement.grade import load_experiment_truth

    truth = load_experiment_truth(result["experiment_id"])
    inside = ci["iroas"][0] <= truth["true_iroas"] <= ci["iroas"][1]
    print(f"\nTruth: iROAS {truth['true_iroas']:.2f}, revenue lost ${truth['true_revenue_lost']:,.0f}, "
          f"spend withheld ${truth['spend_withheld']:,.0f}")
    print(f"Estimate error: {est['iroas'] / truth['true_iroas'] - 1:+.0%}; truth inside the 95% CI: {inside}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result["truth"] = {**truth, "inside_ci95": inside}
    with open(RESULTS_DIR / "geo_holdout.json", "w") as f:
        json.dump(result, f, indent=2, default=float)
    print(f"\nSaved to {RESULTS_DIR / 'geo_holdout.json'}")


if __name__ == "__main__":
    main()
