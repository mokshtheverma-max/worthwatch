"""Phase 4: does `worthwatch status` say things that are true when it says them?

Two sentences it printed that were not true at the moment of printing:

  "daily cap already reached"   Written into last-run.json by the run that stopped on the
                               topics cap, then repeated by status for as long as nothing
                               overwrote it. On a fresh morning, before any run, the cap has
                               rolled and the sentence is false. The run's record was right;
                               status was quoting a fact about yesterday as a fact about now.

  "9 channel(s) never listed"  Printed on 2026-09-20, two lines under "Channel rotation:
                               12/12 covered today". Both came from this tool's own files. The
                               backlog list means "no 2026 backlog pass yet", which is a
                               different claim, and rotation.json only ever kept the current
                               day's coverage so nothing could answer "when was this channel
                               last listed" at all.

Everything here runs in one fresh folder under the OS temp folder. No YouTube request, no
model call.
"""
import datetime as dt
import io
import json
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "status")
ww.INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
# Status asks the environment about the JS challenge solver: a subprocess with nothing to do
# with what is measured here.
ww.ejs_status = lambda: (True, "stubbed")
# The old default seed list was never empty, so status always had a canary video to name. The
# default is [] now, and with no transcribed history cmd_status stops with "min() iterable
# argument is empty" in pick_canary_video (reported to the lead as a defect). This suite is not
# about the canary, so it sets one invented seed, the old behaviour.
ww.CANARY_SEED_VIDEO_IDS = ["MaraSeed001"]

CH = ["https://www.youtube.com/@one", "https://www.youtube.com/@two",
      "https://www.youtube.com/@three", "https://www.youtube.com/@four"]
ww.read_channel_urls = lambda: CH
ww.CHANNELS_PATH = types.SimpleNamespace(exists=lambda: True)

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def reset():
    for f in ww.DATA_DIR.glob("*.json*"):
        f.unlink()
    Path(ww.REQUEST_LOG_PATH).unlink(missing_ok=True)


def write_history(rows):
    with open(ww.HISTORY_PATH, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def status_text():
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        ww.cmd_status()
    finally:
        sys.stdout = real
    return buf.getvalue()


TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()

print('=== A. "daily cap already reached" is only printed when it is reached today ===')
reset()
write_history([{"date": TODAY, "verdict": "MAYBE", "channel": "c", "title": f"v{i}",
                "url": f"https://www.youtube.com/watch?v=vid{i:08d}", "reason": "r",
                "source": "topics"} for i in range(ww.TOPICS_DAILY_CAP)])
still, line = ww.note_still_true(ww.TOPICS_CAP_NOTE)
check(still is True and f"{ww.TOPICS_DAILY_CAP}/{ww.TOPICS_DAILY_CAP}" in line,
      f"with the cap reached today it holds, and says the count: {line!r}")

write_history([{"date": YESTERDAY, "verdict": "MAYBE", "channel": "c", "title": f"v{i}",
                "url": f"https://www.youtube.com/watch?v=vid{i:08d}", "reason": "r",
                "source": "topics"} for i in range(ww.TOPICS_DAILY_CAP)])
still, line = ww.note_still_true(ww.TOPICS_CAP_NOTE)
check(still is False and "not any more" in line and "12 are left" in line,
      f"the SAME note, one day later, is corrected: {line!r}")

write_history([{"date": TODAY, "verdict": "MAYBE", "channel": "c", "title": "v",
                "url": "https://www.youtube.com/watch?v=vid00000001", "reason": "r",
                "source": "topics"}])
still, line = ww.note_still_true(ww.TOPICS_CAP_NOTE)
check(still is False and "11 are left" in line,
      f"and part way through a day it says how many are left: {line!r}")

still, line = ww.note_still_true("something no rule here knows about")
check(still is None and line == "something no rule here knows about",
      "CONTROL: a note this cannot check is passed through as written, not suppressed")

print("\n=== B. and the whole status line reads correctly, both ways ===")
write_history([{"date": TODAY, "verdict": "MAYBE", "channel": "c", "title": f"v{i}",
                "url": f"https://www.youtube.com/watch?v=vid{i:08d}", "reason": "r",
                "source": "topics"} for i in range(ww.TOPICS_DAILY_CAP)])
ww.LAST_RUN_PATH.write_text(json.dumps(
    {"timestamp": f"{TODAY}T14:16:02", "mode": "topics", "new_videos": 0,
     "note": ww.TOPICS_CAP_NOTE}), encoding="utf-8")
text = status_text()
check("the topics daily cap is reached" in text and "not any more" not in text,
      "today, with the cap spent, status says it is reached")
write_history([{"date": YESTERDAY, "verdict": "MAYBE", "channel": "c", "title": f"v{i}",
                "url": f"https://www.youtube.com/watch?v=vid{i:08d}", "reason": "r",
                "source": "topics"} for i in range(ww.TOPICS_DAILY_CAP)])
text = status_text()
check("as of now, not any more" in text, "tomorrow, the same file, status corrects itself")
check("it stopped saying: daily cap already reached" in text,
      "and still says what the run itself recorded, because that record is not wrong")

print("\n=== C. the backlog line says when each channel was listed ===")
reset()
ww.ROTATION_PATH.write_text(json.dumps(
    {"day": TODAY, "index": 2, "scanned_today": CH[:2], "topics_index": 5}), encoding="utf-8")
write_history([{"date": YESTERDAY, "verdict": "YES", "channel": CH[2], "title": "t",
                "url": "https://www.youtube.com/watch?v=vid00000009", "reason": "r",
                "source": "channels"}])
dates = ww.channel_listing_dates(CH)
check(dates[CH[0]] == (TODAY, "today's pass"), f"a channel in today's pass: {dates[CH[0]]}")
check(dates[CH[2]] == (YESTERDAY, "a video it produced"),
      f"a channel that produced a video yesterday: {dates[CH[2]]}")
check(dates[CH[3]] is None,
      "CONTROL: a channel with nothing from any source is None, not a date")

ww.BACKLOG_PATH.write_text(json.dumps(
    {CH[0]: {"backfilled": True, "total_2026_known": 3, "remaining": 0, "stalls": 0,
             "note": None}}), encoding="utf-8")
text = status_text()
check("never listed" not in text, "the words 'never listed' are gone from status")
check("no backlog pass yet" in text, "and the list says what it really means")
check(f"@two: listed {TODAY}" in text, "a channel the rotation reached today carries its date")
check(f"@three: listed {YESTERDAY}" in text, "and one it reached yesterday carries that")
check("@four: no listing recorded" in text,
      "while a channel with no record says so, rather than claiming it was never listed")

print("\n=== D. the record is written, and survives a write that does not mention it ===")
reset()
ww.ROTATION_PATH.write_text(json.dumps(
    {"day": YESTERDAY, "index": 0, "scanned_today": [], "topics_index": 7}), encoding="utf-8")
ww.mark_channel_scanned(CH, CH[1])
state = json.loads(ww.ROTATION_PATH.read_text(encoding="utf-8"))
check(state.get("listed", {}).get(CH[1]) == TODAY,
      f"listing a channel records the date: {state.get('listed')}")
check(state.get("topics_index") == 7, "and the topics cursor is still there")
ww.save_rotation({"day": TODAY, "index": 3, "scanned_today": [CH[0]]})
state = json.loads(ww.ROTATION_PATH.read_text(encoding="utf-8"))
check(state.get("listed", {}).get(CH[1]) == TODAY,
      "a write that does not mention `listed` does not lose it")
check(state.get("scanned_today") == [CH[0]],
      "CONTROL: but scanned_today IS replaced, which is the point of a write")

print("\n=== E. a rotation.json written before today still loads unchanged ===")
OLD = {"day": "2026-09-20", "index": 0,
       "scanned_today": ["https://www.youtube.com/@one"], "topics_index": 10}
ww.ROTATION_PATH.write_text(json.dumps(OLD), encoding="utf-8")
loaded = ww.load_rotation()
check(loaded == OLD, "it loads byte for byte, with no `listed` key")
check(ww.load_rotation().get("listed") is None, "and asking for the new key gives None")
state, scanned = ww.rotation_state(CH)
check(scanned == ["https://www.youtube.com/@one"] and state["index"] == 0,
      "the rotation reads exactly as it did")
dates = ww.channel_listing_dates(CH)
check(dates["https://www.youtube.com/@one"] is not None,
      "and today's pass still answers for the channels in it, with no record to read")
# These three checks used to read the owner's own rotation.json, as the scheduled runs had
# written it since the `listed` key was added. A file written the same way stands in: the
# file from before today, then a run that lists a channel, through the code's own writers.
ww.mark_channel_scanned(CH, CH[2])
real = json.loads(ww.ROTATION_PATH.read_text(encoding="utf-8"))
check(set(real) >= {"day", "index", "scanned_today", "topics_index"},
      f"a rotation.json a run writes now still carries every key the rotation reads "
      f"({sorted(real)})")
check(isinstance(real.get("listed", {}), dict),
      "and the key that was added is a per-channel date map, or absent on a file from before it")
_state, _scanned = ww.rotation_state(CH)
check(isinstance(real.get("scanned_today"), list),
      "scanned_today is still the list the rotation walks, not the new map")

print("\n=== F. a missing or unreadable last-run.json does not cut the report off ===")
# It used to `return` there, so everything below the last-run block was unreachable on a
# machine that had never run. Found while trying to test section C.
ww.LAST_RUN_PATH.unlink(missing_ok=True)
text = status_text()
check("Last run: never" in text and "no backlog pass yet" in text,
      "with no last-run.json, the backlog section still prints")
ww.LAST_RUN_PATH.write_text("{not json at all", encoding="utf-8")
text = status_text()
check("last-run.json unreadable" in text and "no backlog pass yet" in text,
      "and with an unreadable one too")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
