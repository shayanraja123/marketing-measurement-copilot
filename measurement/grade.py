"""Grade a model's channel estimates against the simulator's ground truth.

This is the ONLY place in the measurement code that reads the ground truth. Models produce
their estimates first; this module scores them afterwards.
"""

import json
from pathlib import Path

import pandas as pd

from measurement.data import CHANNELS, ROOT

TRUTH_PATH = ROOT / "data" / "ground_truth" / "ground_truth.json"


def load_truth(path: Path = TRUTH_PATH) -> pd.DataFrame:
    with open(path) as f:
        truth = json.load(f)
    rows = [
        {
            "channel": ch,
            "true_iroas": truth["channels"][ch]["true_iroas"],
            "platform_roas": truth["channels"][ch]["expected_platform_roas"],
        }
        for ch in CHANNELS
    ]
    return pd.DataFrame(rows).set_index("channel")


def grade(estimates: dict[str, pd.Series], truth: pd.DataFrame | None = None) -> pd.DataFrame:
    """Put each method's iROAS estimates next to the truth, with the error of each.

    estimates: {"method name": Series indexed by channel with estimated iROAS}
    """
    truth = load_truth() if truth is None else truth
    table = truth.copy()
    for name, est in estimates.items():
        table[name] = est.reindex(table.index)
        table[f"{name} error"] = (table[name] / table["true_iroas"] - 1).map(lambda x: f"{x:+.0%}")
    return table


def wrong_side_of_breakeven(estimate: pd.Series, truth: pd.DataFrame | None = None) -> list[str]:
    """Channels where the estimate and the truth disagree about whether the channel makes money."""
    truth = load_truth() if truth is None else truth
    est = estimate.reindex(truth.index)
    return [ch for ch in truth.index if (est[ch] >= 1) != (truth.loc[ch, "true_iroas"] >= 1)]
