"""A stranger's first commands on a fresh machine: nothing crashes, and each says what to do.

A fresh machine has no history, no transcripts, no records and no canary videos. Every command a
new user is told to run first has to work there and say something useful, because a crash on
the first command is where most people stop.

No YouTube request and no model call; everything runs in a temp folder on the invented example
person, with every state file absent.

Watch it go red: make pick_canary_video call min() on the pool without checking it is empty, in a
copy of src/worthwatch/core.py, and run this file against the copy.
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "fresh-machine")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def run(fn, *args):
    """(return value, what it printed, the exception it raised or None)."""
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    raised, value = None, None
    try:
        value = fn(*args)
    except Exception as e:  # noqa: BLE001 - which exception is part of what is checked
        raised = e
    finally:
        sys.stdout = real
    return value, buf.getvalue(), raised


print("=== A. nothing on disk yet ===")
for name in ("HISTORY_PATH", "RECORDS_PATH", "SEEN_PATH", "RATE_STATE_PATH"):
    check(not Path(getattr(ww, name)).exists(), f"{name} does not exist yet")
check(not list(ww.RAW_DIR.glob("*.txt")) if ww.RAW_DIR.exists() else True,
      "and there are no transcripts")
check(ww.CANARY_SEED_VIDEO_IDS == [], "and no canary video is configured")

print("\n=== B. `worthwatch status` on a fresh machine ===")
check(ww.canary_pool() == [], "the canary pool is empty")
check(ww.pick_canary_video({}, []) is None, "so there is no next canary video, and that is None, "
                                            "not a crash")
_value, said, raised = run(ww.cmd_status)
check(raised is None, f"status runs to the end ({raised!r})")
check("Canary rotation" in said and "none yet" in said.split("Canary rotation", 1)[-1][:160],
      f"and says there is no canary video yet: "
      f"{said.split('Canary rotation', 1)[-1][:120].strip()!r}")
check(ww.pick_canary_video({}, ["vidaaaaaaa1", "vidbbbbbbb2"]) == "vidaaaaaaa1",
      "CONTROL: with videos in the pool, the first never-canaried one is next")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
