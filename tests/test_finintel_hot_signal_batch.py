from __future__ import annotations

from pathlib import Path
import json
import io

import pandas as pd

import finintel.etf_selector as selector
from strategy_config import StrategyConfig


def test_load_universe_etf_codes_reads_non_empty_lines(tmp_path: Path) -> None:
    path = tmp_path / "default_universe_50.txt"
    path.write_text("512480.SH\n\n159107.SZ\n", encoding="utf-8")

    out = selector.load_universe_etf_codes(path)

    assert out == ["512480.SH", "159107.SZ"]


def test_select_universe_daily_gainers_filters_strictly_above_threshold(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "default_universe_50.txt"
    path.write_text("512480.SH\n159107.SZ\n159998.SZ\n", encoding="utf-8")

    snap = pd.DataFrame(
        {
            "code": ["512480.SH", "159107.SZ", "159998.SZ"],
            "name": ["A", "B", "C"],
            "close": [1.02, 1.01, 0.99],
            "prev_close": [1.00, 1.00, 1.00],
        }
    )
    monkeypatch.setattr(selector, "load_latest_daily_snapshot", lambda codes: snap)

    out = selector.select_universe_daily_gainers(universe_path=path, gain_threshold=0.01)

    assert out["code"].tolist() == ["512480.SH"]
    assert out["source_tag"].tolist() == ["universe_up_gt_1pct"]


def test_select_universe_daily_gainers_include_all_returns_all_and_tags(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "default_universe_50.txt"
    path.write_text("512480.SH\n159107.SZ\n159998.SZ\n", encoding="utf-8")

    snap = pd.DataFrame(
        {
            "code": ["512480.SH", "159107.SZ", "159998.SZ"],
            "name": ["A", "B", "C"],
            "close": [1.02, 1.01, 0.99],
            "prev_close": [1.00, 1.00, 1.00],
        }
    )
    monkeypatch.setattr(selector, "load_latest_daily_snapshot", lambda codes: snap)

    out = selector.select_universe_daily_gainers(universe_path=path, gain_threshold=0.01, include_all=True)

    assert out["code"].tolist() == ["512480.SH", "159107.SZ", "159998.SZ"]
    assert out["source_tag"].tolist() == ["universe_all_50", "universe_all_50", "universe_all_50"]


def test_append_hot_etf_sentiment_history_dedupes(tmp_path: Path, monkeypatch) -> None:
    import finintel.main as finintel_main

    monkeypatch.chdir(tmp_path)
    day = "20260318"
    rows = [
        {"code": "512480.SH", "name": "A", "grade": "B", "confidence": "HIGH"},
        {"code": "159107.SZ", "name": "B", "grade": "C", "confidence": "LOW"},
    ]

    finintel_main._append_hot_etf_sentiment_history(rows, day)

    rows2 = [
        {"code": "512480.SH", "name": "A", "grade": "A", "confidence": "HIGH"},
        {"code": "159107.SZ", "name": "B", "grade": "C", "confidence": "LOW"},
    ]
    finintel_main._append_hot_etf_sentiment_history(rows2, day)

    out_path = tmp_path / "output" / "finintel_50ETF_sentiment_history" / "finintel_sentiment_history.csv"
    assert out_path.exists()
    df = pd.read_csv(out_path, dtype=str)
    assert len(df) == 2
    latest = dict(zip(df["code"], df["grade"]))
    assert latest["512480.SH"] == "A"


def test_load_latest_daily_snapshot_skips_codes_with_insufficient_history(monkeypatch) -> None:
    part = pd.DataFrame(
        {
            "code": ["512480.SH", "512480.SH", "159107.SZ"],
            "time": ["20260306", "20260307", "20260307"],
            "close": [1.0, 1.03, 2.0],
        }
    )
    monkeypatch.setattr(selector, "fetch_daily_history_for_codes", lambda codes, history_days=2: part)

    out = selector.load_latest_daily_snapshot(["512480.SH", "159107.SZ"])

    assert out["code"].tolist() == ["512480.SH"]
    assert out.iloc[0]["prev_close"] == 1.0
    assert out.iloc[0]["close"] == 1.03


def test_merge_signal_candidate_pools_deduplicates_and_merges_source_tags() -> None:
    from finintel.main import merge_signal_candidate_pools

    hot = pd.DataFrame(
        {
            "code": ["512480.SH", "159107.SZ"],
            "name": ["A", "B"],
            "score": [0.9, 0.8],
            "source_tag": ["hot", "hot"],
        }
    )
    gainers = pd.DataFrame(
        {
            "code": ["159107.SZ", "159998.SZ"],
            "name": ["B", "C"],
            "source_tag": ["universe_up_gt_1pct", "universe_up_gt_1pct"],
        }
    )

    out = merge_signal_candidate_pools(hot, gainers)

    assert out["code"].tolist() == ["512480.SH", "159107.SZ", "159998.SZ"]
    assert out["source_tag"].tolist() == ["hot", "hot+universe_up_gt_1pct", "universe_up_gt_1pct"]


def test_strategy_config_default_hot_top_is_10() -> None:
    assert StrategyConfig().hot_top == 10


def test_cleanup_old_signal_outputs_only_removes_matching_old_files(tmp_path: Path) -> None:
    from finintel.main import cleanup_old_signal_outputs

    old_json = tmp_path / "finintel_signal_512480_20260301.json"
    old_eval = tmp_path / "eval" / "finintel_signal_eval_512480_20260301.md"
    keep_json = tmp_path / "finintel_signal_512480_20260307.json"
    keep_other = tmp_path / "other_module_20260301.json"
    old_eval.parent.mkdir(parents=True)
    old_json.write_text("x", encoding="utf-8")
    old_eval.write_text("x", encoding="utf-8")
    keep_json.write_text("x", encoding="utf-8")
    keep_other.write_text("x", encoding="utf-8")

    summary = cleanup_old_signal_outputs(output_dir=tmp_path, today_yyyymmdd="20260308", retention_days=3)

    assert summary["deleted"] == 2
    assert not old_json.exists()
    assert not old_eval.exists()
    assert keep_json.exists()
    assert keep_other.exists()


def test_signal_hot_top_batch_uses_union_of_hot_and_universe_gainers(monkeypatch, tmp_path: Path, capsys) -> None:
    import finintel.main as finintel_main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(finintel_main, "build_session", lambda cfg: object())
    monkeypatch.setattr(finintel_main.DeepSeekClient, "from_env", lambda session: object())
    monkeypatch.setattr(finintel_main, "_today_yyyymmdd", lambda: "20260308")
    monkeypatch.setattr(
        finintel_main,
        "select_top_hot_etfs",
        lambda top_n: pd.DataFrame(
            {
                "code": ["512480.SH", "159107.SZ"],
                "name": ["A", "B"],
                "score": [0.9, 0.8],
            }
        ),
    )
    monkeypatch.setattr(
        finintel_main,
        "_diversify_hot_pool",
        lambda top_df_raw, target_n, max_per_theme: (
            top_df_raw,
            {
                "raw_candidates": len(top_df_raw),
                "selected": len(top_df_raw),
                "max_per_theme": max_per_theme,
                "unique_themes": 2,
                "raw_theme_top": [],
                "selected_theme_top": [],
            },
        ),
    )
    monkeypatch.setattr(finintel_main, "_load_hot_top_must_include_holdings", lambda: ({}, "none"))
    monkeypatch.setattr(finintel_main, "_inject_holdings_into_hot_pool", lambda top_df, holdings: (top_df, []))
    monkeypatch.setattr(
        finintel_main,
        "select_universe_daily_gainers",
        lambda universe_path, gain_threshold=0.01, include_all=False: pd.DataFrame(
            {
                "code": ["159107.SZ", "159998.SZ"],
                "name": ["B", "C"],
                "source_tag": ["universe_up_gt_1pct", "universe_up_gt_1pct"],
            }
        ),
    )
    monkeypatch.setattr(finintel_main, "_load_latest_yesterday_eval", lambda etf_code_norm: "无")
    monkeypatch.setattr(
        finintel_main,
        "run_etf_signal_pipeline",
        lambda *args, **kwargs: {"deepseek_output": "ok", "sentiment_struct": {"sentiment_grade": "B", "confidence": "HIGH"}},
    )
    monkeypatch.setattr(finintel_main, "_write_signal_json_and_optional_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(finintel_main, "_write_signal_human_files", lambda *args, **kwargs: None)
    monkeypatch.setattr(finintel_main, "cleanup_old_signal_outputs", lambda **kwargs: {"deleted": 1, "failed": 0})

    rc = finintel_main.main(["--signal-hot-top", "10", "--no-trace"])

    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["final_selected_count"] == 3
    assert [item["code"] for item in out["selected"]] == ["512480.SH", "159107.SZ", "159998.SZ"]
    assert [item["source_tag"] for item in out["selected"]] == ["hot", "hot+universe_up_gt_1pct", "universe_up_gt_1pct"]
    summary_csv = tmp_path / "output" / "finintel_signal_hot_20260308.csv"
    assert summary_csv.exists()


def test_signal_hot_top_batch_all_50_passes_include_all(monkeypatch, tmp_path: Path, capsys) -> None:
    import finintel.main as finintel_main

    called: dict[str, object] = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(finintel_main, "build_session", lambda cfg: object())
    monkeypatch.setattr(finintel_main.DeepSeekClient, "from_env", lambda session: object())
    monkeypatch.setattr(finintel_main, "_today_yyyymmdd", lambda: "20260308")
    monkeypatch.setattr(
        finintel_main,
        "select_top_hot_etfs",
        lambda top_n: pd.DataFrame(
            {
                "code": ["512480.SH", "159107.SZ"],
                "name": ["A", "B"],
                "score": [0.9, 0.8],
            }
        ),
    )
    monkeypatch.setattr(
        finintel_main,
        "_diversify_hot_pool",
        lambda top_df_raw, target_n, max_per_theme: (
            top_df_raw,
            {
                "raw_candidates": len(top_df_raw),
                "selected": len(top_df_raw),
                "max_per_theme": max_per_theme,
                "unique_themes": 2,
                "raw_theme_top": [],
                "selected_theme_top": [],
            },
        ),
    )
    monkeypatch.setattr(finintel_main, "_load_hot_top_must_include_holdings", lambda: ({}, "none"))
    monkeypatch.setattr(finintel_main, "_inject_holdings_into_hot_pool", lambda top_df, holdings: (top_df, []))

    def fake_select(universe_path, gain_threshold=0.01, include_all=False):
        called["include_all"] = include_all
        return pd.DataFrame(
            {
                "code": ["159107.SZ", "159998.SZ"],
                "name": ["B", "C"],
                "source_tag": ["universe_all_50", "universe_all_50"],
            }
        )

    monkeypatch.setattr(finintel_main, "select_universe_daily_gainers", fake_select)
    monkeypatch.setattr(finintel_main, "_load_latest_yesterday_eval", lambda etf_code_norm: "x")
    monkeypatch.setattr(
        finintel_main,
        "run_etf_signal_pipeline",
        lambda *args, **kwargs: {"deepseek_output": "ok", "sentiment_struct": {"sentiment_grade": "B", "confidence": "HIGH"}},
    )
    monkeypatch.setattr(finintel_main, "_write_signal_json_and_optional_trace", lambda *args, **kwargs: None)
    monkeypatch.setattr(finintel_main, "_write_signal_human_files", lambda *args, **kwargs: None)
    monkeypatch.setattr(finintel_main, "cleanup_old_signal_outputs", lambda **kwargs: {"deleted": 1, "failed": 0})

    rc = finintel_main.main(["--signal-hot-top", "10", "--signal-hot-all-50", "--no-trace"])

    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert called["include_all"] is True
    assert [item["source_tag"] for item in out["selected"]] == ["hot", "hot+universe_all_50", "universe_all_50"]


def test_emit_json_stdout_falls_back_to_ascii_when_stdout_cannot_encode(monkeypatch) -> None:
    import finintel.main as finintel_main

    class FakeStdout(io.StringIO):
        def write(self, s):
            if "•" in s:
                raise UnicodeEncodeError("gbk", s, 0, 1, "illegal multibyte sequence")
            return super().write(s)

    fake_stdout = FakeStdout()
    monkeypatch.setattr(finintel_main.sys, "stdout", fake_stdout)

    finintel_main._emit_json_stdout({"text": "• ok"})

    assert "\\u2022 ok" in fake_stdout.getvalue()
