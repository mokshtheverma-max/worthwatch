"""The six verdict rules, each with a control that proves it can fail.

Every rule here is a precision gate, and a precision gate that fires on everything is worse
than none: it would cost recall. So no rule is checked only by watching it fire. Each one is
checked twice, once on the input it is for and once on an input it must leave alone. The rules
that read your rules.toml are checked against the invented example person's rules
(src/worthwatch/examples/rules.toml), which carry one rule of every shape the code knows: a
plain pattern, an `unless`, a capitalisation-aware name, a heat map that is and is not a page
tracker, a rule that holds for one project only, one a project is exempt from, and a killed
product that only building or selling it breaks.

Watch it go red: replace try_line_contradiction's body with `return None` in a copy of
src/worthwatch/core.py and run this file against the copy.
"""
import io
import json
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "rules")
from worthwatch import scoring

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def summary(applies="nothing", try_line="Do the thing.", opportunity=None):
    """A summary with the sections these rules read, and nothing else."""
    opp = f"\nOpportunity line: {opportunity}" if opportunity else ""
    return (f"## 3. Evidenced versus asserted\nSomething was said.{opp}\n\n"
            f"## 6. What applies to me\n{applies}\n\n"
            f"## 7. What contradicts what I believe\n"
            f"nothing in this video touches the beliefs list\n\n"
            f"## 8. Try this week\n{try_line}\n")


def label(vid, tool, yours, listing="MAYBE", applies="items", title=None):
    """One row of a labels file, as scoring.build_labels writes it."""
    return {"id": vid, "date": "2026-03-02", "channel": "Example Channel",
            "title": title or f"An invented video {vid}", "url": f"https://www.youtube.com/watch?v={vid}",
            "tool_verdict": tool, "listing_verdict": listing, "tool_reason": "",
            "applies": applies, "has_summary": True, "your_verdict": yours}


# ===========================================================================================
print("=== RULE 1. section 6 says nothing, so the verdict is MAYBE ===")
nothing = summary(applies="nothing")
items = summary(applies='Most applicable: a real point. "some words" Applies to: Ledgerleaf')
missing = "## 1. What it is\nA video.\n\n## 8. Try this week\nDo something.\n"

check(ww.applies_is_nothing(nothing), "a section 6 that is the word nothing reads as nothing")
check(not ww.applies_is_nothing(items), "CONTROL: a section 6 with an item does not")
check(not ww.applies_is_nothing(missing),
      "CONTROL: a MISSING section 6 is not nothing, it is unmeasured, and must not demote")

for before in ("YES", "OPPORTUNITY"):
    after, why = ww.demote_if_nothing_applies(before, nothing)
    check(after == "MAYBE" and why, f"{before} with nothing applying is demoted to MAYBE")
    check(ww.demote_if_nothing_applies(before, items) == (before, None),
          f"CONTROL: {before} with a surviving item is left alone")
check(ww.demote_if_nothing_applies("MAYBE", nothing) == ("MAYBE", None),
      "CONTROL: a MAYBE is not touched, and nothing is ever promoted")
check(ww.demote_if_nothing_applies("NO", nothing) == ("NO", None), "CONTROL: nor is a NO")
check(ww.demote_if_nothing_applies("YES", missing)[0] == "YES",
      "CONTROL: an unverified section 6 leaves the verdict where it was")

# The measurement this rule is for, on an invented labels file: the videos the transcript read
# promoted from MAYBE to a positive and whose section 6 then came to nothing are the ones the
# rule demotes, and the score says how many of them you would keep.
LABELS = {r["id"]: r for r in [
    label("MaraVid0001", "YES", "SKIP", applies="nothing"),
    label("MaraVid0002", "OPPORTUNITY", "SKIP", applies="nothing"),
    label("MaraVid0003", "YES", "SKIP", applies="nothing"),
    label("MaraVid0004", "YES", "KEEP", applies="items"),
    label("MaraVid0005", "MAYBE", "SKIP", applies="nothing"),
]}
_scored = scoring.score(LABELS, scoring.recorded(LABELS))
check(len(_scored["upgraded_empty"]["SKIP"]) == 3 and not _scored["upgraded_empty"]["KEEP"],
      f"the labels hold 3 upgrades that came to nothing, all of them skipped "
      f"({_scored['upgraded_empty']})")
check(_scored["upgraded_empty"]["KEEP"] == [],
      "so on these labels the rule costs no keep")
LABELS["MaraVid0003"]["your_verdict"] = "KEEP"
check(scoring.score(LABELS, scoring.recorded(LABELS))["upgraded_empty"]["KEEP"] == ["MaraVid0003"],
      "CONTROL: an upgrade-to-nothing you keep is counted as the true positive it would cost")
LABELS["MaraVid0003"]["your_verdict"] = "SKIP"

# ===========================================================================================
print("\n=== RULE 2. the OPPORTUNITY gate ===")
real = ("works: set up the books for small bakeries | for: bakery owners paying $300 a setup | "
        "evidence: shown: an invoice")
no_number = "works: set up the books for small bakeries | for: bakery owners | evidence: shown: a slide"
# One company buying another, said without the word: the pattern has to see "buys a ... rival".
acquisition = ("works: a bakery chain buys a family-run rival to cut costs | "
               "for: a national bakery chain | evidence: claimed: $7.5 million")
funding = ("works: raise a Series B on a recipe benchmark | for: the founders | "
           "evidence: claimed: $43 million")
hiring = ("works: bakers hired one by one on packages from an acquisition budget | "
          "for: a big chain | evidence: claimed: $100 million offers")

check(ww.opportunity_gate(summary(opportunity=real))[0],
      "CONTROL: a way of getting paid with a number passes the gate")
check(not ww.opportunity_gate(summary(opportunity=no_number))[0],
      "an Opportunity line with no number at all fails")
check(not ww.opportunity_gate(summary())[0], "no Opportunity line at all fails")
for what, line in (("an acquisition", acquisition), ("a funding round", funding),
                   ("a hiring war", hiring)):
    passes, why = ww.opportunity_gate(summary(opportunity=line))
    check(not passes, f"{what} fails the gate ({why})")

verdict, text, why = ww.gate_opportunity_verdict("OPPORTUNITY", summary(opportunity=acquisition))
check(verdict == "MAYBE" and why, "a failed gate demotes OPPORTUNITY to MAYBE")
check("Opportunity line:" not in text,
      "and takes the Opportunity line with it, so OPPORTUNITIES.md records no road")
check(ww.gate_opportunity_verdict("OPPORTUNITY", summary(opportunity=real))[0] == "OPPORTUNITY",
      "CONTROL: a line that passes keeps the verdict")
check(ww.gate_opportunity_verdict("YES", summary())[0] == "YES",
      "CONTROL: the gate never touches a YES")

# On invented OPPORTUNITY write-ups with labels: the one you keep still passes, and at least one
# you skip does not.
OPPS = {"MaraVid0011": (summary(opportunity=real), "KEEP"),
        "MaraVid0012": (summary(opportunity=acquisition), "SKIP"),
        "MaraVid0013": (summary(opportunity=funding), "SKIP")}
_gated = {vid: ww.opportunity_gate(text) for vid, (text, _y) in OPPS.items()}
check(all(_gated[v][0] for v, (_t, y) in OPPS.items() if y == "KEEP"),
      "every OPPORTUNITY you keep still passes the gate")
check(sum(1 for v in OPPS if _gated[v][0]) < len(OPPS), "and at least one you skip does not")

# ===========================================================================================
print("\n=== RULE 3. a Try this week that contradicts a standing decision ===")
cases = [
    ("Run a $10 a day Google Ads campaign for Ledgerleaf.", "no paid ads"),
    ("Boost a post on Facebook to bakery owners in your town.", "no paid ads"),
    ("Cold email forty more bakeries from a scraped list.", "cold email is closed as a first move"),
    ("Add Google Analytics to ledgerleaf.app to see the drop-off.", "no page trackers, ever"),
    ("Publish a pricing page with three tiers for the planner.",
     "nothing new is priced without a test first"),
    ("Announce a free tier on the landing page.", "nothing new is priced without a test first"),
    ("Set the price at $19 a month for the planner template.",
     "nothing new is priced without a test first"),
    ("Tell the bakery the app gives tax advice.", "never promise tax advice"),
    ("Hold the bakeries' takings in escrow until month end.", "never hold customer money"),
    ("Add a countdown timer to the checkout.", "no dark patterns"),
    ("Ship it to the App Store.", "no app stores for a web product"),
    ("Start a dog-walking app this weekend.", "killed products are not re-suggested"),
    ("Spin up an agent team to review the run.", "one coding agent at a time"),
    ("Guess the bakery owner's email from the shop's format.", "never guess an email address"),
]
for line, expected in cases:
    got = ww.try_line_contradiction(line)
    check(got == expected, f"{line[:48]!r} -> {got}")

check(ww.try_line_contradiction("Build a checklist app as a side project this weekend.")
      == "killed products are not re-suggested",
      "building the killed checklist app as a product is caught")
for clean in ["Write one plan doc with an executive summary before the next session.",
              "Send the sixteen follow-up drafts already in your inbox.",
              "Photograph one counter's board and set prices_confirmed to true.",
              # The checklist as a PRODUCT is killed; the morning checklist as a way of working
              # is alive and is in the context file's "How I work". So the verb decides, and
              # "run", "write" and "compare" are deliberately not in the rule's list.
              "Run the morning checklist twice, back to back, on the same day.",
              "Write a checklist for Monday's first bake.",
              "Compare this week's checklist with last week's.",
              "nothing"]:
    check(ww.try_line_contradiction(clean) is None, f"CONTROL: left alone: {clean[:52]!r}")

text, name = ww.enforce_try_against_decisions(summary(try_line="Run Google Ads for a week."))
check(name and ww.CONTRADICTS_LINE.format(name=name) in text,
      "the line is replaced by the contradiction, naming the decision")
check(ww.extract_try_this_week(text).startswith("Not tried:"),
      "and that is what reaches history.jsonl and TRY-NEXT.md")
keepme = "Write one plan doc before the next session."
check(ww.enforce_try_against_decisions(summary(try_line=keepme)) == (summary(try_line=keepme), None),
      "CONTROL: a clean line is returned byte for byte, not rewritten")

# Plausible, Fathom and Amplitude are trackers and also ordinary words. The three count only
# when the tracker is named as one: followed by "analytics", written as a web address, or
# capitalised in the middle of a sentence. A capital at the start of a sentence is not a name.
TRACKERS = "no page trackers, ever"
IMPORT = ("Add a check to the Ledgerleaf importer that feeds it a plausible but subtly wrong "
          "bank export and confirm it is rejected.")
for ordinary in [IMPORT,
                 "Check that a plausible but wrong total fails the Ledgerleaf import check.",
                 "Plausible but wrong totals should fail the import for every bank.",
                 "Measure the amplitude of the mixer's hum before trusting the recording.",
                 "Read one failed import's log to fathom why it stopped early."]:
    check(ww.try_line_contradiction(ordinary, "Ledgerleaf") is None,
          f"an ordinary word is not a tracker: {ordinary[:56]!r}")
for named in ["Add Plausible Analytics to the Tidepool site to see which issues are read.",
              "Install Plausible on the Tidepool site to count visits.",
              "Use plausible.io for the Tidepool site's page views.",
              "Send every sign-up event to Amplitude and read the funnel.",
              "Set up Fathom Analytics on the Ledgerleaf landing page.",
              "Add fathom analytics to the Tidepool archive.",
              "Add PostHog session recording to the Ledgerleaf editor.",
              "Put Mixpanel on the sign-up page."]:
    check(ww.try_line_contradiction(named) == TRACKERS,
          f"CONTROL: a tracker named as one is still struck: {named[:56]!r}")
_text, _name = ww.enforce_try_against_decisions(summary(try_line=IMPORT))
check(_name is None and ww.extract_try_this_week(_text) == IMPORT,
      "the importer action reaches history.jsonl and TRY-NEXT.md as it was written")
_named = IMPORT.replace("a plausible but subtly wrong bank export", "its events to Plausible Analytics")
check(ww.enforce_try_against_decisions(summary(try_line=_named))[1] == TRACKERS,
      "CONTROL: the same line naming the tracker is struck, so the check above can fail")

# A heat map counts as a page tracker only as one of what visitors do on a page (clicks,
# scrolls, taps, the mouse or cursor, visitors or users), one put on a site, a landing page, a
# home page or a web address, or heat-map tracking. A heat map of sales, of errors or of open
# rates is a chart, not a tracker.
REPLAY_TIP = ("after a failed import, open that bank export in the viewer and read the error "
              "density heat map. This would give you a picture of why an import failed, beyond "
              "the numbers in the report.")
for chart in [REPLAY_TIP,
              "In the dashboard, toggle the heat maps (sales by hour, waste by day, orders by "
              "weekday) to inspect the week.",
              "write a script that loads one failed import, saves a density heat map image, and "
              "links it from the import report.",
              "Save the late-filing heatmap of one bakery beside its report, for Ledgerleaf.",
              "Plot a heat map of open rates by issue and weekday for the last nine Tidepool issues."]:
    check(ww.try_line_contradiction(chart, "Ledgerleaf") is None,
          f"a heat map that is not a page tracker is left alone: {chart[:56]!r}")
for page in ["Add heat maps to the Tidepool landing page to see where readers stop reading.",
             "Install heatmaps on tidepool.app for a week.",
             "Put a click heat map on the Ledgerleaf home page.",
             "Watch the scroll heatmap of the pricing page this week.",
             "See where visitors click with a heat map of the sign-up form.",
             "Turn on heat map tracking for the Tidepool site.",
             "Use a website heat map to find where readers drop off.",
             "Add a Hotjar heat map to the Ledgerleaf editor."]:
    check(ww.try_line_contradiction(page) == TRACKERS,
          f"CONTROL: a page's heat map is still struck: {page[:56]!r}")
REPLAY_TRY = "A" + REPLAY_TIP[1:REPLAY_TIP.index(". ") + 1]
_text, _name = ww.enforce_try_against_decisions(summary(try_line=REPLAY_TRY))
check(_name is None and ww.extract_try_this_week(_text) == REPLAY_TRY,
      "the import heat-map action reaches history.jsonl and TRY-NEXT.md as it was written")
_paged = REPLAY_TRY.replace("the error density heat map",
                            "the heat maps of where visitors click on tidepool.app")
check(_paged != REPLAY_TRY and
      ww.enforce_try_against_decisions(summary(try_line=_paged))[1] == TRACKERS,
      "CONTROL: the same line with a page's heat map is struck, so the check above can fail")


# A decision can hold for one project only (`only`) or not for some projects (`exempt`); the
# project is section 6's, which is where the rule learns it.
def filed(project, line):
    return summary(applies=f'Most applicable: a real point. "some words" Applies to: {project}',
                   try_line=line)


PRICE = "nothing new is priced without a test first"
FREE = "no free plan on ledgerleaf"
for project, line, expected in [
        # Tidepool is exempt from the price rule: its sponsor slots are priced as Mara likes.
        ("Tidepool", "Set the sponsor price at $200 a slot for the second sponsor.", None),
        ("Tidepool", "Offer the new sponsor a free trial slot this month.", None),
        ("Ledgerleaf", "Add a written annual price to the quote for the bakery that replied.",
         PRICE),
        ("how I work", "Set the price at $19 a month for the planner template.", PRICE),
        ("Ledgerleaf", "Offer bakeries a free trial of the app this month.", FREE)]:
    check(ww.section6_state(filed(project, line))[1] == project,
          f"the fixture is filed under {project}, as section 6 reads it")
    _text, got = ww.enforce_try_against_decisions(filed(project, line))
    check(got == expected, f"filed under {project}: {line[:50]!r} -> {got}")
check(ww.try_line_contradiction("Add a written annual price to the quote.") == PRICE,
      "CONTROL: a line whose project is not known is still held to the price rule")

# The rule that holds for Ledgerleaf alone: no free plan, said any of the ways it gets said.
for project, line, expected in [
        ("Ledgerleaf", "Announce a free plan for bakeries under ten staff.", FREE),
        ("Ledgerleaf", "Put a free tier on the pricing page.", FREE),
        ("Ledgerleaf", "Offer a free trial for one month to the bakery that replied.", FREE),
        ("Ledgerleaf", 'Write "free to start" on the sign-up button.', FREE),
        # Controls: what the rule allows, and "tier" that is not a price.
        ("Ledgerleaf", "Write down why the free sample at the market worked.", None),
        ("Ledgerleaf", "Pitch the top tier of bakery chains first.", None),
        # Controls: the rule is Ledgerleaf's alone; elsewhere the general price rule decides.
        ("how I work", "Announce a free plan for the planner template.", PRICE),
        ("Tidepool", "Offer readers a free plan for the archive.", None)]:
    _text, got = ww.enforce_try_against_decisions(filed(project, line))
    check(got == expected, f"filed under {project}: {line[:52]!r} -> {got}")
check(ww.try_line_contradiction("Announce a free plan for the template.") == PRICE,
      "CONTROL: a free-plan line whose project is not known is held to the general price rule")

# The drift guard: the machine list and the prose list name the same decisions.
_decisions_section = ""
_ctx = ww.CONTEXT_PATH.read_text(encoding="utf-8")
import re as _re
_m = _re.search(r"##\s*Decisions already made(.*?)(?=\n##\s)", _ctx, _re.S)
if _m:
    # Whitespace collapsed because the file wraps, and ** stripped because it is emphasis.
    _decisions_section = _re.sub(r"\s+", " ", _m.group(1)).replace("*", "").lower()
check(bool(_decisions_section), "the context file has a Decisions already made section")
check(len(ww.STANDING_DECISIONS) >= 10, f"the example profile carries its decisions "
                                        f"({len(ww.STANDING_DECISIONS)})")
for name, _pattern, _unless in ww.STANDING_DECISIONS:
    check(name.lower() in _decisions_section,
          f"the context file states the decision named {name!r} word for word")
check(bool(ww.DECISION_EXEMPT), "the decisions a project is exempt from are where the rule reads them")
for name, projects in ww.DECISION_EXEMPT.items():
    check(name in {n for n, _p, _u in ww.STANDING_DECISIONS} and set(projects) <= set(ww.PROJECTS),
          f"{name!r} is a standing decision and {projects} are projects the prompt offers")
check(bool(ww.DECISION_ONLY), "the decisions that hold for one project only are listed")
for name, projects in ww.DECISION_ONLY.items():
    check(name in {n for n, _p, _u in ww.STANDING_DECISIONS} and set(projects) <= set(ww.PROJECTS),
          f"{name!r} is a standing decision and {projects} are projects the prompt offers")

# ===========================================================================================
print("\n=== RULE 4. a topic result older than two years is skipped before any fetch ===")
LAUNCHED = []
_real_run = ww.run_yt_dlp


def fake_run(cmd, timeout=120):
    LAUNCHED.append(list(cmd))
    return types.SimpleNamespace(stdout="", stderr="", returncode=0)


ww.run_yt_dlp = fake_run
try:
    ww.search_videos("anything", count=3)
finally:
    ww.run_yt_dlp = _real_run
args = LAUNCHED[0] if LAUNCHED else []
check("--extractor-args" in args and "youtubetab:approximate_date" in args,
      "the search listing asks for approximate_date, without which a result has NO date")
check(args.count("--extractor-args") == 1 and "--flat-playlist" in args,
      "CONTROL: it is the same one listing request, with one more argument")

_real_handles = ww.named_channel_handles
ww.named_channel_handles = lambda: {"@example-founders"}
try:
    old = {"id": "a", "title": "The 3 Best Bakery Marketing Ideas of 2023",
           "upload_date": "20230114", "uploader_id": "@someone-else"}
    named = dict(old, id="b", uploader_id="@example-founders")
    fresh = dict(old, id="c", upload_date=ww.dt.date.today().strftime("%Y%m%d"))
    undated = {"id": "d", "title": "no date at all", "uploader_id": "@somebody"}
    kept, skipped, undated_n = ww.drop_stale_search_results([old, named, fresh, undated])
    ids = [v["id"] for v in kept]
    check([v["id"] for v in skipped] == ["a"], f"the 2023 video is skipped (skipped {[v['id'] for v in skipped]})")
    check("b" in ids, "CONTROL: the same age from a channel in channels.txt is kept")
    check("c" in ids, "CONTROL: a recent video is kept")
    check("d" in ids and undated_n == 1,
          "a result with no date is KEPT and counted, because unknown is not old")
    kept2, skipped2, _ = ww.drop_stale_search_results([old], days=100000)
    check(not skipped2 and len(kept2) == 1, "CONTROL: with the bound wide open, nothing is skipped")
finally:
    ww.named_channel_handles = _real_handles
check(ww.TOPIC_MAX_AGE_DAYS == 730, "the bound is two years")
_recent = (ww.dt.date.today() - ww.dt.timedelta(days=30)).strftime("%Y%m%d")
check(ww.older_than("20230101", 730) and not ww.older_than(_recent, 730)
      and not ww.older_than("", 730) and not ww.older_than("not a date", 730),
      "older_than only says yes when it can read the date")

# ===========================================================================================
print("\n=== RULE 5. a confidence with every verdict ===")
check(ww.parse_confidence("VERDICT: YES - x\nCONFIDENCE: 85") == 85, "a number is read")
check(ww.parse_confidence("CONFIDENCE: **20**") == 20, "even in bold, which models do")
check(ww.parse_confidence("VERDICT: YES - x") is None,
      "no number reads as None, never as a default the model never said")
check(ww.parse_confidence("CONFIDENCE: 140") == 100 and ww.parse_confidence("CONFIDENCE: 0") == 0,
      "out of range is clamped, not dropped")
check(ww.confidence_cell(None) == "unknown" and ww.confidence_cell(0) == "0",
      "and unknown prints as a word, while zero prints as zero")

_real_generate = ww.ollama_generate
ww.ollama_generate = lambda *a, **k: ("COPY: a thing\nVERDICT: YES - it teaches a thing\n"
                                      "CONFIDENCE: 77")
try:
    got = ww.triage_video("ctx", "chan", {"title": "t", "description": "d"}, "model")
finally:
    ww.ollama_generate = _real_generate
check(len(got) == 3 and got[0] == "YES" and got[2] == 77,
      f"triage_video returns (verdict, reason, confidence): {got[0]}, {got[2]}")
ww.ollama_generate = lambda *a, **k: "nothing parseable at all"
try:
    got = ww.triage_video("ctx", "chan", {"title": "t"}, "model")
finally:
    ww.ollama_generate = _real_generate
check(got[0] == "MAYBE" and got[2] == 0,
      "an unparsed answer is a MAYBE at zero confidence, not at a number from nowhere")

# ===========================================================================================
print("\n=== RULE 6. the thirty-second daily header ===")
_real_load = ww.load_history_since
_real_totals = ww.todays_totals


def with_rows(rows):
    # The record store is emptied too: "today is exactly these rows" is the whole point of the
    # helper, and rule 4's checks above leave age-filtered records behind in the sandbox.
    ww.RECORDS_PATH.unlink(missing_ok=True)
    ww.load_history_since = lambda *a, **k: rows
    ww.todays_totals = lambda: (len(rows), sum(1 for r in rows if r["verdict"] == "YES"),
                                sum(1 for r in rows if r["verdict"] == "OPPORTUNITY"))


# These rows carry a summary and a URL: the header is built from records, a record is WRITTEN
# only when a summary exists, and section 6 has to name a project. The control for that is at
# the bottom of this block: the same row with its summary removed must not lead the day.
KEEPER_SUMMARY = summary(
    applies='Most applicable: a real point. "some words" Applies to: Ledgerleaf')
_ids = iter(f"vid{n:08d}" for n in range(1000))


def row(verdict, title, reason="it does a thing [copy: drop one file in the repo root]",
        try_this_week=None, summary_text=KEEPER_SUMMARY):
    vid = next(_ids)
    return {"verdict": verdict, "title": title, "reason": reason,
            "try_this_week": try_this_week, "summary": summary_text,
            "date": ww.today_key(), "url": f"https://www.youtube.com/watch?v={vid}"}


try:
    with_rows([row("MAYBE", "a"), row("NO", "b")])
    lines = ww.todays_header_lines()
    check(len(lines) == 1 and lines[0].startswith("Nothing to keep today"),
          f"a day with no keeper is one line: {lines[0]!r}")
    check("2 read and skipped" in lines[0],
          "and it counts what was skipped, in the five buckets")

    with_rows([row("YES", "One"), row("MAYBE", "b"), row("NO", "c")])
    lines = ww.todays_header_lines()
    # A line is verdict, project, one whole sentence, then the video as a short title link.
    check(len(lines) == 2 and lines[0].startswith("- **YES** Ledgerleaf: ")
          and "[One](https://www.youtube.com/watch?v=" in lines[0], "one keeper, two lines")
    # The sentence is the write-up's own section 8, "Do the thing.", whole; never a cut of
    # section 6, whose entries open with the video's quote.
    check("Do the thing." in lines[0] and "A real point" not in lines[0],
          f"the sentence is the write-up's own action, section 8, whole: {lines[0]!r}")
    check("drop one file in the repo root" not in lines[0],
          "and the listing's copy answer is not in the header")
    check(lines[-1].startswith("1 kept, 2 read and skipped"),
          f"and the last line counts the day: {lines[-1]!r}")

    with_rows([row("YES", f"v{i}") for i in range(5)] + [row("NO", "x") for _ in range(4)])
    lines = ww.todays_header_lines()
    check(len(lines) == 4, f"five keepers still fit in four lines (got {len(lines)})")
    check("5 kept" in lines[-1] and "2 more kept below" in lines[-1],
          f"and the two it could not show are accounted for: {lines[-1]!r}")
    check(all(len(l) < 200 for l in lines), "no line runs away")

    # The fallbacks, asked of header_sentence directly.
    check(ww.header_sentence({"project": "Ledgerleaf", "takeaway": None,
                              "try_this_week": "Price one batch of ten setups."})
          == "Price one batch of ten setups.",
          "with no takeaway it falls back to this week's action")
    # The triage's reason is not a sentence of what to do: with no sentence of the write-up's
    # own there is no header sentence, and the line is left out.
    check(ww.header_sentence({"project": "Ledgerleaf", "takeaway": None,
                              "try_this_week": "nothing",
                              "read_reason": "just a reason. and a second sentence."}) is None,
          "and with no action, never the reason")
    check(ww.header_sentence({"project": "Ledgerleaf"}) is None,
          "CONTROL: and with nothing at all there is no sentence rather than an invented one")

    # The same YES row, with no write-up, must not reach the header at all.
    with_rows([row("YES", "Written", summary_text=KEEPER_SUMMARY),
               row("YES", "Never written", summary_text=None)])
    lines = ww.todays_header_lines()
    check(any("Written" in l and "Never written" not in l for l in lines[:-1]),
          "a written keeper still leads the day")
    check(not any("Never written" in l for l in lines),
          "and a YES with no write-up is nowhere in the header")
    check("1 more kept below" not in lines[-1],
          f"nor in its 'more kept below' count: {lines[-1]!r}")
finally:
    ww.load_history_since = _real_load
    ww.todays_totals = _real_totals

check(bool(ww.DIGEST_HEADER_RE.search("<!-- HEADER-START -->\n# Digest x\n\n- **YES** a: b\n"
                                      "1 skipped (2 video(s) today, 1 YES, 0 OPPORTUNITY).\n"
                                      "<!-- HEADER-END -->\n\n")),
      "the header is still delimited, so the next run replaces it instead of stacking")

# ===========================================================================================
print("\n=== BACK COMPATIBILITY. files written before a format change still load ===")
# Any change to a stored file format ships with a test that files written before the change
# still load unchanged: history.jsonl gained "confidence", seen.json's pending entries gained
# "pending_confidence", and the digest table changed its columns.
OLD_HISTORY_ROW = ('{"date": "2026-03-02", "verdict": "YES", "channel": "https://www.youtube.com/@example-founders", '
                   '"title": "How I priced my first bakery app", '
                   '"url": "https://www.youtube.com/watch?v=MaraVid0021", "reason": "a mechanism", '
                   '"summary": null, "backend": null, "try_this_week": null, "source": "channels"}')
old = json.loads(OLD_HISTORY_ROW)
check("confidence" not in old, "the fixture really is a row from before the change")
check(old.get("confidence") is None, "a reader asking for the new key gets None, not a KeyError")
check(ww.header_sentence(old) is None,
      "and every reader of a history row goes through .get(): a row with no write-up has no "
      "header sentence, and asking raises nothing")
# A history file with rows from both sides of the change, as an old install would hold.
_rows = [dict(old, url=f"https://www.youtube.com/watch?v=MaraVid00{30 + n}",
              try_this_week="Price one batch of ten setups." if n % 2 else None,
              summary=KEEPER_SUMMARY if n % 2 else None) for n in range(4)]
_rows += [dict(r, confidence=80, url=r["url"][:-1] + "9") for r in _rows[:2]]
ww.HISTORY_PATH.write_text("".join(json.dumps(r) + "\n" for r in _rows), encoding="utf-8")
real_rows = [json.loads(l) for l in ww.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
             if l.strip()]
before_change = [r for r in real_rows if "confidence" not in r]
after_change = [r for r in real_rows if "confidence" in r]
check(len(before_change) == 4, f"{len(before_change)} of the {len(real_rows)} rows predate the "
                               f"change and every one of them parses")
check(all(r.get("confidence") is None for r in before_change),
      "and every one of them reads as confidence None, not as a KeyError")
_sentences = [ww.header_sentence(r) for r in before_change]
check(all(s is None or s.endswith((".", "!", "?", '."', '!"', '?"')) for s in _sentences)
      and any(s for s in _sentences),
      "and the header reads each without touching the new key: a sentence where the row has an "
      "action, none where it has not")
check(len(after_change) == 2,
      f"CONTROL: {len(after_change)} row(s) DO carry the new key, so this check is not passing "
      f"because nothing writes it")

OLD_PENDING = {"date": "2026-03-02", "verdict": "pending", "title": "t", "channel": "c",
               "url": "u", "pending_verdict": "YES", "pending_reason": "r", "retry_count": 1,
               "note": "captions not ready yet"}
check(OLD_PENDING.get("pending_confidence") is None and OLD_PENDING["pending_verdict"] == "YES",
      "a pending seen.json entry from before the change still says what it always said")
ww.save_seen({"MaraVid0041": OLD_PENDING, "MaraVid0042": dict(OLD_PENDING, pending_confidence=60)})
_seen = ww.load_seen()
check(len(_seen) == 2 and _seen["MaraVid0041"].get("pending_confidence") is None,
      f"and a seen.json holding both shapes loads as it is ({len(_seen)} entries)")

# An old digest: each run appended its own table inside its "## Run at" block, with no
# confidence column.
OLD_DIGEST = ("# Digest 2026-03-02\n\n## Run at 09:00 (channels)\n\nNew videos: 2\n\n"
              "| Verdict | Channel | Title | Reason |\n|---|---|---|---|\n"
              "| YES (the listing said MAYBE) | Example Channel | "
              "[How I priced my first bakery app](https://www.youtube.com/watch?v=MaraVid0051) | "
              "a mechanism |\n"
              "| MAYBE | Example Channel | "
              "[A week of newsletter numbers](https://www.youtube.com/watch?v=MaraVid0052) | "
              "thin |\n\n## Run at 10:00 (topics)\n\nNew videos: 1\n\n"
              "| Verdict | Channel | Title | Reason |\n|---|---|---|---|\n"
              "| MAYBE | Other Channel | "
              "[Indie game launch notes](https://www.youtube.com/watch?v=MaraVid0053) | thin |\n")
_old_digest = ww.OUTPUT_DIR / "DIGEST-2026-03-02.md"
_old_digest.write_text(OLD_DIGEST, encoding="utf-8")
_read_old = scoring.rows_in_digest(_old_digest)
check(len(_read_old) == 3, f"a digest written before the new column still reads ({len(_read_old)} rows)")
check(all(r["confidence"] is None for r in _read_old),
      "with no confidence, said as None rather than invented")
NEW_TABLE = ("## Run at 09:00 (topics)\n\nNew videos: 1\n\n"
             "| Verdict | Confidence | Channel | Title | Reason |\n|---|---|---|---|---|\n"
             "| YES (the listing said MAYBE) | 85 | A Channel | "
             "[A title](https://www.youtube.com/watch?v=abcdefghijk) | because |\n")
_tmp = ww.OUTPUT_DIR / "DIGEST-2026-03-03.md"
_tmp.write_text(NEW_TABLE, encoding="utf-8")
_new = scoring.rows_in_digest(_tmp)
FOUR = ("<!-- TABLE-START -->\n## The day so far\n\n"
        "| Verdict | Channel | Title | Reason |\n|---|---|---|---|\n"
        "| MAYBE | A Channel | [A title](https://www.youtube.com/watch?v=abcdefghijk) | "
        "because [demoted from YES: it is generic advice (x)] |\n"
        "| NOT READ (audio 74.5 MB over the 63 MB ceiling) | B Channel | "
        "[Another](https://www.youtube.com/watch?v=bcdefghijkl) |  |\n<!-- TABLE-END -->\n")
_four = ww.OUTPUT_DIR / "DIGEST-2026-03-04.md"
_four.write_text(FOUR, encoding="utf-8")
_read = scoring.rows_in_digest(_four)
check(len(_read) == 2 and _read[0]["verdict"] == "MAYBE" and _read[0]["confidence"] is None
      and _read[1]["verdict"] == "NOT READ",
      f"and the four-column day table reads, with no confidence: {[r['verdict'] for r in _read]}")
check(len(_new) == 1 and _new[0]["confidence"] == 85 and _new[0]["verdict"] == "YES"
      and _new[0]["listing_verdict"] == "MAYBE",
      f"and the five-column shape reads too: {_new[0]['confidence'] if _new else 'nothing'}")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
