"""FIX 7. Measure the first guess: how often the listing verdict survives being read.

Of the videos read on 2026-09-21, the listing's verdict was reversed after reading in all
but one. Nobody could see that without counting by hand, and it is the number that decides
whether the listing verdict is worth a column in the table at all.

The measurement is one four-way table: the listing said YES or MAYBE, the transcript read
as YES or MAYBE. Only videos that were actually read are in it, which is the part that is
easy to get wrong: three of today's seventeen rows were never fetched, and their "verdict"
is the listing's guess sitting unchallenged. Counting those as agreement would make the
tool look right about videos nobody read.

The triage itself is not touched by any of this.

Everything runs in a fresh folder under the OS temp folder, with the invented example
person's profile. Section B counts a whole day of history.jsonl rows, invented with the shape
of the day that set the number (see DAY_HISTORY). No YouTube request, no model call.
"""
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "firstguess")


def _history_rows():
    """The day of 2026-09-21 as history.jsonl rows, invented with the shape of the real one:
    seventeen videos, fourteen read and three over the byte ceiling and never fetched. Of the
    fourteen, thirteen went from a listing MAYBE to a read YES (eleven written, one written and
    then demoted to MAYBE, one whose summary timed out) and one stayed MAYBE; no listing said
    YES. Rows of the days either side are there too, so the day has to be picked out."""
    def row(n, verdict, reason, summary, date="2026-09-21"):
        return {"date": date, "verdict": verdict, "channel": "Bakery Owners Weekly",
                "title": f"Day video {n}", "url": f"https://www.youtube.com/watch?v=MaraDay{n:04d}",
                "reason": reason, "summary": summary, "backend": "claude:sonnet",
                "try_this_week": None, "source": "topics", "confidence": 95}
    written = "## 1. What it is\nA talk.\n\n## 6. What applies to me\n\nnothing\n"
    listing = "[listing said MAYBE: Description missing prevents identifying any mechanism.]"
    rows = [row(n, "YES", f"It teaches a mechanism {listing}", written) for n in range(1, 12)]
    rows.append(row(12, "MAYBE", f"It teaches a mechanism {listing} [demoted from YES: section 6 "
                                 f"named no project]", written))
    rows.append(row(13, "YES", f"It teaches a mechanism {listing} [summarize failed: claude CLI "
                               f"timed out after 600s and was killed.]", None))
    rows.append(row(14, "MAYBE", f"Nothing to copy here {listing}", written))
    rows += [row(n, "MAYBE", "Description provides no mechanism [not fetched: audio 67.0 MB over "
                             "the 63 MB ceiling]", None) for n in range(15, 18)]
    rows += [row(n, "YES", "From another day [listing said YES: a clear mechanism]", written,
                 date=day) for n, day in ((18, "2026-09-20"), (19, "2026-09-22"))]
    return rows


DAY_HISTORY = "\n".join(json.dumps(r) for r in _history_rows()) + "\n"

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def reset():
    for f in list(ww.DATA_DIR.glob("*.json*")):
        f.unlink()


def rec(vid, listing, read, state="WRITTEN", code="written", **over):
    fields = {"url": f"https://www.youtube.com/watch?v={vid}", "title": f"Video {vid}",
              "channel": "A channel", "listing_verdict": listing, "read_verdict": read,
              "summary_state": state, "reason_code": code, "applies": "items",
              "project": "Ledgerleaf", "takeaway": "Do the thing. Applies to: Ledgerleaf"}
    fields.update(over)
    return ww.update_record(vid, **fields)


# ==========================================================================================
print("=== A. only the videos that were read are counted ===")
reset()
rec("aaaaaaaaaaa", "MAYBE", "YES")
rec("bbbbbbbbbbb", "MAYBE", "MAYBE")
rec("ccccccccccc", "YES", "YES")
rec("ddddddddddd", "YES", "MAYBE")
counts = ww.first_guess_counts(ww.todays_records())
check(counts["read"] == 4, f"four read videos: {counts['read']}")
check(counts["agreed"] == 2, f"two of them agreed: {counts['agreed']}")
check(counts["table"][("MAYBE", "YES")] == 1 and counts["table"][("YES", "MAYBE")] == 1,
      "and the four-way table has each pair once")

rec("eeeeeeeeeee", "MAYBE", None, state="NOT_READ", code="over_ceiling",
    state_reason="audio 67.0 MB over the 63 MB ceiling", project=None, applies=None)
rec("fffffffffff", "MAYBE", None, state="WAITING", code="rolled", project=None, applies=None)
counts = ww.first_guess_counts(ww.todays_records())
check(counts["read"] == 4,
      f"a video nobody read is in neither half of the fraction: still {counts['read']}")
check(counts["never_read"] == 2, f"it is counted separately, and named: {counts['never_read']}")
check(counts["agreed"] == 2, "so the agreement number cannot be inflated by silence")

print("\n  a verdict outside the four squares is reported, not dropped")
rec("ggggggggggg", "MAYBE", "OPPORTUNITY")
rec("hhhhhhhhhhh", "MAYBE", "NO")
counts = ww.first_guess_counts(ww.todays_records())
check(counts["read"] == 6, f"both are still read videos: {counts['read']}")
check(counts["outside"] == 2,
      f"and the two that fall outside YES-or-MAYBE are counted as such: {counts['outside']}")
check(sum(counts["table"].values()) + counts["outside"] == counts["read"],
      "the four squares plus the outsiders account for every read video")

# ==========================================================================================
print("\n=== B. A WHOLE DAY, from history.jsonl rows rather than from records ===")
rows = [json.loads(l) for l in DAY_HISTORY.splitlines() if l.strip()]
today_rows = [r for r in rows if r.get("date") == "2026-09-21"]
records = [ww.record_from_history(r) for r in today_rows]
records = [r for r in records if r.get("id")]
check(len(records) == 17, f"the day has 17 rows in history: {len(records)}")
counts = ww.first_guess_counts(records)
check(counts["read"] == 14,
      f"14 of them were read; the other 3 were over the byte ceiling: {counts['read']}")
check(counts["agreed"] == 1, f"and the first guess survived reading exactly once: "
                              f"{counts['agreed']} of {counts['read']}")
check(counts["table"][("MAYBE", "YES")] == 13,
      f"13 went from MAYBE to YES: {counts['table'][('MAYBE', 'YES')]}")
check(counts["table"][("MAYBE", "MAYBE")] == 1, "and one stayed MAYBE")
check(counts["table"][("YES", "YES")] == 0 and counts["table"][("YES", "MAYBE")] == 0,
      "no video today was a YES from the listing, so the top row of the table is empty")
print(f"  today reads: {ww.first_guess_line(records)}")

# ==========================================================================================
print("\n=== C. 2026-09-25, decision A: the listing's verdict leaves the digest ===")
# The week's numbers made the decision: the first guess agreed with the read verdict 12 times
# in 69, it said MAYBE for 67 of the 69, and it said NO to none of the week's 117 listings, so
# it decided nothing about what got read. The footer and every "the listing said" go. The
# measurement itself stays, for worthwatch status (section D).
reset()
# The digest folder is emptied too: reset() clears only the JSON stores, and a digest left by
# an earlier run of this file keeps the run sections that run appended, whatever they said.
if ww.OUTPUT_DIR.exists():
    for f in ww.OUTPUT_DIR.glob("DIGEST-*.md"):
        f.unlink()
for n in range(3):
    rec(f"foot{n:07d}", "MAYBE", "YES", try_this_week="Do the thing.")
rec("agree000000", "YES", "YES", try_this_week="Do the thing.")
ww.todays_totals = lambda: (4, 4, 0)
path = ww.write_digest([], [], [], label="channels")
text = path.read_text(encoding="utf-8")
check("<!-- FOOTER-START -->" not in text and "First guess" not in text,
      "no footer, and no first-guess table")
check("the listing said" not in text and "listing guessed" not in text,
      "and no row says what the listing said or guessed")
path.write_text(text + "<!-- FOOTER-START -->\n## First guess vs after reading\n\nold\n"
                "<!-- FOOTER-END -->\n", encoding="utf-8")
ww.refresh_digest(ww.today_key())
text = path.read_text(encoding="utf-8")
check("<!-- FOOTER-START -->" not in text,
      "a footer an older run left in the file is removed when the day is regenerated")
counts = ww.first_guess_counts(ww.todays_records())
check(counts["read"] == 4 and counts["agreed"] == 1,
      "CONTROL: the measurement itself still counts, for worthwatch status")
rec("moved000000", "MAYBE", "MAYBE")

print("\n  what decides whether a video is read stays: a listing NO is screened out, and said")
screened = [{"verdict": "NO", "channel": "C", "title": "A listing NO", "reason": "off topic",
             "url": "https://www.youtube.com/watch?v=listingno01", "most_useful": None,
             "fetched": False, "id": "listingno01"},
            {"verdict": "NO", "channel": "C", "title": "Read then dropped", "reason": "empty",
             "url": "https://www.youtube.com/watch?v=readnodrop1", "most_useful": "nothing",
             "fetched": True, "listing_verdict": "MAYBE"}]
path = ww.write_digest([], screened, [], label="topics")
text = path.read_text(encoding="utf-8")
check("### Screened out (NO)" in text and "A listing NO" in text,
      "CONTROL: the Screened out section still names a listing NO, which is never fetched")
check("Read then dropped" in text and "(read, then dropped)" in text
      and "the listing said" not in text,
      "and a video read and then dropped says so, without the listing's verdict")
from worthwatch import scoring as _dr   # the digest reader the golden tooling uses
_rows = {r["title"]: r for r in _dr.rows_in_digest(path)}
check(_rows.get("Read then dropped", {}).get("fetched") is True
      and _rows.get("A listing NO", {}).get("fetched") is False,
      "and the digest reader still tells the read one from the one never fetched")

# ==========================================================================================
print("\n=== D. one line in worthwatch status, reading the same numbers ===")
buf, real = io.StringIO(), sys.stdout
ww.ejs_status = lambda: (True, "stubbed")
# The old behaviour this section was written under: a canary seed list that is never empty.
# CANARY_SEED_VIDEO_IDS is [] by default now, and with nothing transcribed yet the canary pool
# is empty and cmd_status stops in pick_canary_video (min() of nothing). This section is about
# the First guess line, so it sets one invented seed and leaves that crash to its own fix.
ww.CANARY_SEED_VIDEO_IDS = ["MaraSeed001"]
ww.read_channel_urls = lambda: ["https://www.youtube.com/@one"]
import types
ww.CHANNELS_PATH = types.SimpleNamespace(exists=lambda: True)
sys.stdout = buf
try:
    ww.cmd_status()
finally:
    sys.stdout = real
status = buf.getvalue()
line = [l for l in status.splitlines() if "First guess" in l]
check(len(line) == 1, f"exactly one line about it: {len(line)}")
check("agreed 2 of 5" in line[0], f"and it is the day's number: {line[0].strip()}")
check("week" in line[0].lower(), f"with the week beside it, which is the one that decides")

# ==========================================================================================
print("\n=== E. the triage itself is untouched ===")
src = Path(ww.__file__).read_text(encoding="utf-8")
head = src[src.index("TRIAGE_INSTRUCTIONS = "):src.index("TRIAGE_STAGE_LISTING")]
check("first guess" not in head.lower() and "agreed" not in head.lower(),
      "no word of this measurement is in the triage prompt")
check(src.count("def triage_video(") == 1, "and triage_video is still one function")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
