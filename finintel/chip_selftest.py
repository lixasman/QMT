from __future__ import annotations

import argparse
import os

import requests

from finintel.etf_signal_pipeline import run_etf_signal_pipeline


class _DummyDeepSeek:
    def chat(self, *, system: str, user: str, temperature: float = 0.2, force_json: bool = False) -> str:
        raise RuntimeError("DeepSeek 已在自测模式跳过，不应调用到这里")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--etf", required=True)
    parser.add_argument("--holdings", action="store_true")
    args = parser.parse_args()

    os.environ["FININTEL_CHIP_SELFTEST"] = "1"
    session = requests.Session()
    dummy = _DummyDeepSeek()

    r = run_etf_signal_pipeline(
        session,
        dummy,
        etf_code=str(args.etf),
        debug=True,
        max_workers=None,
        etf_source="auto",
        yesterday_evaluation="无",
        fetch_news=False,
        fetch_holdings=bool(args.holdings),
        fetch_fundflow=False,
        timing=True,
    )
    print(r["prompt"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
