"""A write-up whose section 6 is nothing has the header "nothing".

Section 9 is the header a write-up writes for itself. The prompt says it is "nothing" when
section 8 is, and section 8 is "nothing" when section 6 is, but the model does not always do it,
and every guard that empties section 6 rewrote section 8 and never touched section 9. On 27
September one write-up thought aloud, and again when asked once more, so sections 6, 7 and 8
were set to nothing; its section 9 still read a sentence of advice for the work label, in the
write-up and in the day's digest. Of the write-ups on disk then that carried a section 9, it
was the only one whose section 6 was nothing and whose header was not.

Every way section 6 comes to nothing is driven through finalize_summary, which every backend
and entry point goes through: thinking aloud (the real case), the quote check striking every
item, the model writing "nothing" itself, and a local summary. The control is an item that
survives, whose header is left exactly as it was written.

No YouTube request and no model call: the re-ask is a stub, and a call to the local model is
refused and fails the suite. The video, its transcript and its write-up are invented, with
the shape of the real case: sections 1 to 5 whose quotes are all in the transcript, an item
that quotes the transcript, and a header sentence for the work label. Writes nothing outside
its temp folder, and reads the invented example person's profile.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "header-nothing")

TRANSCRIPT = (
    "Hi everyone and welcome back to the corner oven. Today I am going to show you how I plan a "
    "whole bake day in about ten minutes, with no spreadsheets at all. The first thing is the "
    "night before. I sit down with a cup of tea and I write a short plan for the morning: what "
    "goes in the oven first, what can wait, and who is picking up orders. You have to write it "
    "down on paper, not keep it in your head, because at five in the morning your head is not "
    "your friend. Then I look at the orders book and I put every order next to the hour it has "
    "to be ready. If an order has no hour, it is not an order yet. The plan has to fit on one "
    "card, one card and no more, so you can pin it above the ovens. And honestly, the better "
    "the plan you write the better the day you get. On the days I skip it, I burn something, "
    "every time. In the morning I read the card once, I start the first oven, and I do not look "
    "at my phone until the bread is out. At the end of the day I cross out what got done and I "
    "write one line about what went wrong, so the next plan is a little better than this one. "
    "That is the whole method. It takes ten minutes, it costs nothing, and it has saved me more "
    "flour than any machine I ever bought.")
MODEL_CALLS = []


def no_model(*args, **kwargs):
    MODEL_CALLS.append(args[:1])
    raise RuntimeError("this suite makes no model call")


ww.ollama_generate = no_model
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


META = {"title": "How I Plan a Whole Bake Day in Ten Minutes (No Spreadsheets)",
        "uploader": "Corner Oven Diaries"}
# Sections 1 to 5 of the write-up: invented, and every quote in section 5 is the transcript's
# own words, as the real write-up's were, so the quote check leaves them alone.
REPORTING = (
    '## 1. What it is\n'
    'A talk to camera by the owner of a small bakery, showing how she plans a bake day the '
    'night before on one index card. No sponsor, course, product, or affiliate link is '
    'mentioned anywhere in the transcript; the presenter is not selling anything related to '
    'the method shown.\n'
    '\n'
    '## 2. The claim\n'
    'Ten minutes of planning the night before, written on one card, makes the next bake day '
    'faster and wastes less flour, with no software at all.\n'
    '\n'
    '## 3. Evidenced versus asserted\n'
    'Nothing is shown as a measured number. The "ten minutes" in the title and the "more '
    'flour than any machine" said on camera are both only asserted, not timed or weighed on '
    'screen. The card itself is shown, but nothing verifies that the days with a plan went '
    'better than the days without one.\n'
    '\n'
    '## 4. The method\n'
    '1. The night before, write a short plan for the morning on paper.\n'
    '2. Put every order next to the hour it has to be ready.\n'
    '3. Keep the plan to one card and pin it above the ovens.\n'
    '4. In the morning, read the card once and start the first oven.\n'
    '5. At the end of the day, cross out what got done and write one line about what went '
    'wrong.\n'
    '\n'
    '## 5. Worth keeping verbatim\n'
    '- "You have to write it down on paper, not keep it in your head"\n'
    '- "If an order has no hour, it is not an order yet"\n'
    '- "The plan has to fit on one card, one card and no more"\n'
    '- "the better the plan you write the better the day you get"\n'
    '\n')
# Its section 9: one sentence of advice under the work label, as the real one was.
HEADER_27 = "Try writing a short plan the night before each bake day, for how I work."
ITEM = ('Most applicable: "the better the plan you write the better the day you get" - write a '
        'short plan for the next bake day before the day starts. Applies to: how I work.')
ACTION = "Write a short plan for your next bake day before the day starts."
SAID = "section 9, the header, is nothing too"


def writeup(section6, section8, section9=HEADER_27):
    return (REPORTING + "## 6. What applies to me\n\n" + section6 + "\n\n"
            "## 7. What contradicts what I believe\n\nnothing in this video touches the beliefs "
            "list\n\n## 8. Try this week\n\n" + section8 + "\n\n## 9. Header\n" + section9 + "\n")


def finalize(text, **kwargs):
    """finalize_summary as the write-up step calls it, and what it printed to the run's log."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = ww.finalize_summary(text, TRANSCRIPT, model="qwen3.5:9b", verdict="YES",
                                     meta=META, **kwargs)
    return result, out.getvalue()


def body(text, heading_re):
    lines = text.splitlines()
    bounds = ww.section_bounds(lines, heading_re)
    if bounds is None:
        return None
    return [l.strip() for l in lines[bounds[0] + 1:bounds[1]] if l.strip()]


def header_of(text):
    return body(text, ww.HEADER_HEADING_RE)


# ==========================================================================================
print("=== A. 27 September: it thought aloud twice, and sections 6, 7 and 8 were set to nothing ===")
# The run's log says "(the summary reasoned aloud instead of answering; asking once more)" and
# then "(1 line(s) of thinking-aloud removed after a re-ask; sections 6, 7 and 8 set to nothing)".
# It keeps the count of that line, not its words, so the section 8 below stands in for it, and
# the header stands in for the real one. The re-ask is a stub that answers the same way again,
# as the real second answer did.
ALOUD = (ACTION + " Actually, re-reading the rules, that is advice anyone could follow, so it may "
         "not belong here.")
asked = []


def reask(note):
    asked.append(note)
    return writeup(ITEM, ALOUD)


text, log = finalize(writeup(ITEM, ALOUD), regenerate=reask)
check(len(asked) == 1 and "thinking-aloud removed after a re-ask" in log,
      "it was asked once more and thought aloud again, as on 27 September")
check(ww.section6_state(text)[0] == "nothing" and body(text, ww.TRY_HEADING_RE) == ["nothing"],
      "sections 6 and 8 are nothing, as they were")
check(header_of(text) == ["nothing"], f"and its header is nothing, not its old sentence "
                                      f"({header_of(text)})")
check(SAID in log, "and the run's log says so")
check(ww.writeup_header(text) is None, "no header sentence reaches the record")

# ==========================================================================================
print("\n=== B. the quote check strikes every item ===")
INVENTED = ('Most applicable: "always count the takings again before you close the shop" - count '
            'the takings again at closing. Applies to: how I work.')
check(not ww.quote_supported_by("always count the takings again before you close the shop",
                                *ww.transcript_index(TRANSCRIPT)),
      "CONTROL: the video does not say the quote, so this is the quote check's path")
text, log = finalize(writeup(INVENTED, "Count the takings again before you close the shop "
                                       "tonight."))
check(ww.section6_state(text)[0] == "nothing" and "1 item(s) removed here" in text,
      "the item quoted nothing the video said, so section 6 is nothing")
check(body(text, ww.TRY_HEADING_RE) == ["nothing"], "section 8 was set to nothing with it")
check(header_of(text) == ["nothing"], f"and section 9 is nothing too ({header_of(text)})")
check(SAID in log, "and the run's log says so")

# ==========================================================================================
print("\n=== C. the model writes section 6 as nothing, and a header anyway ===")
text, log = finalize(writeup("nothing", "nothing"))
check(header_of(text) == ["nothing"], f"the header is nothing ({header_of(text)})")
check(SAID in log, "and the run's log says so")
first = text

# ==========================================================================================
print("\n=== D. a local summary, whose sections 6, 7 and 8 are always blank ===")
text, log = finalize(writeup(ITEM, ACTION), backend="ollama:qwen3.5:9b")
check(ww.section6_state(text)[0] == "nothing", "section 6 is blank, as a local summary's is")
check(header_of(text) == ["nothing"], f"and so is its header ({header_of(text)})")
check(SAID in log, "and the run's log says so")

# ==========================================================================================
print("\n=== E. CONTROL: an item that survives keeps its header, word for word ===")
KEPT_HEADER = "Write a short plan the night before your next bake day, for how I work."
text, log = finalize(writeup(ITEM, ACTION, KEPT_HEADER))
check(ww.section6_state(text)[0] == "items", "the item's quote is the video's own, so it is kept")
check(header_of(text) == [KEPT_HEADER], f"its header is untouched ({header_of(text)})")
check(SAID not in log, "and the log says nothing about the header")
check(ww.writeup_header(text) == KEPT_HEADER, "and it is the header the record gets")

# ==========================================================================================
print("\n=== F. only section 9 changes, and only once ===")
without_9, _ = finalize(writeup("nothing", "nothing").split("## 9.")[0])
check("## 9." not in without_9, "a write-up with no section 9 is not given one")
check(first.split("## 9.")[0].rstrip() == without_9.rstrip(),
      "sections 1 to 8 come out the same with or without a section 9")
again, log = finalize(first)
check(header_of(again) == ["nothing"] and again.count("## 9.") == 1,
      f"run again, the header is still one section saying nothing ({header_of(again)})")
check(SAID not in log, "and there was nothing to rewrite the second time")

print(f"\n  model calls: {len(MODEL_CALLS)}")
check(not MODEL_CALLS, "no model was called")
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
