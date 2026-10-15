"""The 10:27 run of 2026-09-21, replayed through the new code.

This is the run that broke. Its log, in shape:

    [MAYBE 10] <a 51.7 minute conference talk> ...
    audio: 51.7 min of audio, 51.26 MB, transcribed in 127.0s
      read as YES (95) (listing said MAYBE): <the reader> can copy the workflow
      ERROR summarizing: claude CLI timed out after 600s and was killed.
    [MAYBE 20] <an 18.2 minute video> ...
      read as YES (95), summarized, then demoted: section 6 came to nothing
    [MAYBE 10] <a long interview>
      (no usable captions: audio: the smallest audio-bearing format is 67.0 MB, over the
       63 MB ceiling (AUDIO_MAX_BYTES))
    (this run's transcript cap reached; 7 YES/MAYBE candidate(s) wait for the next run)

and the digest then printed "YES (the listing said MAYBE) | 95" for the first of them,
led its header with it, and said nothing at all about the last eight.

The fixture used to be the run's own evidence: the owner's transcripts in raw/, the summary
history.jsonl stored, and the verdicts and confidences the log recorded. It is invented now,
with the same shape: the same three videos and seven that rolled, the same minutes and sizes,
the same verdicts and confidences, a stored write-up whose section 6 came to nothing, and a
transcript of the talk long enough that fix 2 can write it up for nothing. The stubbed CLI
returns that write-up for the video that was written and raises the timeout for the one that
was not. Nothing is fetched, nothing is asked of a model, and the real pipeline is never run: a
spy raises if anything reaches for YouTube.
"""
import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "replay")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


# --- the fixture, invented in the run's own shape -----------------------------------------
TIMED_OUT = "MaraTalk001"      # a 51.7 min conference talk: read, never written
WRITTEN = "MaraTalk002"        # an 18.2 min video
OVER_CEILING = "MaraTalk003"   # a long interview, 67.0 MB of audio

ROLLED = ["Bookkeeping For A Corner Bakery", "Newsletters: Is The Sponsor Money Real?",
          "How Flour Prices Hit Small Bakeries | A Primer",
          "A Bakery That Does Its Own Books: Two Owners Explain",
          "How Two Founders Use Spreadsheets To Run Three Cafes",
          "Emerging Trends In Small Bakery Software",
          "Running A Bakery's Back Office With One Laptop"]

# (verdict, reason, confidence), which is what triage_video returns, in that order.
LISTING = {
    TIMED_OUT: ("MAYBE", "Description missing prevents identifying any copyable mechanism "
                         "or numbers.", 10),
    WRITTEN: ("MAYBE", "Description lacks specific mechanism or numbers to justify YES or "
                       "OPPORTUNITY", 20),
    OVER_CEILING: ("MAYBE", "Description provides no mechanism or numbers to judge value.", 10),
}
READ = {
    TIMED_OUT: ("YES", "Mara can copy the month-end close to run Ledgerleaf's own books "
                       "[copy: Close every month with the same five checks, in the same order]",
                95),
    WRITTEN: ("YES", "The video teaches a mechanism Mara can apply to Ledgerleaf "
                     "[copy: Price every loaf from its ingredient cost each Monday]", 95),
}
TITLES = {
    TIMED_OUT: "A long talk on bakery bookkeeping: the whole year, month by month, with every number",
    WRITTEN: "How Small Bakeries Reprice When Flour Jumps",
    OVER_CEILING: "Bookkeeping from the bottom up, with a bakery owner",
}
CHANNELS = {TIMED_OUT: "Invented Talks", WRITTEN: "Small Shop Money",
            OVER_CEILING: "Long Conversations"}
MINUTES = {TIMED_OUT: 51.7, WRITTEN: 18.2}

# The write-up history.jsonl stored for the video that was written: the guard had already cut
# its one item, so its section 6 is "nothing".
RECORDED_SUMMARY = (
    "## 1. What it is\nAn 18-minute talk by a bakery owner on how she reprices her bread when "
    "ingredient costs jump, and the spreadsheet she uses to see it coming.\n\n"
    "## 2. What it claimed\n- Flour and butter costs moved by a third in one season, and most "
    "small bakeries noticed months late.\n- A weekly cost-per-loaf number is enough to decide "
    "when to reprice.\n\n"
    "## 3. What it showed versus said\nShown: her spreadsheet, one row per product and a cost "
    "column updated each Monday. Said, not shown: that regulars accept a rise when it is "
    "explained on a card at the till.\n\n"
    "## 4. The method\n1. Every Monday, update the cost of the five main ingredients from the "
    "last invoices.\n2. Recompute the cost of each loaf from its recipe.\n3. When the cost "
    "passes a third of the price, raise the price by a round amount and say why on a card.\n\n"
    "## 5. Worth keeping verbatim\nNone.\n\n"
    "## 6. What applies to me\n\n> **1 item(s) removed here: 1 quoted nothing the video said.**"
    "\n\nnothing\n\n"
    "## 7. What contradicts what I believe\n\nnothing in this video touches the beliefs list\n\n"
    "## 8. Try this week\n\nnothing\n")


def words_of(paragraph, n):
    words = paragraph.split()
    return " ".join((words * (n // len(words) + 1))[:n])


TRANSCRIPTS = {
    TIMED_OUT: words_of(
        "Welcome to the closing talk of the day. I want to walk you through a whole year of "
        "a bakery's books, month by month, the way we close every month with the same five "
        "checks in the same order: the bank export, the supplier invoices, the till totals, "
        "the wages and the cash count. ", 9704),
    WRITTEN: words_of(
        "This season flour went up by a third and butter followed it. Here is the sheet I use "
        "every Monday to see what each loaf costs me now, and how I decide when to raise a "
        "price and by how much. ", 3400),
}

print("=== A. the fixture is in the run's shape, and it is all here ===")
check(RECORDED_SUMMARY and len(RECORDED_SUMMARY) > 1000,
      f"the summary history.jsonl stored for {WRITTEN}: {len(RECORDED_SUMMARY or '')} characters")
check(len(TRANSCRIPTS) == 2,
      f"and both transcripts are here: {sorted(TRANSCRIPTS)}")
check(len(TRANSCRIPTS.get(TIMED_OUT, "").split()) > 9000,
      f"including the one whose summary was killed, {len(TRANSCRIPTS.get(TIMED_OUT, '').split())} "
      f"words of it, which is why fix 2 can write it up for nothing")

# --- the stubs ----------------------------------------------------------------------------
asked = []


class Spy:
    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **k):
        raise AssertionError(f"the replay called {self.name}: it must make no request")


for name in ("run_yt_dlp", "extract_media_info", "download_audio_from_info", "transcribe_audio"):
    setattr(ww, name, Spy(name))


def fake_fetch(url, plan=None):
    vid = ww.video_id_from_url(url)
    if vid == OVER_CEILING:
        return None, ("audio: the smallest audio-bearing format is 67.0 MB, over the "
                      "63 MB ceiling (AUDIO_MAX_BYTES)")
    return {"id": vid, "title": TITLES[vid], "uploader": CHANNELS[vid], "url": url,
            "transcript": TRANSCRIPTS[vid], "raw_path": ww.RAW_DIR / f"{vid}.txt",
            "lang": "en", "track_kind": "audio",
            "audio_stats": {"audio_seconds": MINUTES[vid] * 60, "model": "large-v3",
                            "device": "cuda"}}, None


def fake_triage(context_text, channel_name, details, model, sells_something=None):
    vid = ww.video_id_from_url(details.get("url") or "")
    if details.get("transcript"):
        return READ[vid]
    return LISTING.get(vid, ("MAYBE", "Description lacks specific mechanism.", 20))


def fake_cli(prompt, timeout=600):
    asked.append(timeout)
    if TRANSCRIPTS[TIMED_OUT][:200] in prompt:
        raise RuntimeError(f"claude CLI timed out after {round(timeout)}s and was killed.")
    return RECORDED_SUMMARY


ww.fetch_transcript_by_plan = fake_fetch
ww.triage_video = fake_triage
ww.claude_generate = fake_cli
ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}
ww.save_writeup = lambda *a, **k: None
ww.append_opportunity = lambda *a, **k: None
ww.ensure_local_model = lambda *a, **k: None
ww.local_most_useful = lambda *a, **k: "nothing"
ww._RUN_CLOCK["deadline"] = None

candidates = []
for vid in (TIMED_OUT, WRITTEN, OVER_CEILING):
    candidates.append({"id": vid, "title": TITLES[vid], "channel": CHANNELS[vid],
                       "url": f"https://www.youtube.com/watch?v={vid}", "description": "",
                       "duration": int(MINUTES.get(vid, 30) * 60), "upload_date": "20260919"})
for n, title in enumerate(ROLLED):
    candidates.append({"id": f"rolled{n:05d}", "title": title, "channel": "a channel",
                       "url": f"https://www.youtube.com/watch?v=rolled{n:05d}",
                       "description": "", "duration": 1800, "upload_date": "20260919"})

print("\n=== B. the replay, with the run's own budget of three ===")
pass_rows, no_entries, gave_up, summaries, rate_limited = ww.process_candidates(
    candidates, "CONTEXT", "qwen3.5:9b", False, {}, budget={"remaining": 3}, source="topics")
records = {r["id"]: r for r in ww.todays_records()}
check(len(records) == 10, f"ten records: three attempted and seven rolled ({len(records)})")

timed = records[TIMED_OUT]
verdict, state_text, tags = ww.final_verdict(timed)
check(timed["summary_state"] == "READ_NOT_WRITTEN" and timed["reason_code"] == "summary_timeout",
      f"the long talk is read and not written: {timed['reason_code']}")
check(verdict == "NOT WRITTEN" and "read as YES" in state_text,
      f"its row: {verdict} ({state_text})")
check("header" not in tags and "kept" not in tags,
      "it cannot lead the day and is not counted as a keep")
check(asked and abs(asked[0] - 931) < 5,
      f"and it was given {asked[0]:.0f} s rather than 600, from the fit on its 51.7 minutes")

written = records[WRITTEN]
verdict, _state, tags = ww.final_verdict(written)
check(written["summary_state"] == "WRITTEN", "the second video was written")
check(written["applies"] == "nothing" and verdict == "MAYBE" and "demoted" in tags,
      f"and demoted, because its section 6 came to nothing, exactly as the run recorded: "
      f"{verdict}")

over = records[OVER_CEILING]
verdict, state_text, tags = ww.final_verdict(over)
check(over["reason_code"] == "over_ceiling", "the third was never read")
check(verdict == "NOT READ" and "67.0 MB" in state_text, f"its row: {verdict} ({state_text})")
check("guess" in tags, "and its listing verdict is marked a guess")

waiting = [r for r in records.values() if r["summary_state"] == "WAITING"]
check(len(waiting) == 7, f"the seven that rolled are on the books, not only in the log "
                          f"({len(waiting)})")

print("\n=== C. the corrected section and header ===")
ww.write_digest(pass_rows, no_entries, summaries, gave_up=gave_up, label="topics")
digest = (ww.OUTPUT_DIR / f"DIGEST-{ww.today_key()}.md").read_text(encoding="utf-8")
header = re.search(r"<!-- HEADER-START -->(.*?)<!-- HEADER-END -->", digest, re.S).group(1)
table = re.search(r"<!-- TABLE-START -->(.*?)<!-- TABLE-END -->", digest, re.S).group(1)
print("\n".join("    " + l for l in header.strip().splitlines()))
print("\n".join("    " + l[:150] for l in table.strip().splitlines()))

check("A long talk on bakery bookkeeping" not in header,
      "the long talk is not in the header any more")
check("Nothing to keep today" in header,
      "and with its only keeper gone, this run kept nothing, which the header says")
check("0 kept, 1 read and skipped, 1 read but not written, 1 not read, 7 waiting" in header,
      f"the counts name what happened to all ten")
check("NOT WRITTEN (read as YES; summary timed out after 931 s, attempt 1 of 3)" in table,
      "the table says what happened to the long talk, in one cell")
check("NOT READ (audio 67.0 MB over the 63 MB ceiling)" in table,
      "and what happened to the video nobody could fetch")
# 2026-09-25, fix 5: the line under the table is "Waiting, not fetched ..." now.
check("Waiting, not fetched" in table and "Bookkeeping For A Corner Bakery" in table,
      "and the seven waiting are named under it")
check(table.count("| MAYBE |") == 1 and "How Small Bakeries Reprice When Flour Jumps" in table,
      "while the one video that was actually written keeps its ordinary row")

print("\n=== D. and it cost nothing ===")
check("the replay called" not in "".join(fails), "no YouTube request was made")
check(len(asked) == 2, f"two summary calls, one killed and one written ({len(asked)})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
