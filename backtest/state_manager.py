from __future__ import annotations

from pathlib import Path
from typing import Any

from core.models import PortfolioState


class InMemoryStateManager:
    def __init__(self, initial_state: PortfolioState | None = None) -> None:
        st = PortfolioState() if initial_state is None else initial_state
        self._payload: dict[str, Any] = st.to_dict()
        self._path = Path("output/backtest/memory_state.json")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> PortfolioState:
        return PortfolioState.from_dict(self._payload)

    def save(self, state: PortfolioState) -> None:
        self._payload = state.to_dict()
