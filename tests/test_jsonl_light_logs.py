from __future__ import annotations

from pathlib import Path

from entry import entry_logger
from exit import exit_logger
import position.position_logger as position_logger
from t0 import t0_logger


def test_blank_jsonl_log_paths_are_noops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    position_logger._close_jsonl_writers()

    entry_logger._append_jsonl(Path(""), {"type": "ENTRY"})
    exit_logger._append_jsonl(Path(""), {"type": "EXIT"})
    t0_logger._append_jsonl(log_path="", obj={"type": "T0"})
    position_logger.append_jsonl(log_path="", payload={"type": "POSITION"})

    position_logger._close_jsonl_writers()
    assert list(tmp_path.iterdir()) == []
