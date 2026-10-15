"""Second half of the section-6 guard tests: how section 8 follows section 6, and how the
scoping fix behaves. Kept separate from test_citations.py only so both stay readable."""
import io, sys
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "citations2")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

fails = []
def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: fails.append(msg)

# The invented transcript test_citations.py uses too.
TR = (FIXTURES / "bakery-talk.txt").read_text(encoding="utf-8")
words = ww.canon_tokens(TR)
REAL = " ".join(words[40:49])
REAL2 = " ".join(words[200:207])

# 2026-09-20: fixtures carry the "Applies to:" label and the body is read rather than
# grepped, for the reasons written out at the top of test_citations.py. Same contract, same
# day it changed, same suite that never caught up.
APPLIES = " Applies to: Ledgerleaf"


def labelled(content):
    return content if ww.is_nothing_useful(content) else content + APPLIES


def applies_body(text):
    """Section 6's body, banners dropped: what a reader actually sees under the heading."""
    lines = text.splitlines()
    bounds = ww.section_bounds(lines, ww.APPLIES_HEADING_RE)
    if bounds is None:
        return None
    body = [l.strip() for l in lines[bounds[0] + 1:bounds[1]]]
    return chr(10).join(l for l in body if l and not l.startswith(">"))


def summary(most_applicable, extra_items=(), try_line="Do the thing.", section5="None."):
    items = "\n".join(f"- {labelled(x)}" for x in extra_items)
    return ("## 5. Worth keeping verbatim\n" + section5 + "\n\n"
            "## 6. What applies to me\n"
            "Most applicable: " + labelled(most_applicable) + "\n" + items + "\n"
            "Ignore: the affiliate links, and anything implying a pass guarantee.\n\n"
            "## 7. What contradicts what I believe\nNothing.\n\n"
            "## 8. Try this week\n" + try_line + "\n")

print("=== K. a survivor lower down keeps this week's action alive ===")
out = ww.finalize_summary(summary(
    "Invented top item with no quote at all.",
    extra_items=['A real second point. "' + REAL + '"'],
    try_line="Apply the real second point."), TR)
check("Invented top item" not in out, "the invented top item still falls")
check("Most applicable: A real second point" in out,
      "the cited second item survives, and is promoted into the line the digest reads")
check(ww.extract_try_this_week(out) == "Apply the real second point.",
      "section 8 is NOT forced to nothing when something in section 6 survived")

print("\n=== L. an honest empty section 6 forces section 8 too ===")
# The action is one no standing decision in the example rules touches, so only the section 6
# rule can turn it into "nothing".
try_line = "Go visit ten bakeries and ask how they keep their books."
check(ww.try_line_contradiction(try_line) is None,
      "CONTROL: no standing decision would replace this action on its own")
out = ww.finalize_summary(summary("nothing", try_line=try_line), TR)
check(ww.extract_try_this_week(out) == "nothing",
      "section 8 cannot invent an action the section above found nothing for")
check("removed here" not in out, "and that is not reported as a strike")

print("\n=== M. an unparseable summary is warned about, not overwritten ===")
out = ww.finalize_summary("## 1. What it is\nA video.\n\n## 8. Try this week\nDo something.\n", TR)
check("SECTION 6 NOT VERIFIED" in out, "the banner is there")
check(ww.extract_try_this_week(out) == "Do something.",
      "section 8 left alone when there was no section 6 to check against")

print("\n=== O. an echo of the instruction in section 5 cannot leak into the digest ===")
echo = '"Most applicable: " The strategy of building a functional product first.'
out = ww.finalize_summary(summary("Invented, uncited.", section5=echo), TR)
check(ww.extract_top_takeaway(out) == "nothing",
      "extract_top_takeaway reads section 6, not the echo above it")
check(echo.split('"')[2].strip()[:20] in out, "the echo itself is left where the model put it")

print("\n=== P. both fetch paths run the guard, so no backend can skip it ===")
src = Path(ww.__file__).read_text(encoding="utf-8")
_defs = src.count("def finalize_summary(")
_calls = src.count("finalize_summary(") - _defs
# Five call sites, counted rather than asserted as one total: cmd_resummarize (added
# after this check was written, which is why the old total of 5 read 6 and failed),
# summarise_one, and three in process_video including the --compare pair. Split so
# the next time one moves, the failure says which number moved.
#
# 2026-09-21: it read 6 again when fix 2 gave the retry path its own copy of the
# summarise-then-finalize chain, which is the exact thing this check exists to catch. The
# two copies are now one function, summarise_one, and both paths call it. So the count is
# back to five and the assertion below says which paths those are.
check(_defs == 1 and _calls == 5,
      f"finalize_summary defined once ({_defs}) and called at every summarize site "
      f"({_calls} call(s), want 5)")
_chain = src.count("summarise_one(")
check(src.count("def summarise_one(") == 1 and _chain - 1 >= 2,
      f"the fetch path and the retry path share one summarise chain ({_chain - 1} callers)")
_retry = src[src.index("def run_retry_phase("):src.index("def process_candidates(")]
check("summarise_one(" in _retry and "summarize_claude(" not in _retry,
      "and the retry path reaches the backend only through it, so it cannot skip a guard")
check("normalize_punct(summary)" not in src and "normalize_punct(local_summary)" not in src
      and "normalize_punct(claude_summary)" not in src,
      "no summary path still calls normalize_punct on its own and skips the guard")

print("\n=== Q. the guard survives odd but legal shapes ===")
for label, text in [
    ("empty summary", ""),
    ("section 6 with nothing under it", "## 6. What applies to me\n\n## 7. Next\nx\n"),
    ("section 6 last in the file", "## 6. What applies to me\nMost applicable: no quote here.\n"),
    ("bulleted Most applicable", "## 6. What applies to me\n- Most applicable: no quote.\n"),
    ("bold Most applicable", "## 6. What applies to me\n**Most applicable:** no quote.\n"),
]:
    try:
        got = ww.finalize_summary(text, TR)
        ok = isinstance(got, str)
    except Exception as e:
        ok = False
        got = f"{type(e).__name__}: {e}"
    check(ok, f"{label} -> no crash")
    if ok and "Most applicable" in text:
        check("nothing" in got, f"{label} -> uncited item struck")

print("\n=== R. an empty transcript cannot accidentally validate anything ===")
out = ww.finalize_summary(summary('A claim. "' + REAL + '"'), "")
check(applies_body(out) == "nothing",
      "with no transcript to match against, no quote is supported")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
