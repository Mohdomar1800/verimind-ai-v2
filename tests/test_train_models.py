import numpy as np

from pipeline.train_models import TASK_CONFIG, choose_model, compute_metrics, recall_at_k


def test_recall_at_k() -> None:
    y_true = np.array([1, 0, 1, 0, 1])
    scores = np.array([0.9, 0.1, 0.8, 0.3, 0.2])
    r = recall_at_k(y_true, scores, 0.4)
    assert 0.0 <= r <= 1.0
    assert abs(r - (2 / 3)) < 1e-9


def test_choose_model_pipeline() -> None:
    name, model = choose_model(random_state=42)
    assert name in {"lightgbm", "random_forest"}
    assert hasattr(model, "fit")
    assert hasattr(model, "predict_proba")


def test_compute_metrics_bounds() -> None:
    y_true = np.array([0, 1, 1, 0, 1, 0])
    y_pred = np.array([0, 1, 0, 0, 1, 1])
    y_score = np.array([0.1, 0.8, 0.4, 0.2, 0.7, 0.6])
    m = compute_metrics(y_true, y_pred, y_score)
    for k in ["accuracy", "precision", "recall", "f1", "pr_auc", "roc_auc", "recall_at_10pct", "recall_at_20pct"]:
        assert 0.0 <= m[k] <= 1.0


def test_task_config_has_coverage() -> None:
    assert "coverage" in TASK_CONFIG
    assert TASK_CONFIG["coverage"]["label_col"] == "label_coverage_priority"
