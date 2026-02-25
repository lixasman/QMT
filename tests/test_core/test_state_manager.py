from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from core.models import PortfolioState
from core.state_manager import StateManager


def test_state_manager_atomic_save_load() -> None:
    with TemporaryDirectory() as td:
        p = Path(td) / "state.json"
        sm = StateManager(p)
        st = PortfolioState(nav=123.0, cash=456.0)
        sm.save(st)
        assert p.exists()
        assert not p.with_suffix(p.suffix + ".tmp").exists()
        st2 = sm.load()
        assert st2.to_dict() == st.to_dict()


def test_state_manager_tmp_exists_without_state_raises() -> None:
    with TemporaryDirectory() as td:
        p = Path(td) / "state.json"
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text("{}", encoding="utf-8")
        sm = StateManager(p)
        with pytest.raises(RuntimeError):
            sm.load()

