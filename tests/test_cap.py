"""Proof that BACKLOG_MAX_AGE_DAYS=60 and the 120-item listing actually take effect."""
import io, sys, json, subprocess, datetime as dt
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
_tmpsandbox.sandbox(ww, "cap")

fails = []
def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: fails.append(msg)

today = dt.date.today()
DATES = {
    "10 days old":   (today - dt.timedelta(days=10)).strftime("%Y%m%d"),
    "59 days old":   (today - dt.timedelta(days=59)).strftime("%Y%m%d"),
    "61 days old":   (today - dt.timedelta(days=61)).strftime("%Y%m%d"),
    "200 days old":  (today - dt.timedelta(days=200)).strftime("%Y%m%d"),
    "Jan 2026":      "20260115",
    "Dec 2025":      "20251215",
}

asked_for = []
def fake_run(cmd, timeout=120):
    asked_for.append(int(cmd[cmd.index("--playlist-end") + 1]))
    out = "\n".join(json.dumps({
        "id": f"vid{i:08d}"[:11], "title": label, "duration": 900, "upload_date": d,
    }) for i, (label, d) in enumerate(DATES.items()))
    return subprocess.CompletedProcess(cmd, 0, out, "")

ww.run_yt_dlp = fake_run
ww.wait_for_request_slot = lambda: None
ww.note_request = lambda: None

print(f"settings in core.py: BACKLOG_MAX_AGE_DAYS={ww.BACKLOG_MAX_AGE_DAYS}, "
      f"BACKLOG_WINDOW_FETCH_SIZE={ww.BACKLOG_WINDOW_FETCH_SIZE}, "
      f"BACKFILL_FETCH_SIZE={ww.BACKFILL_FETCH_SIZE}\n")
check(ww.BACKLOG_MAX_AGE_DAYS == 60, "BACKLOG_MAX_AGE_DAYS is 60")
check(ww.BACKLOG_WINDOW_FETCH_SIZE == 120, "the capped listing size is 120")

videos, hit = ww.backlog_candidates("https://www.youtube.com/@x")
kept = [v["title"] for v in videos]
print(f"\n  of {len(DATES)} videos offered, the backlog window kept: {kept}")
check(set(kept) == {"10 days old", "59 days old"},
      "only videos inside the last 60 days enter the backlog")
check("61 days old" not in kept and "200 days old" not in kept,
      "anything past the cap is abandoned, not queued")
check("Dec 2025" not in kept, "MIN_BACKFILL_DATE still holds underneath the cap")

check(asked_for == [120],
      f"the listing asked YouTube for 120 items, not {ww.BACKFILL_FETCH_SIZE} "
      f"(asked for {asked_for})")

print("\n  -- and with the cap switched back off --")
asked_for.clear()
ww.BACKLOG_MAX_AGE_DAYS = None
videos, hit = ww.backlog_candidates("https://www.youtube.com/@x")
kept = [v["title"] for v in videos]
print(f"  kept: {kept}")
check("Jan 2026" in kept and "200 days old" in kept, "None restores the full 2026 catch-up")
check("Dec 2025" not in kept, "and still refuses anything before 2026")
check(asked_for == [ww.BACKFILL_FETCH_SIZE],
      f"an uncapped backlog goes back to a {ww.BACKFILL_FETCH_SIZE}-item listing")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
