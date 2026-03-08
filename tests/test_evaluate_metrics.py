import numpy as np

from pipeline.evaluate_metrics import ndcg_at_k, precision_at_k_binary, recall_at_k_binary


def test_recall_at_k_binary() -> None:
    y = np.array([1, 0, 1, 0, 1])
    s = np.array([0.9, 0.3, 0.8, 0.1, 0.2])
    r = recall_at_k_binary(y, s, 2)
    assert abs(r - (2 / 3)) < 1e-9


def test_precision_at_k_binary() -> None:
    y = np.array([1, 0, 1, 0, 1])
    s = np.array([0.9, 0.3, 0.8, 0.1, 0.2])
    p = precision_at_k_binary(y, s, 2)
    assert abs(p - 1.0) < 1e-9


def test_ndcg_at_k() -> None:
    rel = np.array([3.0, 2.0, 0.0, 1.0])
    scores = np.array([0.9, 0.8, 0.1, 0.4])
    v = ndcg_at_k(rel, scores, 3)
    assert 0.0 <= v <= 1.0
