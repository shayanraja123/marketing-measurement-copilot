"""CUPED on real data: Criteo's public ad-incrementality test (about 14 million users).

The test: 85% of users could see ads (treatment), 15% could not (control). For each user we know
whether they visited the advertiser's site and whether they converted, plus 12 anonymized features
measured BEFORE the test (f0 to f11).

Question: how much did the ads raise the visit rate, and how precisely can we tell?

Three estimates of the same lift:
  1. Plain A/B:  difference in visit rate, treatment minus control.
  2. CUPED:      first remove the part of each user's outcome that the pre-test features explain
                 (a linear regression on f0-f11), then compare. Because treatment was randomized,
                 the features are balanced across groups, so the lift estimate is unchanged on
                 average, but much of the noise is gone, so the confidence interval is narrower.
  3. CUPAC:      the same idea with a machine-learning prediction (gradient boosting) as the
                 covariate. It is cross-fitted: each user's prediction comes from a model trained on
                 the OTHER half of the users, so no user's own outcome leaks into their adjustment.

Confidence intervals come from the standard formula and from a bootstrap (the Poisson bootstrap:
each user gets a random weight with mean 1, which scales to millions of rows).

Honesty checks:
  * A/A test: split the CONTROL group into two random halves. No ads differ between them, so every
    method should find no effect.
  * The `exposure` column is deliberately NOT used as a covariate. It records whether the user
    actually saw an ad, which the treatment itself causes; adjusting for it would bias the lift.

Data: Criteo Uplift Prediction Dataset v2.1 (Diemert et al., 2018), CC BY-NC-SA 4.0.
Downloaded once from Hugging Face (about 300 MB) into data/criteo/, which is not committed.

Usage:
    python -m measurement.criteo_cuped                 # full dataset
    python -m measurement.criteo_cuped --sample 0.2    # a random 20% of users, faster
"""

import argparse
import json
import time
import urllib.request

import numpy as np
import pandas as pd

from measurement.data import ROOT

DATA_DIR = ROOT / "data" / "criteo"
CSV_PATH = DATA_DIR / "criteo-research-uplift-v2.1.csv.gz"
PARQUET_PATH = DATA_DIR / "criteo-uplift-v2.1.parquet"
# Criteo's own link (go.criteo.net) no longer resolves; Hugging Face hosts the same v2.1 file.
URL = "https://huggingface.co/datasets/criteo/criteo-uplift/resolve/main/criteo-research-uplift-v2.1.csv.gz"
DATASET_PAGE = "https://huggingface.co/datasets/criteo/criteo-uplift"
RESULTS_DIR = ROOT / "results" / "criteo_cuped"
FEATURES = [f"f{i}" for i in range(12)]
N_BOOT = 200
SEED = 42


# --------------------------------------------------------------------------- data


def load_criteo() -> pd.DataFrame:
    """Download once, convert to Parquet once, then load quickly from Parquet."""
    if not PARQUET_PATH.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not CSV_PATH.exists():
            print(f"Downloading the Criteo uplift dataset (about 300 MB) to {CSV_PATH} ...")
            partial = CSV_PATH.with_suffix(".part")
            try:
                request = urllib.request.Request(URL, headers={"User-Agent": "marketing-measurement-copilot"})
                with urllib.request.urlopen(request) as response, open(partial, "wb") as out:
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                partial.rename(CSV_PATH)
            except Exception as error:
                partial.unlink(missing_ok=True)
                raise SystemExit(
                    f"\nAutomatic download failed ({error}).\n"
                    f"Download it by hand instead:\n"
                    f"  1. Open {DATASET_PAGE} (sign in and accept the terms if asked)\n"
                    f"  2. Files and versions -> criteo-research-uplift-v2.1.csv.gz -> download\n"
                    f"  3. Move the file to {CSV_PATH}\n"
                    f"  4. Run this command again\n"
                )
        print("Converting to Parquet (one time, about a minute) ...")
        dtypes = {f: "float32" for f in FEATURES} | {c: "int8" for c in ["treatment", "conversion", "visit", "exposure"]}
        pd.read_csv(CSV_PATH, dtype=dtypes).to_parquet(PARQUET_PATH, index=False)
    return pd.read_parquet(PARQUET_PATH)


# --------------------------------------------------------------------------- estimators


def diff_in_means(y: np.ndarray, t: np.ndarray, w: np.ndarray | None = None) -> float:
    if w is None:
        return float(y[t == 1].mean() - y[t == 0].mean())
    wt, wc = w * (t == 1), w * (t == 0)
    return float((wt @ y) / wt.sum() - (wc @ y) / wc.sum())


def analytic_ci(y: np.ndarray, t: np.ndarray) -> tuple[float, float, float]:
    """Difference in means with a 95% normal-approximation interval."""
    yt, yc = y[t == 1], y[t == 0]
    est = yt.mean() - yc.mean()
    se = np.sqrt(yt.var(ddof=1) / len(yt) + yc.var(ddof=1) / len(yc))
    return float(est), float(est - 1.96 * se), float(est + 1.96 * se)


def poisson_bootstrap_ci(y: np.ndarray, t: np.ndarray, rng: np.random.Generator) -> tuple[float, float]:
    draws = [diff_in_means(y, t, rng.poisson(1.0, len(y)).astype(np.float32)) for _ in range(N_BOOT)]
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def cuped_adjust(y: np.ndarray, covariates: np.ndarray) -> np.ndarray:
    """CUPED: y minus the part explained by pre-test covariates (centered, so the mean is unchanged).

    theta comes from one regression on all users pooled. Because treatment is randomized, the
    covariates are unrelated to treatment, so this does not bias the treatment effect.
    """
    X = covariates - covariates.mean(axis=0)
    theta, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(X)), X]), y, rcond=None)
    return y - X @ theta[1:]


def cupac_covariate(df: pd.DataFrame, outcome: str, rng: np.random.Generator) -> np.ndarray:
    """Cross-fitted ML prediction of the outcome from pre-test features (CUPAC).

    Users are split into two halves; each half's predictions come from a model trained on the other
    half, so a user's own outcome never influences their own adjustment.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier

    fold = rng.integers(0, 2, len(df))
    pred = np.zeros(len(df), dtype=np.float64)
    X, y = df[FEATURES].to_numpy(), df[outcome].to_numpy()
    for k in (0, 1):
        model = HistGradientBoostingClassifier(max_iter=100, learning_rate=0.1, random_state=SEED)
        model.fit(X[fold != k], y[fold != k])
        pred[fold == k] = model.predict_proba(X[fold == k])[:, 1]
    return pred[:, None]


# --------------------------------------------------------------------------- randomization check


def balance_check(df: pd.DataFrame, rng: np.random.Generator) -> dict:
    """Did randomization work? Pre-test features should look the same in treatment and control.

    * Standardized mean difference (SMD) per feature: (treatment mean - control mean) / pooled SD.
      With millions of users, even tiny gaps are measurable; the standard error is printed for scale.
    * Classifier test: a cross-fitted model tries to predict who was treated from the features.
      AUC 0.5 = no better than a coin flip (balanced). Clearly above 0.5 = the groups differ.
    CUPED assumes balance. If it fails, CUPED stops being "noise reduction" and starts adjusting for
    differences between the groups, which is why a moving point estimate is a warning sign.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    t = df["treatment"].to_numpy()
    X = df[FEATURES].to_numpy(dtype=np.float64)
    xt, xc = X[t == 1], X[t == 0]
    pooled_sd = np.sqrt((xt.var(axis=0) + xc.var(axis=0)) / 2)
    smd = (xt.mean(axis=0) - xc.mean(axis=0)) / pooled_sd
    se = float(np.sqrt(1 / len(xt) + 1 / len(xc)))

    fold = rng.integers(0, 2, len(df))
    scores = np.zeros(len(df))
    for k in (0, 1):
        model = HistGradientBoostingClassifier(max_iter=100, random_state=SEED)
        model.fit(X[fold != k], t[fold != k])
        scores[fold == k] = model.predict_proba(X[fold == k])[:, 1]
    auc = float(roc_auc_score(t, scores))
    return {"smd": dict(zip(FEATURES, smd.round(4).tolist())), "max_abs_smd": float(np.abs(smd).max()),
            "smd_standard_error": se, "treatment_auc": auc}


# --------------------------------------------------------------------------- analysis


def analyze(df: pd.DataFrame, outcome: str, rng: np.random.Generator, with_cupac: bool = True) -> list[dict]:
    y = df[outcome].to_numpy(dtype=np.float64)
    t = df["treatment"].to_numpy()
    base_var = y.var()
    variants = {"plain A/B": y, "CUPED (linear, f0-f11)": cuped_adjust(y, df[FEATURES].to_numpy(dtype=np.float64))}
    if with_cupac:
        variants["CUPAC (cross-fitted gradient boosting)"] = cuped_adjust(y, cupac_covariate(df, outcome, rng))

    rows = []
    for name, adj in variants.items():
        est, lo, hi = analytic_ci(adj, t)
        b_lo, b_hi = poisson_bootstrap_ci(adj, t, rng)
        reduction = 1 - adj.var() / base_var
        rows.append({
            "outcome": outcome,
            "method": name,
            "lift_pp": est * 100,
            "relative_lift": est / y[t == 0].mean(),
            "ci95_pp": [lo * 100, hi * 100],
            "bootstrap_ci95_pp": [b_lo * 100, b_hi * 100],
            "ci_width_pp": (hi - lo) * 100,
            "variance_reduction": reduction,
            # same precision as the plain test would need this many times the users
            "equivalent_sample_multiplier": 1 / (1 - reduction) if reduction < 1 else float("inf"),
        })
    return rows


def aa_test(df: pd.DataFrame, outcome: str, rng: np.random.Generator) -> list[dict]:
    """Split the control group into two random halves and pretend one half got ads."""
    control = df[df["treatment"] == 0].copy()
    control["treatment"] = rng.integers(0, 2, len(control)).astype(np.int8)
    rows = []
    for row in analyze(control, outcome, rng, with_cupac=False):
        lo, hi = row["ci95_pp"]
        rows.append({"method": row["method"], "lift_pp": row["lift_pp"], "ci95_pp": [lo, hi],
                     "interval_contains_zero": lo <= 0 <= hi})
    return rows


def print_table(rows: list[dict]) -> None:
    for r in rows:
        lo, hi = r["ci95_pp"]
        blo, bhi = r["bootstrap_ci95_pp"]
        print(f"  {r['method']:<40} lift {r['lift_pp']:+.4f} pp ({r['relative_lift']:+.1%})   "
              f"95% CI {lo:+.4f} to {hi:+.4f}   bootstrap {blo:+.4f} to {bhi:+.4f}   "
              f"width {r['ci_width_pp']:.4f}   variance -{r['variance_reduction']:.1%}   "
              f"= {r['equivalent_sample_multiplier']:.2f}x the users")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=float, default=1.0, help="fraction of users to use (0-1)")
    args = parser.parse_args()
    rng = np.random.default_rng(SEED)

    started = time.time()
    df = load_criteo()
    if args.sample < 1:
        df = df.sample(frac=args.sample, random_state=SEED).reset_index(drop=True)
    n_t, n_c = int(df["treatment"].sum()), int((df["treatment"] == 0).sum())
    print(f"\n{len(df):,} users: {n_t:,} treatment, {n_c:,} control\n")

    results = {"users": len(df), "treatment": n_t, "control": n_c, "sample_fraction": args.sample}

    print("Randomization check (pre-test features should match across groups):")
    balance = balance_check(df, rng)
    worst = sorted(balance["smd"].items(), key=lambda kv: -abs(kv[1]))[:4]
    print(f"  largest feature differences (in SDs): " + ", ".join(f"{k} {v:+.3f}" for k, v in worst))
    print(f"  for scale, a difference of {2 * balance['smd_standard_error']:.3f} SD would already be statistically detectable")
    print(f"  can a model predict who was treated? AUC {balance['treatment_auc']:.3f} (0.500 = balanced)\n")
    results["balance_check"] = balance
    for outcome in ["visit", "conversion"]:
        rate = df.loc[df["treatment"] == 0, outcome].mean()
        print(f"{outcome.upper()} (control rate {rate:.3%}): effect of the ads")
        rows = analyze(df, outcome, rng)
        print_table(rows)
        results[outcome] = rows
        print()

    print("A/A check on visits (control split in two random halves; there should be no effect):")
    aa = aa_test(df, "visit", rng)
    for r in aa:
        lo, hi = r["ci95_pp"]
        print(f"  {r['method']:<40} lift {r['lift_pp']:+.4f} pp   95% CI {lo:+.4f} to {hi:+.4f}   "
              f"contains zero: {r['interval_contains_zero']}")
    results["aa_test_visit"] = aa

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_DIR / "cuped_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {RESULTS_DIR / 'cuped_results.json'}  ({time.time() - started:.0f} s)")


if __name__ == "__main__":
    main()
