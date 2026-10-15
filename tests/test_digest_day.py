"""FIX 4. One row per video per day, and a row that is true about what happened to it.

What the digest of 2026-09-21 did instead. One video was never fetched, because its
smallest audio was 67.0 MB against a 63 MB ceiling, and its row read
"MAYBE | 10 | Description provides no mechanism" as though somebody had judged it. Seven
candidates rolled to the next run and three results were dropped on age, and none of that
appeared anywhere except a log line. The header folded the lot into "11 skipped".

So: the day's table is regenerated from the records on every run, every cell of it comes
from final_verdict, and the counts under it name what actually happened.

  a video that will never be read      gets a row that says so, with its listing verdict
                                       shown as the guess it is
  a video that is going to be tried     gets no row yet, one line under the table, and its
  again                                row appears in the run that reads it
  an age-filtered result               gets one line with its upload date, and is in no
                                       count: it was dropped before anything read it

Everything runs in a fresh folder under the OS temp folder, with the invented example
person's profile. No YouTube request, no model call.
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
TMP = _tmpsandbox.sandbox(ww, "digest")
from worthwatch import scoring as dr   # the digest reader the golden tooling uses

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def reset():
    for f in list(ww.DATA_DIR.glob("*.json*")):
        f.unlink()
    if ww.OUTPUT_DIR.exists():
        for f in ww.OUTPUT_DIR.glob("*.md"):
            f.unlink()


def put(vid, **fields):
    fields.setdefault("url", f"https://www.youtube.com/watch?v={vid}")
    fields.setdefault("title", f"Video {vid}")
    fields.setdefault("channel", "A channel")
    fields.setdefault("listing_verdict", "MAYBE")
    fields.setdefault("listing_reason", "Description provides no mechanism or numbers.")
    fields.setdefault("listing_confidence", 10)
    return ww.update_record(vid, **fields)


# The day of 2026-09-21, as the records would have it: five keeps, eight read and skipped,
# one read and never written, three never read, seventeen waiting, ten age-filtered.
def build_the_day():
    reset()
    for n in range(5):
        put(f"keep{n:07d}", title=f"Keeper {n}", read_verdict="YES", read_confidence=95,
            summary_state="WRITTEN", reason_code="written", applies="items",
            project="Ledgerleaf", takeaway="Do the specific thing.",
            try_this_week="Do the specific thing.")   # 2026-09-25: a keeper has its action
    for n in range(8):
        put(f"skip{n:07d}", title=f"Read and skipped {n}", read_verdict="YES",
            read_confidence=95, summary_state="WRITTEN", reason_code="written",
            applies="nothing", project=None)
    put("MaraVid0001", title="Small Shop Pricing Talk", read_verdict="YES", read_confidence=95,
        summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout", attempts=1,
        state_reason="summary timed out after 600 s")
    put("MaraVid0003", title="Bakery books from the bottom up", summary_state="NOT_READ",
        reason_code="over_ceiling",
        state_reason="audio 67.0 MB over the 63 MB ceiling")
    put("MaraVid0004", title="A market stall founder", summary_state="NOT_READ",
        reason_code="over_ceiling", state_reason="audio 79.1 MB over the 63 MB ceiling")
    put("MaraVid0005", title="Newsletter Sponsorship Workshop", summary_state="NOT_READ",
        reason_code="over_ceiling", state_reason="audio 122.3 MB over the 63 MB ceiling")
    for n in range(17):
        put(f"wait{n:07d}", title=f"Rolled {n}", summary_state="WAITING",
            reason_code="rolled", state_reason="rolled to the next run; nothing spent on it")
    # Fix 6, 2026-09-22: with the moment they were listed, 10:00 on 2026-09-21 local, which
    # is what makes 2023-09-22 a date yt-dlp computed from "3 years ago" rather than a date.
    listed = dt.datetime(2026, 9, 21, 17, 0, tzinfo=dt.timezone.utc).astimezone()
    for n in range(10):
        put(f"old{n:08d}", title=f"Too old {n}", summary_state="NOT_READ",
            reason_code="too_old", upload_date="20230922",
            created=listed.replace(tzinfo=None).isoformat(timespec="seconds"),
            state_reason="uploaded 2023-09-22, older than two years")


# ==========================================================================================
print("=== A. the five counts, and what each of them means ===")
build_the_day()
counts = ww.day_counts(ww.todays_records())
check(counts["kept"] == 5, f"kept: {counts['kept']}")
check(counts["read_and_skipped"] == 8, f"read and skipped: {counts['read_and_skipped']}")
check(counts["read_not_written"] == 1, f"read but not written: {counts['read_not_written']}")
check(counts["not_read"] == 3, f"not read: {counts['not_read']}")
check(counts["waiting"] == 17, f"waiting: {counts['waiting']}")
line = ww.day_count_line(ww.todays_records())
check(line.startswith("5 kept, 8 read and skipped, 1 read but not written, 3 not read, "
                      "17 waiting"), f"the line reads: {line}")
under_table = "\n".join(l for l in ww.day_table_lines(ww.todays_records())
                        if not l.startswith("|"))
check("10" in under_table and "age" in under_table,
      "and the ten age-filtered results are named under the table, in none of the five, "
      "because they were dropped before anything read them")
check("age" not in line,
      "the count line itself stays the five numbers and nothing else (fix 5 moved it into "
      "the header, where four lines is the whole budget)")

print("\n  and every record lands in exactly one of them")
records = ww.todays_records()
total = sum(counts[c] for c in ww.COUNT_TAGS)
aged = sum(1 for r in records if "age_filtered" in ww.final_verdict(r)[2])
check(total + aged == len(records),
      f"{total} counted plus {aged} age-filtered is {len(records)} records")

# ==========================================================================================
print("\n=== B. THE INVARIANT. one row per video, and every row true to its record ===")
lines = ww.day_table_lines(ww.todays_records())
table = [l for l in lines if l.startswith("| ") and not l.startswith("| Verdict")
         and not l.startswith("|---")]
ids = [dr.video_id(m.group(1)) for m in
       (re.search(r"\]\((https?://[^)]+)\)", l) for l in table) if m]
check(len(ids) == len(set(ids)), f"no video has two rows ({len(ids)} rows, {len(set(ids))} ids)")
check(len(table) == 5 + 8 + 1 + 3,
      f"and the rows are exactly the videos that have been attempted ({len(table)})")
for vid in ("wait0000000", "old00000000"):
    check(vid not in "\n".join(table), f"{vid[:4]} has no row, which is the point")
by_id = {r["id"]: r for r in ww.todays_records()}
wrong = []
for row in table:
    m = re.search(r"\]\((https?://[^)]+)\)", row)
    rec = by_id[dr.video_id(m.group(1))]
    verdict, state_text, _tags = ww.final_verdict(rec)
    if verdict not in row:
        wrong.append(rec["id"])
check(not wrong, f"every row's verdict is its record's ({len(wrong)} disagree)")

# ==========================================================================================
print("\n=== C. a video that was never read does not look judged ===")
row = next(l for l in table if "Bakery books from the bottom up" in l)
check("NOT READ" in row, f"the row says NOT READ: {row[:76]}")
check("67.0 MB" in row and "63 MB ceiling" in row, "and why, in bytes")
# 2026-09-25, decision A: a row nobody read shows why it was not read and not what the listing
# guessed. These two checks are the inverse of the ones they replace, which asked for the
# guess to be shown and marked; decision B takes the guessed number off it too.
check("listing guessed" not in row.lower(),
      f"and the listing's guess is not on it any more: {row[:150]}")
check("MAYBE" not in row, "not the guessed verdict either")
# 2026-09-25, decision B: no confidence column, so no guessed number on a row nobody read.
# Confidence was 95 on 64 of the week's 69 read rows, 95.0 on average on the kept and 91.7 on
# the rest, so it separated nothing; the stores keep it for a later calibration.
check("guess" not in row.lower() and "| 10 |" not in row,
      f"and no guessed number either: {row[:150]}")
check("| Verdict | Channel | Title | Reason |" in "\n".join(lines)
      and "Confidence" not in "\n".join(lines),
      "the table has four columns, and none of them is confidence")
kept_row = next(l for l in table if "Keeper 0" in l)
check("guess" not in kept_row.lower(),
      "CONTROL: a video that WAS read carries no such mark")

print("\n  and the ones that were not attempted are named under the table, not hidden")
under = "\n".join(l for l in lines if not l.startswith("|"))
check("17" in under and "Rolled 0" in under,
      "the rolled candidates: the count and the titles, in one line under the table")
check("Too old 0 (about 3 years ago)" in under and "2023-09-22" not in under,
      "the age-filtered ones: the titles and their age as the listing gave it, never a date "
      "the listing only implied (fix 6, 2026-09-22)")
# 2026-09-25, fix 5: the line says what waiting means now, not "rolled to the next run".
check("nothing spent on them" in under and "returns it again" in under,
      "and it says what rolled means")

# ==========================================================================================
print("\n=== D. the row appears in the run that reads it, not before ===")
reset()
put("later000000", title="Read tomorrow", summary_state="WAITING", reason_code="rolled",
    state_reason="rolled to the next run; nothing spent on it")
rows = [l for l in ww.day_table_lines(ww.todays_records())
        if l.startswith("| ") and "Read tomorrow" in l]
check(not rows, "a waiting video has no row")
put("later000000", title="Read tomorrow", read_verdict="YES", summary_state="WRITTEN",
    reason_code="written", applies="items", project="Tidepool", takeaway="Do it.")
rows = [l for l in ww.day_table_lines(ww.todays_records())
        if l.startswith("| ") and "Read tomorrow" in l]
# The verdict cell, not the whole line: the reason text can say "demoted from YES" on a MAYBE.
_cell = rows[0].split("|")[1].strip() if rows else None
_want = ww.final_verdict(ww.load_records()["later000000"])[0]
check(len(rows) == 1 and _cell == _want,
      f"and exactly one once it is read, its verdict cell the record's final verdict "
      f"({_cell} vs {_want}): {rows[0][:60] if rows else 'none'}")

# ==========================================================================================
print("\n=== E. the digest file: one table, regenerated, never stacked ===")
build_the_day()
ww.todays_totals = lambda: (17, 6, 0)
path = ww.write_digest([], [], [], label="channels")
text = path.read_text(encoding="utf-8")
check(text.count("<!-- TABLE-START -->") == 1, "one table block")
check(text.count("<!-- HEADER-START -->") == 1, "one header block")
first = text
path = ww.write_digest([], [], [], label="topics")
text = path.read_text(encoding="utf-8")
check(text.count("<!-- TABLE-START -->") == 1,
      "a second run of the day replaces the table rather than adding one")
check(text.count("| Verdict |") == 1, "so the file holds one table header, not one per run")
before = re.search(r"<!-- TABLE-START -->.*?<!-- TABLE-END -->", first, re.S).group(0)
after = re.search(r"<!-- TABLE-START -->.*?<!-- TABLE-END -->", text, re.S).group(0)
check(before == after, "and the two builds are byte-identical, because nothing changed")

print("\n  the golden tooling still reads both shapes")
rows_new = dr.rows_in_digest(path)
check(len(rows_new) == 17, f"the new day table reads: {len(rows_new)} rows")
# A digest in the shape the tool wrote before the day table, invented: each run appended its
# own table, with a Confidence column and "(the listing said ...)" in the verdict cell, then its
# summaries and its Screened out list. Twelve videos in two runs, and the reader must find all.
OLD_RUNS = (
    ("09:00 (channels)",
     [("YES (the listing said MAYBE)", "95", "Bakery Owners Weekly", "Pricing a cake menu"),
      ("YES (the listing said MAYBE)", "95", "Bakery Owners Weekly", "Invoices a bakery forgets"),
      ("MAYBE", "60", "Small Shop Talks", "A day at a two-person software shop"),
      ("OPPORTUNITY (the listing said MAYBE)", "90", "Indie Game Letters", "Selling sponsor slots"),
      ("YES", "95", "Small Shop Talks", "How one founder found her first ten customers")],
     [("A cooking show clip", "Kitchen Clips", "off topic", ""),
      ("A long product tour", "Tool Tours", "nothing to copy", " (read; the listing said MAYBE)")]),
    ("10:00 (topics)",
     [("MAYBE", "40", "Indie Game Letters", "Ten newsletters compared"),
      ("YES (the listing said MAYBE)", "95", "Small Shop Talks", "A welcome email that sells"),
      ("NO", "80", "Tool Tours", "Unboxing a label printer"),
      ("MAYBE (the listing said YES)", "70", "Bakery Owners Weekly", "Opening a second oven")],
     [("A travel diary", "Road Clips", "off topic", "")]),
)


def old_digest_text():
    out, n = ["# Digest 2026-09-18", ""], 0
    for when, table, nos in OLD_RUNS:
        out += [f"## Run at {when}", "", f"New videos: {len(table) + len(nos)}", "",
                "| Verdict | Confidence | Channel | Title | Reason |", "|---|---|---|---|---|"]
        for verdict, confidence, channel, title in table:
            n += 1
            out.append(f"| {verdict} | {confidence} | {channel} | [{title}]"
                       f"(https://www.youtube.com/watch?v=MaraOld{n:04d}) | a reason |")
        first = table[0]
        out += ["", "### Summaries", "",
                f"#### [{first[0].split()[0]}] {first[3]} ({first[2]}, claude:sonnet, 98.2s)",
                "", "## 6. What applies to me", "", "nothing", "",
                f"### Screened out (NO) - {len(nos)}", ""]
        for title, channel, why, tail in nos:
            n += 1
            out.append(f"- [{title}](https://www.youtube.com/watch?v=MaraOld{n:04d}) ({channel})"
                       f" - {why}{tail}")
        out += ["", "---", ""]
    return "\n".join(out) + "\n"


old_digest = TMP / "before" / "DIGEST-2026-09-18.md"
old_digest.parent.mkdir(exist_ok=True)
old_digest.write_text(old_digest_text(), encoding="utf-8")
rows_old = dr.rows_in_digest(old_digest)
built = sum(len(table) + len(nos) for _when, table, nos in OLD_RUNS)
check(len(rows_old) > 10 and len(rows_old) == built,
      f"and a digest written before today still reads: {len(rows_old)} rows of {built}")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
