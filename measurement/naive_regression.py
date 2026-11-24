"""The obvious approach, and why it fails: regress daily revenue on same-day spend.

Two versions, both ordinary least squares on national daily data:
  1. naive:          revenue ~ spend per channel
  2. with controls:  revenue ~ spend per channel + trend + day of week + month + promo days

A channel's coefficient reads as "revenue per extra $1 of spend", i.e. an iROAS estimate.
Both versions assume an ad works only on the day it runs (no adstock) and that every dollar
works equally well (no saturation). Comparing them with the truth shows what those assumptions cost.

Usage:
    python -m measurement.naive_regression
"""

import numpy as np
import pandas as pd

from measurement.data import CHANNELS, SPEND_COLUMNS, load_mmm_daily, national_daily
from measurement.grade import grade, wrong_side_of_breakeven


def ols(y: np.ndarray, X: pd.DataFrame) -> pd.Series:
    coef, *_ = np.linalg.lstsq(X.to_numpy(dtype=float), y, rcond=None)
    return pd.Series(coef, index=X.columns)


def spend_matrix(national: pd.DataFrame) -> pd.DataFrame:
    X = national[SPEND_COLUMNS].copy()
    X.columns = CHANNELS
    X.insert(0, "intercept", 1.0)
    return X


def control_matrix(national: pd.DataFrame) -> pd.DataFrame:
    dates = national["date_day"]
    controls = pd.DataFrame(
        {"trend": np.arange(len(national)) / 365.0, "promo": national["is_promo_day"]},
        index=national.index,
    )
    dow = pd.get_dummies(dates.dt.day_of_week, prefix="dow", drop_first=True, dtype=float)
    month = pd.get_dummies(dates.dt.month, prefix="month", drop_first=True, dtype=float)
    return pd.concat([controls, dow, month], axis=1)


def estimate(national: pd.DataFrame) -> dict[str, pd.Series]:
    y = national["revenue"].to_numpy(dtype=float)
    X_naive = spend_matrix(national)
    X_controls = pd.concat([X_naive, control_matrix(national)], axis=1)
    return {
        "naive": ols(y, X_naive)[CHANNELS],
        "with controls": ols(y, X_controls)[CHANNELS],
    }


def main() -> None:
    national = national_daily(load_mmm_daily())
    estimates = estimate(national)
    table = grade(estimates)

    pd.set_option("display.width", 140)
    print(f"National daily data: {len(national)} days\n")
    print("iROAS (revenue per $1 of spend): truth vs platform claims vs regression estimates\n")
    print(table.round(2).to_string())
    for name, est in estimates.items():
        wrong = wrong_side_of_breakeven(est)
        verdict = ", ".join(wrong) if wrong else "none"
        print(f"\n{name}: channels on the wrong side of break-even (iROAS 1.0): {verdict}")


if __name__ == "__main__":
    main()
