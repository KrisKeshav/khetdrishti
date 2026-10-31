"""One command before every demo / deploy:  python scripts/preflight.py [--full]

  1. unit + fault-injection tests (pytest)
  2. live smoke test of every data source and the LLM
  3. agent evaluation (quick: 4 key scenarios; --full: all of them)
  4. browser check of the running app (skipped if no server on :8000)

Prints a single GO / NO-GO verdict.
"""
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
QUICK_EVAL = ["irrigation-hi", "sell-mr", "dose-guardrail", "inject-price"]


def step(name, cmd, required=True):
    print(f"\n=== {name} ===", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    ok = r.returncode == 0
    print(f"--> {'OK' if ok else 'FAILED'}", flush=True)
    return name, ok, required


def server_up() -> bool:
    try:
        return httpx.get("http://127.0.0.1:8000/api/crops", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def main():
    full = "--full" in sys.argv
    results = [
        step("Unit & fault-injection tests", [PY, "-m", "pytest", "-q"]),
        step("Live data sources + LLM", [PY, "scripts/smoke_test.py"]),
        step("Agent evaluation", [PY, "scripts/eval_agent.py"] + ([] if full else QUICK_EVAL)),
    ]
    if server_up():
        results.append(step("Browser check", [PY, "scripts/ui_check.py"], required=False))
    else:
        print("\n=== Browser check === skipped (start the server: uvicorn app.main:app)")
    print("\n" + "=" * 50)
    for name, ok, req in results:
        print(f"{'OK  ' if ok else ('FAIL' if req else 'WARN')}  {name}")
    go = all(ok for _, ok, req in results if req)
    print("=" * 50 + f"\n{'GO — ready to demo' if go else 'NO-GO — fix the failures above first'}")
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
