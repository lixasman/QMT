import pytest

from exit.accel import compute_accel_k


def test_accel_k_no_profit():
    assert compute_accel_k(2.8, 0.0, 0.05, 0.2, 1.0) == pytest.approx(2.8)


def test_accel_k_steps():
    assert compute_accel_k(2.8, 0.10, 0.05, 0.2, 1.0) == pytest.approx(2.4)


def test_accel_k_floor():
    assert compute_accel_k(1.2, 1.0, 0.05, 0.2, 1.0) == pytest.approx(1.0)
