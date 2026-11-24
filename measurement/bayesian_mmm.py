"""Bayesian media mix model (PyMC-Marketing) on national daily data, graded against the truth.

What the model assumes, and why each piece is there (all from the simulator walkthrough):
  * Adstock (geometric, up to 14 days): an ad keeps working for days after it runs.
  * Saturation (logistic): each extra dollar in a channel earns less than the one before.
  * Baseline: an intercept, a growth trend, day-of-week effects, yearly seasonality and the
    brand's promo days, so the model doesn't credit ads for sales that would have happened anyway.

Being Bayesian, every estimate comes with an uncertainty range: the model reports a 94% interval
for each channel's iROAS, and the grade checks whether the truth falls inside it.

The model reads only mart_mmm_daily. The ground truth is read afterwards by measurement/grade.py.

Usage:
    python -m measurement.bayesian_mmm            # full run: 4 chains x 1,000 draws (~10-20 min)
    python -m measurement.bayesian_mmm --quick    # quick check: 2 chains x 300 draws (~3-5 min)
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from measurement.data import CHANNELS, ROOT, SPEND_COLUMNS, load_mmm_daily, national_daily
from measurement.grade import grade, load_truth, summary_metrics, wrong_side_of_breakeven
from measurement.tracking import log_run

RESULTS_DIR = ROOT / "results" / "bayesian_mmm"
MODEL_DIR = ROOT / "data" / "models"  # large model files; data/ is not committed
HDI_PROB = 0.94


def build_inputs(national: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Model inputs: spend per channel plus baseline controls."""
    X = national[["date_day", *SPEND_COLUMNS]].copy()
    X["trend"] = np.arange(len(X)) / len(X)
    X["promo"] = national["is_promo_day"].astype(float)
    dow = pd.get_dummies(national["date_day"].dt.day_of_week, prefix="dow", drop_first=True, dtype=float)
    X = pd.concat([X, dow], axis=1)
    controls = ["trend", "promo", *dow.columns]
    # PyMC-Marketing names the target "y" by default, so the revenue series is renamed to match.
    y = national["revenue"].astype(float).rename("y")
    return X, y, controls


def build_model(X: pd.DataFrame, controls: list[str]):
    from pymc_extras.prior import Prior
    from pymc_marketing.mmm import MMM, GeometricAdstock, LogisticSaturation

    # Common practice: a channel's effect prior is scaled by its share of total spend, so the model
    # starts from "bigger budgets can have bigger effects" and lets the data move it from there.
    spend_share = X[SPEND_COLUMNS].sum() / X[SPEND_COLUMNS].sum().sum()
    import xarray as xr

    prior_sigma = xr.DataArray((len(CHANNELS) * spend_share).to_numpy(), dims=("channel",))

    model_config = {
        "intercept": Prior("Normal", mu=0.5, sigma=0.2),
        "saturation_beta": Prior("HalfNormal", sigma=prior_sigma, dims="channel"),
        "gamma_control": Prior("Normal", mu=0, sigma=0.1, dims="control"),
        "gamma_fourier": Prior("Laplace", mu=0, b=0.2, dims="fourier_mode"),
        "likelihood": Prior("Normal", sigma=Prior("HalfNormal", sigma=2)),
    }
    return MMM(
        model_config=model_config,
        date_column="date_day",
        channel_columns=SPEND_COLUMNS,
        control_columns=controls,
        adstock=GeometricAdstock(l_max=14),
        saturation=LogisticSaturation(),
        yearly_seasonality=2,
    )


def iroas_posterior(mmm, X: pd.DataFrame):
    """Posterior samples of iROAS per channel: incremental revenue / spend, over the whole period."""
    import xarray as xr

    roas = mmm.incrementality.contribution_over_spend(frequency="all_time")
    if not isinstance(roas, xr.DataArray):
        roas = roas.to_array() if hasattr(roas, "to_array") else xr.DataArray(roas)
    sample_dims = [d for d in roas.dims if d != "channel"]
    roas = roas.stack(sample=sample_dims)
    # channel labels are the spend column names; strip the prefix to match the truth table
    roas = roas.assign_coords(channel=[str(c).replace("spend_", "") for c in roas["channel"].values])
    return roas


def summarize(roas) -> pd.DataFrame:
    low_q, high_q = (1 - HDI_PROB) / 2, 1 - (1 - HDI_PROB) / 2
    return pd.DataFrame(
        {
            "estimate": roas.mean("sample").to_series(),
            "low": roas.quantile(low_q, dim="sample").to_series(),
            "high": roas.quantile(high_q, dim="sample").to_series(),
            "prob_profitable": (roas > 1).mean("sample").to_series(),
        }
    ).reindex(CHANNELS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="fewer samples, for a fast check")
    args = parser.parse_args()
    chains, draws, tune = (2, 300, 300) if args.quick else (4, 1000, 1000)

    national = national_daily(load_mmm_daily())
    X, y, controls = build_inputs(national)
    mmm = build_model(X, controls)

    print(f"Fitting on {len(X)} days, {len(CHANNELS)} channels: {chains} chains x {draws} draws ...")
    mmm.fit(X=X, y=y, chains=chains, draws=draws, tune=tune, target_accept=0.95, random_seed=42)

    divergences = int(mmm.idata["sample_stats"]["diverging"].sum().item())
    summary = summarize(iroas_posterior(mmm, X))

    # ---- grading (the first and only time the truth is read)
    truth = load_truth()
    table = grade({"MMM": summary["estimate"]}, truth)
    table["MMM 94% interval"] = [f"{lo:.2f} to {hi:.2f}" for lo, hi in zip(summary["low"], summary["high"])]
    table["truth inside interval"] = (truth["true_iroas"] >= summary["low"]) & (truth["true_iroas"] <= summary["high"])
    table["P(profitable)"] = summary["prob_profitable"].map(lambda p: f"{p:.0%}")

    pd.set_option("display.width", 160)
    print(f"\nSampler divergences: {divergences} (0 is ideal)\n")
    print("iROAS (revenue per $1 of spend): truth vs platform claims vs the Bayesian MMM\n")
    print(table.round(2).to_string())
    wrong = wrong_side_of_breakeven(summary["estimate"], truth)
    print(f"\nChannels on the wrong side of break-even: {', '.join(wrong) if wrong else 'none'}")
    print(f"Truth inside the 94% interval: {int(table['truth inside interval'].sum())} of {len(CHANNELS)} channels")

    # ---- save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS_DIR / "iroas_vs_truth.csv")
    with open(RESULTS_DIR / "run_info.json", "w") as f:
        json.dump({"chains": chains, "draws": draws, "tune": tune, "target_accept": 0.95,
                   "divergences": divergences,
                   "days": len(X), "quick": args.quick}, f, indent=2)
    mmm.save(str(MODEL_DIR / "bayesian_mmm.nc"))
    metrics = summary_metrics(summary["estimate"], truth, summary["low"], summary["high"])
    metrics["divergences"] = divergences
    run_id = log_run(
        run_name="Bayesian MMM (national)" + (" quick" if args.quick else ""),
        params={"method": "PyMC-Marketing MMM", "level": "national", "adstock": "geometric, l_max=14",
                "saturation": "logistic", "yearly_seasonality": 2, "chains": chains, "draws": draws,
                "tune": tune, "target_accept": 0.95, "days": len(X)},
        estimate=summary["estimate"],
        metrics=metrics,
        scorecard=table,
    )
    print(f"Logged to MLflow (run {run_id[:8]})")
    print(f"\nSaved results to {RESULTS_DIR} and the fitted model to {MODEL_DIR}")


if __name__ == "__main__":
    main()
