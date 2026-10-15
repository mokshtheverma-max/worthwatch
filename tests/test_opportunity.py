"""OPPORTUNITY is its own lane, judged on its own terms.

A video about money being made is often no use to any of your projects and still worth knowing
about. So OPPORTUNITY is kept only if the video shows a buyer that is a company or a developer
who already pays (never consumers), a price, deal size or revenue figure marked as shown or only
said, and a concrete first step toward a first customer. It is never demoted for naming no live
project; age, cost and hired help are notes on its card; with a sign word set, anything needing
someone else's signature carries that word; a killed idea is never surfaced; at most 3 a day, in
their own section after the project items, in the full and the short digest, and a weekly file
that collects them.

Each rule below is checked on the input it is for and, as its control, on one it must leave
alone. No YouTube request and no model call: the screen's model and the card's writing call are
stubs, and anything else that would reach a model or YouTube fails the suite. All of it runs on
the invented example person in a temp folder (tests/_tmpsandbox.py).

Watch it go red: make card_problem return None in a copy of src/worthwatch/core.py and run this
file against the copy.
"""
import io
import json
import re
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "opportunity")
from worthwatch import jev

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def section(title, fn):
    """One section; a section that raises fails, says why, and the next one still runs."""
    print(f"\n=== {title} ===")
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc(limit=2, file=sys.stdout)
        check(False, f"{title}: raised {e.__class__.__name__}: {e}")


class Spy:
    """Anything that would leave this machine or wake a model fails the test."""

    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **k):
        raise AssertionError(f"the test called {self.name}; it must make no request")


for _name in ("run_yt_dlp", "extract_media_info", "download_audio_from_info", "transcribe_audio",
              "fetch_channel_feed", "load_whisper", "ollama_generate", "claude_generate"):
    setattr(ww, _name, Spy(_name))
ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}
ww.polite_delay = lambda *a, **k: None

CONTEXT_FLAT = re.sub(r"\s+", " ", ww.CONTEXT_PATH.read_text(encoding="utf-8")).lower()
TODAY = ww.today_key()

SCREEN_YES = ("BUYER: Bakery chains with three to ten shops that already pay for payroll software\n"
              "FIGURE: $300 a month per chain (said)\n"
              "FIRST STEP: Email one chain's operations lead a sample week's schedule\n"
              "OPPORTUNITY: YES")
SCREEN_CONSUMER = ("BUYER: Etsy shoppers who buy dog-breed shirts\n"
                   "FIGURE: $10,000 a month in sales (said)\n"
                   "FIRST STEP: Open an Etsy store with ten breed designs\n"
                   "OPPORTUNITY: YES")
SCREEN_NO = "BUYER: none\nFIGURE: none\nFIRST STEP: none\nOPPORTUNITY: NO"
CARD = ("What it is: Selling shift-scheduling software to small bakery chains, priced per shop.\n"
        "Who pays: Bakery chains with three to ten shops, which already pay for payroll software.\n"
        "How much: $300 a month per chain, from customers the founder names in the video (said)\n"
        "Why now: Payroll tools stopped bundling scheduling this year.\n"
        "Route to a first customer: Write one sample week's schedule for a real chain and send it "
        "to its operations lead with a per-shop price.\n"
        "Fit with you: Ledgerleaf's bakeries are the same buyers; a contract with a chain needs "
        "a signature.\n"
        "Red flags: The biggest payroll tools may add scheduling back.\n"
        "Verdict: worth a research run")


def card_with(**fields):
    card = ww.parse_card(CARD)
    card.update(fields)
    return ww.finish_card(card)


# ==========================================================================================
def a_screen():
    s = ww.parse_screen(SCREEN_YES)
    check(s["buyer"].startswith("Bakery chains") and s["says"] == "YES" and s["mark"] == "said"
          and "$300" in s["figure"] and s["first_step"].startswith("Email"),
          f"the four lines are read ({s})")
    check(ww.screen_passes(s), "a business buyer, a figure and a first step pass the screen")
    check(not ww.screen_passes(ww.parse_screen(SCREEN_CONSUMER)),
          "CONTROL: consumers as the buyer do not pass, whatever the model said")
    check(not ww.screen_passes(ww.parse_screen(SCREEN_NO)), "CONTROL: a NO does not pass")
    bold = SCREEN_YES.replace("BUYER:", "**BUYER:**").replace("OPPORTUNITY:", "**OPPORTUNITY:**")
    check(ww.screen_passes(ww.parse_screen(bold)), "the same answer in bold still passes")
    check(not ww.screen_passes(ww.parse_screen(SCREEN_YES.replace("$300 a month", "a good price"))),
          "CONTROL: no number in the figure does not pass")
    check(not ww.screen_passes(ww.parse_screen(SCREEN_YES.rsplit("\n", 1)[0])),
          "CONTROL: an answer with no OPPORTUNITY line does not pass")
    check(not ww.screen_passes({"error": "OllamaDown: refused"}),
          "CONTROL: a screen that could not be asked does not pass")


def b_route():
    yes = ww.parse_screen(SCREEN_YES)
    no = ww.parse_screen(SCREEN_NO)
    v, r = ww.route_for_opportunity("MAYBE", "thin", yes)
    check(v == "OPPORTUNITY" and "the triage said MAYBE" in r,
          f"a MAYBE whose screen passes is read as OPPORTUNITY, and says so ({r})")
    check(ww.route_for_opportunity("NO", "nothing to copy", yes)[0] == "OPPORTUNITY",
          "a NO whose screen passes is read as OPPORTUNITY")
    check(ww.route_for_opportunity("YES", "a mechanism", yes)[0] == "YES",
          "CONTROL: a YES stays YES (its card is written beside it)")
    check(ww.route_for_opportunity("OPPORTUNITY", "x", no)[0] == "OPPORTUNITY",
          "CONTROL: a screen that does not pass demotes nothing")
    check(ww.route_for_opportunity("MAYBE", "x", no) == ("MAYBE", "x"),
          "CONTROL: a MAYBE whose screen does not pass is left exactly as it was")
    check(ww.route_for_opportunity("MAYBE", "x", {"error": "down"}) == ("MAYBE", "x"),
          "CONTROL: nor does a screen that could not be asked")


def c_card():
    card = ww.finish_card(ww.parse_card(CARD))
    check(card["kind"] == "card" and card["verdict"] == "worth a research run"
          and card["mark"] == "said" and card["who_pays"].startswith("Bakery chains"),
          "the eight lines are read")
    check(ww.card_problem(card) is None and ww.card_surfaces(card), "the card passes every check")
    check(not card.get("sign") and not any("SIGN" in l for l in ww.card_lines(card)),
          "with no sign word set (the default), a contract in the fit line adds no mark")
    saved = ww.SIGN_WORD
    try:
        ww.SIGN_WORD = "SIGN"
        signed = ww.finish_card(ww.parse_card(CARD))
        check(signed.get("sign") is True, "with the sign word set, a contract in its fit line "
                                          "marks the card")
        check(any("SIGN" in l for l in ww.card_lines(signed)), "and the card's fit line shows it")
        plain = ww.finish_card(ww.parse_card(CARD.replace(
            "a contract with a chain needs a signature", "she can build it in the evenings")))
        check(not plain.get("sign"), "CONTROL: a fit line that needs nobody's signature is not "
                                     "marked")
        check(ww.card_sign_notes()[0] and "SIGN" in ww.card_sign_notes()[1],
              "and the card prompt is told to use the word")
    finally:
        ww.SIGN_WORD = saved
    check(ww.card_sign_notes() == ("", ""), "CONTROL: with no sign word the prompt says nothing "
                                            "about signatures")
    for label, broken, want in (
            ("consumers pay", dict(who_pays="Parents of high-school students"), "consumers"),
            ("nobody named", dict(who_pays="Whoever wants it"), "no company, organisation or developer"),
            ("no figure", dict(how_much="a lot of money (said)"), "no price, deal size or revenue"),
            ("no shown or said", dict(how_much="$500 a month", mark=None), "shown or only said"),
            ("no first step", dict(route="Start soon."), "no concrete first step"),
            ("an acquisition", dict(what="A bakery chain acquiring a family-run rival for $7.5 "
                                         "million."), "acquisition"),
            ("a killed idea", dict(what="A dog-walking app that books walkers by the hour."),
             "dog-walking apps"),
            ("no verdict", dict(verdict="maybe later"), "not one of the three"),
            ("no what", dict(what=""), "'what it is'")):
        got = ww.card_problem(card_with(**broken))
        check(got is not None and want in got, f"{label}: not an opportunity ({got})")
    for label, ok in (("red flags none", dict(red_flags="none")),
                      ("why now unknown", dict(why_now="UNKNOWN")),
                      ("schools as the buyer", dict(who_pays="Private schools, which already pay "
                                                              "for writing tools")),
                      ("developers as the buyer", dict(who_pays="Developers who already pay for "
                                                                "coding agents")),
                      ("an acquisition named only after the first clause",
                       dict(what="Selling scheduling software to bakery chains, which have also "
                                 "been acquiring family-run rivals."))):
        got = ww.card_problem(card_with(**ok))
        check(got is None, f"CONTROL {label}: still an opportunity ({got})")
    no = card_with(verdict="no")
    check(ww.card_problem(no) is None and not ww.card_surfaces(no),
          "a card that passes but whose own verdict is no is not surfaced")
    nc = ww.finish_card(ww.parse_card("NO CARD: no buyer pays for this"))
    check(nc["kind"] == "no_card" and "no buyer" in ww.card_problem(nc), "NO CARD is read")
    k = ww.finish_card(ww.parse_card("KILLED: a recipe subscription box: offices now pay $40 a "
                                     "head each week for recipe boxes."))
    check(k["kind"] == "killed" and "recipe subscription box" in ww.card_problem(k)
          and "$40" in k["evidence"], "KILLED is read, with its new evidence")
    check(not ww.card_surfaces(k), "and it is never surfaced as an opportunity")
    check(ww.card_lines(k)[0].startswith("Killed idea (a recipe subscription box)"),
          "it becomes one line, marked as a killed idea")
    check(ww.card_problem({"kind": "error", "why": "timed out"}) is not None,
          "CONTROL: a card that could not be written is not an opportunity")


# One sample for each idea the example person killed (the [[killed]] rules of her rules.toml),
# and live ideas that share a word with one of them without being it.
KILLED_SAMPLES = {
    "dog-walking apps": "A dog-walking app that books walkers for busy offices.",
    "a recipe subscription box": "Recipe subscription boxes sold to office kitchens.",
    "crypto tipping for creators": "Crypto tipping for streamers, sold to streaming platforms.",
    "a checklist app": "A checklist app for restaurant opening shifts.",
}
LIVE_IDEAS = (
    "Shift-scheduling software for small bakery chains.",
    "Sponsor matching for indie game newsletters, sold to game studios.",
    "Bank-export cleanup sold to bookkeeping firms.",
    "A walk-in cooler temperature log for restaurants.",
    "Recipe costing software for bakeries.",
    "Tip pooling reports for cafes.",
    "A checklist template pack sold to cafe owners.",
)


def d_killed():
    names = [name for name, _p in getattr(ww, "KILLED_IDEAS", ())]
    check(len(names) == len(KILLED_SAMPLES) and set(names) == set(KILLED_SAMPLES),
          f"every killed name has a sample here ({len(names)} names)")
    for name in names:
        check(name in CONTEXT_FLAT, f"'{name}' is a phrase of the context file's killed list")
        card = card_with(what=KILLED_SAMPLES.get(name, "missing sample"))
        check(ww.killed_idea_in(card) == name,
              f"  and it names its own sample ({ww.killed_idea_in(card)})")
    for line in LIVE_IDEAS:
        card = card_with(what=line)
        check(ww.killed_idea_in(card) is None and ww.card_problem(card) is None,
              f"CONTROL a live idea is no killed idea: {line} ({ww.killed_idea_in(card)})")


# ==========================================================================================
def rec(**fields):
    base = {"id": "opp00000001", "summary_state": "WRITTEN", "reason_code": "written",
            "read_verdict": "OPPORTUNITY", "settled_verdict": "OPPORTUNITY", "applies": "nothing",
            "project": None, "takeaway": None, "try_this_week": "nothing", "header": None,
            "listing_verdict": "MAYBE", "opp_lane": 1, "opp_card": ww.finish_card(ww.parse_card(CARD)),
            "opp_cap": None, "written_day": TODAY, "day": TODAY}
    base.update(fields)
    return base


def e_verdict():
    v, t, tags = ww.final_verdict(rec())
    check(v == "OPPORTUNITY" and "kept" in tags and "opportunity" in tags and "header" not in tags,
          f"an OPPORTUNITY with a passing card and no project is kept, in its own section ({v}, {sorted(tags)})")
    check("no project" not in (t or ""), "and nothing says it named no project")
    check(tags <= ww.VERDICT_TAGS, "every tag is in the closed vocabulary")
    v, t, tags = ww.final_verdict(rec(opp_card=card_with(how_much="a lot (said)")))
    check(v == "MAYBE" and "demoted from OPPORTUNITY" in t and "no price" in t,
          f"a card that fails a test is a MAYBE that says which ({t})")
    v, t, tags = ww.final_verdict(rec(opp_card=card_with(verdict="no")))
    check(v == "MAYBE" and "own verdict is no" in t, f"a card whose verdict is no is a MAYBE ({t})")
    v, t, tags = ww.final_verdict(rec(opp_cap="the day's opportunity number 4, and at most 3 a day are surfaced"))
    check(v == "MAYBE" and "number 4" in t, f"the fourth of a day is a MAYBE ({t})")
    v, t, tags = ww.final_verdict(rec(opp_card=None))
    check(v == "MAYBE" and "no opportunity card" in t, f"an OPPORTUNITY with no card is a MAYBE ({t})")
    keep = rec(read_verdict="YES", settled_verdict="YES", applies="items", project="Ledgerleaf",
               takeaway="Import one bank export twice. Applies to: Ledgerleaf",
               try_this_week="Import one bank export twice and compare the two reports.",
               header="Import one bank export twice and compare the two reports, for Ledgerleaf.")
    v, t, tags = ww.final_verdict(keep)
    check(v == "YES" and "header" in tags, "a YES the project rules keep stays YES, in the header")
    v, t, tags = ww.final_verdict(rec(read_verdict="YES", settled_verdict="MAYBE"))
    check(v == "OPPORTUNITY", f"a YES whose section 6 came to nothing but whose card passes is an OPPORTUNITY ({v}: {t})")
    v, t, tags = ww.final_verdict(rec(read_verdict="YES", settled_verdict="MAYBE", opp_card=None))
    check(v == "MAYBE" and "named no project" in t,
          f"CONTROL: the same YES with no card is demoted as before ({t})")
    legacy = rec()
    for k in ("opp_lane", "opp_card", "opp_cap"):
        legacy.pop(k)
    v, t, tags = ww.final_verdict(legacy)
    check(v == "MAYBE" and "named no project" in t,
          f"CONTROL: a record written before the lane keeps the old rule exactly ({t})")


def summary_text(section6="nothing", section8="nothing", section9="nothing"):
    return ("## 1. What it is\nA podcast.\n\n## 2. The claim\nChains buy schedules.\n\n"
            "## 3. Evidenced versus asserted\nOnly said.\n\n## 4. The method\n1. Build.\n\n"
            "## 5. Worth keeping verbatim\nNone.\n\n## 6. What applies to me\n\n" + section6 +
            "\n\n## 7. What contradicts what I believe\n\nnothing in this video touches the "
            "beliefs list\n\n## 8. Try this week\n\n" + section8 + "\n\n## 9. Header\n" +
            section9 + "\n")


def write_lane_record(vid, card, title=None):
    ww.update_record(vid, url=f"https://www.youtube.com/watch?v={vid}", title=title or f"Video {vid}",
                     channel="C", source="topics", listing_verdict="MAYBE",
                     summary_state="READ_NOT_WRITTEN", reason_code="summary_pending",
                     read_verdict="OPPORTUNITY", read_day=TODAY)
    fields = {"opp_lane": 1, "opp_screen": ww.parse_screen(SCREEN_YES), "opp_card": card,
              "opp_cap": ww.opportunity_cap_for(vid, card)}
    return ww.summary_written(vid, summary_text() + "\n" + ww.card_section(card), "OPPORTUNITY",
                              **fields)


def f_cap_and_digest():
    for path in (ww.RECORDS_PATH, ww.HISTORY_PATH):
        Path(path).unlink(missing_ok=True)
    good = ww.finish_card(ww.parse_card(CARD))
    written = [write_lane_record(f"capvideo00{n}", good, title=f"Bakery schedules, part {n}")
               for n in range(1, 5)]
    shown = [ww.final_verdict(r)[0] for r in written]
    check(shown == ["OPPORTUNITY", "OPPORTUNITY", "OPPORTUNITY", "MAYBE"],
          f"at most 3 a day, first come first kept ({shown})")
    check("number 4" in ww.final_verdict(written[3])[1], "the fourth says why")
    killed = ww.finish_card(ww.parse_card("KILLED: a recipe subscription box: offices now pay $40 "
                                          "a head."))
    write_lane_record("capvideo005", killed, title="Recipe boxes")
    path = ww.write_day_digest(TODAY, "", create=True)
    page = path.read_text(encoding="utf-8")
    h, o, tb = (page.find("<!-- HEADER-END -->"), page.find("<!-- OPPS-START -->"),
                page.find("<!-- TABLE-START -->"))
    check(0 <= h < o < tb, f"the Opportunities block sits after the header and before the table "
                            f"({h}, {o}, {tb})")
    check(page.count("### ") >= 3 and "## Opportunities today" in page,
          "the full digest carries the day's three cards")
    check("Bakery schedules, part 4" not in page[o:tb], "and not the fourth")
    check("Killed idea (a recipe subscription box)" in page[o:tb],
          "a killed idea with new evidence is one line, marked as killed")
    short = (ww.OUTPUT_DIR / f"DIGEST-{TODAY}-short.md").read_text(encoding="utf-8")
    so, st = short.find("## Opportunities today"), short.find("## The day so far")
    check(0 <= so < st, f"the short digest carries the same section, before its table ({so}, {st})")
    check(short.count("Route to a first customer") == 3, "with the three cards in full")
    kept = short[short.find("## Kept"):]
    check("Bakery schedules" not in kept,
          "and its Kept list leaves the opportunities to their own section")
    monday = ww.week_monday(TODAY).isoformat()
    weekly = ww.OUTPUT_DIR / f"OPPORTUNITIES-WEEK-{monday}.md"
    check(weekly.exists(), f"the weekly file is written: {weekly.name}")
    text = weekly.read_text(encoding="utf-8") if weekly.exists() else ""
    check(text.count("Route to a first customer") == 3 and TODAY in text,
          "and it collects the day's three cards under the day")
    log = ww.opportunities_path()
    # Only the fetch and retry paths append the one-line log; nothing here wrote to it.
    check(not log.exists() or "Bakery schedules" not in log.read_text(encoding="utf-8"),
          "CONTROL: regenerating a digest appends nothing to OPPORTUNITIES.md")


def g_no_lane_day():
    for path in (ww.RECORDS_PATH, ww.HISTORY_PATH):
        Path(path).unlink(missing_ok=True)
    day = "2026-03-02"
    ww.update_record("legacyvid01", url="https://www.youtube.com/watch?v=legacyvid01",
                     title="Legacy", channel="C", summary_state="WRITTEN", reason_code="written",
                     read_verdict="YES", settled_verdict="YES", applies="items",
                     project="Ledgerleaf", takeaway="x Applies to: Ledgerleaf",
                     try_this_week="Import one export twice.",
                     header="Import one export twice, for Ledgerleaf.",
                     written_day=day, day=day)
    page = ww.write_day_digest(day, "", create=True).read_text(encoding="utf-8")
    check("OPPS-START" not in page and "Opportunities today" not in page,
          "CONTROL: a day the lane never judged gets no Opportunities block")


# ==========================================================================================
MARKER = "PRIVATE-CONTEXT-MARKER"
TALK = ("bakery chains pay three hundred dollars a month per chain for schedules and we sold our "
        "first one by emailing a sample week to one chain's operations lead ")
PROMPTS = {"screen": [], "card": []}


def h_end_to_end():
    for path in (ww.RECORDS_PATH, ww.HISTORY_PATH, ww.SEEN_PATH):
        Path(path).unlink(missing_ok=True)
    ww.start_this_run()

    def fetch(url, plan=None):
        vid = ww.video_id_from_url(url)
        return {"id": vid, "title": "How we sold schedules to bakery chains", "uploader": "U",
                "url": url, "transcript": TALK * 60, "raw_path": ww.RAW_DIR / f"{vid}.txt",
                "lang": "en", "track_kind": "audio",
                "audio_stats": {"audio_seconds": 600.0, "model": "stub"}}, None

    def triage(context_text, channel, details, model, sells_something=None):
        if "transcript" in details:
            return "MAYBE", "too thin to tell", 50
        return "MAYBE", "Description missing", 10

    def screen_model(model, prompt, **k):
        PROMPTS["screen"].append(prompt)
        return SCREEN_YES

    def card_model(prompt, timeout=600):
        PROMPTS["card"].append(prompt)
        return CARD

    def summarise(context_text, meta, model, local_only, verdict, attempt=1):
        return (summary_text(), "claude:sonnet", "stub", 9.0, False,
                "MAYBE" if verdict == "OPPORTUNITY" else verdict,
                ["demoted from OPPORTUNITY: no Opportunity line survived, so nothing names a way "
                 "of getting paid"] if verdict == "OPPORTUNITY" else [])

    ww.fetch_transcript_by_plan = fetch
    ww.triage_video = triage
    ww.summarise_one = summarise
    ww.may_start_a_summary = lambda: (True, "")
    ww.local_most_useful = lambda *a, **k: None
    ww.ollama_generate = screen_model
    ww.claude_generate = card_model
    listing = [{"id": "lanevideo01", "title": "How we sold schedules to bakery chains", "channel": "C",
                "url": "https://www.youtube.com/watch?v=lanevideo01", "description": "",
                "duration": 600, "upload_date": "20260301"}]

    # These sections drive the local screen (its prompt is stubbed above); K drives Jev's.
    ww.OPPORTUNITY_SCREEN_BACKEND = "local"
    ww._LANE["on"] = False
    seen = {}
    ww.process_candidates(listing, f"CONTEXT {MARKER}", "model", False, seen,
                          budget={"remaining": 3}, source="topics")
    r = ww.load_records()["lanevideo01"]
    check(not PROMPTS["screen"] and not PROMPTS["card"],
          "CONTROL: with the lane off, no screen and no card are asked for")
    check(ww.final_verdict(r)[0] == "MAYBE", "and the video stays the MAYBE the triage said")

    for path in (ww.RECORDS_PATH, ww.HISTORY_PATH, ww.SEEN_PATH):
        Path(path).unlink(missing_ok=True)
    ww.start_this_run()
    ww._LANE["on"] = True
    seen = {}
    ww.process_candidates(listing, f"CONTEXT {MARKER}", "model", False, seen,
                          budget={"remaining": 3}, source="topics")
    r = ww.load_records()["lanevideo01"]
    v, t, tags = ww.final_verdict(r)
    check(len(PROMPTS["screen"]) == 1 and len(PROMPTS["card"]) == 1,
          f"the lane asks one screen and writes one card ({len(PROMPTS['screen'])}, "
          f"{len(PROMPTS['card'])})")
    check(MARKER not in PROMPTS["screen"][0], "the screen is never shown the context file")
    check(MARKER in PROMPTS["card"][0], "the card is, for its fit line")
    check(r.get("read_verdict") == "OPPORTUNITY" and r.get("triage_verdict") == "MAYBE",
          f"read as OPPORTUNITY, the triage's MAYBE kept beside it ({r.get('read_verdict')}, "
          f"{r.get('triage_verdict')})")
    check(v == "OPPORTUNITY" and "opportunity" in tags, f"and shown as an OPPORTUNITY ({v}: {t})")
    rows = [json.loads(l) for l in Path(ww.HISTORY_PATH).read_text(encoding="utf-8").splitlines()
            if l.strip()]
    written = [x for x in rows if x.get("summary")]
    check(written and written[-1]["verdict"] == "OPPORTUNITY",
          f"history.jsonl says OPPORTUNITY too ({written[-1]['verdict'] if written else None})")
    check(written and "## 10. Opportunity card" in written[-1]["summary"],
          "and the write-up carries the card as section 10")
    log = ww.opportunities_path()
    check(log.exists() and "shift-scheduling software" in log.read_text(encoding="utf-8"),
          "OPPORTUNITIES.md gains the card's line")
    ww._LANE["on"] = False


def h2_failed_card():
    """A card that fails says why in its own words, and the old gate's word on the Opportunity
    line, which the lane sets aside, is nowhere in the row or the history."""
    for path in (ww.RECORDS_PATH, ww.HISTORY_PATH, ww.SEEN_PATH):
        Path(path).unlink(missing_ok=True)
    ww.start_this_run()
    ww.claude_generate = lambda prompt, timeout=600: CARD.replace(
        "Bakery chains with three to ten shops, which already pay for payroll software",
        "Parents of high-school students")
    listing = [{"id": "lanevideo02", "title": "Selling to parents", "channel": "C",
                "url": "https://www.youtube.com/watch?v=lanevideo02", "description": "",
                "duration": 600, "upload_date": "20260301"}]
    ww._LANE["on"] = True
    ww.process_candidates(listing, f"CONTEXT {MARKER}", "model", False, {},
                          budget={"remaining": 3}, source="topics")
    ww._LANE["on"] = False
    r = ww.load_records()["lanevideo02"]
    v, t, tags = ww.final_verdict(r)
    check(v == "MAYBE" and "consumers" in t, f"the row is a MAYBE that names the buyer test ({t})")
    rows = [json.loads(l) for l in Path(ww.HISTORY_PATH).read_text(encoding="utf-8").splitlines()
            if l.strip()]
    reason = next((x["reason"] for x in rows if "lanevideo02" in x.get("url", "")
                   and x.get("summary")), "")
    check("consumers" in reason and "no Opportunity line survived" not in reason,
          f"and history.jsonl gives the card's reason, not the old gate's ({reason[-160:]})")
    check(r.get("settled_verdict") == "OPPORTUNITY",
          "the write-up's own verdict is left for the card to decide")
    ww.claude_generate = lambda prompt, timeout=600: (PROMPTS["card"].append(prompt), CARD)[1]


def i_retry():
    for path in (ww.RECORDS_PATH, ww.HISTORY_PATH, ww.SEEN_PATH):
        Path(path).unlink(missing_ok=True)
    ww.start_this_run()
    vid = "retrylane01"
    (ww.RAW_DIR / f"{vid}.txt").write_text(TALK * 60, encoding="utf-8")
    ww.update_record(vid, url=f"https://www.youtube.com/watch?v={vid}", title="Selling schedules",
                     channel="C", source="topics", listing_verdict="MAYBE",
                     summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout",
                     read_verdict="OPPORTUNITY", read_reason="screen passed", read_day=TODAY,
                     attempts=1, minutes=10.0, words=1500, opp_lane=1,
                     opp_screen=ww.parse_screen(SCREEN_YES), triage_verdict="MAYBE")
    before = len(PROMPTS["screen"])
    ww._LANE["on"] = True
    ww.run_retry_phase(f"CONTEXT {MARKER}", "model", False)
    ww._LANE["on"] = False
    r = ww.load_records()[vid]
    check(len(PROMPTS["screen"]) == before, "the retry reuses the screen it already has")
    check(r.get("summary_state") == "WRITTEN" and r.get("opp_card")
          and ww.final_verdict(r)[0] == "OPPORTUNITY",
          f"and a write-up done on a retry gets its card and its lane verdict "
          f"({r.get('summary_state')}, {ww.final_verdict(r)[0]})")


class FakeJev:
    """Stands in for jev.ask: records what it was sent and answers with a set probability."""

    def __init__(self, p=None, fail=None):
        self.p, self.fail, self.calls = p, fail, []

    def __call__(self, state, questions, ledger, **kw):
        self.calls.append((state, questions, ledger))
        if self.fail:
            raise self.fail
        return {"answers": {"opportunity": {"type": "noul", "noul": self.p}},
                "usage": {"input_tokens": 900, "output_tokens": 5, "cost": 0.0000378},
                "cost": 0.0000378, "seconds": 0.2, "model": "typesafe/jev-1.13"}


def k_jev_screen():
    real_ask = jev.ask
    meta = {"title": "How we sold schedules to bakery chains", "channel": "C",
            "transcript": TALK * 20, "url": "https://www.youtube.com/watch?v=jevscreen01"}
    try:
        ww.OPPORTUNITY_SCREEN_BACKEND = "jev"
        check(ww.JEV_SCREEN_THRESHOLD == 0.4, "the line Jev's score has to reach is 0.4")
        jev.ask = FakeJev(p=0.84)
        s = ww.screen_opportunity(meta, "model")
        check(s.get("model") == "jev" and s.get("p") == 0.84 and ww.screen_passes(s),
              f"Jev at 0.84 passes the screen ({s})")
        state, questions, ledger = jev.ask.calls[-1]
        check(set(state) <= jev.PUBLIC_STATE_KEYS and MARKER not in json.dumps(state),
              "it is sent public video text only, never the context file")
        check(ledger.per_day and Path(ledger.path) == Path(ww.JEV_SPEND_PATH)
              and ledger.cap == ww.JEV_DAILY_CAP_USD, "through the tool's daily ledger")
        v, r = ww.route_for_opportunity("MAYBE", "thin", s)
        check(v == "OPPORTUNITY" and "Jev" in r and "0.84" in r, f"and routes a MAYBE, saying so ({r})")
        jev.ask = FakeJev(p=0.39)
        s = ww.screen_opportunity(meta, "model")
        check(not ww.screen_passes(s), "CONTROL: Jev at 0.39 does not pass")
        before = len(PROMPTS["screen"])
        jev.ask = FakeJev(fail=jev.SpendCapReached("refused: the day's cap is spent"))
        s = ww.screen_opportunity(meta, "model")
        check(s.get("model") != "jev" and "cap" in (s.get("jev_error") or "")
              and len(PROMPTS["screen"]) == before + 1,
              f"Jev out of its daily cap: the local model screens it, and the record says why ({s.get('jev_error')})")
        jev.ask = FakeJev(fail=jev.KeyUnavailable("OPENROUTER_API_KEY is not set"))
        s = ww.screen_opportunity(meta, "model")
        check(s.get("model") != "jev" and "OPENROUTER_API_KEY" in (s.get("jev_error") or ""),
              "no key in the environment: the local model screens it")
        ww.OPPORTUNITY_SCREEN_BACKEND = "local"
        jev.ask = FakeJev(p=0.99)
        ww.screen_opportunity(meta, "model")
        check(not jev.ask.calls, "CONTROL: with the backend set to local, Jev is never asked")
    finally:
        jev.ask = real_ask
        ww.OPPORTUNITY_SCREEN_BACKEND = "local"


def j_switch():
    src = Path(ww.__file__).read_text(encoding="utf-8")
    main_src = src[src.index("def main():"):]
    check('_LANE["on"] = True' in main_src,
          "main() switches the lane on, for the run modes")
    check(src.count('_LANE["on"] = True') == 1, "and nothing else in core.py does")
    check('_LANE["on"] = False' in main_src[main_src.index("finally:"):],
          "and main() switches it off again when it returns, so it never outlives the run")
    defs = src[:src.index("def main():")]
    check("_LANE = {\"on\": False}" in defs, "the lane is off at import, so no other suite meets it")


def l_screen_on_rows():
    """Each row of the full digest carries the screen's answer, so a reader can see why a video
    read that day was not an opportunity; the short digest leaves it out."""
    for path in (ww.RECORDS_PATH, ww.HISTORY_PATH):
        Path(path).unlink(missing_ok=True)

    def read(vid, title, screen, day=TODAY):
        fields = dict(url=f"https://www.youtube.com/watch?v={vid}", title=title, channel="C",
                      source="topics", listing_verdict="MAYBE", summary_state="WRITTEN",
                      reason_code="written", read_verdict="YES", settled_verdict="MAYBE",
                      read_reason="a reason", applies="nothing", project=None, takeaway=None,
                      try_this_week="nothing", header=None, read_day=day, written_day=day, day=day)
        if screen is not None:
            fields.update(opp_lane=1, opp_screen=screen, opp_card=None, opp_cap=None,
                          triage_verdict="YES")
        return ww.update_record(vid, **fields)

    read("scorevid001", "Video one, three in a hundred",
         {"model": "jev", "p": 0.03, "says": "NO", "cost": 0.0001, "seconds": 0.3})
    read("scorevid002", "Video two, a little more", {"model": "jev", "p": 0.164, "says": "NO"})
    read("scorevid003", "Video three, answered locally",
         dict(ww.parse_screen(SCREEN_NO), model="qwen", jev_error="JevError: no key"))
    read("scorevid004", "Video four, nobody to ask",
         {"error": "OllamaDown: refused", "model": "qwen", "jev_error": "JevError: no key"})
    read("scorevid005", "Video five, before the lane existed", None)
    path = ww.write_day_digest(TODAY, "", create=True)
    page = path.read_text(encoding="utf-8")
    table = page[page.find("<!-- TABLE-START -->"):page.find("<!-- TABLE-END -->")]

    def row(text, title):
        return next((l for l in text.splitlines() if l.startswith("|") and title in l), "")

    first = row(table, "Video one, three in a hundred")
    check(first.rstrip().endswith("[Jev 0.03] |"),
          f"the full digest's row carries Jev's score, last in its reason ({first[-60:]!r})")
    check("named no project" in first and "a reason" in first,
          "and keeps its reason and its demotion note")
    check("[Jev 0.16]" in row(table, "Video two, a little more"),
          "the score is shown to two places (0.164 reads 0.16)")
    check("[Jev did not answer; the local screen said NO]" in row(table, "Video three, answered locally"),
          "a row the local screen answered for Jev says so, and what it said")
    check("[the opportunity screen could not be asked]" in row(table, "Video four, nobody to ask"),
          "a row whose screen could not be asked says that")
    never = row(table, "Video five, before the lane existed")
    check(bool(never) and "Jev" not in never and "screen" not in never,
          f"CONTROL: a row that was never screened says nothing about the screen ({never[-50:]!r})")
    block = page[page.find("<!-- OPPS-START -->"):page.find("<!-- OPPS-END -->")]
    check("None surfaced. Of the 4 video(s) read, 0 passed the screen and 0 got a card" in block,
          "the block still counts the day: 4 screened, 0 passed, 0 cards")
    check("each row of the table carries the screen's answer" in block and "passes at 0.4" in block,
          "and says where the answers are, and the line Jev's score has to reach")
    check("says why it is not an opportunity" not in page,
          "the sentence no row bore out is gone")
    short = (ww.OUTPUT_DIR / f"DIGEST-{TODAY}-short.md").read_text(encoding="utf-8")
    stable = short[short.find("## The day so far"):short.find("## Kept")]
    check("Video one, three in a hundred" in stable and "[Jev" not in stable
          and "the local screen said" not in stable and "could not be asked" not in stable,
          "the short digest's rows carry no screen answer: the full digest only")
    sblock = short[short.find("## Opportunities today"):short.find("## The day so far")]
    check("None surfaced. Of the 4 video(s) read, 0 passed the screen and 0 got a card" in sblock
          and "on its row in the full digest" in sblock
          and "each row of the table carries" not in sblock,
          "and its block says the answers are in the full digest, which is true of that file")
    check(not any("[Jev" in l for l in ww.day_table_lines(ww.todays_day_records(TODAY))),
          "CONTROL: the table builder adds no note unless it is asked to")
    ww.write_day_digest(TODAY, "", create=True)
    check(path.read_text(encoding="utf-8") == page, "writing the digest again changes nothing")
    # A card that surfaces: the block shows the card, and the row still carries the score.
    good = ww.finish_card(ww.parse_card(CARD))
    ww.update_record("scorevid006", url="https://www.youtube.com/watch?v=scorevid006",
                     title="Video six, the card passed", channel="C", source="topics",
                     listing_verdict="MAYBE", summary_state="WRITTEN", reason_code="written",
                     read_verdict="OPPORTUNITY", settled_verdict="OPPORTUNITY",
                     read_reason="a reason", applies="nothing", project=None, takeaway=None,
                     try_this_week="nothing", header=None, read_day=TODAY, written_day=TODAY,
                     day=TODAY, opp_lane=1, opp_screen={"model": "jev", "p": 0.79, "says": "YES"},
                     opp_card=good, opp_cap=None, triage_verdict="MAYBE")
    page = ww.write_day_digest(TODAY, "", create=True).read_text(encoding="utf-8")
    table = page[page.find("<!-- TABLE-START -->"):page.find("<!-- TABLE-END -->")]
    kept = row(table, "Video six, the card passed")
    check(kept.startswith("| OPPORTUNITY |") and "[Jev 0.79]" in kept,
          f"a surfaced opportunity's row carries its score too ({kept[:40]!r} ... {kept[-30:]!r})")
    check("None surfaced" not in page and "### 1." in page,
          "and the block shows its card instead of the sentence")
    # A day the lane never judged gains nothing.
    old_day = "2026-03-02"
    read("scorevid007", "Video seven, a day before the lane", None, day=old_day)
    old_page = ww.write_day_digest(old_day, "", create=True).read_text(encoding="utf-8")
    check("Video seven, a day before the lane" in old_page and "Jev" not in old_page
          and "Opportunities today" not in old_page,
          "CONTROL: a day the lane never judged gains no note and no block")
    local_only = [r for r in ww.todays_day_records(TODAY)
                  if (r.get("opp_screen") or {}).get("model") == "qwen"]
    lines = "\n".join(ww.opportunity_block_lines(TODAY, local_only, full=True))
    check(len(local_only) == 2 and "None surfaced. Of the 2 video(s) read" in lines
          and "carries the screen's answer" in lines and "Jev" not in lines,
          f"CONTROL: a day only the local screen judged does not speak of Jev's score ({lines[-90:]!r})")


section("A. the screen, read and passed", a_screen)
section("B. the screen routes and never demotes", b_route)
section("C. the card and its checks", c_card)
section("D. killed ideas are never surfaced", d_killed)
section("E. the verdict: never demoted for naming no project", e_verdict)
section("F. at most three a day, in their own section, in both digests and the weekly", f_cap_and_digest)
section("G. a day the lane never judged", g_no_lane_day)
section("H. the fetch path, end to end, with the lane off and on", h_end_to_end)
section("H2. a card that fails says why", h2_failed_card)
section("I. the retry path", i_retry)
section("J. who switches the lane on", j_switch)
section("K. Jev is the screen, and the local model answers when it cannot", k_jev_screen)
section("L. the screen's answer on each row of the full digest, and only there", l_screen_on_rows)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
