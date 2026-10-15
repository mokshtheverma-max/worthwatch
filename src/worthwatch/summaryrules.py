"""What a write-up is allowed to say: the checks every summary passes before anyone reads it.

Moved out of core.py word for word. Everything here is plain text in, plain text out: it reads
no setting and no folder, and it never imports core, so nothing core rebinds at run time (your
projects, the people in your context file, the folders) can change under it. The parts of these
rules that need your settings stay in core.py: the closed list of projects (PROJECTS,
PROJECT_ALIASES, PEOPLE_IN_CONTEXT, projects_named, people_named) and section 6's rebuild,
enforce_applies_citations. core imports back every name here that it uses, so core.X still
works, and tests/test_modules.py checks that the split stays safe.

  the quote check     a double-quoted span is a claim that the video said those words, so
                      quote_supported_by() checks it against the transcript
  no thinking         thinking_aloud_lines() finds a model talking to itself
  section 7           enforce_contradictions() collapses a paragraph that only explains why
                      there is no contradiction
  Opportunity line    enforce_opportunity_line() keeps it only on an OPPORTUNITY, with three
                      real fields
  the verdict gates   demote_if_nothing_applies() and gate_opportunity_verdict()
"""
import re

APPLIES_HEADING_RE = re.compile(r"^##\s*6\b")
BELIEFS_HEADING_RE = re.compile(r"^##\s*7\b")
TRY_HEADING_RE = re.compile(r"^##\s*8\b")
MOST_APPLICABLE_RE = re.compile(r"^(\s*(?:[-*]\s*)?(?:\*\*)?Most applicable:(?:\*\*)?\s*)(.*)$",
                                re.IGNORECASE)
IGNORE_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?Ignore:", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s*#")
MOST_APPLICABLE_PREFIX = "Most applicable: "
# The bullet, number or ordinal a ranked item carries. Stripped when the item is read, so
# rebuilding the section cannot produce "- - The skill-audit prompt..." and promoting the
# first survivor cannot produce "Most applicable: - Second: the Manage panel...".
ITEM_MARKER_RE = re.compile(r"^\s*(?:[-*]\s*|\d+[.)]\s*)*(?:(?:also|second|third|next|finally)\s*:\s*)?",
                            re.IGNORECASE)
APPLIES_LABEL_RE = re.compile(r"applies to:\s*(.*)$", re.IGNORECASE)

# Section 7, when there is nothing. One line, these words, and no paragraph explaining
# that a cooking video does not contradict a belief about pricing software.
NO_CONTRADICTION_LINE = "nothing in this video touches the beliefs list"
# A section 7 that says any of these is announcing that there is no contradiction, however
# many sentences it takes to do it.
DENIAL_RE = re.compile(
    r"(does not contradict|doesn't contradict|do not contradict|don't contradict"
    r"|not contradict any|nothing (?:here |in this video )?(?:directly )?contradicts"
    r"|no (?:direct )?contradictions?\b|nothing directly contradicts|nothing contradicts"
    r"|does not (?:test|address|touch|challenge)|doesn't (?:test|address|touch|challenge)"
    r"|is irrelevant|are irrelevant|none of (?:your|my|the) beliefs"
    r"|it does not (?:contradict|conflict)|no beliefs? (?:are|is) contradicted"
    r"|nothing (?:in (?:it|this video) )?pushes against|never touches|never addresses)",
    re.IGNORECASE)
# The other half of a denial: the section simply opens by saying there is none.
OPENS_WITH_NOTHING_RE = re.compile(
    r"\s*(nothing\b|no (?:direct )?contradictions?\b|none of\b|not applicable\b|no beliefs?\b)",
    re.IGNORECASE)
# A body that is the word "nothing" and no more: what is left after this substitution is
# empty only for "Nothing", "None.", "N/A", "nothing at all" and the like.
BARE_NOTHING_RE = re.compile(r"^\s*(?:nothing|none|n/?a|not applicable)\b(?:\s+at all)?[\s.:,;!-]*$",
                             re.IGNORECASE)
# ...unless it also names one. A section that opens "nothing contradicts" and then names a
# specific thing the video said that pulls the other way has done its job; it is the
# section that only explains the absence that rule 1.1 is about. These are the phrases the
# write-ups that already work use to name it ("an analytics tool being recommended as a
# standard sits against your analytics-is-a-deliberate-decision rule").
#
# Two phrases are deliberately NOT here. "challenges the belief", because a local model used
# it to challenge a belief that is not on the list. And "contradicts the belief", because it
# is the tail of "Nothing here contradicts the beliefs list" - the assumption that these
# phrases cannot survive a "not" in front of them is true of the rest and false of that one,
# which would quietly rescue sections that say nothing.
AFFIRMATIVE_RE = re.compile(
    r"(in tension with|sits against|pushes against the belief|cuts against|runs counter"
    r"|pulls against"
    r"|closest tension|closest friction|one real friction|friction point"
    r"|tension worth|worth flagging|one tension|one real conflict"
    r"|this is the contradiction)", re.IGNORECASE)

# The Opportunity line belongs to an OPPORTUNITY video and nowhere else. A MAYBE that writes
# "works: N/A | for: N/A" has a line with an empty field, which is worse than no line,
# because append_opportunity() says UNKNOWN honestly when the line is absent.
OPPORTUNITY_LINE_RE_ANY = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?Opportunity line:", re.IGNORECASE)
EMPTY_FIELD_RE = re.compile(r"^\s*(?:\*\*)?(n/?a|unknown|none|tbd|not applicable|-)\s*(?:\*\*)?\s*$",
                            re.IGNORECASE)

# Thinking aloud. Sentence-initial markers are matched case-sensitively on the capital,
# because "actually" is an ordinary adverb in the middle of a sentence and "Actually," at
# the head of one is the model talking to itself.
THINKING_ALOUD_OPENERS = re.compile(
    r"(?:^|(?<=[.!?])\s|(?<=[.!?]\s)\s*)(Actually|Wait|Hmm|Hold on|On reflection|"
    r"On second thought|Re-reading|Rereading|Looking back at|Let me|First, let)\b")
THINKING_ALOUD_ANY = re.compile(
    r"\b(re-?reading the (?:rules|instructions|context)|let me (?:re-?read|check|think|see)"
    r"|i should (?:not )?(?:write|include|say|check)|i need to (?:check|find|re-?read)"
    r"|there are no items in this section|every potential takeaway"
    r"|cannot be anchored|can't be anchored|per the rules above|the rules say"
    r"|the instructions? (?:say|above|require)|does not belong here because"
    r"|which does ?n[o']?t specifically apply)", re.IGNORECASE)

REASK_NOTE = """IMPORTANT, the previous attempt at this summary was rejected. It thought aloud in the output: it wrote its own reasoning about whether an item qualified instead of writing only the finished answer. Write the summary again from the same transcript.

Sections 6, 7 and 8 each contain EITHER finished items OR the single word "nothing" - never a paragraph about what you considered and rejected, never "Actually", never "re-reading", never "let me", never "I should". If nothing qualifies, the section is the word "nothing" and stops there."""


def section_bounds(lines, heading_re):
    """(start, end) line indices for one numbered heading, end exclusive, or None."""
    start = next((i for i, l in enumerate(lines) if heading_re.match(l.strip())), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")),
               len(lines))
    return start, end


def is_nothing_useful(text):
    if not text:
        return True
    return text.strip().lower().startswith(("none", "nothing", "n/a", "not applicable"))


def canon_tokens(text):
    """Lowercase alphanumeric words, nothing else. Auto-generated captions carry no
    reliable punctuation or capitalisation and clean_vtt leaves HTML entities behind, so a
    quote can only honestly be compared on its words."""
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text.lower())
    return re.findall(r"[a-z0-9]+", text)


def transcript_index(transcript_text):
    """(haystack, bigrams) - what quote_supported_by needs, built once per summary."""
    words = canon_tokens(transcript_text)
    return " " + " ".join(words) + " ", {(words[i], words[i + 1]) for i in range(len(words) - 1)}


def document_index(transcript_text, meta=None):
    """The index the whole-summary quote check uses: the transcript plus the video's own
    title and uploader. Section 1 has to be able to name the channel - "Dr. G's Lifestyle
    Medicine Network" is a true quotation of something the video never says out loud - and
    deleting section 1 to protect section 6 would be a worse guard than none. Section 6
    still checks against the transcript alone."""
    extra = " ".join(str((meta or {}).get(k) or "") for k in ("title", "uploader", "channel"))
    return transcript_index(transcript_text + " " + extra)


def quote_supported_by(quote, transcript_words, transcript_bigrams):
    """Is this quote really in the transcript? Exact match on the canonical word string
    first. Then one tolerant pass, because a model quoting a caption tends to repair its
    grammar in passing: a quote whose consecutive word-pairs are nearly all present in the
    transcript is still the video's own words. Below that it is an invention."""
    words = canon_tokens(quote)
    if len(words) < 3:
        return False       # two words prove nothing; they would match almost any video
    if " " + " ".join(words) + " " in transcript_words:
        return True
    pairs = [(words[i], words[i + 1]) for i in range(len(words) - 1)]
    return bool(pairs) and sum(1 for pr in pairs if pr in transcript_bigrams) / len(pairs) >= 0.8


def quoted_spans(text):
    """Every double-quoted span on this line, read as pairs.

    This was a regex until 2026-09-10, and the regex was wrong in a way that only showed
    up once the quote check was run over whole summaries: `"([^"\\n]{8,200})"` on the line
    `"Brick" (the phone-locking tool he uses daily). "Zebra calendar" (his term...)` skips
    "Brick" for being under eight characters and then matches from Brick's CLOSING quote
    to Zebra's OPENING one, so the tool's own prose - never spoken by anyone - was handed
    to the transcript check as a quotation and of course failed. Quotes come in pairs;
    splitting on the quote character is the only reading that cannot straddle two of them.
    An unterminated final quote is dropped, because there is no span to check."""
    parts = text.split('"')
    if len(parts) < 3:
        return []
    spans = parts[1::2]
    if len(parts) % 2 == 0:        # odd number of quote marks: the last one opens nothing
        spans = spans[:-1]
    return [s for s in spans if len(s) <= 400]


def checkable_quotes(text):
    """The quoted spans long enough to prove anything. A two-word quote is not evidence
    and is not a forgery either, so it is neither kept nor struck on."""
    return [q for q in quoted_spans(text) if len(canon_tokens(q)) >= 3]


def line_quotes_the_video(line, haystack, bigrams):
    """True, False, or None when the line quotes nothing checkable."""
    quotes = checkable_quotes(line)
    if not quotes:
        return None
    return any(quote_supported_by(q, haystack, bigrams) for q in quotes)


def unquoted(text):
    """The line with its quoted spans removed. Everything that looks for the model's own
    voice has to look here: a video that says "let me show you" is not thinking aloud."""
    return re.sub(r'"[^"\n]*"', " ", text)


def thinking_aloud_lines(summary_text):
    """Line numbers (0-based) where the model talked to itself instead of answering.
    Quoted spans are removed first, and headings are exempt."""
    out = []
    for i, raw in enumerate(summary_text.splitlines()):
        if HEADING_RE.match(raw) or not raw.strip():
            continue
        probe = unquoted(raw)
        if THINKING_ALOUD_OPENERS.search(probe) or THINKING_ALOUD_ANY.search(probe):
            out.append(i)
    return out


# Which sections a quotation mark is a claim about the video in. Three are exempt, each
# for a reason that was measured over the 70 Sonnet summaries already on disk rather than
# assumed:
#   3  is where on-screen numbers are reported, and text on screen is not in the
#      transcript by definition. Every one of the five section 3 lines this rule would
#      have touched was a real figure read off a real dashboard.
#   7  is checked against CONTEXT's beliefs, so quoting one of my beliefs is the correct
#      way to write it, not a forgery.
#   8  is my action, not the video's words; Sonnet uses quote marks there for placeholders
#      ("X parent links sent by people I actually know") and for my own open decisions.
# 6 is exempt here only because it has a harder rule of its own (core's
# enforce_applies_citations).
QUOTE_EXEMPT_SECTIONS = (r"^##\s*3\b", r"^##\s*6\b", r"^##\s*7\b", r"^##\s*8\b")


def enforce_quotes_are_the_video(summary_text, haystack, bigrams):
    """Rule 1.4. A quoted span is a claim that the video said those words, and CONTEXT.md
    is the most convincing thing a model has to hand when the video gave it nothing. So
    every quote outside the exempt sections is checked, and one that is not in the
    transcript loses its quote marks. The words stay - they may well be a fair paraphrase,
    and deleting a whole section 1 to punish one loose phrase would be a worse guard than
    none - but the claim that the video said them does not, and the banner counts it, so
    the removal is visible rather than a quiet laundering. Returns (text, n)."""
    lines = summary_text.splitlines()
    exempt = set()
    for pattern in QUOTE_EXEMPT_SECTIONS:
        bounds = section_bounds(lines, re.compile(pattern))
        if bounds:
            exempt.update(range(bounds[0], bounds[1]))
    unquoted_count = 0
    for i, raw in enumerate(lines):
        if i in exempt or HEADING_RE.match(raw) or not raw.strip():
            continue
        bad = [q for q in checkable_quotes(raw) if not quote_supported_by(q, haystack, bigrams)]
        for span in bad:
            lines[i] = lines[i].replace(f'"{span}"', span.strip())
            unquoted_count += 1
    if not unquoted_count:
        return summary_text, 0
    banner = (f"> **{unquoted_count} phrase(s) below lost their quote marks: the transcript "
              f"was searched and the video does not say them in those words.**\n")
    return banner + "\n".join(lines), unquoted_count


def tidy_blanks(lines):
    """A deleted line leaves its blank behind, and two blanks in a row read as a gap where
    something used to be - exactly the wrong impression to give."""
    out = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return out


def enforce_contradictions(summary_text):
    """Section 7 says what the video pushed against, or it says one line. A paragraph
    explaining that a cooking video does not contradict a belief about pricing software is
    not a finding; it is the model filling a heading. Returns
    (text, collapsed)."""
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, BELIEFS_HEADING_RE)
    if bounds is None:
        return summary_text, False
    start, end = bounds
    body = "\n".join(lines[start + 1:end]).strip()
    if not body:
        collapse = True
    elif not BARE_NOTHING_RE.sub("", body).strip():
        # The body is the word "nothing" and no more. This was a length bound first and
        # that was wrong: is_nothing_useful() only looks at how a string STARTS, almost
        # every good section 7 here opens with the word "Nothing" and then names a real
        # tension, and any bound loose enough to catch "None." also caught "Nothing here
        # contradicts outright, but it pulls against distribution-is-the-hard-part."
        collapse = True
    elif APPLIES_LABEL_RE.search(body):
        # An "Applies to:" label means this is a section 6 or section 8 item that landed
        # under the wrong heading - measured 2026-09-10, when the local model put a
        # strength-training action here and left section 8 off the page entirely. It is
        # not a contradiction whatever else it is, and section 6 has already had its say.
        collapse = True
    else:
        denial = bool(DENIAL_RE.search(body)) or bool(OPENS_WITH_NOTHING_RE.match(body))
        collapse = denial and not AFFIRMATIVE_RE.search(body)
    if not collapse:
        return summary_text, False
    already = body.strip().lower().rstrip(".") == NO_CONTRADICTION_LINE
    lines[start + 1:end] = ["", NO_CONTRADICTION_LINE, ""]
    return "\n".join(tidy_blanks(lines)), not already


# The summary's own Opportunity line, read into its three fields. (OPPORTUNITIES.md, the file
# these lines are recorded in, is written by core.)
SUMMARY_OPPORTUNITY_RE = re.compile(
    r"Opportunity line:\s*works:\s*(.*?)\s*\|\s*for:\s*(.*?)\s*\|\s*evidence:\s*(.*)", re.IGNORECASE)


def _opportunity_field(text):
    text = re.sub(r"\*\*|__", "", str(text or "")).replace("|", "/")
    return re.sub(r"\s+", " ", text).strip() or "UNKNOWN"


def parse_opportunity_line(summary_text):
    """The summary's Opportunity line as {works, for, evidence}, or None if it wrote none."""
    for line in (summary_text or "").splitlines():
        m = SUMMARY_OPPORTUNITY_RE.search(line)
        if m:
            return {"works": _opportunity_field(m.group(1)), "for": _opportunity_field(m.group(2)),
                    "evidence": _opportunity_field(m.group(3))}
    return None


def enforce_opportunity_line(summary_text, verdict):
    """The Opportunity line is written for an OPPORTUNITY video and for nothing else, and
    never with an empty field in it. Dropping it is not a loss: append_opportunity() falls
    back to section 2 and says UNKNOWN, which is true, where "works: N/A" is just noise in
    OPPORTUNITIES.md and in the weekly table built from it. Returns (text, dropped_why)."""
    lines = summary_text.splitlines()
    for i, raw in enumerate(lines):
        if not OPPORTUNITY_LINE_RE_ANY.match(raw):
            continue
        parsed = parse_opportunity_line(raw)
        if verdict and verdict != "OPPORTUNITY":
            why = f"the verdict is {verdict}, not OPPORTUNITY"
        elif parsed is None:
            why = "it did not have the three fields"
        elif any(EMPTY_FIELD_RE.match(parsed[k]) for k in ("works", "for", "evidence")):
            why = "a field came back empty (N/A or UNKNOWN)"
        else:
            return summary_text, None
        del lines[i]
        return "\n".join(tidy_blanks(lines)), why
    return summary_text, None


# --- the three verdict gates ---------------------------------------------------------
# Measured against one reader's own labels: far more videos were called YES or OPPORTUNITY
# than were worth keeping, and recall was already complete, so every gate here is a precision
# gate that must not cost a keep. `worthwatch score` measures them against your labels.


def applies_is_nothing(summary_text):
    """True when section 6 came to the word "nothing", after every guard has run.

    Read off the finished text rather than passed down from clean_summary's report, and
    deliberately so: the text is what the reader sees, and process_video, cmd_resummarize
    and process_candidates all reach this point by different routes. A missing section 6 is
    NOT "nothing" - it raises its own SECTION 6 NOT VERIFIED banner and means unmeasured,
    which is a different fact and must not demote anything."""
    lines = (summary_text or "").splitlines()
    bounds = section_bounds(lines, APPLIES_HEADING_RE)
    if bounds is None:
        return False
    body = [l.strip() for l in lines[bounds[0] + 1:bounds[1]]]
    body = [l for l in body if l and not l.startswith(">")]     # the guard's own banners
    if not body:
        return True
    return all(is_nothing_useful(MOST_APPLICABLE_RE.sub(r"\2", l) or l) for l in body)


# RULE 1. Whatever the listing said and whatever the transcript read said, a video whose
# section 6 came to "nothing" is a MAYBE. The digest's job is to tell you what to use this
# week; a YES that applies to none of your projects is a YES about nothing.
def demote_if_nothing_applies(verdict, summary_text):
    """(verdict, why or None). Only ever demotes, and only to MAYBE."""
    if verdict not in ("YES", "OPPORTUNITY"):
        return verdict, None
    if not applies_is_nothing(summary_text):
        return verdict, None
    return "MAYBE", (f"demoted from {verdict}: section 6 came to nothing, so the video "
                     f"applies to no project")


# RULE 2. The OPPORTUNITY gate. An OPPORTUNITY is "a documented way a person in my position
# gets paid, with numbers shown, that is not already killed", and "a story about a big
# company, a merger, a funding round or a hiring war is never OPPORTUNITY". The prompt says it
# and this checks it, because the prompt alone does not hold.
#
# The two halves are checked on the Opportunity line, which is where the summary states what
# works and who pays, and not on the whole summary: a summary of an acquisition video
# mentions the acquisition everywhere, and the question is what the video says somebody is
# PAID for.
OPPORTUNITY_NEEDS_A_NUMBER = re.compile(r"\d")
# Derived from real Opportunity lines rather than imagined, and it needs re-measuring as more
# arrive. Each entry is (what it is, the pattern).
NEVER_AN_OPPORTUNITY = (
    ("an acquisition or a merger",
     re.compile(r"\b(acquisitions?|acquir\w+|mergers?|merge[sd]?\s+with|takeovers?|bought\s+by"
                r"|buyer\s+of|sells?\s+itself|sold\s+to\s+the\s+incumbent"
                # One company buying another, said without the word: the real Texas
                # Instruments line reads "buys a design-only rival", and the first version of
                # this pattern missed it. The object has to be a company, because "buy a
                # domain and resell it" is a real way to get paid and must not match.
                r"|buys?\s+(?:a|an|the|its)\s+(?:\w+[\s-]+){0,3}"
                r"(?:rival|competitor|compan\w+|startups?|firms?|makers?|business|team|lab))\b",
                re.I)),
    ("a funding round or a valuation",
     re.compile(r"\b(series\s+[a-h]\b|seed\s+round|funding\s+round|venture\s+round"
                r"|rais\w+\s+(?:\$|\d|about)|valuations?|ipo)\b", re.I)),
    ("a hiring war",
     re.compile(r"\b(talent\s+war|hiring\s+war|poach\w*|hired?\s+one\s+by\s+one"
                r"|compensation\s+packages?|salary\s+band)\b", re.I)),
)


def opportunity_gate(summary_text):
    """(passes, why not). Pure text, so it can be run over a summary already on disk."""
    line = parse_opportunity_line(summary_text)
    if line is None:
        return False, ("no Opportunity line survived, so nothing names a way of getting paid")
    claim = f"{line['works']} | {line['for']}"
    for what, pattern in NEVER_AN_OPPORTUNITY:
        m = pattern.search(claim)
        if m:
            return False, f"what works is {what} ({m.group(0)!r}), never an OPPORTUNITY"
    if not OPPORTUNITY_NEEDS_A_NUMBER.search(" ".join(line.values())):
        return False, "the Opportunity line carries no number at all"
    return True, None


def gate_opportunity_verdict(verdict, summary_text):
    """(verdict, text, why). An OPPORTUNITY that fails the gate becomes a MAYBE, and its
    Opportunity line goes with it: append_opportunity() would otherwise record a road under a
    verdict that no longer exists, and the weekly table is built from that file."""
    if verdict != "OPPORTUNITY":
        return verdict, summary_text, None
    passes, why = opportunity_gate(summary_text)
    if passes:
        return verdict, summary_text, None
    text, _ = enforce_opportunity_line(summary_text, "MAYBE")
    return "MAYBE", text, f"demoted from OPPORTUNITY: {why}"
