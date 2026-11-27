"""The Bayesian MMM, calibrated with the brand's geo holdout experiment.

The plain MMM learns only from history, where budgets for every channel rise together in Q4, so it
can't fully separate ad effects from seasonality. The geo test is direct evidence from a real
intervention: paid social was switched off in 3 regions and sales fell. Calibration adds that
result to the model as a "lift test": the model must explain both the history AND the experiment.

What goes in (all per day of the test, in dollars, national totals):
  x        what the brand normally spent on paid social per day (before the cut)
  delta_x  the change in spend per day during the test (negative: spend was cut)
  delta_y  the change in revenue per day it caused (negative: sales fell)
  sigma    how uncertain delta_y is. We use the stricter PLACEBO-based spread (noise seen in 60
           periods with no test), not the narrower bootstrap, so a borderline experiment only nudges
           the model instead of overruling it.

The experiment is re-analysed from the warehouse here (measurement/geo_experiment.py); the
ground truth is read only afterwards, by the grading step.

Usage:
    python -m measurement.calibrated_mmm
"""

import json

import pandas as pd

from measurement.bayesian_mmm import RESULTS_DIR as PLAIN_RESULTS_DIR
from measurement.bayesian_mmm import build_inputs, build_model, iroas_posterior, summarize
from measurement.data import CHANNELS, ROOT, load_mmm_daily, national_daily
from measurement.geo_experiment import did_estimate, group_daily, load_experiment, placebo_tests, windows
from measurement.grade import grade, load_truth, summary_metrics, wrong_side_of_breakeven
from measurement.tracking import log_run

RESULTS_DIR = ROOT / "results" / "calibrated_mmm"
MODEL_DIR = ROOT / "data" / "models"


def lift_test_from_experiment(mart: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Turn the geo holdout result into PyMC-Marketing's lift-test format (per day of the test)."""
    exp = load_experiment()
    daily = group_daily(mart, exp)
    pre, window = windows(daily, exp.start, exp.end)
    est = did_estimate(pre, window)

    test = daily.loc[exp.start : exp.end]
    test_days = len(test)
    spend_ratio = pre["spend_treatment"].sum() / pre["spend_control"].sum()
    # national spend per day the brand had planned: control spend + what treatment would have spent
    x = test["spend_control"].sum() * (1 + spend_ratio) / test_days

    placebo_sd = placebo_tests(daily, exp)["lift_pct"].std()
    expected_treatment_revenue = est["revenue_lost"] / est["lift_pct"]
    sigma = placebo_sd * expected_treatment_revenue / test_days

    lift = pd.DataFrame(
        [
            {
                "channel": f"spend_{exp.channel}",
                "x": x,
                "delta_x": -est["spend_withheld"] / test_days,
                "delta_y": -est["revenue_lost"] / test_days,
                "sigma": sigma,
            }
        ]
    )
    info = {
        "experiment_id": exp.experiment_id,
        "test_days": test_days,
        "experiment_iroas": est["iroas"],
        "x_per_day": x,
        "delta_x_per_day": float(lift["delta_x"].iloc[0]),
        "delta_y_per_day": float(lift["delta_y"].iloc[0]),
        "sigma_per_day": sigma,
    }
    return lift, info


def main() -> None:
    mart = load_mmm_daily()
    national = national_daily(mart)
    X, y, controls = build_inputs(national)
    lift, info = lift_test_from_experiment(mart)

    print(f"Calibrating with {info['experiment_id']} (experiment iROAS {info['experiment_iroas']:.2f}):")
    print(f"  normal paid social spend:  ${info['x_per_day']:,.0f}/day")
    print(f"  spend change in the test:  ${info['delta_x_per_day']:,.0f}/day")
    print(f"  revenue change it caused:  ${info['delta_y_per_day']:,.0f}/day  (+/- ${info['sigma_per_day']:,.0f})\n")

    mmm = build_model(X, controls)
    mmm.build_model(X, y)
    mmm.add_lift_test_measurements(lift)
    chains, draws, tune = 4, 1000, 1000
    mmm.fit(X=X, y=y, chains=chains, draws=draws, tune=tune, target_accept=0.95, random_seed=42)

    # Make sure the experiment really is part of the fitted model (fit must not have rebuilt it without it)
    if not any("lift" in name for name in mmm.model.named_vars):
        raise RuntimeError(
            "The lift test is not in the fitted model: fit() rebuilt the model without it. "
            "Paste this message to get the calibration step adjusted for your PyMC-Marketing version."
        )

    divergences = int(mmm.idata["sample_stats"]["diverging"].sum().item())
    summary = summarize(iroas_posterior(mmm, X))

    # ---- grading (truth read only here)
    truth = load_truth()
    plain = pd.read_csv(PLAIN_RESULTS_DIR / "iroas_vs_truth.csv", index_col="channel")["MMM"]
    table = grade({"MMM": plain, "calibrated MMM": summary["estimate"]}, truth)
    table["calibrated 94% interval"] = [f"{lo:.2f} to {hi:.2f}" for lo, hi in zip(summary["low"], summary["high"])]
    table["truth inside interval"] = (truth["true_iroas"] >= summary["low"]) & (truth["true_iroas"] <= summary["high"])

    pd.set_option("display.width", 170)
    print(f"\nSampler divergences: {divergences} (0 is ideal)\n")
    print("iROAS (revenue per $1 of spend): truth vs the plain MMM vs the calibrated MMM\n")
    print(table.drop(columns=["platform_roas"]).round(2).to_string())

    plain_metrics = summary_metrics(plain, truth)
    metrics = summary_metrics(summary["estimate"], truth, summary["low"], summary["high"])
    wrong = wrong_side_of_breakeven(summary["estimate"], truth)
    print(f"\nChannels on the wrong side of break-even: {', '.join(wrong) if wrong else 'none'} "
          f"(plain MMM: {int(plain_metrics['channels_wrong_side'])})")
    print(f"Mean absolute error: {metrics['mean_abs_pct_error']:.0%} (plain MMM: {plain_metrics['mean_abs_pct_error']:.0%})")
    print(f"Truth inside the 94% interval: {int(table['truth inside interval'].sum())} of {len(CHANNELS)} channels")

    # ---- save and track
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(RESULTS_DIR / "iroas_vs_truth.csv")
    with open(RESULTS_DIR / "run_info.json", "w") as f:
        json.dump({**info, "chains": chains, "draws": draws, "tune": tune, "target_accept": 0.95,
                   "divergences": divergences}, f, indent=2, default=float)
    mmm.save(str(MODEL_DIR / "calibrated_mmm.nc"))
    metrics["divergences"] = divergences
    run_id = log_run(
        run_name="Bayesian MMM (national, calibrated with geo test)",
        params={"method": "PyMC-Marketing MMM", "level": "national", "calibration": info["experiment_id"],
                "lift_sigma": "placebo-based", "adstock": "geometric, l_max=14", "saturation": "logistic",
                "chains": chains, "draws": draws, "tune": tune, "target_accept": 0.95, "days": len(X)},
        estimate=summary["estimate"],
        metrics=metrics,
        scorecard=table,
    )
    print(f"\nSaved results to {RESULTS_DIR}; logged to MLflow (run {run_id[:8]})")


if __name__ == "__main__":
    main()
