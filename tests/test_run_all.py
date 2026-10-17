"""run_all.py tells GitHub which test file failed, so anyone can read it on the run's own page.

The first CI run after the quote-check fix (2026-10-16) was red on two Windows jobs, and what
GitHub showed without a sign-in was only "Process completed with exit code 1". run_all.py had
printed the failing test's last lines into the job's log, and a log can be read only by someone
who is signed in. A line that starts with ::error makes GitHub attach an annotation to the run,
and an annotation shows on the run's page and in its API for everyone. So when GITHUB_ACTIONS is
set, run_all.py prints one for each failing test file: the file, a title, and the last lines of
its output as one line (GitHub wants % written %25, a carriage return %0D and a line feed %0A).
Off GitHub, and for a passing file, it prints nothing extra.

This runs a copy of run_all.py over a folder of invented test files, one that passes and one that
fails. No network, no YouTube request, no model call.

Watch it go red: stop run_all.py printing the annotation, print it when GITHUB_ACTIONS is not set,
print one for a passing file too, or leave the line feeds in the message, and run this file.
"""
import atexit
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = Path(__file__).resolve().parent
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


PASSING = 'print("all fine")\n'
FAILING = 'print("first line")\nprint("100% sure, second line")\nraise SystemExit(1)\n'


def run_runner(on_github, files):
    """Run a copy of run_all.py over a folder holding `files` (file name -> source). Returns
    (exit code, the runner's output lines)."""
    root = Path(tempfile.mkdtemp(prefix="ww-runall-"))
    atexit.register(shutil.rmtree, root, True)
    folder = root / "tests"
    folder.mkdir()
    shutil.copyfile(HERE / "run_all.py", folder / "run_all.py")
    for name, source in files.items():
        (folder / name).write_text(source, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}
    if on_github:
        env["GITHUB_ACTIONS"] = "true"
    proc = subprocess.run([sys.executable, str(folder / "run_all.py")], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    return proc.returncode, (proc.stdout or "").splitlines()


BOTH = {"test_aaa_passes.py": PASSING, "test_bbb_fails.py": FAILING}

print("=== A. on GitHub, a failing test file gets an annotation ===")
code, lines = run_runner(True, BOTH)
check(code == 1 and any(l.startswith("FAIL") and "test_bbb_fails.py" in l for l in lines)
      and any(l.startswith("PASS") and "test_aaa_passes.py" in l for l in lines),
      "CONTROL: the runner passed one file, failed the other, and exited 1")
notes = [l for l in lines if l.startswith("::")]
check(len(notes) == 1, f"exactly one workflow command line is printed ({len(notes)})")
note = notes[0] if notes else ""
head, _, message = note.partition("failed::")
check(head == "::error file=tests/test_bbb_fails.py,title=test_bbb_fails.py ",
      f"it is an error annotation for the failing file, titled with its name ({head!r})")
check("first line%0A" in message and "second line" in message,
      f"the failing test's last lines are in it as one line, joined with %0A ({message!r})")
check("100%25 sure" in message, "a percent sign in the output is written %25")
check("test_aaa_passes" not in note, "the file that passed has no annotation")

print("\n=== B. off GitHub, nothing extra is printed ===")
code, lines = run_runner(False, BOTH)
check(code == 1 and "FAIL" in "".join(lines), "CONTROL: it still fails the same file and exits 1")
check(not [l for l in lines if l.startswith("::")], "no workflow command line at all")

print("\n=== C. on GitHub, when every file passes, nothing extra is printed ===")
code, lines = run_runner(True, {"test_aaa_passes.py": PASSING})
check(code == 0 and "1 of 1 test files passed" in "\n".join(lines),
      "CONTROL: one file, passed, exit 0")
check(not [l for l in lines if l.startswith("::")], "no workflow command line")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
