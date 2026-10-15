"""Run every test file, one at a time, and say which passed.

    python tests/run_all.py            every tests/test_*.py
    python tests/run_all.py ideas yes  only the files whose name holds one of these words

Each test file is a script: it prints a PASS or FAIL line per check and exits 1 when any check
failed. They run one after another, never in parallel, each in its own process with its own
temporary folder, and none of them touches the network (tests/_tmpsandbox.py refuses any
connection that leaves the machine).
"""
import os
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 900


def main(argv):
    words = [a.lower() for a in argv[1:]]
    files = sorted(p for p in TESTS.glob("test_*.py")
                   if not words or any(w in p.stem.lower() for w in words))
    if not files:
        print("no test file matched")
        return 2
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    failed, started = [], time.monotonic()
    for path in files:
        t0 = time.monotonic()
        try:
            proc = subprocess.run([sys.executable, str(path)], cwd=TESTS.parent, env=env,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=TIMEOUT_SECONDS)
            code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as e:
            code, out = "timeout", f"{e.stdout or ''}\n(timed out after {TIMEOUT_SECONDS} s)"
        seconds = time.monotonic() - t0
        ok = code == 0
        print(f"{'PASS' if ok else 'FAIL'}  {path.name:<28} {seconds:6.1f}s")
        if not ok:
            failed.append(path.name)
            tail = [l for l in out.splitlines() if l.strip()][-25:]
            print("\n".join("      " + l for l in tail))
    total = time.monotonic() - started
    print(f"\n{len(files) - len(failed)} of {len(files)} test files passed in {total:.0f}s"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
