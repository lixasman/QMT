from __future__ import annotations

import argparse
import logging
from pathlib import Path

from strategy_config import parse_strategy_config
from strategy_runner import StrategyRunner


def _setup_logging() -> None:
    Path("data/logs").mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler("data/logs/strategy.log", encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def main() -> int:
    p = argparse.ArgumentParser(prog="main.py")
    p.add_argument("--mode", choices=["normal", "replay"], default="normal")
    ns, rest = p.parse_known_args()

    _setup_logging()
    log = logging.getLogger("strategy")

    if ns.mode != "normal":
        raise NotImplementedError("replay mode is not implemented yet")

    cfg = parse_strategy_config(rest)
    log.info(
        "startup | adapter=%s watchlist=%s watch_auto=%s watch_auto_no_filter=%s phase2_s_micro_missing=%s",
        cfg.trading_adapter_type,
        ",".join(cfg.watchlist_etf_codes),
        bool(getattr(cfg, "watch_auto", False)),
        bool(getattr(cfg, "watch_auto_no_filter", False)),
        ("" if getattr(cfg, "phase2_s_micro_missing", None) is None else str(getattr(cfg, "phase2_s_micro_missing"))),
    )
    if getattr(cfg, "phase2_s_micro_missing", None) is not None:
        log.warning(
            "TEST override enabled | phase2_s_micro_missing=%s (used only when micro factors are missing)",
            float(getattr(cfg, "phase2_s_micro_missing")),
        )
    runner = StrategyRunner(cfg)
    runner.run_day(wait_for_market=True)
    log.info("shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
