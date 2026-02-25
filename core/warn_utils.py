from __future__ import annotations

import logging
import os

_warned: set[str] = set()


def _strict_mode() -> bool:
    return str(os.environ.get("QT_STRICT_WARNINGS", "")).strip() in ("1", "true", "TRUE", "yes", "YES", "on", "ON")


def log_once(key: str, msg: str, *, logger_name: str = "strategy", level: str = "WARNING") -> None:
    k = str(key)
    if k in _warned:
        return
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


def warn_once(key: str, msg: str, *, logger_name: str = "strategy") -> None:
    log_once(key, msg, logger_name=logger_name, level="WARNING")


def info_once(key: str, msg: str, *, logger_name: str = "strategy") -> None:
    log_once(key, msg, logger_name=logger_name, level="INFO")
