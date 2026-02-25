from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print("python", sys.version.replace("\n", " "))
    print("DEEPSEEK_API_KEY", "set" if bool(os.environ.get("DEEPSEEK_API_KEY", "").strip()) else "missing")
    try:
        import xtquant  # type: ignore

        from xtquant import xtdata  # type: ignore

        _ = (xtquant, xtdata)
        print("xtquant", "ok")
    except Exception as e:
        print("xtquant", "fail", repr(e))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
