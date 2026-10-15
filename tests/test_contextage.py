"""2026-09-26 night, brief part 6: stale context shows up.

Every digest's first line under its title says how old the context file is, in plain words,
and says it loudly when that is more than seven days, because every verdict in the digest is
judged against the context file and a stale one judges against projects that have moved on.

Counted from the file's last change to the digest's day. Everything is written inside a fresh
folder under the OS temp folder, the context file included; no model, no request.
"""
import io
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "contextage")
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


ww.CONTEXT_PATH.write_text("# Context\n", encoding="utf-8")
DAY = "2026-09-26"
ww.update_record("ctxvideo001", url="https://www.youtube.com/watch?v=ctxvideo001", title="A video",
                 channel="C", summary_state="WAITING", reason_code="rolled", state_reason="rolled",
                 day=DAY)


def aged(days):
    when = ww.dt.datetime(2026, 9, 26, 12, 0) - ww.dt.timedelta(days=days)
    os.utime(ww.CONTEXT_PATH, (when.timestamp(), when.timestamp()))
    ww.refresh_digest(DAY, create=True)
    text = (ww.OUTPUT_DIR / f"DIGEST-{DAY}.md").read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()]
    title = lines.index(f"# Digest {DAY}") if f"# Digest {DAY}" in lines else -1
    return text, (lines[title + 1] if 0 <= title < len(lines) - 1 else "")


print("=== the first line under the title says how old the context file is ===")
for days, words, loud in ((0, "today", False), (1, "1 day ago", False),
                          (7, "7 days ago", False), (8, "8 days ago", True),
                          (30, "30 days ago", True)):
    text, first = aged(days)
    check(first.lstrip("*").startswith(f"Context file last updated {words}"),
          f"{days} day(s): {first}")
    check(("more than a week" in first and first.startswith("**")) == loud,
          f"{days} day(s): {'flagged' if loud else 'not flagged'}")
text, first = aged(3)
text, first = aged(3)
check(text.count("Context file last updated") == 1, "a digest written twice has the line once")
check("Context file last updated" in text
      and text.index("Context file last updated") < text.index("<!-- HEADER-END -->"),
      "and it is part of the header, regenerated with it on every write")
ww.CONTEXT_PATH.unlink()
ww.refresh_digest(DAY)
text = (ww.OUTPUT_DIR / f"DIGEST-{DAY}.md").read_text(encoding="utf-8")
check("The context file is missing" in text,
      "and a context file that is gone is said, not skipped")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
