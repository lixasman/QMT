from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finintel.etf_signal_pipeline import run_etf_signal_pipeline


class DeepSeekStub:
    def chat(self, system: str, user: str, temperature: float = 0.2, force_json: bool = False) -> str:
        return 'SENTIMENT_JSON: {"sentiment_grade":"C","confidence":"LOW"}'


def main() -> int:
    os.environ.setdefault("FININTEL_SKIP_BREADTH", "1")
    os.environ.setdefault("FININTEL_SKIP_XTDATA_DOWNLOAD", "1")
    os.environ.setdefault("FININTEL_SKIP_XTDATA_MARKET", "1")

    r = run_etf_signal_pipeline(
        requests.Session(),
        DeepSeekStub(),
        etf_code="159107",
        fetch_news=False,
        fetch_holdings=False,
        fetch_fundflow=False,
        fetch_share_snapshot=False,
        timing=True,
    )
    print("ok", r.get("etf_code_norm"), r.get("sentiment_struct"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

