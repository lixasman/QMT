from __future__ import annotations

import logging
import os

from core.warn_utils import log_once as _log_once


class BacktestDegradeAbort(SystemExit):
    """Backtest-only fail-fast abort for non-ideal execution paths."""


_FAIL_FAST = str(os.getenv("BACKTEST_FAIL_FAST", "0")).strip().lower() in {"1", "true", "yes", "y", "on"}


def set_fail_fast(enabled: bool) -> None:
    global _FAIL_FAST
    _FAIL_FAST = bool(enabled)


def is_fail_fast() -> bool:
    return bool(_FAIL_FAST)


def _abort(*, key: str, msg: str, logger_name: str) -> None:
    logger = logging.getLogger(str(logger_name))
    reason = f"[BACKTEST_DEGRADE_ABORT] key={str(key)} msg={str(msg)}"
    logger.error("%s", reason)
    raise BacktestDegradeAbort(reason)


def warn_once(key: str, msg: str, *, logger_name: str = "backtest") -> None:
    _log_once(str(key), str(msg), logger_name=logger_name, level="WARNING")
    if _FAIL_FAST:
        _abort(key=str(key), msg=str(msg), logger_name=logger_name)


def degrade_once(key: str, msg: str, *, logger_name: str = "backtest") -> None:
    tagged = f"[DEGRADE] {str(msg)}"
    _log_once(f"degrade:{str(key)}", tagged, logger_name=logger_name, level="WARNING")
    if _FAIL_FAST:
        _abort(key=f"degrade:{str(key)}", msg=str(msg), logger_name=logger_name)
