"""`worthwatch doctor`: one read-only command that prints green or red for each thing a run
needs, and the exact thing to do wherever something is red. It never changes a setting itself.

Each check is shown green and red on stubbed inputs. Then the real probes are run against a spy
that records every command they start and answers with canned output: the only command is
`nvidia-smi --query-gpu`, and no file is written. No model is asked and nothing leaves the
machine; everything runs on the invented example person in a fresh folder under the OS temp
folder.

Watch it go red: make the disk line green whatever is free (ok = True) in a copy of
src/worthwatch/core.py and run this file against the copy.
"""
import importlib
import io
import os
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "doctor")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


NOW = ww.dt.datetime(2026, 3, 9, 8, 0, 0)


def by_name(checks):
    return {c[1]: c for c in checks}


def line(checks, name):
    ok, _name, detail, fix = by_name(checks)[name]
    return ok, detail, fix or ""


# The probes, each a function doctor_checks calls; stubbed here one at a time.
ALL_BRAINS = {k: (True, f"{k} is there") for k in ("agent", "claude", "codex", "api", "ollama")}
GOOD = {
    "doctor_ollama_models": lambda: [ww.DEFAULT_LOCAL_MODEL, "another-model:7b"],
    "doctor_gpu": lambda: {"name": "Example GPU", "total_mib": 16303, "free_mib": 14500},
    "doctor_whisper_ready": lambda: (True, "faster-whisper and the model are ready"),
    "disk_free_bytes": lambda path=None: 700 * 10 ** 9,
    "doctor_golden": lambda: {"yes_precision": 0.8, "yes_recall": 0.9, "projects_found": 3,
                              "projects_total": 3},
    "doctor_ytdlp": lambda: "2026.7.4",
    "ejs_status": lambda: (True, "yt-dlp-ejs 0.8.0"),
    "doctor_js_runtime": lambda: (True, "deno, at the scripts folder of this Python"),
    "doctor_brains": lambda: dict(ALL_BRAINS),
}
for name, fn in GOOD.items():
    setattr(ww, name, fn)
ww.AUDIO_PATH_ENABLED = True
ww.BRAIN = "claude"
ww.LABELS_PATH.write_text('{"videos": []}', encoding="utf-8")
# A context file of the reader's own, not the example person's.
ww.CONTEXT_PATH.write_text("# My context\n\nI make pottery and sell it online.\n", encoding="utf-8")
ww.CHANNELS_PATH.write_text("https://www.youtube.com/@example-pottery\n", encoding="utf-8")
t = (NOW - ww.dt.timedelta(days=1)).timestamp()
os.utime(ww.CONTEXT_PATH, (t, t))
ww.save_records({})
ww.save_seen({})
ww.save_rate_state({})

print("=== A. everything well: green, and exit 0 ===")
checks = ww.doctor_checks(now=NOW)
for ok, name, detail, fix in checks:
    print(f"    {'green' if ok else 'RED  '} {name}: {detail}")
check(all(c[0] for c in checks), "every line is green")
names = [c[1] for c in checks]
for needed in ("yt-dlp", "YouTube challenge solver", "JavaScript runtime", "brain", "folders",
               "Ollama", "transcriber", "disk", "context file", "channels",
               "score against your labels", "state files"):
    check(needed in names, f"there is a line for {needed}")
out = io.StringIO()
real, sys.stdout = sys.stdout, out
try:
    code = ww.cmd_doctor(now=NOW)
finally:
    sys.stdout = real
said = out.getvalue()
check(code == 0 and "Everything is ready" in said and "Nothing was changed" in said,
      f"exit 0, and it says everything is ready and that nothing was changed ({code})")
check("OK" in said and "RED" not in said and "green" not in said,
      "in plain words: OK, not colours")

print("\n=== B. each check goes red on its own, and says what to do ===")


def red_with(name, stub_name, stub, expect_in_fix, expect_in_detail=""):
    saved = getattr(ww, stub_name)
    setattr(ww, stub_name, stub)
    try:
        ok, detail, fix = line(ww.doctor_checks(now=NOW), name)
    finally:
        setattr(ww, stub_name, saved)
    check(not ok and expect_in_fix in fix and expect_in_detail in detail,
          f"{name} red: {detail[:70]} / to fix: {fix[:70]}")


def down():
    raise ww.OllamaDown("the connection was refused")


red_with("Ollama", "doctor_ollama_models", down, "ollama serve")
red_with("Ollama", "doctor_ollama_models", lambda: ["another-model:7b"],
         f"ollama pull {ww.DEFAULT_LOCAL_MODEL}")
red_with("transcriber", "doctor_gpu",
         lambda: {"name": "Example GPU", "total_mib": 16303, "free_mib": 1200},
         "nvidia-smi", "1.2 GB free")
red_with("transcriber", "doctor_whisper_ready",
         lambda: (False, "faster-whisper is not installed"), "worthwatch[whisper]")
red_with("disk", "disk_free_bytes", lambda path=None: 1 * 10 ** 9, "", "1.0 GB free")
red_with("yt-dlp", "doctor_ytdlp", lambda: None, "uv tool install --reinstall worthwatch",
         "not installed")
red_with("YouTube challenge solver", "ejs_status",
         lambda: (False, "yt-dlp-ejs is not installed"), "uv tool install --reinstall worthwatch")
red_with("JavaScript runtime", "doctor_js_runtime",
         lambda: (False, "deno was not found"), "js_runtime", "deno")

print("\n  the brain you chose")
red_with("brain", "doctor_brains",
         lambda: dict(ALL_BRAINS, claude=(False, "`claude` is not installed")),
         "worthwatch init --brain", "claude")
ww.BRAIN = "agent"
ok, detail, fix = line(ww.doctor_checks(now=NOW), "brain")
check(ok and "mcp" in detail.lower(), f"agent mode needs no model, and says how to connect ({detail})")
ww.BRAIN = "claude"

print("\n  the folders, the context file and the channels")
saved_config = ww.CONFIG_DIR
ww.CONFIG_DIR = ww.CONFIG_DIR.parent / "not-made-yet"
ok, detail, fix = line(ww.doctor_checks(now=NOW), "folders")
check(not ok and "worthwatch init" in fix, f"a config folder that is not there says run init ({detail})")
ww.CONFIG_DIR = saved_config
mine = ww.CONTEXT_PATH.read_text(encoding="utf-8")
ww.CONTEXT_PATH.write_text((ww._settings.EXAMPLES_DIR / "context.md").read_text(encoding="utf-8"),
                           encoding="utf-8")
os.utime(ww.CONTEXT_PATH, ((NOW - ww.dt.timedelta(days=1)).timestamp(),) * 2)
ok, detail, fix = line(ww.doctor_checks(now=NOW), "context file")
check(not ok and "example" in detail and str(ww.CONTEXT_PATH) in fix,
      f"a context file that is still the example person's is red, and says where yours goes ({detail})")
ww.CONTEXT_PATH.write_text(mine, encoding="utf-8")
os.utime(ww.CONTEXT_PATH, ((NOW - ww.dt.timedelta(days=1)).timestamp(),) * 2)
ww.CHANNELS_PATH.write_text("# one link a line\n", encoding="utf-8")
ww.TOPICS_PATH.write_text("# one search a line\n", encoding="utf-8")
ok, detail, fix = line(ww.doctor_checks(now=NOW), "channels")
check(not ok and str(ww.CHANNELS_PATH) in fix, f"no channel and no search is red ({detail})")
ww.TOPICS_PATH.write_text("bookkeeping for bakeries\n", encoding="utf-8")
ok, detail, fix = line(ww.doctor_checks(now=NOW), "channels")
check(ok and "1 search" in detail, f"CONTROL: one search is enough to run on ({detail})")
ww.CHANNELS_PATH.write_text("https://www.youtube.com/@example-pottery\n", encoding="utf-8")

saved_gpu = ww.doctor_gpu
ww.doctor_gpu = lambda: None
ok, detail, fix = line(ww.doctor_checks(now=NOW), "transcriber")
ww.doctor_gpu = saved_gpu
check(ok and "on the CPU" in detail,
      f"no GPU is not red: the transcriber runs on the CPU, and says so ({detail[:80]})")
ww.AUDIO_PATH_ENABLED = False
check("transcriber" not in [c[1] for c in ww.doctor_checks(now=NOW)],
      "CONTROL: with the audio path off there is no transcriber line at all")
ww.AUDIO_PATH_ENABLED = True

saved_floor = dict(ww.GOLDEN_FLOOR)
ww.GOLDEN_FLOOR.update({"yes_precision": 0.7, "yes_recall": 0.7})
red_with("score against your labels", "doctor_golden",
         lambda: {"yes_precision": 0.6, "yes_recall": 0.9, "projects_found": 3,
                  "projects_total": 3}, "worthwatch score --rules", "under the floor")
red_with("score against your labels", "doctor_golden",
         lambda: {"yes_precision": 0.8, "yes_recall": 0.9, "projects_found": 2,
                  "projects_total": 3}, "worthwatch score --rules", "2 of 3")
ok, detail, fix = line(ww.doctor_checks(now=NOW), "score against your labels")
check(ok, f"CONTROL: a score over the floor you set is green ({detail})")
ww.GOLDEN_FLOOR.clear()
ww.GOLDEN_FLOOR.update(saved_floor)
ww.LABELS_PATH.unlink()
check("score against your labels" not in [c[1] for c in ww.doctor_checks(now=NOW)],
      "CONTROL: with no labels file there is no score line, red or green")

t = (NOW - ww.dt.timedelta(days=9)).timestamp()
os.utime(ww.CONTEXT_PATH, (t, t))
ok, detail, fix = line(ww.doctor_checks(now=NOW), "context file")
check(not ok and "9 days ago" in detail, f"a context file older than a week is red: {detail}")
moved = ww.CONTEXT_PATH.with_name("context-moved-aside.md")
ww.CONTEXT_PATH.replace(moved)
ok, detail, fix = line(ww.doctor_checks(now=NOW), "context file")
check(not ok and "missing" in detail and "worthwatch init" in fix,
      f"a missing context file is red, and says to run init: {fix}")
moved.replace(ww.CONTEXT_PATH)
ww.RECORDS_PATH.write_text('{"torn": ', encoding="utf-8")
ok, detail, fix = line(ww.doctor_checks(now=NOW), "state files")
check(not ok and "records.json" in detail, f"a torn state file is red: {detail[:90]}")
ww.save_records({})

print("\n=== C. read only: the real probes start one query and nothing else ===")
started = []


def spy_run(cmd, *a, **k):
    started.append([str(c) for c in cmd])
    if "nvidia-smi" in str(cmd[0]).lower():
        return subprocess.CompletedProcess(cmd, 0, b"Example GPU, 16303, 14500\n", b"")
    raise AssertionError(f"doctor started a command it must not: {cmd}")


real_run = subprocess.run
ww = importlib.reload(ww)             # the real probes again, not the stubs above
real_root = _tmpsandbox.sandbox(ww, "doctor-real")
ww.save_records({})
# Ollama is asked over the local network (port 11434), and so is the brain finder's Ollama
# check; the score reads a labels file. None of them starts a command, so they are stubbed to
# keep this about commands. The transcriber is said to be ready so that the real GPU probe runs.
ww.doctor_ollama_models = GOOD["doctor_ollama_models"]
ww.doctor_brains = GOOD["doctor_brains"]
ww.doctor_golden = GOOD["doctor_golden"]
ww.doctor_whisper_ready = GOOD["doctor_whisper_ready"]
ww.AUDIO_PATH_ENABLED = True
before = {p: (p.stat().st_mtime, p.stat().st_size) for p in real_root.rglob("*") if p.is_file()}
subprocess.run = spy_run
try:
    checks = ww.doctor_checks(now=NOW)
finally:
    subprocess.run = real_run
after = {p: (p.stat().st_mtime, p.stat().st_size) for p in real_root.rglob("*") if p.is_file()}
check(started and all("nvidia-smi" in c[0].lower() and any("--query-gpu" in x for x in c)
                      for c in started),
      f"only nvidia-smi --query-gpu was started ({[' '.join(c[:2]) for c in started]})")
check(line(checks, "transcriber")[0], "and its answer was read: the transcriber line is green")
check(before == after, "and no file was written or touched")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
