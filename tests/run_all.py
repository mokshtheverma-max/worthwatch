"""Run every test file, one at a time, and say which passed.

    python tests/run_all.py                    every tests/test_*.py
    python tests/run_all.py ideas yes          only the files whose name holds one of these words
    python tests/run_all.py --clock 2031-01-01 the same, with Python's clock moved to that day
    python tests/run_all.py --clock +365       ... or that many days after today

Each test file is a script: it prints a PASS or FAIL line per check and exits 1 when any check
failed. They run one after another, never in parallel, each in its own process with its own
temporary folder, and none of them touches the network (tests/_tmpsandbox.py refuses any
connection that leaves the machine).

--clock moves the date in every Python process the tests start (tests/clock/sitecustomize.py,
with time-machine from the dev extra), so a test whose fixture date will run out fails now,
not on the day it runs out. Before any test runs, a child process and its own child (one that
sets its own PYTHONPATH, as some suites do) must both see the new date, or nothing runs.
"""
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 900
CLOCK = TESTS / "clock"
PROBE = ("import datetime, os, subprocess, sys\n"
         "print(datetime.date.today().isoformat())\n"
         "inner = 'import datetime; print(datetime.date.today().isoformat())'\n"
         "print(subprocess.run([sys.executable, '-c', inner], env=dict(os.environ, PYTHONPATH=''),\n"
         "                     capture_output=True, text=True).stdout.strip())\n")


def moved_clock(env, when):
    """The environment that moves Python's clock to `when` (YYYY-MM-DD, or +N days from today),
    and that day; or an error message when the clock did not move."""
    try:
        day = (dt.date.today() + dt.timedelta(days=int(when[1:])) if when.startswith("+")
               else dt.date.fromisoformat(when))
    except ValueError:
        return None, None, f"--clock takes a day (YYYY-MM-DD) or +N days, not {when!r}"
    paths = [str(CLOCK)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env = dict(env, PYTHONPATH=os.pathsep.join(paths), WORTHWATCH_TEST_DATE=day.isoformat())
    proc = subprocess.run([sys.executable, "-c", PROBE], env=env, capture_output=True, text=True,
                          timeout=120)
    seen = proc.stdout.split()
    if seen != [day.isoformat()] * 2:
        return None, None, (f"the clock did not move to {day}: a test process saw {seen[:1]}, its "
                            f"child {seen[1:2]}. Is time-machine installed (pip install -e "
                            f"\".[dev]\")? {proc.stderr.strip()[-300:]}")
    return env, day, None


def main(argv):
    args, when = argv[1:], None
    if "--clock" in args:
        at = args.index("--clock")
        when = args[at + 1] if at + 1 < len(args) else ""
        del args[at:at + 2]
    words = [a.lower() for a in args]
    files = sorted(p for p in TESTS.glob("test_*.py")
                   if not words or any(w in p.stem.lower() for w in words))
    if not files:
        print("no test file matched")
        return 2
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    clock_note = ""
    if when is not None:
        env, day, error = moved_clock(env, when)
        if error:
            print(error)
            return 2
        clock_note = f" with Python's clock moved to {day}"
        print(f"Python's clock is moved to {day} in every test process.\n")
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
          + clock_note + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
