"""The day's header: the write-up's own sentence, never the listing's guess.

A header line is the verdict, the project, one whole sentence the write-up itself wrote (its
section 9 header, else its section 8 "Try this week"), and the video as a short title link.
Only videos that were written and kept are in it, ordered by the project order of your
settings, at most three keepers and a count line under them.

What it must never do: show a video that has no write-up, show the triage's guess at what to
copy (written from the title before anything was read), show section 6's entry (which opens
with the video's own quote, the evidence and not the point), or cut a sentence mid word.

All of it runs on the invented example person (src/worthwatch/examples) in a temp folder. No
YouTube request, no model call.

Watch it go red: make header_sentence return record.get("takeaway") in a copy of
src/worthwatch/core.py and run this file against the copy.
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "header")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def reset():
    for f in list(ww.RECORDS_PATH.parent.glob("*.json*")):
        f.unlink()


def keeper(vid, title, project, takeaway, action=None, **over):
    """A written keeper. `takeaway` is section 6's "Most applicable" entry and `action` is
    section 8, the write-up's own sentence of what to do this week. The header reads the action
    (or the write-up's section 9) and never section 6, so a keeper built without an action has
    no header sentence."""
    fields = {"url": f"https://www.youtube.com/watch?v={vid}", "title": title,
              "channel": "An example channel", "listing_verdict": "MAYBE",
              "listing_reason": "a reason [copy: the listing's own guess at what to copy]",
              "listing_confidence": 10, "read_verdict": "YES", "read_confidence": 95,
              "read_reason": "read as YES [copy: the listing's own guess at what to copy]",
              "summary_state": "WRITTEN", "reason_code": "written", "applies": "items",
              "project": project, "takeaway": takeaway, "try_this_week": action}
    fields.update(over)
    return ww.update_record(vid, **fields)


# Four invented keepers, each with section 6's entry and its own section 8 line.
KEEPERS = [
    ("MaraHdr0001", "Churn Interviews That Work | The Small Software Show", "Ledgerleaf",
     '"ask the ones who left before you ask the ones who stayed" - talk to churned bakeries '
     "first. Applies to: Ledgerleaf",
     "Email the three bakeries that churned last month and ask which report they missed."),
    ("MaraHdr0002", "Import Day: Testing a Bakery Bank Export", "Ledgerleaf",
     "Test every import against a real export. \"a total that is wrong by a cent is wrong\". "
     "Applies to: Ledgerleaf",
     "Record a two-minute demo that imports one real bank export from start to finish."),
    ("MaraHdr0003", "The Friday Ship: A Solo Developer's Week", "how I work",
     "Add a backup check to the morning checklist. It lists what must be true before a "
     "release. Applies to: how I work",
     "Add the backup check to the morning checklist before Friday's release."),
    ("MaraHdr0004", "Evals for Hobby Tools | How to Check a Classifier", "this tool",
     "Check the tool's verdicts against yours on a sample. Hand-label a batch of past videos. "
     "Applies to: this tool",
     "Label ten past digest videos with your own verdict before reading the tool's, and count "
     "the disagreements."),
]

# ==========================================================================================
print("=== A. the sentence is the write-up's own, never the listing's copy answer ===")
reset()
for vid, title, project, takeaway, action in KEEPERS[:1]:
    keeper(vid, title, project, takeaway, action)
lines = ww.todays_header_lines()
check(lines and lines[0].startswith("- **YES** "), f"a keeper leads: {lines[0][:60]}")
check("the listing's own guess" not in "\n".join(lines),
      "the [copy:] text from the listing is nowhere in the header")
check("Email the three bakeries that churned last month" in lines[0],
      f"and the sentence is the write-up's own action, section 8: {lines[0][20:110]}")
check("talk to churned bakeries first" not in lines[0],
      "and not section 6's entry, which is never the source")
check("Applies to:" not in lines[0], "without the label, which the reader does not need")
check('"' not in lines[0], "and without the quote, which is the evidence and not the point")

print("\n  and a video with no write-up is not here at all")
ww.update_record("MaraHdr0009", url="https://www.youtube.com/watch?v=MaraHdr0009",
                 title="A Talk With No Write-up", read_verdict="YES",
                 summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout", attempts=1,
                 state_reason="summary timed out after 600 s")
lines = ww.todays_header_lines()
check("A Talk With No Write-up" not in "\n".join(lines),
      "the video that was read but never written up is not in the header")
check("1 read but not written" in lines[-1],
      f"and is counted where it belongs instead: {lines[-1]}")

# ==========================================================================================
print("\n=== B. the order is your settings' project order ===")
reset()
# Put in backwards, with run times that say so: the day met the this-tool video first and
# the first Ledgerleaf video last.
for n, (vid, title, project, takeaway, action) in enumerate(reversed(KEEPERS)):
    keeper(vid, title, project, takeaway, action, created=f"2026-03-02T10:00:0{n}")
lines = ww.todays_header_lines()
check(len(lines) == 4, f"three keepers and a count line ({len(lines)})")
check("Import Day" in lines[0] and "Churn Interviews" in lines[1],
      "the two Ledgerleaf videos come first, in the order the day met them, which is the "
      "reverse of the order they are listed above")
check("The Friday Ship" in lines[2], "then how I work, the work label, which comes after your "
                                     "projects")
check("Evals for Hobby Tools" not in "\n".join(lines[:3]),
      "and this tool, which is last, is the one that does not fit")

check(ww.PROJECTS[:4] == ("Ledgerleaf", "Tidepool", "how I work", "this tool"),
      f"the list the header sorts by is the list the prompt offers: your projects, the work "
      f"label, this tool ({ww.PROJECTS[:4]})")
context = ww.CONTEXT_PATH.read_text(encoding="utf-8")
projects_section = context.split("## Projects and status today", 1)[-1].split("\n## ", 1)[0]
order = [projects_section.find(f"**{p}**") for p in ("Ledgerleaf", "Tidepool", "this tool")]
check(-1 not in order and order == sorted(order),
      f"and the context file lists the projects in that same order: {order}")

# ==========================================================================================
print("\n=== C. the cap, the counts and the plurals ===")
reset()
keeper("one00000000", "Only one", "Ledgerleaf", "Do the single thing. Applies to: Ledgerleaf",
       "Do the single thing.")
lines = ww.todays_header_lines()
check(len(lines) == 2 and "more kept below" not in lines[-1],
      f"one keeper, no 'more kept below': {lines[-1]}")
keeper("two00000000", "Second", "Tidepool", "Do the second thing. Applies to: Tidepool",
       "Do the second thing.")
keeper("thr00000000", "Third", "this tool", "Do the third thing. Applies to: this tool",
       "Do the third thing.")
keeper("fou00000000", "Fourth", "Ledgerleaf", "Do the fourth thing. Applies to: Ledgerleaf",
       "Do the fourth thing.")
lines = ww.todays_header_lines()
check(len(lines) == 4, f"four keepers still fit in four lines ({len(lines)})")
check("1 more kept below" in lines[-1], f"and the one it cannot show is counted: {lines[-1]}")
keeper("fiv00000000", "Fifth", "Tidepool", "Do the fifth thing. Applies to: Tidepool",
       "Do the fifth thing.")
lines = ww.todays_header_lines()
check(len(lines) == 4 and "2 more kept below" in lines[-1],
      f"five keepers, two more kept below, still four lines: {lines[-1]}")
check("2 more kept belows" not in lines[-1] and "1 more kept below." not in
      ww.todays_header_lines()[-1].replace("2 more kept below.", ""),
      "the plural is right at one and at two")
check("5 kept," in lines[-1], f"the count line names all five buckets: {lines[-1]}")

reset()
lines = ww.todays_header_lines()
check(len(lines) == 1 and "Nothing to keep today" in lines[0],
      f"a day with nothing kept is one line: {lines[0]}")

# ==========================================================================================
print("\n=== D. a whole sentence, never cut, and never mid word ===")
reset()
long_one = ("Import the bank exports of every bakery automatically, each night, before anyone "
            "opens the books, and then check every total against the bank's own statement. "
            "Applies to: Ledgerleaf")
long_action = ("Import one bakery's bank export into Ledgerleaf from the file the bank emails "
               "each month, before anyone opens the books, and then check every total in the "
               "report against the bank's own statement by hand.")
LONG_TITLE = ("Receipts to Reports: How Small Bakeries Close the Month in an Hour and Stop "
              "Losing Paper Receipts")
keeper("lng00000000", LONG_TITLE, "Ledgerleaf", long_one, long_action)
line = ww.todays_header_lines()[0]
# The line ends in a link, whose address a reader never sees, so the checks are on what is
# shown. There is no length bound on the sentence: a sentence cut to fit is a fragment, and the
# only part that gives is the title.
shown = re.sub(r"\]\([^)]*\)$", "]", line)
check(long_action in line, f"the long sentence is there whole ({len(long_action)} characters)")
words = re.findall(r"[A-Za-z']+", shown)
check(all(w in (long_action + " " + LONG_TITLE + " YES Ledgerleaf") for w in words),
      "every word shown is a whole word from the title or the sentence")
check(" :" not in shown and "Close the Mo" not in shown,
      "no title is cut mid word or left hanging on its separator")
sentence_part = shown.split(": ", 1)[1].rsplit(" [", 1)[0]
check(sentence_part.endswith("."), f"the sentence ends on a full stop: {sentence_part[-30:]!r}")
check(line.endswith(")") and "[Receipts to Reports](https://www.youtube.com/watch?v=" in line,
      f"and the line ends on the video as a short title link: {line[-60:]!r}")

check(ww.truncate_words("one two three four", 9) == "one two",
      f"the truncation itself: {ww.truncate_words('one two three four', 9)!r}")
check(ww.truncate_words("short", 40) == "short", "CONTROL: a short string is untouched")
check(ww.truncate_words("supercalifragilistic", 5) == "super",
      "and a single word longer than the budget is cut, because something must give")

print("\n  the first sentence, and only the first")
check(ww.first_sentence("One thing. Two thing. Three.") == "One thing.",
      "a plain sentence ends at its full stop")
check(ww.first_sentence('Do the thing. She said "no. really." and left.') == "Do the thing.",
      "a full stop inside quotes does not end the sentence before it")
check(ww.first_sentence("No full stop here") == "No full stop here",
      "CONTROL: text with no full stop comes back whole")
check(ww.first_sentence("") == "", "and empty is empty, not an exception")

# ==========================================================================================
print("\n=== E. regenerated from the records, and byte-identical twice ===")
reset()
for vid, title, project, takeaway, action in KEEPERS:
    keeper(vid, title, project, takeaway, action)
first = ww.todays_header_lines()
second = ww.todays_header_lines()
check(first == second, "two consecutive builds of the header are identical")
ww.todays_totals = lambda: (4, 4, 0)
path = ww.write_digest([], [], [], label="channels")
text_one = path.read_text(encoding="utf-8")
path = ww.write_digest([], [], [], label="topics")
text_two = path.read_text(encoding="utf-8")
block_one = re.search(r"<!-- HEADER-START -->.*?<!-- HEADER-END -->", text_one, re.S).group(0)
block_two = re.search(r"<!-- HEADER-START -->.*?<!-- HEADER-END -->", text_two, re.S).group(0)
check(block_one == block_two,
      "and two consecutive writes of the file leave byte-identical header blocks")
check(text_two.count("<!-- HEADER-START -->") == 1, "one header block, not two")
# One line about the context file's age goes first under the title of every digest; the
# four-line bound is the header's own lines, so it leaves that line out.
AGE_LINE = ("Context file last updated", "context file is missing")
content = [l for l in block_two.splitlines()
           if l.strip() and not l.startswith(("<!--", "# Digest"))
           and not any(a in l for a in AGE_LINE)]
check(len(content) <= 4,
      f"and the block itself is at most four lines under the title: {len(content)}")
check(sum(1 for l in block_two.splitlines() if any(a in l for a in AGE_LINE)) == 1,
      "besides the one line about the context file's age")

# ==========================================================================================
print("\n=== F. verdict, project, one whole sentence, a title link: two whole days ===")
# Every expected line below was written into this file by hand from the rules, before the run
# that checks it. Section 6's entry is put on each record too, so the test proves it is not
# what the line shows.
DAY_A = [  # (id, run time, project, title, section 6's entry, section 8)
    ("MaraDay0001", "2026-03-02T10:01:43", "Tidepool",
     "How Small Newsletters Price Their First Sponsor",
     '"a sponsor buys the reader, not the slot" - for the next sponsor conversation, lead with '
     "who reads Tidepool rather than with the price. Applies to: Tidepool.",
     "Write a one-paragraph sponsor page for Tidepool that names the price, the audience and "
     "the one slot a week."),
    ("MaraDay0002", "2026-03-02T10:01:45", "Ledgerleaf",
     "Top 10 Bookkeeping Mistakes Bakeries Make in a Busy Year",
     '"This notebook should be full after the first call" - before showing Ledgerleaf to the '
     "next bakery, run an actual discovery conversation first. Applies to: Ledgerleaf.",
     "Write five discovery questions (what they use now, what it costs them, where it breaks) "
     "to ask the next bakery before showing Ledgerleaf."),
    ("MaraDay0003", "2026-03-02T11:05:27", "this tool",
     "Building a Video Digest | Part 3 | Sorting by Project",
     'grouping rows by source, "you skim by who said it before what they said" - the digest '
     "table could carry the channel. Applies to: this tool.",
     "Add the channel name to each row of this tool's digest table so a reader can skim by "
     "source."),
    ("MaraDay0004", "2026-03-02T12:08:10", "Ledgerleaf",
     "How to sell software to bakeries: Interview with a Bakery Owner",
     "\"they open three reports and ignore the rest\" - before the next Ledgerleaf release, "
     "check those three reports first. Applies to: Ledgerleaf",
     "List the three reports a bakery owner opens first and check that Ledgerleaf's import "
     "fills each one."),
]
EXPECTED_A = [
    "- **YES** Ledgerleaf: Write five discovery questions (what they use now, what it costs "
    "them, where it breaks) to ask the next bakery before showing Ledgerleaf. [Top 10 "
    "Bookkeeping Mistakes Bakeries Make](https://www.youtube.com/watch?v=MaraDay0002)",
    "- **YES** Ledgerleaf: List the three reports a bakery owner opens first and check that "
    "Ledgerleaf's import fills each one. [How to sell software to "
    "bakeries](https://www.youtube.com/watch?v=MaraDay0004)",
    "- **YES** Tidepool: Write a one-paragraph sponsor page for Tidepool that names the price, "
    "the audience and the one slot a week. [How Small Newsletters Price Their First "
    "Sponsor](https://www.youtube.com/watch?v=MaraDay0001)",
]
DAY_B = [
    ("MaraDay0011", "2026-03-05T11:08:00", "Ledgerleaf",
     "Receipts to Reports: Automating a Bakery's Month-End Close",
     '"we close the month in an hour now" and "nobody types a receipt" - for Ledgerleaf, this '
     "points at importing photographed receipts. Applies to: Ledgerleaf.",
     "Sketch how one photographed receipt would become one Ledgerleaf entry, using last "
     "week's real receipts."),
    ("MaraDay0012", "2026-03-05T12:09:00", "Tidepool",
     "Welcome Emails for Newsletters - What the Best Ones Do in the First Week",
     '"the first email is the one they all open" - a direct prompt to rewrite the Tidepool '
     "welcome email around the issues readers already like. Applies to: Tidepool.",
     "Rewrite the Tidepool welcome email so it links the three most-read issues."),
    ("MaraDay0013", "2026-03-05T12:10:00", "how I work",
     "My Friday Release Checklist (Solo Developer Edition)",
     '"write what changed before you ship it" - for how I work, add release notes to the '
     "Friday checklist. Applies to: how I work.",
     "Add a release-notes line to the Friday checklist for every change shipped."),
]
EXPECTED_B = [
    "- **YES** Ledgerleaf: Sketch how one photographed receipt would become one Ledgerleaf "
    "entry, using last week's real receipts. [Receipts to "
    "Reports](https://www.youtube.com/watch?v=MaraDay0011)",
    "- **YES** Tidepool: Rewrite the Tidepool welcome email so it links the three most-read "
    "issues. [Welcome Emails for Newsletters](https://www.youtube.com/watch?v=MaraDay0012)",
    "- **YES** how I work: Add a release-notes line to the Friday checklist for every change "
    "shipped. [My Friday Release Checklist](https://www.youtube.com/watch?v=MaraDay0013)",
]
for day, keepers, expected, more in (("day A", DAY_A, EXPECTED_A, 1),
                                     ("day B", DAY_B, EXPECTED_B, 0)):
    reset()
    for vid, created, project, title, takeaway, action in keepers:
        keeper(vid, title, project, takeaway, action, created=created)
    lines = ww.todays_header_lines()
    for n, want in enumerate(expected):
        got = lines[n] if n < len(lines) else ""
        check(got == want, f"{day} line {n + 1} is exactly the line written before the run:"
                           f"\n        want {want}\n        got  {got}")
    if more:
        check(lines[-1].endswith(f"; {more} more kept below."),
              f"{day}: and the count line under them: {lines[-1]!r}")
    else:
        check("more kept below" not in lines[-1],
              f"{day}: and the count line under them, with none left over: {lines[-1]!r}")
    check(not any(k[4][:20] in "\n".join(lines) for k in keepers),
          f"{day}: and no line carries a word of section 6's entry in its order")

print("\n  the rules, one at a time, each with the case that must not change")
sentence = getattr(ww, "header_sentence", None)
short = getattr(ww, "short_title", None)
whole = getattr(ww, "whole_sentence", None)
check(sentence is not None and short is not None and whole is not None,
      "one function makes the sentence, one checks a sentence is whole, one the short title")
if sentence and short and whole:
    check(whole("Do the single thing.") == "Do the single thing.",
          "CONTROL: a whole sentence comes through untouched")
    check(whole("do the single thing") == "Do the single thing.",
          "a lower-case start is capitalised and a missing full stop added")
    check(whole('"we close the month in an hour" and "nobody types" - for Ledgerleaf.') is None,
          "a line that opens with the video's quote is not a sentence of what to do")
    check(whole("Build the receipt importer - for Ledgerleaf.") is None,
          "a line ending in a dangling '- for <project>' is not whole either")
    check(whole("nothing") is None and whole("None.") is None,
          "and 'nothing' is not a sentence")
    check(whole("Not tried: contradicts a standing decision (no page trackers, ever).") is None,
          "nor is rule 3's replacement for an action it struck")
    check(whole("Import the export twice. Then compare the totals.") == "Import the export twice.",
          "two sentences give the first, whole")
    long_plain = ("Import one bakery's bank export and check every total by hand and then import "
                  "a second one and check that one the same way and then compare the two reports "
                  "side by side, on the laptop and on the test server, with the dates pinned, "
                  "before anything else is built")
    check(whole(long_plain) == long_plain + ".",
          f"and a long sentence is never cut ({len(long_plain)} characters)")
    check(sentence({"takeaway": "Do the single thing. Applies to: Ledgerleaf"}) is None,
          "section 6's entry alone gives no sentence: it is never the source")
    check(sentence({"takeaway": "x. Applies to: Ledgerleaf", "try_this_week": "Do the thing."})
          == "Do the thing.", "section 8 is the source when the write-up has no header")
    check(sentence({"header": "For Ledgerleaf, import one export twice and compare.",
                    "try_this_week": "Do the thing."})
          == "For Ledgerleaf, import one export twice and compare.",
          "the write-up's own header (section 9) comes first")
    check(sentence({"header": "Import it twice. Compare.", "try_this_week": "Do the thing."})
          == "Do the thing.", "a header of two sentences is not used; section 8 is")
    check(sentence({"read_reason": "Teaches a mechanism.", "try_this_week": "nothing"}) is None,
          "and never the triage's reason: with no sentence of the write-up's there is none")
    check(ww.first_sentence("One thing. Two thing. Three.") == "One thing.",
          "first_sentence still ends at a full stop")
    fn1 = ('two bakeries can share one login - "Now the first person who signs in will get the '
           'code sent." This would let a family shop share one account.')
    check(ww.first_sentence(fn1).endswith('the code sent."'),
          f"and a quote that ends in a full stop ends the sentence: {ww.first_sentence(fn1)[-40:]!r}")
    check(short("The Bakery Owner's Guide to Month-End")
          == "The Bakery Owner's Guide to Month-End", "CONTROL: a short title is whole")
    check(short("Small Newsletters, Real Sponsors: A Field Guide " + chr(0x2014) + " Pell Varga")
          == "Small Newsletters, Real Sponsors: A Field Guide", "cut at the dash separator")
    check(short("How to sell software to bakeries: Interview with a Bakery Owner")
          == "How to sell software to bakeries", "cut at the colon")
    check(short("Top 10 Bookkeeping Mistakes Bakeries Make in a Busy Year")
          == "Top 10 Bookkeeping Mistakes Bakeries Make",
          "with no separator, at a word, and never on a small word like 'in a'")
    check(short("[Dashboard - Live demo] of it all") == "\\[Dashboard - Live demo\\] of it all",
          "and square brackets in a title cannot break the link")

# ==========================================================================================
print("\n=== G. a week of write-ups: every one gets its whole sentence ===")
# An invented week of keepers, with the line an older header put beside each ("shown_before")
# and whether that line was cut. The older header took a fragment of section 6, so it opened
# with quotes, ended in a dangling "- for <project>", or was cut mid sentence.
WEEK = [
    {"id": "MaraWeek001", "project": "Ledgerleaf",
     "try_this_week": "Email the three bakeries that churned last month and ask which report "
                      "they missed.",
     "shown_before": '"ask the ones who left before you ask the ones who stayed" - talk to',
     "cut_before": True},
    {"id": "MaraWeek002", "project": "Tidepool",
     "try_this_week": "write the second sponsor slot's one-line pitch and send it to the studio "
                      "that asked",
     "shown_before": "Pitch sponsors who already read the newsletter - for Tidepool",
     "cut_before": False},
    {"id": "MaraWeek003", "project": "how I work",
     "try_this_week": "Add the backup check to the morning checklist.",
     "shown_before": "Add the backup check to the morning checklist.", "cut_before": False},
    {"id": "MaraWeek004", "project": "Ledgerleaf",
     "try_this_week": "Not tried: contradicts a standing decision (no paid ads).",
     "shown_before": '"put money behind the post that already works" - boost',
     "cut_before": True},
    {"id": "MaraWeek005", "project": "this tool",
     "try_this_week": "Label ten past digest videos with your own verdict before reading the "
                      "tool's.",
     "shown_before": "Label ten past digest videos with your own verdict before reading the",
     "cut_before": True},
    {"id": "MaraWeek006", "project": "Tidepool",
     "try_this_week": "Rewrite the welcome email so it links the three most-read issues.",
     "shown_before": '"the first email is the one they all open"', "cut_before": False},
    {"id": "MaraWeek007", "project": "Ledgerleaf",
     "try_this_week": "Record a two-minute demo that imports one real bank export.",
     "shown_before": "Record a two-minute demo that imports one real bank export.",
     "cut_before": False},
]
DANGLING = re.compile(r"\s-\s+for\s+\S")
got_sentence, got_none = [], []
for row in WEEK:
    s = ww.header_sentence(row)
    (got_sentence if s else got_none).append((row, s))
check(len(got_sentence) == 6 and [r["id"] for r, _ in got_none] == ["MaraWeek004"],
      f"6 get a sentence; the one whose section 8 is rule 3's 'Not tried' gets none "
      f"({[r['id'] for r, _ in got_none]})")
bad = [(r["id"], s) for r, s in got_sentence
       if s[:1] in "\"'" or DANGLING.search(s) or not s.endswith(".")]
check(not bad, f"none opens with a quote, ends in a dangling '- for', or lacks a full stop: {bad[:3]}")
uncut = [(r["id"], s) for r, s in got_sentence
         if s.rstrip(".") != ww.one_line(r["try_this_week"]).strip().rstrip(".")[:1].upper()
         + ww.one_line(r["try_this_week"]).strip().rstrip(".")[1:]]
check(not uncut, f"and every one is its write-up's whole sentence, not a cut: {uncut[:2]}")
named = {r["id"]: s for r, s in got_sentence}
check(named.get("MaraWeek002") == "Write the second sponsor slot's one-line pitch and send it "
      "to the studio that asked.",
      f"a lower-case line without a full stop is repaired, not dropped: {named.get('MaraWeek002')!r}")
check(named.get("MaraWeek005") == "Label ten past digest videos with your own verdict before "
      "reading the tool's.",
      f"and the line the older header cut is whole: {named.get('MaraWeek005')!r}")
before = [r for r in WEEK if r["shown_before"][:1] in "\"'"
          or DANGLING.search(r["shown_before"]) or r["cut_before"]]
check(len(before) == 5, f"CONTROL: the older lines really were broken, 5 of 7 ({len(before)})")

# ==========================================================================================
print("\n=== H. a new write-up writes its own header, section 9 ===")
check("## 9. Header" in ww.SUMMARY_INSTRUCTIONS,
      "the write-up is asked for a section 9, its own one-line header")
WRITEUP = ("## 6. What applies to me\n\nMost applicable: \"a quote\" - a point. Applies to: "
           "Ledgerleaf\n\n## 7. What contradicts what I believe\n\nnothing in this video "
           "touches the beliefs list\n\n## 8. Try this week\n\nImport one export twice.\n\n"
           "## 9. Header\n\nFor Ledgerleaf, import one bank export twice with the dates pinned "
           "and compare the two reports.\n")
header_of = getattr(ww, "writeup_header", None)
check(header_of is not None, "one function reads section 9")
if header_of:
    check(header_of(WRITEUP) == "For Ledgerleaf, import one bank export twice with the dates "
          "pinned and compare the two reports.", f"section 9 is read whole: {header_of(WRITEUP)!r}")
    check(header_of(WRITEUP.replace("For Ledgerleaf, import", '"import')) is None,
          "a section 9 that opens with a quote is not used")
    check(header_of(WRITEUP.replace("Import one export twice.", "nothing")) is None,
          "and a section 9 is not used when section 8 is nothing, whatever it says")
    check(header_of(WRITEUP.split("## 9.")[0]) is None,
          "CONTROL: a write-up with no section 9 has no header of its own")
    reset()
    ww.update_record("hdr00000000", url="https://www.youtube.com/watch?v=hdr00000000",
                     title="A new video", read_verdict="YES", summary_state="WAITING",
                     reason_code="rolled")
    ww.summary_written("hdr00000000", WRITEUP, "YES")
    lines = ww.todays_header_lines()
    check(lines[0] == "- **YES** Ledgerleaf: For Ledgerleaf, import one bank export twice with "
          "the dates pinned and compare the two reports. [A new "
          "video](https://www.youtube.com/watch?v=hdr00000000)",
          f"and the header line shows it, verdict and project first: {lines[0]!r}")
    reset()
    ww.update_record("hdr00000001", url="https://www.youtube.com/watch?v=hdr00000001",
                     title="An old video", read_verdict="YES", summary_state="WAITING",
                     reason_code="rolled")
    ww.summary_written("hdr00000001", WRITEUP.split("## 9.")[0], "YES")
    lines = ww.todays_header_lines()
    check("Import one export twice." in lines[0],
          f"CONTROL: without a section 9 the line is section 8's sentence: {lines[0]!r}")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
