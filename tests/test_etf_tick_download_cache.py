from __future__ import annotations

import json

import etf_chip_engine.data.xtdata_provider as xtp


def _read_state(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_ensure_tick_data_downloaded_deduplicates_by_day(tmp_path, monkeypatch) -> None:
    calls: list[tuple[list[str], str]] = []

    def fake_download(codes: list[str], trade_date: str) -> None:
        calls.append((list(codes), str(trade_date)))

    monkeypatch.setattr(xtp, "download_tick_data", fake_download)
    trade_date = "20260224"
    codes = ["510300.SH", "510050.SH"]

    s1 = xtp.ensure_tick_data_downloaded(codes, trade_date, state_dir=tmp_path, chunk_size=1)
    s2 = xtp.ensure_tick_data_downloaded(codes, trade_date, state_dir=tmp_path, chunk_size=1)

    assert len(calls) == 2
    assert s1["pending_count"] == 2
    assert s2["pending_count"] == 0
    assert s2["skipped_by_cache"] == 2

    state = _read_state(tmp_path / f"tick_{trade_date}.json")
    assert set(state["downloaded_codes"]) == {"510300.SH", "510050.SH"}


def test_ensure_tick_data_downloaded_only_downloads_new_codes(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_download(codes: list[str], trade_date: str) -> None:
        calls.append(list(codes))

    monkeypatch.setattr(xtp, "download_tick_data", fake_download)
    trade_date = "20260224"

    xtp.ensure_tick_data_downloaded(["510300.SH"], trade_date, state_dir=tmp_path)
    xtp.ensure_tick_data_downloaded(["510300.SH", "510500.SH"], trade_date, state_dir=tmp_path)

    assert calls[0] == ["510300.SH"]
    assert calls[1] == ["510500.SH"]


def test_ensure_tick_data_downloaded_force_redownload(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_download(codes: list[str], trade_date: str) -> None:
        calls.append(list(codes))

    monkeypatch.setattr(xtp, "download_tick_data", fake_download)
    trade_date = "20260224"
    codes = ["510300.SH", "510050.SH"]

    xtp.ensure_tick_data_downloaded(codes, trade_date, state_dir=tmp_path)
    xtp.ensure_tick_data_downloaded(codes, trade_date, state_dir=tmp_path, force=True)

    assert calls == [codes, codes]


def test_retry_download_for_empty_tick_code_once_per_day(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_download(codes: list[str], trade_date: str) -> None:
        calls.append(list(codes))

    monkeypatch.setattr(xtp, "download_tick_data", fake_download)
    trade_date = "20260224"

    first = xtp.retry_download_for_empty_tick_code_once("510300.SH", trade_date, state_dir=tmp_path, timeout_sec=0)
    second = xtp.retry_download_for_empty_tick_code_once("510300.SH", trade_date, state_dir=tmp_path, timeout_sec=0)

    assert first is True
    assert second is False
    assert calls == [["510300.SH"]]

    state = _read_state(tmp_path / f"tick_{trade_date}.json")
    assert "510300.SH" in state["empty_retry_codes"]


def test_retry_download_for_empty_tick_code_once_timeout_warns_and_marks_retry(tmp_path, monkeypatch, capsys) -> None:
    trade_date = "20260224"
    calls: list[list[str]] = []

    def fake_timeout(codes: list[str], td: str, *, timeout_sec: int):
        assert td == trade_date
        assert timeout_sec == 5
        return [], list(codes)

    def fake_download(codes: list[str], td: str) -> None:
        calls.append(list(codes))

    monkeypatch.setattr(xtp, "_download_tick_data_with_timeout", fake_timeout)
    monkeypatch.setattr(xtp, "download_tick_data", fake_download)

    first = xtp.retry_download_for_empty_tick_code_once("510300.SH", trade_date, state_dir=tmp_path, timeout_sec=5)
    second = xtp.retry_download_for_empty_tick_code_once("510300.SH", trade_date, state_dir=tmp_path, timeout_sec=5)

    assert first is True
    assert second is False
    assert calls == []

    state = _read_state(tmp_path / f"tick_{trade_date}.json")
    assert "510300.SH" in state["empty_retry_codes"]
    assert "510300.SH" not in state["downloaded_codes"]

    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "empty-tick retry timeout/failed" in out


def test_ensure_tick_data_downloaded_handles_corrupted_state(tmp_path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_download(codes: list[str], trade_date: str) -> None:
        calls.append(list(codes))

    monkeypatch.setattr(xtp, "download_tick_data", fake_download)
    trade_date = "20260224"
    state_path = tmp_path / f"tick_{trade_date}.json"
    state_path.write_text("{invalid json", encoding="utf-8")

    stats = xtp.ensure_tick_data_downloaded(["510300.SH"], trade_date, state_dir=tmp_path)
    assert stats["pending_count"] == 1
    assert calls == [["510300.SH"]]


def test_ensure_tick_data_downloaded_timeout_mode_partial_success(tmp_path, monkeypatch) -> None:
    trade_date = "20260224"

    def fake_timeout_download(codes: list[str], td: str, *, timeout_sec: int):
        assert td == trade_date
        assert timeout_sec == 7
        return [codes[0]], codes[1:]

    monkeypatch.setattr(xtp, "_download_tick_data_with_timeout", fake_timeout_download)

    stats = xtp.ensure_tick_data_downloaded(
        ["510300.SH", "510050.SH"],
        trade_date,
        state_dir=tmp_path,
        timeout_sec=7,
    )
    assert stats["pending_count"] == 2
    assert stats["downloaded_now"] == 1
    assert stats["failed_count"] == 1

    state = _read_state(tmp_path / f"tick_{trade_date}.json")
    assert state["downloaded_codes"] == ["510300.SH"]
