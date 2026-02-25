from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import PortfolioState


class StateManager:
    def __init__(self, state_path: str | Path) -> None:
        self._path = Path(state_path)
        self._tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> PortfolioState:
        if self._tmp_path.exists() and not self._path.exists():
            raise RuntimeError(f"state restore failed: tmp exists but state missing: {self._tmp_path}")
        if not self._path.exists():
            return PortfolioState()
        data: dict[str, Any]
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"state load failed: {self._path}") from e
        if not isinstance(data, dict):
            raise RuntimeError(f"state invalid: root is not object: {self._path}")
        return PortfolioState.from_dict(data)

    def save(self, state: PortfolioState) -> None:
        payload = state.to_dict()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            self._tmp_path.write_text(body, encoding="utf-8")
            os.replace(str(self._tmp_path), str(self._path))
        except Exception as e:
            raise RuntimeError(f"state save failed: {self._path}") from e

