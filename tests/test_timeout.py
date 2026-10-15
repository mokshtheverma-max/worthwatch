"""FIX 3. A timeout that fits the talk, and a run that cannot eat the next slot.

Until today every summary got 600 seconds, whatever it was summarising. On 2026-09-21 a
51.7 minute conference talk was killed at 600 seconds after it had already cost two YouTube
requests, a 51 MB download and 127 seconds of GPU. A fixed timeout is a guess about the
longest talk applied to every talk.

The replacement is max(600, k x predicted) under a ceiling, and the two numbers that matter
are checked here against a window of past summaries rather than against a story about them:

  it must not kill anything it used to finish   every summary in the fitting window gets a
                                                timeout at least as long as the time it
                                                actually took
  and it must not be a no-op                    at least one video gets more than the
                                                old 600 s, or the change bought nothing

The window used to be parsed out of the owner's own run logs by scripts/fit_timeout.py, which is
not part of worthwatch. An invented window stands in, below: summaries whose least-squares line
is the one in core.py, with a weak fit and a worst actual-over-predicted ratio under k, as the
logs had, one failed summary (the 51.7 minute talk), and the run lengths before and after the
deadline was chosen.

The run deadline is the other half. Six summaries a slot at 600 seconds is already sixty
minutes of summary alone, so the longest POSSIBLE run was over an hour before today and no
per-call ceiling can fix that on its own.

No YouTube request, no model call, no clock that matters: the deadline is injected.
"""
import io
import math
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "timeout")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


# --- the invented window ------------------------------------------------------------------
WINDOW_START, WINDOW_END = "2026-09-17", "2026-09-21"
_FITTED = [(8.0, 170.7), (8.0, 49.6), (9.6, 182.1), (9.6, 52.9), (11.2, 193.4), (11.2, 56.2),
           (12.9, 205.5), (12.9, 59.7), (14.3, 215.4), (14.3, 62.5), (16.8, 233.2), (16.8, 67.7),
           (19.5, 252.4), (19.5, 73.3), (22.1, 270.8), (22.1, 78.6), (25.6, 295.7), (25.6, 85.8),
           (31.0, 334.0), (31.0, 97.0), (38.4, 386.5), (38.4, 112.2), (44.9, 432.7), (44.9, 125.6)]
_DAYS = ("2026-09-17", "2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21")
videos = [{"date": _DAYS[n % len(_DAYS)], "minutes": m, "summary_s": s}
          for n, (m, s) in enumerate(_FITTED)]
# Summaries written after the window the constants were fitted over: they are not in the fit,
# and every one of them is still held to "kills nothing it used to finish".
videos += [{"date": "2026-09-22", "minutes": 51.7, "summary_s": 122.6, "retry": True},
           {"date": "2026-09-23", "minutes": 18.2, "summary_s": 140.0},
           {"date": "2026-09-25", "minutes": 60.3, "summary_s": 401.2},
           {"date": "2026-09-28", "minutes": 12.4, "summary_s": 95.3}]
errors = [{"date": "2026-09-21", "minutes": 51.7,
           "error": "claude CLI timed out after 600s and was killed."}]
# Every run's length, by the day it started on.
runs = [{"start": d, "seconds": round(m * 60)} for d, m in (
    ("2026-09-17", 8.2), ("2026-09-18", 12.5), ("2026-09-18", 17.9), ("2026-09-19", 21.4),
    ("2026-09-20", 26.8), ("2026-09-21", 14.0), ("2026-09-21", 9.7),
    ("2026-09-24", 18.3), ("2026-10-08", 33.1), ("2026-10-09", 24.6), ("2026-10-10", 41.0))]


def fit(pairs):
    """Least squares over (minutes, seconds): the line, its r, and the worst actual-over-predicted."""
    n = len(pairs)
    mx = sum(x for x, _ in pairs) / n
    my = sum(y for _, y in pairs) / n
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    syy = sum((y - my) ** 2 for _, y in pairs)
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    slope = sxy / sxx
    intercept = my - slope * mx
    return {"intercept": intercept, "slope": slope, "r": sxy / math.sqrt(sxx * syy),
            "worst_ratio": max(y / (intercept + slope * x) for x, y in pairs)}


# ==========================================================================================
print("=== A. the fit is the one in the window, not a number somebody liked ===")
window = [v for v in videos if v["date"] >= WINDOW_START]
# Item 0b, 2026-09-22: the constants are compared with the fit over the window they were
# fitted over, which ends on WINDOW_END. Every later summary still goes into section B's
# "kills nothing it used to finish", which is the check that more data should make harder.
fitted = [v for v in window if v["date"] <= WINDOW_END]
f = fit([(v["minutes"], v["summary_s"]) for v in fitted])
check(len(fitted) >= 20, f"the fitting window holds {len(fitted)} summarised videos")
check(abs(f["intercept"] - ww.SUMMARY_FIT_INTERCEPT) < 5,
      f"the intercept in core.py ({ww.SUMMARY_FIT_INTERCEPT}) is the window's ({f['intercept']:.1f})")
check(abs(f["slope"] - ww.SUMMARY_FIT_SLOPE) < 0.2,
      f"and the slope ({ww.SUMMARY_FIT_SLOPE}) is the window's ({f['slope']:.2f})")
check(ww.SUMMARY_TIMEOUT_K > f["worst_ratio"],
      f"k ({ww.SUMMARY_TIMEOUT_K}) clears the worst actual-over-predicted ever seen "
      f"({f['worst_ratio']:.2f})")
check(0.3 < f["r"] < 0.7,
      f"and the fit is honestly weak, r = {f['r']:.3f}: length explains some of the time "
      f"and how much the model writes explains the rest")

print("\n  every failed summary in the window, which is the evidence for the change")
check(len(errors) == 1, f"exactly one: {len(errors)}")
for e in errors:
    check(e["minutes"] == 51.7 and "timed out after 600s" in e["error"],
          f"{e['date']}, {e['minutes']} min, {e['error'][:44]}")

# ==========================================================================================
print("\n=== B. THE MEASUREMENT. it kills nothing it used to finish, and is not a no-op ===")
killed = [v for v in window if v["summary_s"] > ww.summary_timeout_for(v["minutes"])[0]]
check(not killed, f"none of the {len(window)} summaries in the window would now be killed "
                  f"({len(killed)} would)")
raised = [v for v in window if ww.summary_timeout_for(v["minutes"])[0] > 600]
check(raised, f"CONTROL: {len(raised)} of them would get more than the old fixed 600 s, so "
              f"this is not a change that does nothing")
timeout, why = ww.summary_timeout_for(51.7)
check(timeout > 600 and "fit" in why,
      f"the 51.7 minute talk would have been given {timeout:.0f} s rather than 600 ({why})")

# ==========================================================================================
print("\n=== C. the floor, the fit and the ceiling, each where it should be ===")
check(ww.predicted_summary_seconds(0) is None, "no minutes means no prediction, not zero")
check(abs(ww.predicted_summary_seconds(51.7) - 310) < 5,
      f"51.7 min predicts {ww.predicted_summary_seconds(51.7):.0f} s")
t, why = ww.summary_timeout_for(17)
check(t == ww.SUMMARY_TIMEOUT_FLOOR and "floor" in why,
      f"a median 17 minute video gets the floor, unchanged at {t:.0f} s ({why})")
t, why = ww.summary_timeout_for(None)
check(t == ww.SUMMARY_TIMEOUT_FLOOR and "floor" in why,
      "and so does a video whose length nobody knows")
t, why = ww.summary_timeout_for(90)
check(t == ww.SUMMARY_TIMEOUT_CEILING and "ceiling" in why,
      f"the longest video the duration filter allows hits the ceiling: {t:.0f} s ({why})")
crossover = next(m for m in range(1, 91) if ww.summary_timeout_for(m)[0] > ww.SUMMARY_TIMEOUT_FLOOR)
check(25 <= crossover <= 30,
      f"the floor governs everything under about {crossover} minutes, which is most videos")
for m in (1, 5, 17, 30, 51.7, 89, 90, 200):
    t, _why = ww.summary_timeout_for(m)
    check(ww.SUMMARY_TIMEOUT_FLOOR <= t <= ww.SUMMARY_TIMEOUT_CEILING,
          f"{m} min stays between the floor and the ceiling ({t:.0f} s)")

print("\n  a retry gets longer, so the third attempt is not the first one repeated")
t1, _w = ww.summary_timeout_for(51.7, attempt=1)
t2, w2 = ww.summary_timeout_for(51.7, attempt=2)
t3, w3 = ww.summary_timeout_for(51.7, attempt=3)
check(t2 > t1, f"attempt 2 gets more than attempt 1 ({t1:.0f} -> {t2:.0f} s)")
check(t3 == ww.SUMMARY_TIMEOUT_CEILING and "ceiling" in w3,
      f"and attempt 3 reaches the ceiling ({t3:.0f} s), which is where NOT WRITTEN "
      f"(too long for the ceiling) comes from")
check(ww.summary_timeout_for(17, attempt=3)[0] <= ww.SUMMARY_TIMEOUT_CEILING,
      "CONTROL: escalating never climbs past the ceiling either")

# ==========================================================================================
print("\n=== D. the run deadline, which is the only thing that bounds the whole slot ===")
old_worst = 6 * 600 / 60
check(old_worst > 55,
      f"before today the longest possible run was {old_worst:.0f} min of summary alone "
      f"(six summaries a slot at a fixed 600 s), already over the 55")
bound = 2 * ww.RUN_DEADLINE_SECONDS + 300
check(bound < 55 * 60,
      f"now: two invocations of {ww.RUN_DEADLINE_SECONDS} s plus 150 s of tail each is "
      f"{bound / 60:.1f} min, under 55")
check(ww.SUMMARY_TIMEOUT_CEILING < ww.RUN_DEADLINE_SECONDS,
      "and one summary can never outlast the invocation it is in")
# 2026-10-08 night: the 30-minute line is a fact about the runs the deadline was chosen from
# (every run up to WINDOW_END), so it is read over that window, the way section A reads the fit
# (item 0b). A later run may come near both invocations' deadlines, which the deadline allows;
# every run since the deadline is held to the bound it was built for, below.
before = [r for r in runs if r["start"] <= WINDOW_END]
longest = max(r["seconds"] for r in before)
check(longest / 60 < 30, f"the longest run up to {WINDOW_END} is "
                         f"{longest / 60:.1f} min, so the deadline is above anything that had "
                         f"actually happened")
since = [r for r in runs if r["start"] > WINDOW_END]
longest_since = max((r["seconds"] for r in since), default=0)
check(since and longest_since < bound,
      f"and each of the {len(since)} runs since stayed inside the {bound / 60:.1f} min the two "
      f"deadlines and their tails allow (the longest {longest_since / 60:.1f} min)")

ww._RUN_CLOCK["deadline"] = None
check(ww.run_seconds_left() is None, "a hand-run command has no deadline at all")
check(ww.may_start_a_summary()[0], "and may always start a summary")
ww.start_run_clock(700)
t, why = ww.summary_timeout_for(51.7)
check(t <= 700 and "deadline" in why,
      f"with 700 s of the run left, a 931 s timeout is cut to {t:.0f} s ({why})")
check(ww.may_start_a_summary()[0], "700 s is still over the floor, so a summary may start")
ww.start_run_clock(300)
may, why = ww.may_start_a_summary()
check(not may and "600" in why,
      f"with 300 s left it may not, and says why: {why}")
check(ww.summary_timeout_for(17)[0] <= 300,
      "CONTROL: the clamp holds for a short video too, not only for a long one")
ww.start_run_clock(ww.RUN_DEADLINE_SECONDS)
check(ww.run_seconds_left() > ww.RUN_DEADLINE_SECONDS - 5, "starting the clock resets it")
ww._RUN_CLOCK["deadline"] = None

# ==========================================================================================
print("\n=== E. what the brief said not to touch is untouched ===")
check(ww.AUDIO_MAX_BYTES == 60 * 1024 * 1024, "AUDIO_MAX_BYTES")
check(ww.TOPICS_DAILY_CAP == 12 and ww.MAX_CAPTION_FETCHES_PER_RUN == 3, "the daily caps")
check(ww.CAPTION_CAP_START == 8 and ww.CAPTION_CAP_MAX == 30, "the adaptive caption cap")
src = Path(ww.__file__).read_text(encoding="utf-8")
check('"--model", "sonnet"' in src, "the CLI model is still sonnet")
check("TRIAGE_TRANSCRIPT_CHARS = 60_000" in src,
      "and nothing truncates or chunks a transcript differently for the timeout")

# ==========================================================================================
print("\n=== F. the fetch path really asks for it, which is where this nearly went wrong ===")
# The first version of this fix read meta["minutes"], and nothing on the fetch path ever put
# "minutes" on the meta: every real video would have gone on getting the 600 s floor while
# every unit check above passed. So this asserts the number that reaches the CLI, driven
# through process_candidates with the network, the model and the clock all stubbed.


def clean():
    for f in list(ww.DATA_DIR.glob("*.json*")) + list(ww.RAW_DIR.glob("*")):
        f.unlink()
    if ww.TRANSCRIPTS_DIR.exists():
        for f in ww.TRANSCRIPTS_DIR.iterdir():
            f.unlink()


clean()
TRANSCRIPT = "The talk says the same sentence over and over. " * 900
asked = []
VID = "MaraTalk001"
TITLE = "A long talk on bakery bookkeeping"


def fake_fetch(url, plan=None):
    """What the audio path returns for the 51.7 minute talk, to the minute."""
    return {"id": VID, "title": TITLE, "uploader": "Invented Talks", "url": url,
            "transcript": TRANSCRIPT, "raw_path": ww.RAW_DIR / f"{VID}.txt",
            "lang": "en", "track_kind": "audio",
            "audio_stats": {"audio_seconds": 3102.0, "model": "large-v3", "device": "cuda",
                            "transcribe_seconds": 127.0}}, None


def fake_cli(prompt, timeout=600):
    asked.append(timeout)
    return "## 6. What applies to me\nnothing\n\n## 8. Try this week\nnothing\n"


ww.fetch_transcript_by_plan = fake_fetch
ww.triage_video = lambda *a, **k: ("YES", "a mechanism [copy: do the thing]", 95)
ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}
ww.save_writeup = lambda *a, **k: None
ww.append_opportunity = lambda *a, **k: None
ww.ensure_local_model = lambda *a, **k: None
ww.finalize_summary = lambda text, *a, **k: text
ww.claude_generate = fake_cli
ww._RUN_CLOCK["deadline"] = None

CANDIDATES = [{"id": VID, "title": TITLE, "channel": "Invented Talks",
               "url": f"https://www.youtube.com/watch?v={VID}", "description": "",
               "duration": 3102, "upload_date": "20260920"}]

ww.process_candidates(CANDIDATES, "CONTEXT", "qwen3.5:9b", False, {},
                      budget={"remaining": 1}, source="topics")
check(len(asked) == 1, f"the CLI was called once ({len(asked)})")
check(asked and abs(asked[0] - 931) < 5,
      f"and asked for {asked[0] if asked else 'nothing'} s rather than the old fixed 600, "
      f"because the minutes reached the summariser")
record = ww.load_records().get(VID, {})
check(record.get("minutes") == 51.7,
      f"the record carries the measured minutes: {record.get('minutes')}")
check((ww.TRANSCRIPTS_DIR / f"{VID}.json").exists(),
      "and the transcript was saved before the call, so a failure there is retried for free")

print("\n  and with the slot nearly over, no summary is started at all")
asked.clear()
clean()
ww.start_run_clock(120)
ww.process_candidates(CANDIDATES, "CONTEXT", "qwen3.5:9b", False, {},
                      budget={"remaining": 1}, source="topics")
check(not asked, "the CLI was not called with 120 s of the run left")
record = ww.load_records().get(VID, {})
check(record.get("summary_state") == "READ_NOT_WRITTEN"
      and record.get("reason_code") == "no_run_time",
      f"the record says why: {record.get('reason_code')}")
queue = ww.retry_queue()
check(queue and queue[0]["id"] == VID,
      "and it is in the retry queue, where the next run writes it up for nothing")
ww._RUN_CLOCK["deadline"] = None
clean()

# ==========================================================================================
print("\n=== G. FIX 3, 2026-09-22. the retry sizes its timeout from the real minutes ===")
# At 09:02 on 2026-09-22 the retry of the 51.7 minute talk logged "summary timeout: 1200s
# (ceiling, the fit wanted 1845 s)". 1845 is 2 x 3 x (73.5 + 4.58 x 51.07): the attempt
# multiplier the plan of 2026-09-21 asked for, and minutes estimated from the words in raw/
# (9,704 / 190 = 51.07), because raw/ has no sidecar. The record held the measured 51.7 all
# along, and the request log has the 3,099.8 s it was measured from.
import io as _io
import json as _json
import contextlib

# An invented transcript of 9,704 words, the length of the talk's, so the word-count estimate
# lands on the same 51.1 minutes the real one did.
_PARAGRAPH = ("Every evening after we closed I typed each receipt into one spreadsheet, and by "
              "the end of the month I could see where the money went. Flour had gone up by a "
              "third, two suppliers had charged us for deliveries we never got, and the weekly "
              "cash number told me when to stop and look before buying anything new. ").split()
REAL_TRANSCRIPT = " ".join((_PARAGRAPH * (9704 // len(_PARAGRAPH) + 1))[:9704])
check(len(REAL_TRANSCRIPT.split()) == 9704, "the fixture is a transcript of 9,704 words")


def failed_talk(minutes=51.7):
    """The record as the retry found it that morning, and its transcript in raw/ only."""
    clean()
    (ww.RAW_DIR / f"{VID}.txt").write_text(REAL_TRANSCRIPT, encoding="utf-8")
    fields = dict(url=f"https://www.youtube.com/watch?v={VID}", channel="Invented Talks",
                  title=f"{TITLE}: the whole year, month by month", source="topics",
                  listing_verdict="MAYBE", read_verdict="YES", read_confidence=95,
                  summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout",
                  state_reason="summary timed out after 600 s", attempts=1)
    if minutes is not None:
        fields["minutes"] = minutes
    return ww.update_record(VID, **fields)


measured = getattr(ww, "measured_minutes", None)
check(measured is not None, "one function says how long a video's audio really was")
record = failed_talk()
minutes, source = measured(VID, record) if measured else (None, None)
check(minutes == 51.7 and source, f"for the talk's record: {minutes} min, from {source}")
t1, why1 = ww.summary_timeout_for(minutes or 0, attempt=1)
check(round(t1) == 931,
      f"THE FORMULA'S VALUE: max(600, 3 x (73.5 + 4.58 x 51.7)) = {t1:.0f} s on attempt 1")

asked.clear()
out = _io.StringIO()
with contextlib.redirect_stdout(out):
    ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
log = out.getvalue()
check(asked and asked[-1] == ww.SUMMARY_TIMEOUT_CEILING,
      f"the retry that ran was attempt 2 and was given {asked[-1] if asked else None} s")
check("the fit wanted 1862 s" in log and "1845" not in log,
      "and it says the fit wanted 1862 s, which is attempt 2 of the formula at 51.7 minutes, "
      "not 1845 from an estimate")
check("51.7 min of audio from the video's record" in log,
      "the retry's own line says the minutes it used and where they came from")

print("\n  each place the real minutes are held, and the estimate only when none is")
record = failed_talk(minutes=None)
ww.REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
ww.REQUEST_LOG_PATH.write_text(_json.dumps({
    "ts": "2026-09-21T10:12:00", "kind": "audio_transcribe", "endpoint": "local/faster-whisper",
    "status": "ok", "video_id": VID, "audio_seconds": 3099.8}) + "\n", encoding="utf-8")
minutes, source = measured(VID, record) if measured else (None, None)
check(minutes == 51.7 and "request log" in (source or ""),
      f"no minutes on the record: the request log's 3,099.8 s gives {minutes} ({source})")
ww.REQUEST_LOG_PATH.unlink(missing_ok=True)
text, info = ww.saved_transcript(VID)
check(info.get("minutes") == 51.1 and info.get("minutes_estimated") is True,
      f"measured nowhere: the word-count estimate, {info.get('minutes')} min, said to be one")
record = failed_talk(minutes=51.7)
ww.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
(ww.TRANSCRIPTS_DIR / f"{VID}.json").write_text(_json.dumps(
    {"id": VID, "minutes": 51.6, "minutes_estimated": False, "words": 9704}), encoding="utf-8")
(ww.TRANSCRIPTS_DIR / f"{VID}.txt").write_text(REAL_TRANSCRIPT, encoding="utf-8")
text, info = ww.saved_transcript(VID)
check(info.get("minutes") == 51.6 and info.get("minutes_estimated") is False,
      f"CONTROL: a transcript's own measured record is used first ({info.get('minutes')})")

print("\n  the fetch path says which of the two its minutes are")
clean()
ww.process_candidates(CANDIDATES, "CONTEXT", "qwen3.5:9b", False, {},
                      budget={"remaining": 1}, source="topics")
record = ww.load_records().get(VID, {})
check(record.get("minutes") == 51.7 and record.get("minutes_estimated") is False,
      f"measured audio goes on the record as measured: {record.get('minutes_estimated')}")
clean()

print("\n  and the window pairs the retry of 2026-09-22 with its minutes")
# This check used to ask scripts/fit_timeout.py's log parser to pair the retry's summary time
# with its minutes. That script is not part of worthwatch; the retry's own log line that the
# parser read is checked above ("51.7 min of audio from the video's record"), and here the
# retry's pair is held to the timeout its attempt was given.
retried = [v for v in videos if v.get("date") == "2026-09-22" and v.get("retry")]
check(len(retried) == 1 and retried[0]["minutes"] == 51.7 and retried[0]["summary_s"] == 122.6
      and retried[0]["summary_s"] <= ww.summary_timeout_for(51.7, attempt=2)[0],
      f"the retry's 122.6 s at 51.7 min is a paired summary, inside its attempt-2 timeout: "
      f"{[(v['minutes'], v['summary_s']) for v in retried]}")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
