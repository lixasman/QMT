from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def setup_backtest_logging(*, out_dir: str | Path, run_tag: str | None = None) -> dict[str, str]:
    out = Path(out_dir)
    logs_dir = out / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    tag = str(run_tag or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = logs_dir / f"backtest_run_{tag}.log"

    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    root.setLevel(logging.DEBUG)

    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt))

    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter(fmt))

    root.addHandler(fh)
    root.addHandler(sh)
    logging.captureWarnings(True)

    return {
        "run_tag": str(tag),
        "log_path": str(log_path),
    }

