"""Record every model run in MLflow: its settings, its scores against the truth, and its scorecard.

Runs are stored locally in data/mlflow.db (data/ is not committed). To browse and compare them:
    mlflow ui --backend-store-uri sqlite:///data/mlflow.db --port 5001
then open http://127.0.0.1:5001
(Port 5000 also works, but on macOS it can clash with AirPlay Receiver, so use 127.0.0.1, not localhost.)
"""

from pathlib import Path

import pandas as pd

from measurement.data import CHANNELS, ROOT

TRACKING_URI = f"sqlite:///{ROOT / 'data' / 'mlflow.db'}"
ARTIFACT_ROOT = (ROOT / "data" / "mlartifacts").as_uri()
EXPERIMENT = "channel-iroas-vs-truth"


def _experiment_id(mlflow) -> str:
    mlflow.set_tracking_uri(TRACKING_URI)
    existing = mlflow.get_experiment_by_name(EXPERIMENT)
    if existing is not None:
        return existing.experiment_id
    return mlflow.create_experiment(EXPERIMENT, artifact_location=ARTIFACT_ROOT)


def log_run(
    run_name: str,
    params: dict,
    estimate: pd.Series,
    metrics: dict,
    scorecard: pd.DataFrame,
) -> str:
    """Log one model run. Returns the MLflow run id."""
    import mlflow

    experiment_id = _experiment_id(mlflow)
    with mlflow.start_run(experiment_id=experiment_id, run_name=run_name) as run:
        mlflow.log_params(params)
        mlflow.log_metrics({f"iroas_{ch}": float(estimate[ch]) for ch in CHANNELS})
        mlflow.log_metrics({k: float(v) for k, v in metrics.items()})
        out = Path(ROOT / "data" / "tmp_scorecard.csv")
        scorecard.to_csv(out)
        mlflow.log_artifact(str(out), artifact_path="scorecard")
        out.unlink()
        return run.info.run_id
