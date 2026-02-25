from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from core.enums import FSMState

from position.correlation import pearson_corr_20d
from position.fsm_transitions import check_transition
from position.rebuild import assert_rebuild_allowed
from position.t0_mutex import ensure_hold_time_under


def test_assert_illegal_fsm_transition() -> None:
    with pytest.raises(AssertionError):
        check_transition(current_state=FSMState.S1_TRIAL, new_state=FSMState.S3_SCALED, trigger="ILLEGAL")


def test_assert_rebuild_limit() -> None:
    with pytest.raises(AssertionError):
        assert_rebuild_allowed(rebuild_count_this_wave=1)


def test_assert_mutex_hold_timeout() -> None:
    with pytest.raises(AssertionError):
        ensure_hold_time_under(hold_started_at=datetime.now() - timedelta(seconds=3), max_s=2.0)


def test_assert_correlation_input_length() -> None:
    with pytest.raises(AssertionError):
        pearson_corr_20d(x=[1.0] * 20, y=[1.0] * 20)
