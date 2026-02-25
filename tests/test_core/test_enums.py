from __future__ import annotations

from core.enums import DataQuality, FSMState


def test_enum_values() -> None:
    assert DataQuality.OK.value == "OK"
    assert FSMState.S0_IDLE.value == "S0"

