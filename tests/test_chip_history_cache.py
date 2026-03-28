from __future__ import annotations

import os
import time
from pathlib import Path

from integrations.chip_history import ChipDPCHistory


def test_get_5d_reuses_cached_history_until_file_changes(tmp_path: Path, monkeypatch) -> None:
    hist = ChipDPCHistory(history_dir=tmp_path)
    p = tmp_path / "dpc_512480_SH.json"
    p.write_text(
        """
[
  {"trade_date":"20260301","dpc_peak_density":0.1},
  {"trade_date":"20260302","dpc_peak_density":0.2},
  {"trade_date":"20260303","dpc_peak_density":0.3},
  {"trade_date":"20260304","dpc_peak_density":0.4},
  {"trade_date":"20260305","dpc_peak_density":0.5}
]
        """.strip(),
        encoding="utf-8",
    )

    read_count = 0
    original_read_text = Path.read_text

    def _spy_read_text(self: Path, *args, **kwargs):
        nonlocal read_count
        if self == p:
            read_count += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _spy_read_text)

    first = hist.get_5d("512480.SH")
    second = hist.get_5d("512480.SH")

    assert first == [0.1, 0.2, 0.3, 0.4, 0.5]
    assert second == first
    assert read_count == 1

    before_mtime_ns = p.stat().st_mtime_ns
    p.write_text(
        """
[
  {"trade_date":"20260302","dpc_peak_density":0.2},
  {"trade_date":"20260303","dpc_peak_density":0.3},
  {"trade_date":"20260304","dpc_peak_density":0.4},
  {"trade_date":"20260305","dpc_peak_density":0.5},
  {"trade_date":"20260306","dpc_peak_density":0.6}
]
        """.strip(),
        encoding="utf-8",
    )
    for _ in range(20):
        os.utime(p, None)
        if p.stat().st_mtime_ns != before_mtime_ns:
            break
        time.sleep(0.01)

    third = hist.get_5d("512480.SH")
    assert third == [0.2, 0.3, 0.4, 0.5, 0.6]
    assert read_count == 2
