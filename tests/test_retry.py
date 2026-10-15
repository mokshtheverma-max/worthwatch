"""FIX 2. A failed summary is retried from the transcript already on disk, for free.

On 2026-09-21 one talk cost two YouTube requests, a 51 MB download and 127 seconds of
GPU, and then the summary timed out and all of it was thrown away. The transcript was on
disk the whole time. Nothing read it again, and nothing ever will: seen.json says the video
is done, so the next run skips it.

So a transcript is saved with its own small record before the summary is called, a failed
summary puts the video in a retry queue, and each scheduled run empties up to three of that
queue first, summary only. The three things this file has to prove:

  the retry costs nothing      a spy on run_yt_dlp that raises if it is called at all, so
                               "no YouTube request" is measured and not asserted
  a failure is not final       a stubbed CLI that times out once and then succeeds, and the
                               record ends WRITTEN on attempt 2
  and it does not loop         three failures is terminal, with the reason kept

Everything runs in a fresh folder under the OS temp folder, with the invented example
person's profile; the sandbox points INDEX.md and TRY-NEXT.md there too. No YouTube request,
no model call, no clock.
"""
import io
import json
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "retry")
ww.INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ww.load_context = lambda: "CONTEXT for the test."
ww.ensure_local_model = lambda *a, **k: None

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def reset():
    for pattern in ("*.json", "*.jsonl"):
        for f in ww.DATA_DIR.glob(pattern):
            f.unlink()
    ww.REQUEST_LOG_PATH.unlink(missing_ok=True)
    for d in (ww.RAW_DIR, ww.TRANSCRIPTS_DIR):
        if d.exists():
            for f in d.iterdir():
                f.unlink()


VID = "MaraVid0001"
# The summary below quotes the transcript's first two sentences, because finalize_summary
# checks every quoted span against the transcript it was written from. A fixture whose quote
# is not in its own transcript is struck, correctly, and then this file is measuring the
# quote guard instead of the retry. Both are invented.
TRANSCRIPT = ("Welcome everyone to the small shop session. Today we are going to talk about "
              "beyond spreadsheets, how a two-person shop onboards bakeries onto a bookkeeping "
              "tool. " + "We send the welcome checklist on the first day. " * 300).strip()
GOOD_SUMMARY = (
    "## 1. What it is\nA talk.\n\n"
    "## 6. What applies to me\n"
    "Most applicable: send the welcome checklist on the first day, the way the talk does it. "
    '"beyond spreadsheets, how a two-person shop onboards bakeries" '
    "Applies to: Ledgerleaf\n\n"
    "## 7. What contradicts what I believe\nnothing in this video touches the beliefs list\n\n"
    "## 8. Try this week\nSend one welcome checklist to a new bakery.\n")

TIMEOUT = "claude CLI timed out after 600s and was killed."


class Spy:
    """Anything that must not be called on the retry path. Raises, loudly, by name."""

    def __init__(self, name):
        self.name = name
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise AssertionError(f"the retry path called {self.name}, which is a YouTube request")


# ==========================================================================================
print("=== A. the transcript is saved before the summary is called, with its own record ===")
reset()
meta = {"id": VID, "title": "Small Shop Onboarding Talk", "uploader": "Small Shop Talks",
        "url": f"https://www.youtube.com/watch?v={VID}", "transcript": TRANSCRIPT,
        "audio_stats": {"audio_seconds": 3102.0, "model": "large-v3", "device": "cuda"}}
saved = ww.save_transcript(meta, source="topics")
check((ww.TRANSCRIPTS_DIR / f"{VID}.txt").exists(), "the text is on disk in transcripts/")
check((ww.TRANSCRIPTS_DIR / f"{VID}.json").exists(), "and a small record beside it")
side = json.loads((ww.TRANSCRIPTS_DIR / f"{VID}.json").read_text(encoding="utf-8"))
check(side["id"] == VID and side["words"] == len(TRANSCRIPT.split()),
      f"which carries the video id and the word count ({side['words']})")
check(abs(side["minutes"] - 51.7) < 0.1, f"the minutes of audio: {side['minutes']}")
check(side["whisper_model"] == "large-v3", "the whisper model that produced it")
check(side.get("created"), "and when it was made")
text, info = ww.saved_transcript(VID)
check(text == TRANSCRIPT and info["minutes"] == side["minutes"], "and it reads back whole")

print("\n  a transcript in raw/ with no record still reads, because that is where this")
print("  tool has always written them, and today's one failure is sitting in there")
reset()
(ww.RAW_DIR / f"{VID}.txt").write_text(TRANSCRIPT, encoding="utf-8")
text, info = ww.saved_transcript(VID)
check(text == TRANSCRIPT, "the fallback finds it")
check(info["minutes"] and info["minutes"] > 0,
      f"and estimates the minutes from the word count ({info['minutes']}), saying so")
check(info["minutes_estimated"] is True, "marked as an estimate, never as a measurement")
check(ww.saved_transcript("nosuchvideoo") == (None, None),
      "CONTROL: a video with no transcript anywhere reads as nothing")

# ==========================================================================================
print("\n=== B. a failed summary joins the retry queue, and the queue is oldest first ===")
reset()
(ww.RAW_DIR / f"{VID}.txt").write_text(TRANSCRIPT, encoding="utf-8")
ww.update_record(VID, url=meta["url"], title=meta["title"], channel="Small Shop Talks",
                 source="topics", listing_verdict="MAYBE", read_verdict="YES", read_confidence=95,
                 summary_state="WRITTEN", reason_code="written", project="Ledgerleaf",
                 applies="items")
check(ww.retry_queue() == [], "CONTROL: a written record is not in the queue")
ww.summary_failed(VID, RuntimeError(TIMEOUT), attempts=1)
queue = ww.retry_queue()
check([r["id"] for r in queue] == [VID], "a timed-out summary is")
ww.update_record("bbbbbbbbbbb", summary_state="READ_NOT_WRITTEN", reason_code="summary_error",
                 state_reason="something else went wrong", attempts=1, read_verdict="YES",
                 created="2020-01-01T00:00:00")
check([r["id"] for r in ww.retry_queue()][0] == "bbbbbbbbbbb",
      "and the oldest goes first, whatever order the store is in")
ww.update_record("bbbbbbbbbbb", terminal=True, attempts=3)
check([r["id"] for r in ww.retry_queue()] == [VID],
      "a record that has spent its three attempts is not queued again")
ww.update_record("ccccccccccc", summary_state="READ_NOT_WRITTEN",
                 reason_code="read_then_dropped", state_reason="read, then screened out",
                 read_verdict="NO")
check(VID in [r["id"] for r in ww.retry_queue()] and len(ww.retry_queue()) == 1,
      "and a video that was read and deliberately not summarised is not a failure to retry")

# ==========================================================================================
print("\n=== C. THE RETRY. it times out once, then succeeds, and costs no request ===")
reset()
(ww.RAW_DIR / f"{VID}.txt").write_text(TRANSCRIPT, encoding="utf-8")
ww.update_record(VID, url=meta["url"], title=meta["title"], channel="Small Shop Talks",
                 source="topics", listing_verdict="MAYBE", listing_confidence=10, read_verdict="YES",
                 read_confidence=95, summary_state="READ_NOT_WRITTEN",
                 reason_code="summary_timeout", state_reason="summary timed out after 600 s",
                 attempts=1)

calls = {"n": 0, "prompts": []}


def stub_claude(prompt, timeout=600):
    calls["n"] += 1
    calls["prompts"].append(prompt)
    if calls["n"] == 1:
        raise RuntimeError(TIMEOUT)
    return GOOD_SUMMARY


spies = {}
for name in ("run_yt_dlp", "extract_media_info", "download_audio_from_info",
             "fetch_transcript_by_plan", "transcribe_audio", "note_fetch"):
    spies[name] = Spy(name)
    setattr(ww, name, spies[name])
ww.claude_generate = stub_claude
ww.local_most_useful = lambda *a, **k: "nothing"
ww.ensure_one_sentence = lambda text, model: text

rows, summaries = ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
record = ww.load_records()[VID]
check(calls["n"] == 1 and record["summary_state"] == "READ_NOT_WRITTEN",
      "attempt 2 timed out too, so the record is still not written")
check(record["attempts"] == 2, f"and the attempt count moved to {record['attempts']}")
verdict, state_text, tags = ww.final_verdict(record)
check(verdict == "NOT WRITTEN" and "attempt 2 of 3" in state_text,
      f"which the row says out loud: {verdict} ({state_text})")

rows, summaries = ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
record = ww.load_records()[VID]
check(calls["n"] == 2, "the next run tries again")
check(record["summary_state"] == "WRITTEN", "and this time the summary lands")
check(record["project"] == "Ledgerleaf", "section 6 goes on the record with it")
verdict, state_text, tags = ww.final_verdict(record)
check(verdict == "YES" and "header" in tags,
      f"so the video is a keep at last, and may lead the day: {verdict}")
check(len(rows) == 1 and len(summaries) == 1, "and the run has a row and a summary to show")
check(TRANSCRIPT[:60] in calls["prompts"][-1],
      "the prompt was built from the transcript on disk, not from a new fetch")

print("\n  the spies, which is how 'no YouTube request' is measured rather than claimed")
for name, spy in spies.items():
    check(spy.calls == 0, f"{name} was not called once in either retry")
check(ww.retry_queue() == [], "and the queue is empty, so nothing is retried for ever")

# ==========================================================================================
print("\n=== D. three failures is terminal, with the reason kept ===")
reset()
(ww.RAW_DIR / f"{VID}.txt").write_text(TRANSCRIPT, encoding="utf-8")
ww.update_record(VID, url=meta["url"], title=meta["title"], channel="Small Shop Talks",
                 read_verdict="YES", summary_state="READ_NOT_WRITTEN",
                 reason_code="summary_timeout", state_reason="summary timed out after 600 s",
                 attempts=1)
ww.claude_generate = lambda prompt, timeout=600: (_ for _ in ()).throw(RuntimeError(TIMEOUT))
ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
record = ww.load_records()[VID]
check(record["attempts"] == 3 and record["terminal"] is True,
      "after the third attempt the record is terminal")
check(ww.retry_queue() == [], "and it is not queued a fourth time")
verdict, state_text, _tags = ww.final_verdict(record)
check(verdict == "NOT WRITTEN" and "attempt 3 of 3" in state_text
      and "no further attempt" in state_text,
      f"the row says it is over, and why: {verdict} ({state_text})")
check("timed out" in state_text, "and keeps the reason rather than a shrug")

# ==========================================================================================
print("\n=== E. a transcript that is gone sends the video back to the fetch queue, once ===")
reset()
ww.update_record(VID, url=meta["url"], title=meta["title"], channel="Small Shop Talks",
                 read_verdict="YES", summary_state="READ_NOT_WRITTEN",
                 reason_code="summary_timeout", state_reason="summary timed out after 600 s",
                 attempts=1)
check(ww.saved_transcript(VID) == (None, None), "the transcript really is gone")
ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
record = ww.load_records()[VID]
check(record["summary_state"] == "WAITING" and record.get("refetch") is True,
      "the record goes back to waiting, marked for one more fetch")
check(ww.retry_queue() == [], "and it is out of the retry queue, because there is nothing to retry")
_v, state_text, tags = ww.final_verdict(record)
check("waiting" in tags and "row" not in tags,
      "so it has no row today and is counted as waiting, which is what it is")
ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
check(ww.load_records()[VID].get("refetch") is True and ww.retry_queue() == [],
      "a second retry run does not queue it again: once is once")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
