"""FIX 1. No write-up, no keep: one record per video, one function that decides its verdict.

The defect this is for, from 2026-09-21 at 10:12. A talk was fetched, transcribed in 127 s,
read as YES with confidence 95, and then the summary call timed out after 600 s and was
killed. The digest still printed "YES (the listing said MAYBE) | 95", the day's header still
led with it as the second keeper, and no write-up for it exists anywhere. A verdict escaped
without the evidence that is supposed to produce it, because the verdict was decided in one
place and the summary in another and nothing joined them up.

So: every video gets one record with an explicit summary state, and exactly one function
turns a record into what the reader sees. final_verdict is pure. It reads no file, asks no
clock and calls no model, which is why every state and every transition can be checked here
by construction rather than by running the tool.

Against a core.py without final_verdict it cannot report failures: it stops on the first
line with "module has no attribute 'final_verdict'", which is the honest shape of "the
function did not exist". Everything runs in a fresh folder under the OS temp folder, with
the invented example person's profile.
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "records")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


# The seen.json and rotation.json of a machine that ran before the record store existed,
# invented, in the shapes the tool wrote then: 320 done entries and one pending entry from
# before 2026-09-20, which has no confidence. The store must load beside them and never write
# to seen.json; section G loads both through the tool's own readers.
OLD_SEEN = {f"MaraOld{n:04d}": {"date": "2026-09-19", "verdict": "MAYBE",
                                "title": f"Old video {n}"} for n in range(320)}
OLD_SEEN["MaraPend001"] = {"date": "2026-09-19", "verdict": "pending", "title": "t",
                           "channel": "c", "url": "u", "pending_verdict": "YES",
                           "pending_reason": "r", "retry_count": 1,
                           "note": "captions not ready yet"}
ww.SEEN_PATH.write_text(json.dumps(OLD_SEEN, indent=2), encoding="utf-8")
OLD_ROTATION = {"day": "2026-09-19", "index": 4, "topics_index": 2,
                "scanned_today": ["https://www.youtube.com/@example-one"]}
ww.ROTATION_PATH.write_text(json.dumps(OLD_ROTATION, indent=2), encoding="utf-8")
SEEN_BEFORE = ww.SEEN_PATH.read_bytes()


def rec(**over):
    """A record with every field the function reads, so a test says what it changes."""
    base = {"id": "abcdefghijk", "url": "https://www.youtube.com/watch?v=abcdefghijk",
            "title": "A video", "channel": "A channel", "source": "topics",
            "day": "2026-09-21", "listing_verdict": "MAYBE", "listing_reason": "thin",
            "listing_confidence": 10, "read_verdict": None, "read_reason": None,
            "read_confidence": None, "summary_state": "WAITING", "state_reason": "",
            "reason_code": "rolled", "attempts": 0, "terminal": False, "applies": None,
            "project": None, "takeaway": None, "decision_hit": None}
    base.update(over)
    return base


# 2026-09-25, fix 4: a keeper carries its action, section 8, because a YES without one is a
# MAYBE now. Every keeper fixture in this file gains the line, and section C checks the other
# half.
WRITTEN_KEEP = rec(summary_state="WRITTEN", reason_code="written", read_verdict="YES",
                   applies="items", project="Ledgerleaf", takeaway="Do the thing.",
                   try_this_week="Do the thing.")

# ==========================================================================================
print("=== A. the store: one record per video, updated in place, in its own file ===")
ww.update_record("abcdefghijk", title="A video", listing_verdict="MAYBE",
                 summary_state="WAITING", reason_code="rolled", day="2026-09-21")
ww.update_record("abcdefghijk", summary_state="WRITTEN", reason_code="written",
                 read_verdict="YES", project="Ledgerleaf", applies="items")
store = ww.load_records()
check(list(store) == ["abcdefghijk"], "two updates to one video leave one record, not two")
check(store["abcdefghijk"]["title"] == "A video",
      "a field the second update did not mention is still there")
check(store["abcdefghijk"]["summary_state"] == "WRITTEN", "and the field it did mention moved")
check(store["abcdefghijk"].get("created") and store["abcdefghijk"].get("updated"),
      "the record carries when it was created and when it last changed")
ww.update_record("bbbbbbbbbbb", title="Another", summary_state="WAITING", reason_code="rolled")
check(len(ww.load_records()) == 2, "a second video is a second record")
check(len(ww.todays_records()) == len([r for r in ww.load_records().values()
                                        if r.get("day") == ww.today_key()]),
      "todays_records is the records this day's runs touched")
check(ww.SEEN_PATH.read_bytes() == SEEN_BEFORE,
      "and seen.json is untouched, byte for byte, by any of it")

# ==========================================================================================
print("\n=== B. THE DEFECT. a record that was never written can never display a keep ===")
timed_out = rec(summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout",
                read_verdict="YES", read_confidence=95, attempts=1,
                state_reason="summary timed out after 600 s")
verdict, state_text, tags = ww.final_verdict(timed_out)
check(verdict == "NOT WRITTEN",
      f"the timed-out talk displays NOT WRITTEN, not YES (got {verdict!r})")
check("read as YES" in state_text, f"and keeps the transcript verdict as history: {state_text!r}")
check("600" in state_text and "attempt 1 of 3" in state_text,
      f"with the reason and the attempt count: {state_text!r}")
check("header" not in tags, "it cannot enter the header")
check("kept" not in tags, "and it is not counted as a keep")
check("row" in tags and "read_not_written" in tags, "it does get a row, and its own count")

print("\n  and not for one state only: every state that is not WRITTEN, for every verdict")
for state, code in (("READ_NOT_WRITTEN", "summary_timeout"), ("READ_NOT_WRITTEN", "summary_error"),
                    ("READ_NOT_WRITTEN", "no_run_time"),
                    ("READ_NOT_WRITTEN", "too_long_for_the_ceiling"),
                    ("NOT_READ", "over_ceiling"), ("NOT_READ", "no_transcript"),
                    ("NOT_READ", "transcribe_failed"), ("NOT_READ", "screened_out"),
                    ("NOT_READ", "too_old"), ("WAITING", "rolled"),
                    ("WAITING", "refused_429"), ("WAITING", "captions_not_ready")):
    for read_verdict in ("YES", "OPPORTUNITY"):
        v, _t, tg = ww.final_verdict(rec(summary_state=state, reason_code=code,
                                          read_verdict=read_verdict, project="Ledgerleaf",
                                          applies="items"))
        check(v not in ("YES", "OPPORTUNITY") and "header" not in tg and "kept" not in tg,
              f"{state}/{code} read as {read_verdict} displays {v!r}, no header, no keep")

# ==========================================================================================
print("\n=== C. CONTROL: a written record with a project is still a keep ===")
for read_verdict in ("YES", "OPPORTUNITY"):
    v, t, tg = ww.final_verdict(rec(summary_state="WRITTEN", reason_code="written",
                                     read_verdict=read_verdict, applies="items",
                                     project="Ledgerleaf", try_this_week="Do the thing."))
    check(v == read_verdict and "header" in tg and "kept" in tg and "row" in tg,
          f"WRITTEN {read_verdict} with a project is {v!r}, in the header, counted as a keep")
    check(t == "", "and says nothing extra about its state, because there is nothing to say")
v, t, tg = ww.final_verdict(rec(summary_state="WRITTEN", reason_code="written",
                                 read_verdict="YES", applies="items", project="Ledgerleaf"))
check(v == "MAYBE" and "kept" not in tg and "names no action" in t,
      f"and fix 4's half: the same YES with no action to take is a MAYBE that says so ({t})")

# ==========================================================================================
print("\n=== D. section 6 decides a written keep, and missing counts as nothing ===")
v, t, tg = ww.final_verdict(rec(summary_state="WRITTEN", reason_code="written",
                                 read_verdict="YES", applies="nothing", project=None))
check(v == "MAYBE" and "demoted" in tg, "summary present but section 6 says nothing: demoted")
check("nothing" in t or "no project" in t, f"and says why: {t!r}")
v, t, tg = ww.final_verdict(rec(summary_state="WRITTEN", reason_code="written",
                                 read_verdict="YES", applies="missing", project=None))
check(v == "MAYBE" and "demoted" in tg,
      "state WRITTEN but section 6 missing: demoted too, because missing counts as nothing")
check("missing" in t, f"and says which of the two it was: {t!r}")
v, _t, tg = ww.final_verdict(rec(summary_state="WRITTEN", reason_code="written",
                                  read_verdict="MAYBE", applies="nothing"))
check(v == "MAYBE" and "demoted" not in tg,
      "CONTROL: a MAYBE with nothing applying is not demoted, because nothing is ever promoted")
v, _t, tg = ww.final_verdict(rec(summary_state="WRITTEN", reason_code="written",
                                  read_verdict="NO", applies="items", project="Ledgerleaf"))
check(v == "NO" and "read_and_skipped" in tg and "kept" not in tg,
      "CONTROL: a NO after reading is read and skipped, whatever section 6 said")

# ==========================================================================================
print("\n=== E. the listing verdict survives as a guess, and never as a judgment ===")
v, t, tg = ww.final_verdict(rec(summary_state="NOT_READ", reason_code="over_ceiling",
                                 state_reason="audio 67.0 MB over the 63 MB ceiling"))
check(v == "NOT READ" and "67.0 MB" in t, f"over the byte ceiling: {v!r} ({t!r})")
check("guess" in tg, "and the listing verdict on that row is tagged a guess")
check("row" in tg and "not_read" in tg, "it gets a row and the not-read count")
v, _t, tg = ww.final_verdict(rec(summary_state="WAITING", reason_code="rolled"))
check(v == "WAITING" and "row" not in tg and "waiting" in tg,
      "rolled to the next run: no row yet, counted as waiting")
v, _t, tg = ww.final_verdict(rec(summary_state="NOT_READ", reason_code="too_old",
                                  state_reason="uploaded 2023-09-22, older than two years"))
check("age_filtered" in tg and "row" not in tg,
      "age-filtered: no row, its own line, and counted in none of the five")
check(ww.count_bucket(tg) is None, "which is what counting in none of the five means")

# ==========================================================================================
print("\n=== F. every record falls in exactly one count, and the vocabulary is closed ===")
seen_buckets = []
for code, (state, _row, _bucket) in ww.REASON_CODES.items():
    # Both shapes of a written record, or the loop never reaches "read and skipped" and the
    # control below would be the only thing to notice.
    for read_verdict, applies, project in (("YES", "items", "Ledgerleaf"),
                                            ("MAYBE", "nothing", None)):
        r = rec(summary_state=state, reason_code=code, read_verdict=read_verdict,
                applies=applies, project=project,
                try_this_week="Do the thing." if project else None)
        _v, _t, tg = ww.final_verdict(r)
        in_counts = [c for c in ww.COUNT_TAGS if c in tg]
        check(len(in_counts) <= 1, f"{code}/{read_verdict}: falls in at most one count ({in_counts})")
        # 2026-09-25: the topic filter's drop is the age filter's twin (fix 3), so it joins
        # no count either.
        check(code in ("too_old", "off_topic") or len(in_counts) == 1,
              f"{code}/{read_verdict}: and in one unless it is age-filtered or off topic")
        check(tg <= ww.VERDICT_TAGS,
              f"{code}/{read_verdict}: every tag is in the closed vocabulary ({sorted(tg)})")
        seen_buckets += in_counts
check(set(seen_buckets) == set(ww.COUNT_TAGS),
      f"CONTROL: all five counts are reachable, not just the easy ones ({sorted(set(seen_buckets))})")

print("\n  a state or a code nothing knows must fail loudly, never pass quietly")
for bad in (rec(summary_state="FINISHED", reason_code="written"),
            rec(summary_state="WRITTEN", reason_code="banana")):
    try:
        ww.final_verdict(bad)
        check(False, f"an unknown {bad['summary_state']}/{bad['reason_code']} was accepted")
    except ValueError as e:
        check(True, f"unknown state or code raises rather than guessing: {str(e)[:60]}")

# ==========================================================================================
print("\n=== G. yesterday's data still loads, under the new schema ===")
OLD_HISTORY_ROW = json.loads(
    '{"date": "2026-09-19", "verdict": "YES", "channel": "c", "title": "t", '
    '"url": "https://www.youtube.com/watch?v=MaraVid0002", "reason": "a mechanism", '
    '"summary": null, "backend": null, "try_this_week": null, "source": "channels"}')
check("confidence" not in OLD_HISTORY_ROW and OLD_HISTORY_ROW.get("confidence") is None,
      "a history row from before 2026-09-20 has no confidence and reads as None")
built = ww.record_from_history(OLD_HISTORY_ROW)
check(built["listing_verdict"] in ("YES", None) and built["summary_state"] in ww.SUMMARY_STATES,
      f"and a record can still be built from it: state {built['summary_state']}")
OLD_PENDING = {"date": "2026-09-19", "verdict": "pending", "title": "t", "channel": "c",
               "url": "u", "pending_verdict": "YES", "pending_reason": "r", "retry_count": 1,
               "note": "captions not ready yet"}
check(OLD_PENDING.get("pending_confidence") is None and OLD_PENDING["pending_verdict"] == "YES",
      "a pending seen.json entry from before 2026-09-20 still says what it always said")
old_seen = ww.load_seen()
check(len(old_seen) > 300 and old_seen == OLD_SEEN,
      f"and a seen.json in yesterday's shape loads as it is ({len(old_seen)} entries)")
old_rot = ww.load_rotation()
check({"day", "index", "scanned_today", "topics_index"} <= set(old_rot),
      "a rotation.json in yesterday's shape loads with every key the rotation reads")
check(ww.load_records() and isinstance(ww.load_records(), dict),
      "and the new store is a dict keyed by video id, in a file of its own")
missing = ww.DATA_DIR / "not-there.json"
missing.unlink(missing_ok=True)
ww.RECORDS_PATH, keep = missing, ww.RECORDS_PATH
check(ww.load_records() == {} and ww.todays_records() == [],
      "a machine with no records.json reads as empty rather than raising")
ww.RECORDS_PATH = keep

# ==========================================================================================
print("\n=== H. the timed-out talk, said the way a person reads it ===")
v, t, _tg = ww.final_verdict(rec(
    summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout", read_verdict="YES",
    read_confidence=95, attempts=1, title="Small Shop Pricing Talk",
    state_reason="summary timed out after 600 s"))
line = f"{v} ({t})"
check(line == "NOT WRITTEN (read as YES; summary timed out after 600 s, attempt 1 of 3)",
      f"the row reads: {line}")

# ==========================================================================================
print("\n=== I. FIX 2, 2026-09-22. once written, a row says what is true now ===")
# The timed-out talk's record as records.json would hold it on the evening of 2026-09-22,
# after the retry of 09:02 had written it up in 122.6 s, plus the failure history this fix
# keeps. Its row in that day's digest still ended "[summarize failed: claude CLI timed out
# after 600s and was killed.]", as if the failure were the current state.
RETRIED = rec(
    id="MaraVid0001", title="Small Shop Pricing Talk: Beyond Spreadsheets: How Two People "
                          "Price a Bookkeeping Tool for Bakeries",
    channel="Small Shop Talks", day="2026-09-22", listing_verdict="MAYBE",
    listing_reason="Description missing prevents identifying any copyable mechanism or numbers.",
    listing_confidence=10, read_verdict="YES", settled_verdict="MAYBE", read_confidence=95,
    read_reason=("Mara can copy the onboarding checklist to win Ledgerleaf bakeries "
                 "[copy: Send a three-email welcome sequence from the bank-export page] "
                 "[listing said MAYBE: Description missing prevents identifying any copyable "
                 "mechanism or numbers.] [summarize failed: claude CLI timed out after 600s "
                 "and was killed.]"),
    summary_state="WRITTEN", reason_code="written", state_reason="", attempts=1,
    applies="nothing", project=None, read_day="2026-09-21", written_day="2026-09-22",
    written_attempt=2, summary_seconds=122.6,
    failures=[{"attempt": 1, "day": "2026-09-21", "code": "summary_timeout", "timeout_s": 600,
               "why": "summary timed out after 600 s"}])
v, t, tg = ww.final_verdict(RETRIED)
check(v == "MAYBE", f"the verdict is what the retry came to: {v!r}")
check(t == ("written on retry 2 of 3 on 2026-09-22; the first attempt hung past 600 s; "
            "demoted from YES: section 6 named no project"),
      f"and the state carries the history, decided here: {t!r}")
check({"row", "read_and_skipped", "demoted", "retried"} <= tg and "kept" not in tg,
      f"tagged as a retried write-up, read and skipped: {sorted(tg)}")
check(tg <= ww.VERDICT_TAGS, "every tag is in the closed vocabulary")
row = next((l for l in ww.day_table_lines([RETRIED]) if l.startswith("| MAYBE")), "")
check(row and "summarize failed" not in row and "timed out after 600s" not in row,
      "the row drops the failure tag, which is no longer true")
check("[written on retry 2 of 3 on 2026-09-22; the first attempt hung past 600 s; demoted "
      "from YES: section 6 named no project]" in row, f"and carries the note instead: {row[-150:]}")
check("[copy: Send a three-email welcome sequence" in row,
      "CONTROL: the rest of the reason stays; only the stale failure goes")

print("\n  'hung' is measured, not assumed, and the day is named only when it differs")
same_day = dict(RETRIED, read_day="2026-09-22")
check(ww.final_verdict(same_day)[1].startswith(
    "written on retry 2 of 3; the first attempt hung past 600 s"),
      f"read and written the same day: {ww.final_verdict(same_day)[1]!r}")
slow = dict(same_day, summary_seconds=700.0)
t_slow = ww.final_verdict(slow)[1]
check("hung" not in t_slow and "stopped at 600 s" in t_slow and "700 s" in t_slow,
      f"CONTROL: a success slower than the timeout that failed is not a hang: {t_slow!r}")
unknown = dict(same_day, summary_seconds=None)
check("hung" not in ww.final_verdict(unknown)[1] and "timed out after 600 s" in
      ww.final_verdict(unknown)[1], "and with no time recorded it only says it timed out")
two = dict(same_day, written_attempt=3, summary_seconds=90.0,
           failures=RETRIED["failures"] + [{"attempt": 2, "day": "2026-09-22",
                                            "code": "summary_timeout", "timeout_s": 1200,
                                            "why": "summary timed out after 1200 s"}])
check(ww.final_verdict(two)[1].startswith("written on retry 3 of 3; the first attempt hung "
                                          "past 600 s; the second hung past 1200 s"),
      f"two failures, both said: {ww.final_verdict(two)[1]!r}")
errored = dict(same_day, failures=[{"attempt": 1, "day": "2026-09-22", "code": "summary_error",
                                    "timeout_s": None,
                                    "why": "summary failed: claude CLI failed (exit 1): busy"}])
check(ww.final_verdict(errored)[1].startswith(
    "written on retry 2 of 3; the first attempt failed: claude CLI failed (exit 1): busy"),
      f"a failure that was not a timeout says what it was: {ww.final_verdict(errored)[1]!r}")
keeper = dict(same_day, applies="items", project="Ledgerleaf", settled_verdict="YES",
              try_this_week="Do the thing.")
v, t, tg = ww.final_verdict(keeper)
check(v == "YES" and "header" in tg and "kept" in tg and "retried" in tg
      and t == "written on retry 2 of 3; the first attempt hung past 600 s",
      f"a keeper written on retry is still a keeper, and says it was a retry: {v} ({t})")
first_time = rec(summary_state="WRITTEN", reason_code="written", read_verdict="YES",
                 applies="items", project="Ledgerleaf", written_attempt=1,
                 try_this_week="Do the thing.")
v, t, tg = ww.final_verdict(first_time)
check(v == "YES" and t == "" and "retried" not in tg,
      "CONTROL: a write-up on the first attempt carries no note at all")

print("\n  and the store keeps the history the note is made from")
ww.RECORDS_PATH.unlink(missing_ok=True)
ww.update_record("MaraVid0001", title="Small Shop Pricing Talk", read_verdict="YES",
                 listing_verdict="MAYBE", summary_state="READ_NOT_WRITTEN",
                 reason_code="summary_timeout", attempts=0)
ww.summary_failed("MaraVid0001", RuntimeError("claude CLI timed out after 600s and was killed."),
                  attempts=1)
stored = ww.load_records()["MaraVid0001"]
check(stored.get("failures") == [{"attempt": 1, "day": ww.today_key(), "code": "summary_timeout",
                                  "timeout_s": 600, "why": "summary timed out after 600 s"}],
      f"summary_failed records the failure: {stored.get('failures')}")
ww.summary_written("MaraVid0001", "## 6. What applies to me\n\nnothing\n", "MAYBE", attempt=2,
                   seconds=122.6)
stored = ww.load_records()["MaraVid0001"]
check(len(stored.get("failures") or []) == 1 and stored["summary_state"] == "WRITTEN",
      "and writing it up keeps that history rather than wiping it")
check(ww.final_verdict(stored)[1].startswith("written on retry 2 of 3; the first attempt hung "
                                             "past 600 s"),
      f"so the note comes from the store, not from a fixture: {ww.final_verdict(stored)[1]!r}")

ww.RECORDS_PATH.unlink(missing_ok=True)
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
