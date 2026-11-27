"""The Bayesian MMM fitted on 10 regions instead of national totals.

Why: summing regions into national totals throws information away. In the national data, the geo
holdout is just a small dip; in the regional data, paid social is at $0 in 3 regions for 4 weeks
while it keeps running in the other 7. That contrast is the strongest evidence in the data about
paid social's real effect, and the regions give the model 10x more variation to separate channel
effects from seasonality.

What changes from the national model (measurement/bayesian_mmm.py), and nothing else:
  * Data: one row per day per region (7,300 rows) instead of one per day (730).
  * Scaling: each region is scaled by its own size, so regions are compared on a relative footing.
  * Channel parameters (carryover, saturation, effect size) are shared across regions: the same ad
    is assumed to work similarly everywhere, so all 10 regions inform one set of channel effects.
  * No separate lift test: the experiment is already inside the regional data; adding it again
    would count the same evidence twice.
Priors, controls, seasonality and sampler settings match the national model, so the comparison
isolates one change: regional vs national data.

Usage:
    python -m measurement.regional_mmm            # full run (several minutes)
    python -m measurement.regional_mmm --quick    # quick check
"""

import argparse
import json

import numpy as np
import pandas as pd

from measurement.data import CHANNELS, ROOT, SPEND_COLUMNS, load_mmm_daily
from measurement.grade import grade, load_truth, summary_metrics, wrong_side_of_breakeven
from measurement.tracking import log_run

RESULTS_DIR = ROOT / "results" / "regional_mmm"
MODEL_DIR = ROOT / "data" / "models"
HDI_PROB = 0.94


def regional_daily(mart: pd.DataFrame) -> pd.DataFrame:
    """One row per day per region, with display's missing export days filled within each region."""
    df = mart.sort_values(["region", "date_day"]).copy()
    gap = df["display_export_missing"].astype(bool)
    df.loc[gap, "spend_display"] = np.nan
    df["spend_display"] = df.groupby("region")["spend_display"].transform(
        lambda s: s.interpolate(limit_direction="both")
    )
    return df.rename(columns={"region": "geo"}).reset_index(drop=True)


def build_inputs(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    days = (df["date_day"] - df["date_day"].min()).dt.days
    X = df[["date_day", "geo", *SPEND_COLUMNS]].copy()
    X["trend"] = days / days.max()
    X["promo"] = df["is_promo_day"].astype(float)
    dow = pd.get_dummies(df["date_day"].dt.day_of_week, prefix="dow", drop_first=True, dtype=float)
    X = pd.concat([X, dow], axis=1)
    controls = ["trend", "promo", *dow.columns]
    y = df["revenue"].astype(float).rename("y")
    return X, y, controls


def build_model(X: pd.DataFrame, controls: list[str]):
    import xarray as xr
    from pymc_extras.prior import Prior
    from pymc_marketing.mmm import GeometricAdstock, LogisticSaturation
    from pymc_marketing.mmm.mmm import MMM

    # Same priors as the national model (see bayesian_mmm.py for the reasoning)
    spend_share = X[SPEND_COLUMNS].sum() / X[SPEND_COLUMNS].sum().sum()
    prior_sigma = xr.DataArray((len(CHANNELS) * spend_share).to_numpy(), dims=("channel",))
    model_config = {
        "intercept": Prior("Normal", mu=0.5, sigma=0.2),
        "saturation_beta": Prior("HalfNormal", sigma=prior_sigma, dims="channel"),
        "gamma_control": Prior("Normal", mu=0, sigma=0.1, dims="control"),
        "gamma_fourier": Prior("Laplace", mu=0, b=0.2, dims="fourier_mode"),
        "likelihood": Prior("Normal", sigma=Prior("HalfNormal", sigma=2)),
    }
    return MMM(
        date_column="date_day",
        target_column="y",
        channel_columns=SPEND_COLUMNS,
        control_columns=controls,
        dims=("geo",),
        # scale over dates within each region, i.e. each region by its own size
        scaling={
            "channel": {"method": "max", "dims": ()},
            "target": {"method": "max", "dims": ()},
        },
        adstock=GeometricAdstock(l_max=14),
        saturation=LogisticSaturation(),
        yearly_seasonality=2,
        model_config=model_config,
    )


def national_iroas_posterior(mmm, X: pd.DataFrame):
    """Posterior iROAS per channel for the whole brand: total incremental revenue / total spend.

    If the library reports iROAS per region, regions are combined weighted by their spend
    (sum of region contributions / sum of region spend), the same definition as the national model.
    """
    import xarray as xr

    roas = mmm.incrementality.contribution_over_spend(frequency="all_time")
    if not isinstance(roas, xr.DataArray):
        roas = roas.to_array() if hasattr(roas, "to_array") else xr.DataArray(roas)
    roas = roas.assign_coords(channel=[str(c).replace("spend_", "") for c in roas["channel"].values])
    if "geo" in roas.dims:
        spend = X.groupby("geo")[SPEND_COLUMNS].sum()
        spend.columns = CHANNELS
        weights = xr.DataArray(
            spend.T.to_numpy(), dims=("channel", "geo"),
            coords={"channel": CHANNELS, "geo": spend.index.tolist()},
        )
        weights = weights.sel(geo=roas["geo"].values, channel=roas["channel"].values)
        roas = (roas * weights).sum("geo") / weights.sum("geo")
    sample_dims = [d for d in roas.dims if d != "channel"]
    return roas.stack(sample=sample_dims)


def summarize(roas) -> pd.DataFrame:
    low_q, high_q = (1 - HDI_PROB) / 2, 1 - (1 - HDI_PROB) / 2
    return pd.DataFrame(
        {
            "estimate": roas.mean("sample").to_series(),
            "low": roas.quantile(low_q, dim="sample").to_series(),
            "high": roas.quantile(high_q, dim="sample").to_series(),
        }
    ).reindex(CHANNELS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="fewer samples, for a fast check")
    args = parser.parse_args()
    chains, draws, tune = (2, 300, 300) if args.quick else (4, 1000, 1000)

    df = regional_daily(load_mmm_daily())
    X, y, controls = build_inputs(df)
    mmm = build_model(X, controls)

    print(f"Fitting on {len(X):,} rows ({X['geo'].nunique()} regions x {X['date_day'].nunique()} days): "
          f"{chains} chains x {draws} draws ...")
    mmm.fit(X=X, y=y, chains=chains, draws=draws, tune=tune, target_accept=0.95, random_seed=42)

    divergences = int(mmm.idata["sample_stats"]["diverging"].sum().item())
    summary = summarize(national_iroas_posterior(mmm, X))

    # ---- grading (truth read only here)
    truth = load_truth()
    plain = pd.read_csv(ROOT / "results" / "bayesian_mmm" / "iroas_vs_truth.csv", index_col="channel")["MMM"]
    table = grade({"national MMM": plain, "regional MMM": summary["estimate"]}, truth)
    table["regional 94% interval"] = [f"{lo:.2f} to {hi:.2f}" for lo, hi in zip(summary["low"], summary["high"])]
    table["truth inside interval"] = (truth["true_iroas"] >= summary["low"]) & (truth["true_iroas"] <= summary["high"])

    pd.set_option("display.width", 170)
    print(f"\nSampler divergences: {divergences} (0 is ideal)\n")
    print("iROAS (revenue per $1 of spend): truth vs the national MMM vs the regional MMM\n")
    print(table.drop(columns=["platform_roas"]).round(2).to_string())

    national_metrics = summary_metrics(plain, truth)
    metrics = summary_metrics(summary["estimate"], truth, summary["low"], summary["high"])
    wrong = wrong_side_of_breakeven(summary["estimate"], truth)
    print(f"\nChannels on the wrong side of break-even: {', '.join(wrong) if wrong else 'none'} "
          f"(national MMM: {int(national_metrics['channels_wrong_side'])})")
    print(f"Mean absolute error: {metrics['mean_abs_pct_error']:.0%} "
          f"(national MMM: {national_metrics['mean_abs_pct_error']:.0%})")
    print(f"Truth inside the 94% interval: {int(table['truth inside interval'].sum())} of {len(CHANNELS)} channels")

    # total ad credit vs truth: the national model gave ads ~31% too little
    truth_full = json.load(open(ROOT / "data" / "ground_truth" / "ground_truth.json"))["channels"]
    true_total = sum(truth_full[c]["true_iroas"] * truth_full[c]["total_spend"] for c in CHANNELS)
    est_total = sum(summary.loc[c, "estimate"] * truth_full[c]["total_spend"] for c in CHANNELS)
    print(f"Total revenue credited to ads: ${est_total / 1e6:.1f}M vs a true ${true_total / 1e6:.1f}M "
          f"({est_total / true_total - 1:+.0%}; national MMM: -31%)")

    # ---- save and track
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS_DIR / "iroas_vs_truth.csv")
    with open(RESULTS_DIR / "run_info.json", "w") as f:
        json.dump({"rows": len(X), "regions": int(X["geo"].nunique()), "chains": chains, "draws": draws,
                   "tune": tune, "target_accept": 0.95, "divergences": divergences, "quick": args.quick,
                   "ad_credit_vs_truth": est_total / true_total - 1}, f, indent=2)
    mmm.save(str(MODEL_DIR / "regional_mmm.nc"))
    metrics["divergences"] = divergences
    metrics["ad_credit_error"] = est_total / true_total - 1
    run_id = log_run(
        run_name="Bayesian MMM (regional)" + (" quick" if args.quick else ""),
        params={"method": "PyMC-Marketing MMM", "level": "regional (10 geos)", "pooling": "shared channel params",
                "scaling": "per region", "adstock": "geometric, l_max=14", "saturation": "logistic",
                "chains": chains, "draws": draws, "tune": tune, "target_accept": 0.95, "rows": len(X)},
        estimate=summary["estimate"],
        metrics=metrics,
        scorecard=table,
    )
    print(f"\nSaved results to {RESULTS_DIR}; logged to MLflow (run {run_id[:8]})")


if __name__ == "__main__":
    main()
