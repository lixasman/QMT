from __future__ import annotations

import sys
from pathlib import Path

import pytest

root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))


@pytest.fixture(autouse=True)
def _isolate_alert_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Tests should not write alert artifacts into the repo working tree.
    monkeypatch.setenv("QT_ALERT_DIR", str(tmp_path / "alerts"))
