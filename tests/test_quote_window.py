"""One quote, one passage: a quote passes the quote check only if ONE stretch of the transcript
matches it, in order.

Fixed on 2026-10-16, after a reader of the repository pointed out that quote_supported_by passed
a quote when 80% of its word pairs appeared ANYWHERE in the transcript. A quote stitched together
from two different places has one broken pair, at the join, and one in five was allowed. This
file holds that case and the cases around it: an exact quote, a quote with one word different,
a quote with too many different, a scattered quote that is 80% right, a quote that runs from the
transcript into the video's title, and the number of different words a quote may have at each
length (none under 8 words, then one more for every 8; the measurements behind that are written
next to allowed_differences in summaryrules.py). The last sections check the fast search against
a slow exact one on thousands of random cases, time it on a long transcript, and drive a
stitched quote through a whole write-up.

The transcript is invented (a school garden club) and so is every quote. No YouTube request, no
model call.

Watch it go red: put the old pair rule back in quote_supported_by (the share of the quote's word
pairs found anywhere in the transcript, 0.8 or more), let allowed_differences return 0, or let a
match run across a passage break, and run this file.
"""
import contextlib
import io
import random
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "quote-window")
from worthwatch import summaryrules as rules  # noqa: E402

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


TRANSCRIPT = (
    "Hi everyone, welcome back to the garden club channel. Today I want to tell you what "
    "happened in our first year, because a lot of you asked. We started with six raised beds "
    "behind the gym. We planted tomatoes in the north bed because the soil there drains well "
    "and gets sun until late afternoon. The students water by hand every morning before class, "
    "and honestly that was the part nobody wanted to do. The big change was the water barrels "
    "by the shed. They fill up in one night of rain, so we never pay for watering in the "
    "spring. A parent built the lids from old pallets, and the whole thing cost less than a "
    "pizza night. In June we sold the first harvest at the front office for two dollars a bag. "
    "Teachers bought most of it. We made forty dollars and spent every cent of it on seeds for "
    "the fall. Then the principal asked if we could supply the cafeteria, and I said we are "
    "not ready for that yet, and I think that was the right answer. The lesson for the club "
    "was simple. Start small, write down what each bed produces, and let the students decide "
    "what to plant next. Nobody argued about the plan once the numbers were on the board. "
    "That is the whole story. If you want to start a garden club, start with one bed and one "
    "barrel, and keep a notebook. Thanks for watching, and see you next week.")
TITLE = "How We Built a Garden Club in One Year"
UPLOADER = "Green Thumb Kids"
META = {"title": TITLE, "uploader": UPLOADER, "channel": UPLOADER}
WORDS = rules.canon_tokens(TRANSCRIPT)
INDEX = rules.transcript_index(TRANSCRIPT)
DOC_INDEX = rules.document_index(TRANSCRIPT, META)


def supported(quote, index=INDEX):
    return rules.quote_supported_by(quote, *index)


def in_one_place(quote, text):
    """Is the quote word for word in the text, after the check's own normalisation?"""
    return (" " + " ".join(rules.canon_tokens(quote)) + " "
            in " " + " ".join(rules.canon_tokens(text)) + " ")


def pair_share(quote, text):
    """The old rule's measure: the share of the quote's word pairs found anywhere in the text."""
    q, t = rules.canon_tokens(quote), rules.canon_tokens(text)
    have = {(t[i], t[i + 1]) for i in range(len(t) - 1)}
    pairs = [(q[i], q[i + 1]) for i in range(len(q) - 1)]
    return sum(1 for p in pairs if p in have) / len(pairs)


def fewest_differences(q, t):
    """The slow, exact reference: the fewest changed, missing or extra words between the word
    list q and any ONE stretch of the word list t. A full table, no shortcuts."""
    m, n = len(q), len(t)
    table = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        table[i][0] = i
        for j in range(1, n + 1):
            table[i][j] = min(table[i - 1][j - 1] + (q[i - 1] != t[j - 1]),
                              table[i - 1][j] + 1, table[i][j - 1] + 1)
    return min(table[m])


try:
    ALLOWED = rules.allowed_differences
except AttributeError:
    ALLOWED = None
check(ALLOWED is not None, "summaryrules.allowed_differences says how many words a quote of a "
                           "given length may have different")
if ALLOWED is None:
    def ALLOWED(n_words):    # the old code has none; this keeps the later sections measuring
        return n_words // 8

# ==========================================================================================
print("=== A. a quote that is in the transcript word for word passes ===")
EXACT_13 = "we planted tomatoes in the north bed because the soil there drains well"
EXACT_9 = "they fill up in one night of rain so"
check(in_one_place(EXACT_13, TRANSCRIPT) and in_one_place(EXACT_9, TRANSCRIPT),
      "CONTROL: both are in the transcript word for word")
check(supported(EXACT_13), "a 13-word quote from one place passes")
check(supported("WE planted tomatoes, in the NORTH bed... because the soil there drains well!"),
      "capitals and punctuation do not matter")
check(supported("write down what each bed produces"), "a 6-word exact quote passes")
check(supported(" ".join(WORDS[30:75])), "a 45-word exact quote passes")
check(not supported("garden club"), "two words prove nothing, exact or not")
check(not supported("always count the takings again before you close the shop"),
      "a quote the video never says does not pass")

# ==========================================================================================
print("\n=== B. a real quote with one word different still passes (8 words and up) ===")
SUBSTITUTED = "they fill up in one evening of rain so"      # night -> evening, in the middle
EXTRA = "they fill up in just one night of rain so"         # one extra word
DROPPED = "they fill up in night of rain so"                # one word missing
CHANGED_13 = "we planted tomatoes in the north bed because the ground there drains well"
check(not in_one_place(SUBSTITUTED, TRANSCRIPT) and pair_share(SUBSTITUTED, TRANSCRIPT) < 0.8,
      f"CONTROL: the substituted quote is not in the transcript, and the old rule turned it "
      f"down ({pair_share(SUBSTITUTED, TRANSCRIPT):.0%} of its pairs)")
check(supported(SUBSTITUTED), "one word changed in the middle of a 9-word quote passes")
check(not in_one_place(EXTRA, TRANSCRIPT) and pair_share(EXTRA, TRANSCRIPT) < 0.8,
      f"CONTROL: the extra-word quote is not in the transcript, and the old rule turned it "
      f"down too ({pair_share(EXTRA, TRANSCRIPT):.0%} of its pairs)")
check(supported(EXTRA), "one extra word in a 10-word quote passes")
check(supported(DROPPED), "one word missing from an 8-word quote passes")
check(supported(CHANGED_13), "one word changed in a 13-word quote passes")

# ==========================================================================================
print("\n=== C. too many different words fail, and so does any difference in a short quote ===")
TWO_CHANGED = "they fill up in one evening of rainfall so"
SEVEN_ONE_CHANGED = "the soil there drains well and takes"   # gets -> takes, 7 words
check(not supported(TWO_CHANGED), "two words changed in a 9-word quote fails")
check(not in_one_place(SEVEN_ONE_CHANGED, TRANSCRIPT)
      and pair_share(SEVEN_ONE_CHANGED, TRANSCRIPT) >= 0.8,
      f"CONTROL: the 7-word quote is not in the transcript, and the old rule passed it "
      f"({pair_share(SEVEN_ONE_CHANGED, TRANSCRIPT):.0%} of its pairs)")
check(not supported(SEVEN_ONE_CHANGED),
      "under 8 words a quote must match word for word: a short phrase with one word different "
      "is too easy to find by chance in a video that never said it")

# ==========================================================================================
print("\n=== D. the number of different words allowed, by length ===")
check([ALLOWED(n) for n in (3, 7, 8, 15, 16, 23, 24, 32)] == [0, 0, 1, 1, 2, 2, 3, 4],
      "none under 8 words, then one more for every 8")


def changed(n_words, how_many):
    """A real stretch of n_words words with how_many of them swapped for a word nobody says."""
    stretch = WORDS[5:5 + n_words]
    for k in range(how_many):
        stretch[2 + 4 * k] = "zzq"
    return " ".join(stretch)


for n in (8, 15, 16, 23, 24, 31, 32):
    allowed = n // 8
    check(supported(changed(n, allowed)), f"{n} words with {allowed} different passes")
    check(not supported(changed(n, allowed + 1)), f"{n} words with {allowed + 1} different fails")
for n in (3, 5, 7):
    check(supported(changed(n, 0)) and not supported(changed(n, 1)),
          f"{n} words: word for word passes, one different fails")

# ==========================================================================================
print("\n=== E. words stitched from two places fail ===")
HALF_A = "we planted tomatoes in the north bed"      # from the start of the talk
HALF_B = "never pay for watering in the spring"       # from the middle
STITCHED = f"{HALF_A} {HALF_B}"
check(in_one_place(HALF_A, TRANSCRIPT) and in_one_place(HALF_B, TRANSCRIPT)
      and not in_one_place(STITCHED, TRANSCRIPT),
      "CONTROL: each half is in the transcript, the whole is not")
check(pair_share(STITCHED, TRANSCRIPT) >= 0.8,
      f"CONTROL: {pair_share(STITCHED, TRANSCRIPT):.0%} of its word pairs are in the "
      f"transcript, so the old rule passed it")
check(supported(HALF_A) and supported(HALF_B), "each half on its own passes")
check(not supported(STITCHED), "the two halves joined do not pass")
check(not supported(f"{HALF_B} {HALF_A}"), "nor in the other order")

# "let the" is from one place and "the numbers" from another.
EVERY_PAIR = "start small write down what each bed produces and let the numbers were on the board"
check(pair_share(EVERY_PAIR, TRANSCRIPT) == 1.0 and not in_one_place(EVERY_PAIR, TRANSCRIPT),
      "CONTROL: every word pair of this quote is in the transcript, and the quote is not")
check(not supported(EVERY_PAIR),
      "a stitched quote whose join is a pair the transcript has elsewhere fails")

# ==========================================================================================
print("\n=== F. a quote that is 80% right but scattered fails ===")
SCATTERED_4 = ("start small write down what sold the first harvest at built the lids from old "
               "right answer the lesson for")                 # four pieces, three joins
SCATTERED_3 = "the soil there drains well sold the first harvest at the front keep a notebook"
for label, quote in (("four pieces", SCATTERED_4), ("three pieces", SCATTERED_3)):
    check(pair_share(quote, TRANSCRIPT) >= 0.8 and not in_one_place(quote, TRANSCRIPT),
          f"CONTROL ({label}): {pair_share(quote, TRANSCRIPT):.0%} of the word pairs are in "
          f"the transcript, the quote is not")
    check(not supported(quote), f"a quote of {label} from different places fails")

# ==========================================================================================
print("\n=== G. a quote may not run from one source into the next ===")
TAIL_AND_TITLE = "see you next week how we built a garden"        # transcript, then the title
TITLE_AND_UPLOADER = "garden club in one year green thumb kids"   # the title, then the uploader
glued = f"{TRANSCRIPT} {TITLE} {UPLOADER}"
check(in_one_place(TAIL_AND_TITLE, glued) and in_one_place(TITLE_AND_UPLOADER, glued),
      "CONTROL: both quotes are word for word in the sources glued together")
check(not any(in_one_place(q, s) for q in (TAIL_AND_TITLE, TITLE_AND_UPLOADER)
              for s in (TRANSCRIPT, TITLE, UPLOADER)),
      "CONTROL: and in none of the sources alone")
check(not supported(TAIL_AND_TITLE, DOC_INDEX),
      "the end of the transcript plus the start of the title fails")
check(not supported(TITLE_AND_UPLOADER, DOC_INDEX),
      "the end of the title plus the uploader's name fails")
check(supported("how we built a garden club in one year", DOC_INDEX),
      "the whole title still passes, because section 1 names the video")
check(supported("green thumb kids", DOC_INDEX), "and so does the uploader's name")
check(not supported("how we built a garden club in one year"),
      "but not against the transcript alone, which is all section 6 may use")
check(supported("welcome back to the garden club channel", DOC_INDEX),
      "a quote from the transcript still passes against all the sources")

# ==========================================================================================
print("\n=== H. the fast search agrees with the slow exact one on random cases ===")
rng = random.Random(20261016)
VOCAB = "a b c d e f g h".split()


def random_words(n):
    return [rng.choice(VOCAB) for _ in range(n)]


def edited(stretch):
    """A stretch with up to 3 random edits: a word changed, dropped or added."""
    out = list(stretch)
    for _ in range(rng.randint(0, 3)):
        kind = rng.choice("sdi")
        at = rng.randrange(len(out)) if out else 0
        if kind == "s" and out:
            out[at] = rng.choice(VOCAB + ["zzq"])
        elif kind == "d" and len(out) > 3:
            del out[at]
        else:
            out.insert(at, rng.choice(VOCAB + ["zzq"]))
    return out


def word_for_word(quote, sources):
    text = " " + " ".join(quote) + " "
    return any(text in " " + " ".join(s) + " " for s in sources)


cases = agreed = said_yes = near_yes = 0
disagreements = []
for case in range(1500):
    sources = [random_words(rng.randint(30, 70)) for _ in range(rng.randint(1, 3))]
    texts = [" ".join(s) for s in sources]
    index = (rules.transcript_index(texts[0]) if len(texts) == 1
             else rules.document_index(texts[0], dict(zip(("title", "uploader"), texts[1:]))))
    source = rng.choice(sources)
    length = rng.randint(3, 26)
    start = rng.randrange(max(1, len(source) - length))
    kind = rng.random()
    if kind < 0.55:
        quote = edited(source[start:start + length])
    elif kind < 0.8:
        other = rng.choice(sources)
        cut = rng.randint(2, max(2, length - 2))
        tail = other[rng.randrange(max(1, len(other) - length)):][:length - cut]
        quote = edited(source[start:start + cut] + tail)
    else:
        quote = random_words(length)
    expected = len(quote) >= 3 and min(fewest_differences(quote, s) for s in sources) <= ALLOWED(len(quote))
    got = rules.quote_supported_by(" ".join(quote), *index)
    cases += 1
    agreed += expected == got
    said_yes += got
    near_yes += got and not word_for_word(quote, sources)
    if expected != got and len(disagreements) < 3:
        disagreements.append((" ".join(quote), texts, expected))
print(f"  {cases} random quotes; the check said yes to {said_yes}, {near_yes} of them not word "
      f"for word")
check(agreed == cases, f"the check agrees with the slow exact search on all {cases} "
                       f"({cases - agreed} disagree)")
for quote, texts, expected in disagreements:
    print(f"    disagreement: expected {expected} for {quote!r} in {texts}")
check(said_yes >= 300 and near_yes >= 150,
      "CONTROL: enough of the cases were yes, and enough of those were near matches, for the "
      "agreement to mean something")

# ==========================================================================================
print("\n=== I. a long, repetitive transcript is searched quickly ===")
SENTENCE = "we check the list again before we close the shop and then we count the cash twice"
LONG_TEXT = " ".join([SENTENCE] * 2000) + " " + TRANSCRIPT
long_index = rules.transcript_index(LONG_TEXT)
NEAR_OK = SENTENCE.replace("shop", "zzq")                                          # 1 different
NEAR_BAD = SENTENCE.replace("list", "zzq").replace("shop", "zzq").replace("cash", "zzq")   # 3
# Three different, all in two of the quote's three pieces: the first piece is found 2000 times
# and every one of those places has to be looked at before the answer is no.
HEAVY = SENTENCE.split()
for at in (6, 8, 12):
    HEAVY[at] = "zzq"
HEAVY_BAD = " ".join(HEAVY)
started = time.monotonic()
answers = [supported(q, long_index)
           for q in (SENTENCE, NEAR_OK, NEAR_BAD, HEAVY_BAD, EXACT_13, STITCHED)]
took = time.monotonic() - started
print(f"  {len(LONG_TEXT.split())} words, 6 quotes: {took:.2f} s")
check(answers == [True, True, False, False, True, False],
      f"a long transcript gives the right answers, repeats and all ({answers})")
check(took < 30, f"6 quotes against {len(LONG_TEXT.split())} words took {took:.1f} s (limit 30 s)")

# ==========================================================================================
print("\n=== J. through a whole write-up: the reader never sees a stitched quote in quote marks ===")
APPLIES = " Applies to: Ledgerleaf"


def writeup(section1, section5, item):
    return (f"## 1. What it is\n{section1}\n\n"
            f"## 5. Worth keeping verbatim\n{section5}\n\n"
            f"## 6. What applies to me\n{item}\n\n"
            "## 7. What contradicts what I believe\nNothing.\n\n"
            "## 8. Try this week\nDo the thing.\n")


def run(text):
    with contextlib.redirect_stdout(io.StringIO()):
        return ww.clean_summary(text, TRANSCRIPT, verdict="YES", meta=META)


def section6(text):
    lines = text.splitlines()
    bounds = ww.section_bounds(lines, ww.APPLIES_HEADING_RE)
    return "\n".join(l.strip() for l in lines[bounds[0] + 1:bounds[1]]
                     if l.strip() and not l.startswith(">"))


real, fake = f'- "{EXACT_13}"', f'- "{STITCHED}"'
good_item = f'Most applicable: Water the beds from barrels. "{EXACT_9}"{APPLIES}'
text, report = run(writeup("A school garden club.", f"{real}\n{fake}", good_item))
check(f'"{EXACT_13}"' in text and f'"{STITCHED}"' not in text and STITCHED in text,
      "section 5: the real quote keeps its marks, the stitched one loses them and keeps its words")
check(report["invented"] == 1 and "1 phrase(s) below lost their quote marks" in text,
      f"and the banner counts exactly that one ({report['invented']})")
check(section6(text).startswith("Most applicable: Water the beds") and report["struck"] == 0,
      "section 6: an item whose quote is real is kept")

for label, bad in (("stitched", STITCHED), ("scattered", SCATTERED_4)):
    item = f'Most applicable: Plant tomatoes in the north bed. "{bad}"{APPLIES}'
    text, report = run(writeup("A school garden club.", real, item))
    check(section6(text) == "nothing" and report["struck"] == 1 and report["kept"] == 0,
          f"section 6: an item whose only quote is {label} is cut, and section 6 says nothing")

with contextlib.redirect_stdout(io.StringIO()):
    final = ww.finalize_summary(writeup("A school garden club.", f"{real}\n{fake}", good_item),
                                TRANSCRIPT, verdict="YES", meta=META)
check(f'"{STITCHED}"' not in final and f'"{EXACT_13}"' in final,
      "finalize_summary, which every backend goes through, gives the same result")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
