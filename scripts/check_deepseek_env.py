import os
import sys


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    keys = [
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "DEEPSEEK_TIMEOUT",
        "PHASE2_MAX_WORKERS",
    ]

    for k in keys:
        v = os.environ.get(k)
        if v is None or v == "":
            print(f"{k}=<MISSING>")
            continue
        if k == "DEEPSEEK_API_KEY":
            print(f"{k}=<SET len={len(v)}>")
        else:
            print(f"{k}={v}")

    try:
        from finintel.deepseek_client import DeepSeekClient, DeepSeekError
        from newsget.http import HttpConfig, build_session

        s = build_session(HttpConfig())
        DeepSeekClient.from_env(s)
        print("DeepSeekClient.from_env=OK")
    except Exception as e:
        print(f"DeepSeekClient.from_env=FAIL {repr(e)}")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
