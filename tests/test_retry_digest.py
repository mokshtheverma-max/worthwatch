"""FIX 1, 2026-09-22. A write-up produced on a retry is readable, in the digest of the day
the retry ran, and the day the video was first read stops saying something untrue.

What happened on 2026-09-22 at 09:02. The retry path wrote up one talk from the
transcript already on disk (Sonnet, 122.6 s) and main() threw away the rows and summaries
run_retry_phase returned, so the write-up reached neither digest. It exists in the
per-video file, in history.jsonl and in one INDEX.md line, and the digest of 2026-09-21
still says "NOT WRITTEN (read as YES; summary timed out after 600 s, attempt 1 of 3)".

So the retry day's digest carries a "Written on retry" section above that day's runs,
rebuilt from the records every time the file is written, and the first-read day's digest
is regenerated so its row shows the record as it is now.

Everything runs in a fresh folder under the OS temp folder, with the invented example
person's profile. The day the video was first read is a fixed date; the retry runs "today",
whatever today is. No YouTube request (spies raise), no model call (the CLI is stubbed).
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "retrydigest")
ww.load_context = lambda: "CONTEXT for the test."
ww.ensure_local_model = lambda *a, **k: None
ww.local_most_useful = lambda *a, **k: "nothing"
ww.ensure_one_sentence = lambda text, model: text

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


class Spy:
    """Anything that would be a YouTube request. Raises, loudly, by name."""

    def __init__(self, name):
        self.name = name
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise AssertionError(f"the retry path called {self.name}, which is a YouTube request")


spies = {}
for _name in ("run_yt_dlp", "extract_media_info", "download_audio_from_info",
              "fetch_transcript_by_plan", "transcribe_audio", "note_fetch"):
    spies[_name] = Spy(_name)
    setattr(ww, _name, spies[_name])


def reset():
    for pattern in ("*.json", "*.jsonl"):
        for f in ww.DATA_DIR.glob(pattern):
            f.unlink()
    ww.REQUEST_LOG_PATH.unlink(missing_ok=True)
    for d in (ww.RAW_DIR, ww.TRANSCRIPTS_DIR, ww.OUTPUT_DIR):
        if d.exists():
            for f in d.iterdir():
                f.unlink()


FIRST_DAY = "2026-09-21"
VID = "MaraVid0001"
URL = f"https://www.youtube.com/watch?v={VID}"
TITLE = "Small Shop Talk: Beyond Spreadsheets"
TRANSCRIPT = ("Welcome everyone to the small shop session. Today we are going to talk about "
              "beyond spreadsheets, how a two-person shop onboards bakeries onto a bookkeeping "
              "tool. " + "We send the welcome checklist on the first day. " * 300).strip()
# Section 6 comes to nothing, as it did in the case this file is for, so the retry demotes
# the YES to MAYBE.
WRITEUP_LINE = "A recorded talk with a live demo of a bakery's bank export read into a ledger."
WRITEUP = ("## 1. What it is\n\n" + WRITEUP_LINE + "\n\n"
           "## 6. What applies to me\n\nnothing\n\n"
           "## 7. What contradicts what I believe\n\nnothing in this video touches the "
           "beliefs list\n\n## 8. Try this week\n\nnothing\n")


def first_day_digest():
    """The first-read day's digest as the tool wrote it that day: blocks from that day's
    records and one run section, which a regeneration must keep exactly as it is."""
    records = [r for r in ww.load_records().values() if r.get("day") == FIRST_DAY]
    text = ("<!-- HEADER-START -->\n# Digest " + FIRST_DAY + "\n\nthe header of that day\n"
            "<!-- HEADER-END -->\n\n"
            "<!-- TABLE-START -->\n" + "\n".join(ww.day_table_lines(records))
            + "\n<!-- TABLE-END -->\n\n"
            "## Run at 10:27 (topics)\n\nNew videos: 1 - YES 1, OPPORTUNITY 0, MAYBE 0, NO 0\n"
            "\n\n---\n\n"
            "<!-- FOOTER-START -->\n" + "\n".join(ww.first_guess_block(records))
            + "\n<!-- FOOTER-END -->\n")
    ww.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = ww.OUTPUT_DIR / f"DIGEST-{FIRST_DAY}.md"
    path.write_text(text, encoding="utf-8")
    return path


def the_failed_record():
    """The talk as it stood on the evening it was first read: fetched, read as YES, and the
    summary killed at 600 s."""
    (ww.RAW_DIR / f"{VID}.txt").write_text(TRANSCRIPT, encoding="utf-8")
    ww.update_record(VID, url=URL, title=TITLE, channel="Small Shop Talks", source="topics",
                     listing_verdict="MAYBE", listing_confidence=10,
                     listing_reason="Description missing.", read_verdict="YES",
                     read_confidence=95, read_reason="Mara can copy the onboarding checklist.",
                     summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout",
                     state_reason="summary timed out after 600 s", attempts=1,
                     minutes=51.7, day=FIRST_DAY, read_day=FIRST_DAY,
                     created=f"{FIRST_DAY}T10:00:04")


# ==========================================================================================
print("=== A. the retry's write-up is in the digest of the day the retry ran ===")
reset()
the_failed_record()
first_path = first_day_digest()
check("NOT WRITTEN (read as YES; summary timed out after 600 s" in first_path.read_text(
    encoding="utf-8"), "CONTROL: the first-read day's digest starts with the failure in it")

calls = {"n": 0}


def stub_claude(prompt, timeout=600):
    calls["n"] += 1
    return WRITEUP


ww.claude_generate = stub_claude
rows, summaries = ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
today = ww.today_key()
record = ww.load_records()[VID]
check(calls["n"] == 1 and record["summary_state"] == "WRITTEN",
      "the retry wrote it up, from the transcript on disk")
for name, spy in spies.items():
    check(spy.calls == 0, f"and {name} was not called: no YouTube request")

today_path = ww.OUTPUT_DIR / f"DIGEST-{today}.md"
text = today_path.read_text(encoding="utf-8") if today_path.exists() else ""
check(bool(text), f"the retry itself wrote today's digest ({today_path.name})")
check(text.count("## Written on retry") == 1, "with one 'Written on retry' section")
check(WRITEUP_LINE in text, "and the write-up itself is in it, readable")
heading = next((l for l in text.splitlines() if l.startswith("#### [") and TITLE in l), "")
check(heading.startswith("#### [MAYBE] "),
      f"under a heading with the verdict it came to: {heading[:70]!r}")
check("claude:sonnet" in heading, f"and the backend that wrote it: {heading[-60:]!r}")
check(f"first read on {FIRST_DAY}" in text,
      "and a note that says which day it was first read, where its old row is")

print("\n  it sits above the day's runs, and a later run neither loses it nor stacks it")
ww.write_digest([{"verdict": "MAYBE", "channel": "c", "title": "Another video",
                  "reason": "r", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
                  "id": "bbbbbbbbbbb"}], [], [], label="channels")
ww.write_digest([], [], [], label="topics")
text = today_path.read_text(encoding="utf-8")
check(text.count("## Written on retry") == 1, "two more writes, still one section")
check(WRITEUP_LINE in text, "and the write-up is still there after both")
at_retry, at_run = text.find("## Written on retry"), text.find("## Run at")
check(0 < at_retry < at_run, "above the first '## Run at', after the day's table")
check(0 < text.find("<!-- TABLE-END -->") < at_retry,
      "and below the table, whose row says what the video came to")
row = next((l for l in text.splitlines() if l.startswith("| ") and TITLE in l), "")
check(row.startswith("| MAYBE"), f"today's row: {row[:60]!r}")

# ==========================================================================================
print("\n=== B. the first-read day's digest no longer says something untrue ===")
first = first_path.read_text(encoding="utf-8")
check("NOT WRITTEN" not in first and "timed out after 600 s, attempt 1 of 3" not in first,
      "the NOT WRITTEN row is gone from the day it was first read")
row = next((l for l in first.splitlines() if l.startswith("| ") and TITLE in l), "")
check(row.startswith("| MAYBE"),
      f"and that day still has its row, showing the record as it is now: {row[:60]!r}")
check("## Run at 10:27 (topics)" in first,
      "the run section that day wrote is kept as it was")
check("0 read but not written" in first,
      "and that day's count no longer carries it as not written")
# 2026-09-25, decision A: the fixture above is a digest as written before the footer went,
# and regenerating it removes the footer rather than keeping or stacking it.
check(first.count("<!-- TABLE-START -->") == 1 and first.count("<!-- FOOTER-START -->") == 0,
      "one table, regenerated rather than stacked, and the old footer gone")

print("\n  the record says when each thing happened")
check(record.get("read_day") == FIRST_DAY, f"read_day {record.get('read_day')}")
check(record.get("written_day") == today, f"written_day {record.get('written_day')}")
check(record.get("written_attempt") == 2, f"written_attempt {record.get('written_attempt')}")
check(isinstance(record.get("summary_seconds"), (int, float)),
      f"summary_seconds {record.get('summary_seconds')}")

# ==========================================================================================
print("\n=== C. CONTROL: a write-up on the first attempt adds no retry section ===")
reset()
ww.update_record("ccccccccccc", url="https://www.youtube.com/watch?v=ccccccccccc",
                 title="Written first time", channel="c", listing_verdict="MAYBE",
                 read_verdict="MAYBE", summary_state="WRITTEN", reason_code="written",
                 applies="nothing", written_day=ww.today_key(), written_attempt=1)
path = ww.write_digest([], [], [], label="channels")
text = path.read_text(encoding="utf-8") if path else ""
check(text and "## Written on retry" not in text,
      "a video written on its first attempt is in the table and nowhere else")
refresh = getattr(ww, "refresh_digest", None)
check(refresh is not None, "there is one function that regenerates any day's digest")
if refresh is not None:
    refresh(FIRST_DAY)
    check(not (ww.OUTPUT_DIR / f"DIGEST-{FIRST_DAY}.md").exists(),
          "and it does not create a past day's digest that was never written")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
