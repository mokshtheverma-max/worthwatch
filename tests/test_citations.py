"""Does the section-6 guard actually delete invented advice and keep real quotes?
Uses an invented transcript (tests/fixtures/bakery-talk.txt), then replays a small invented
history.jsonl written into the sandbox, the way the guard would meet stored summaries."""
import io, sys, json, re
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "citations")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

fails = []
def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: fails.append(msg)

TR = (FIXTURES / "bakery-talk.txt").read_text(encoding="utf-8")
words = ww.canon_tokens(TR)
REAL = " ".join(words[40:49])            # nine consecutive real words from the transcript
print(f"transcript: {len(words)} words; real quote used: {REAL!r}\n")

# 2026-09-20: every fixture below now carries the "Applies to:" label, and the two
# assertions that read "Most applicable: nothing" now read the section body instead. Both
# are the 2026-09-10 contract catching up with the suite, not a rule being loosened. That
# day added the closed project list (an item names exactly one project from PROJECTS, read
# only from the text after "Applies to:") and made an empty section 6 the bare word
# "nothing" rather than "Most applicable: nothing". These fixtures predate it: they were
# written on 2026-09-05 and label nothing, so from 09-10 onwards every "a real item
# survives" case here failed for the one reason the case is not about. Measured before the
# repair: 11 of 25 checks red in this file and 5 of 19 in test_citations2.py, on a tree
# whose production path had not changed.
APPLIES = " Applies to: Ledgerleaf"


def labelled(content):
    """A fixture item as the current prompt asks for one. "nothing" is left alone: it is
    the absence of an item, and an absence does not apply to a project."""
    return content if ww.is_nothing_useful(content) else content + APPLIES


def applies_body(text):
    """Section 6's body, banners dropped: what a reader actually sees under the heading."""
    lines = text.splitlines()
    bounds = ww.section_bounds(lines, ww.APPLIES_HEADING_RE)
    if bounds is None:
        return None
    body = [l.strip() for l in lines[bounds[0] + 1:bounds[1]]]
    return "\n".join(l for l in body if l and not l.startswith(">"))


def summary(most_applicable, extra_items=(), try_line="Do the thing."):
    items = "\n".join(f"- {labelled(x)}" for x in extra_items)
    return (f"## 5. Worth keeping verbatim\nNone.\n\n"
            f"## 6. What applies to me\n"
            f"Most applicable: {labelled(most_applicable)}\n{items}\n"
            f"Ignore: the affiliate links, and anything implying a pass guarantee.\n\n"
            f"## 7. What contradicts what I believe\nNothing.\n\n"
            f"## 8. Try this week\n{try_line}\n")

print("=== A. a real quote from the video survives ===")
out = ww.finalize_summary(summary(
    f'Ship a landing page before building. "{REAL}" - this would change Ledgerleaf\'s sign-up page.'), TR)
check("Most applicable: Ship a landing page" in out, "cited item kept verbatim")
check("removed here" not in out, "no strike banner when nothing was struck")
check("Do the thing." in out, "section 8 left alone when section 6 stands")

print("\n=== B. the actual failure mode: a bakery bridge with no quote ===")
bridge = ("Before pitching any bakery, ask one to talk about their actual weekly "
          "manual task, in their words - not to demo anything yet.")
out = ww.finalize_summary(summary(bridge), TR)
check(applies_body(out) == "nothing", "uncited bridge replaced with 'nothing'")
check(bridge not in out, "the invented advice is gone, not merely flagged")
check("removed here" in out, "a banner says an item was removed and why")
check(ww.is_nothing_useful(ww.extract_top_takeaway(out)),
      "extract_top_takeaway now reports nothing, so no digest 'most useful' line")
check(ww.extract_try_this_week(out) == "nothing",
      "section 8 forced to 'nothing' instead of carrying the bridge down")

print("\n=== C. a plausible but fabricated quote is struck ===")
out = ww.finalize_summary(summary(
    'Talk to bakeries first. "you have to sell to bakeries before you write code"'), TR)
check(applies_body(out) == "nothing", "a quote not in the transcript does not save an item")

print("\n=== D. lightly repaired grammar still counts as the video's words ===")
# The invented transcript is built so that this slice's fifth word is "receipt" and the talk
# also says "receipts into" elsewhere: the repaired quote is not in the transcript word for
# word, and keeps six of its seven word pairs, which is the tolerant tier and not the exact one.
repaired = " ".join(words[100:108])
tweaked = repaired.replace(words[104], words[104] + "s", 1) if len(words[104]) > 3 else repaired
check(tweaked != repaired and f" {tweaked} " not in " " + " ".join(words) + " ",
      f"the fixture really alters the quote, so the exact tier cannot pass it ({tweaked!r})")
out = ww.finalize_summary(summary(f'A real point. "{tweaked}"'), TR)
check("Most applicable: A real point" in out,
      "an 8-word quote with one word altered is still accepted (tolerant tier)")

print("\n=== E. a two-word quote proves nothing ===")
out = ww.finalize_summary(summary(f'A claim. "{" ".join(words[10:12])}"'), TR)
check(applies_body(out) == "nothing", "quotes under three words are rejected")

print("\n=== F. ranked list items are checked one by one ===")
out = ww.finalize_summary(summary(
    f'Real one. "{REAL}"',
    extra_items=[f'Second real one. "{" ".join(words[200:207])}"',
                 'Invented one: cold email every bakery in town.']), TR)
check("Second real one" in out, "a cited list item survives")
check("cold email every bakery" not in out, "an uncited list item is deleted")
check("1 item(s) removed" in out, "the banner counts exactly the struck item")

print("\n=== G. the Ignore line is never touched ===")
check("Ignore: the affiliate links" in out, "Ignore: is not a claim of applicability")

print("\n=== H. a missing section 6 fails loudly rather than passing quietly ===")
out = ww.finalize_summary("## 1. What it is\nA video.\n\n## 8. Try this week\nDo something.\n", TR)
check("SECTION 6 NOT VERIFIED" in out, "banner warns the summary was never checked")

print("\n=== I. an honest 'nothing' is left exactly as written ===")
out = ww.finalize_summary(summary("nothing", try_line="nothing"), TR)
check(applies_body(out) == "nothing" and "removed here" not in out,
      "an already-empty section is not counted as a strike")

# ---------------------------------------------------------------------------
# The replay used to read the owner's own history.jsonl and raw/. It reads an invented one
# now, written into the sandbox, with the same kinds of row: a stored summary whose item
# quotes its video, one whose item quotes nothing (section N's), one that already said
# nothing, one whose transcript is gone from disk, and a row with no summary at all.
NEWSLETTER_TALK = (
    "I write a weekly newsletter about sourdough for home bakers. It has about three thousand "
    "readers now. For the first year it made nothing, and I was fine with that. Then a flour "
    "mill asked if they could sponsor one issue, and I said yes without knowing what to charge. "
    "I picked a number that felt fair, and they booked four more issues. Since then I keep a "
    "simple page that says how many readers I have, how many open each issue, and what one slot "
    "costs. Most sponsors find me through that page. The lesson for me was to write down what "
    "you offer before anyone asks, so you are not inventing a number on the phone.")
UNCITED = ("## 1. What it is\nA newsletter writer explains how sponsors started paying for her "
           "sourdough newsletter.\n\n## 5. Worth keeping verbatim\nNone.\n\n"
           "## 6. What applies to me\nMost applicable: Offer every past sponsor a second slot at "
           "a lower rate before the next issue goes out. Applies to: Tidepool\n"
           "Ignore: the sourdough recipes.\n\n"
           "## 7. What contradicts what I believe\nNothing.\n\n"
           "## 8. Try this week\nWrite to three past sponsors and offer each a second slot.\n")
HISTORY = [
    ("MaraCite001", "How one bakery fixed its books in ten minutes a night", TR,
     summary(f'Type each receipt in the same night. "{REAL}"')),
    ("MaraCite002", "A Sourdough newsletter that pays for itself", NEWSLETTER_TALK, UNCITED),
    ("MaraCite003", "Ten tips for indie game trailers", TR, summary("nothing", try_line="nothing")),
    ("MaraCite004", "Pricing a small app for the first time", None,
     summary(f'Price before you build. "{REAL}"')),
    ("MaraCite005", "A video that was never written up", TR, None),
]
with open(ww.HISTORY_PATH, "w", encoding="utf-8") as f:
    for vid, title, transcript, stored in HISTORY:
        row = {"date": "2026-09-05", "verdict": "YES" if stored else "NO", "channel": "Invented",
               "title": title, "url": f"https://www.youtube.com/watch?v={vid}", "reason": "r"}
        if stored:
            row["summary"] = stored
        f.write(json.dumps(row) + "\n")
        if transcript is not None:
            (ww.RAW_DIR / f"{vid}.txt").write_text(transcript, encoding="utf-8")

print("\n=== J. replay: how much of the existing output was invented? ===")
rows = [json.loads(l) for l in Path(ww.HISTORY_PATH).open(encoding="utf-8") if l.strip()]
have = [r for r in rows if r.get("summary")]
checked = kept = struck = no_transcript = 0
for r in have:
    vid = ww.video_id_from_url(r["url"])
    raw = ww.RAW_DIR / f"{vid}.txt"
    if not raw.exists():
        no_transcript += 1
        continue
    tr = raw.read_text(encoding="utf-8", errors="replace")
    before = ww.extract_top_takeaway(r["summary"])
    if ww.is_nothing_useful(before):
        continue
    checked += 1
    after, (k, s, _c) = ww.enforce_applies_citations(ww.normalize_punct(r["summary"]), tr)
    if ww.is_nothing_useful(ww.extract_top_takeaway(after)):
        struck += 1
    else:
        kept += 1
print(f"  {len(have)} stored summaries, {no_transcript} with no transcript left on disk")
print(f"  {checked} had a real 'Most applicable' line; under the new guard "
      f"{kept} would survive and {struck} would be cut to 'nothing'")
if checked:
    print(f"  => {struck / checked * 100:.0f}% of the advice already written was unsupported "
          f"by anything the video said")
check(checked > 0, "the replay actually had summaries to check")
check((len(have), no_transcript, checked, kept, struck) == (4, 1, 2, 1, 1),
      "and it sorts the invented history as it was built: four stored summaries, one with no "
      "transcript, one already empty, one cited item kept and one uncited item cut")

print(chr(10) + "=== N. end to end on a stored summary ===")
_r = next(r for r in rows if r.get("summary") and "Sourdough" in r["title"])
_vid = ww.video_id_from_url(_r["url"])
_tr = (ww.RAW_DIR / (_vid + ".txt")).read_text(encoding="utf-8", errors="replace")
_before_top = ww.extract_top_takeaway(_r["summary"])
_before_try = ww.extract_try_this_week(_r["summary"])
print("  video:  " + _r["title"][:65])
print("  BEFORE: " + _before_top[:105])
_out = ww.finalize_summary(_r["summary"], _tr)
print("  AFTER:  " + str(ww.extract_top_takeaway(_out))[:105])
print("  try this week: " + repr(_before_try[:70]) + "  ->  " + repr(ww.extract_try_this_week(_out)))
check(not ww.is_nothing_useful(_before_top), "CONTROL: before the guard the stored summary had an item")
check(ww.is_nothing_useful(ww.extract_top_takeaway(_out)), "an uncited stored summary is emptied")
check(ww.extract_try_this_week(_out) == "nothing", "and its action goes with it")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
