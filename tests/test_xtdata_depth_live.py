import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.getenv("QMT_LIVE") != "1", reason="requires QMT_LIVE=1 and a running MiniQMT connection")
def test_xtdata_returns_depth_at_least_5():
    code = os.getenv("QMT_PROBE_CODE", "512480.SH")
    timeout = os.getenv("QMT_PROBE_TIMEOUT", "15")
    cmd = [sys.executable, "tools/xtdata_depth_probe.py", "--code", code, "--timeout", str(timeout)]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    if p.returncode != 0 and "TIMEOUT" in (p.stdout or ""):
        pytest.skip(p.stdout.strip() or "xtdata depth probe timeout")
    assert p.returncode == 0, f"exit={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
