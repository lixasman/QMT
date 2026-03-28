from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_warned: set[str] = set()


def _strict_mode() -> bool:
    return str(os.environ.get("QT_STRICT_WARNINGS", "")).strip() in ("1", "true", "TRUE", "yes", "YES", "on", "ON")


def _alerts_enabled() -> bool:
    s = str(os.environ.get("QT_ALERTS_ENABLED", "1")).strip().lower()
    return s not in ("0", "false", "no", "off", "disable", "disabled")


def _alert_dir() -> Path:
    d = str(os.environ.get("QT_ALERT_DIR", "")).strip()
    return Path(d) if d else (Path("data") / "alerts")


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _record_alert_event(*, once_key: str, msg: str, logger_name: str, level: str) -> None:
    if not _alerts_enabled():
        return
    k = str(once_key)
    if k.startswith("degrade:"):
        typ = "DEGRADE"
        base_key = k[len("degrade:") :]
    elif k.startswith("alert:"):
        typ = "ALERT"
        base_key = k[len("alert:") :]
    else:
        return

    now = datetime.now()
    out = _alert_dir() / f"alerts_{now.strftime('%Y%m%d')}.jsonl"
    payload: dict[str, Any] = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "type": typ,
        "key": base_key,
        "once_key": k,
        "logger": str(logger_name),
        "level": str(level),
        "message": str(msg),
    }
    try:
        _append_jsonl(out, payload)
    except Exception as e:
        # Best-effort: never crash strategy execution because alert persistence failed.
        logging.getLogger(str(logger_name)).error("alert sink failed: %s", repr(e))


def log_once(key: str, msg: str, *, logger_name: str = "strategy", level: str = "WARNING") -> bool:
    k = str(key)
    if k in _warned:
        return False
    _warned.add(k)
    logger = logging.getLogger(str(logger_name))
    lv = str(level or "WARNING").upper()
    if _strict_mode() and lv in ("INFO", "DEBUG"):
        lv = "WARNING"
    if lv == "DEBUG":
        logger.debug("%s", str(msg))
    elif lv == "INFO":
        logger.info("%s", str(msg))
    elif lv == "ERROR":
        logger.error("%s", str(msg))
    else:
        logger.warning("%s", str(msg))

    _record_alert_event(once_key=k, msg=str(msg), logger_name=str(logger_name), level=lv)
    return True


def warn_once(key: str, msg: str, *, logger_name: str = "strategy") -> None:
    log_once(key, msg, logger_name=logger_name, level="WARNING")


def info_once(key: str, msg: str, *, logger_name: str = "strategy") -> None:
    log_once(key, msg, logger_name=logger_name, level="INFO")


def alert_once(key: str, msg: str, *, logger_name: str = "strategy") -> None:
    """
    Emit a one-time explicit alert. This is stronger than warn_once and should be used for
    data-quality or execution-path issues that can materially change strategy behavior.

    Side effects (best-effort):
    - logs at ERROR level
    - appends a JSONL record to `data/alerts/alerts_YYYYMMDD.jsonl` (configurable via env)
    """
    log_once(f"alert:{str(key)}", f"[ALERT] {str(msg)}", logger_name=logger_name, level="ERROR")


def degrade_once(key: str, msg: str, *, logger_name: str = "strategy") -> None:
    """
    Log a one-time explicit degradation alert.
    Use this for fallback/default behavior caused by missing data or exceptions.
    """
    log_once(f"degrade:{str(key)}", f"[DEGRADE] {str(msg)}", logger_name=logger_name, level="WARNING")
