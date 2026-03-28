from __future__ import annotations

import json
from pathlib import Path

import position.position_logger as position_logger


def test_append_jsonl_reuses_open_file_handle_for_same_path(tmp_path: Path, monkeypatch) -> None:
    position_logger._close_jsonl_writers()
    log_path = tmp_path / "position_decisions.jsonl"

    open_count = 0
    original_open = Path.open

    def _spy_open(self: Path, *args, **kwargs):
        nonlocal open_count
        if self == log_path and args and args[0] == "a":
            open_count += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _spy_open)

    position_logger.append_jsonl(log_path=str(log_path), payload={"type": "A", "x": 1})
    position_logger.append_jsonl(log_path=str(log_path), payload={"type": "B", "x": 2})
    position_logger._close_jsonl_writers()

    rows = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert open_count == 1
    assert rows == [{"type": "A", "x": 1}, {"type": "B", "x": 2}]
