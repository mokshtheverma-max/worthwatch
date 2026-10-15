"""The ideas lane.

Every write-up by the writing brain also lists, in the same call, every product, business or
project idea the video contains or implies, as short cards checked in code: a figure the
transcript does not hold reads unknown, a killed idea is one line unless the video brings new
evidence, the same idea on another day backs the card already on the board, and nothing about
the reader's age, money, licences, hired help, consumers or a project ever drops a card. The
cards go to ideas.json, the board IDEAS.md, the day's digest ("Ideas today" first), a daily
remix and the Friday weekly. Behind the triage, a keep rule: a NO with a money figure or a
founder's story is a MAYBE.

No YouTube request and no model call: every model is a stub, and a spy raises on anything else
that would leave this machine. All of it runs on the invented example person, inside one fresh
folder under the OS temp folder (tests/_tmpsandbox.py).

Watch it go red: make check_idea_card drop a card whose who_pays names consumers (return None
for it) in a copy of src/worthwatch/core.py and run this file against the copy.
"""
import datetime as dt
import io
import re
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "ideas")

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
        traceback.print_exc(limit=3, file=sys.stdout)
        check(False, f"{title}: raised {e.__class__.__name__}: {e}")


class Spy:
    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **k):
        raise AssertionError(f"the test called {self.name}; it must make no request")


for _name in ("run_yt_dlp", "extract_media_info", "download_audio_from_info", "transcribe_audio",
              "fetch_channel_feed", "load_whisper", "ollama_generate", "claude_generate"):
    setattr(ww, _name, Spy(_name))
if hasattr(ww, "claude_generate_with"):
    ww.claude_generate_with = Spy("claude_generate_with")
ww.polite_delay = lambda *a, **k: None

DAY = "2026-03-04"
URL1 = "https://www.youtube.com/watch?v=ideavideo01"
URL2 = "https://www.youtube.com/watch?v=ideavideo02"
FILLER = ("and so we kept going one week after another learning what worked and what did not "
          "work for the people we talked to every single day ") * 40
TRANSCRIPT = (
    "Hi everyone, today I want to tell you how I built a tiny app that checks restaurant menus "
    "for allergens. " + FILLER +
    "We charge restaurants 49 dollars a month and about 300 restaurants are paying right now. " +
    FILLER + "That is roughly 14,700 dollars a month in revenue and it took us three months. " +
    FILLER + "My advice is to use a coding agent to write the first version in a weekend. " + FILLER)
MINUTES = 20.0

CARD_ALLERGEN = """### Idea 1: An allergen checker for restaurant menus
- Who pays: restaurants
- How much: $49 a month per restaurant (said)
- Why now: menus change weekly and allergy rules are getting stricter
- First step: build a menu-photo checker with a coding agent and show it to three local cafes
- Also works for: school cafeterias; food trucks; Ledgerleaf's import checks applied to menus
- The catch: one wrong answer is a health risk
- Where in the video: "We charge restaurants 49 dollars a month"
- Project: none"""
CARD_DOGWALK = """### Idea 2: A dog-walking app that books walkers for offices
- Who pays: offices that let staff bring dogs
- How much: unknown
- Why now: more offices allow dogs this year
- First step: call five offices and ask who walks the dogs at noon
- Also works for: apartment buildings; hotels
- The catch: three free apps already do it
- Where in the video: "use a coding agent to write the first version"
- Project: none
- Killed: dog-walking apps | new evidence: none"""
CARD_GATED = """### Idea 3: A licensed tax-filing service for teenagers' first jobs
- Who pays: consumers, parents of teens with a first job
- How much: $5,000 a month (shown)
- Why now: more teens work part time
- First step: needs a licence, $50,000 and hired help; a parent would have to sign for a teenager
- Also works for: college students; gig workers
- The catch: needs a licence and an adult; consumers are hard to reach
- Where in the video: "these words are never in the transcript at all"
- Project: none"""
PICK = "Pick: Idea 1, because restaurants already pay for it and the first step fits this month"
IDEAS_PART = "\n\n".join([CARD_ALLERGEN, CARD_DOGWALK, CARD_GATED, PICK])

WRITEUP = """## 1. What it is
A founder interview about an allergen app.

## 2. The claim
A small app can earn money from restaurants.

## 3. Evidenced versus asserted
The 49 dollar price was only said.

## 4. The method
1. Build it in a weekend.

## 5. Worth keeping verbatim
None.

## 6. What applies to me
nothing

## 7. What contradicts what I believe
nothing in this video touches the beliefs list

## 8. Try this week
nothing

## 9. Header
nothing"""
REPLY = WRITEUP + "\n\n## Ideas\n\n" + IDEAS_PART


def meta_for(url=URL1, title="How I built a $14k a month allergen app", transcript=TRANSCRIPT):
    return {"id": ww.video_id_from_url(url), "url": url, "title": title, "uploader": "Founders",
            "channel": "Founders", "transcript": transcript, "minutes": MINUTES}


def reset_store():
    for path in (ww.IDEAS_PATH, ww.ideas_board_path(), ww.ideas_taste_path()):
        Path(path).unlink(missing_ok=True)


# ==========================================================================================
def a_split():
    up, part = ww.split_ideas_section(REPLY)
    check(up == WRITEUP, "the write-up is exactly sections 1 to 9, as the model wrote them")
    check(part is not None and part.startswith("### Idea 1"), "and the Ideas part is set aside")
    up2, part2 = ww.split_ideas_section(WRITEUP)
    check(up2 == WRITEUP and part2 is None,
          "CONTROL: a reply with no Ideas heading is left whole, and has no part")
    early = WRITEUP.replace("## 4. The method", "## Ideas\nnot really\n\n## 4. The method")
    _u, p3 = ww.split_ideas_section(early + "\n\n## Ideas\n\n" + IDEAS_PART)
    check(p3 is not None and p3.startswith("### Idea 1"), "the last Ideas heading is the one")
    bare = ww.split_ideas_section(WRITEUP + "\n\n" + IDEAS_PART)
    check(bare[0] == WRITEUP and bare[1] and bare[1].startswith("### Idea 1"),
          "cards written with no Ideas heading are still split off after section 9")
    _u, p4 = ww.split_ideas_section(WRITEUP + "\n\n## Ideas\n\nnone")
    check(p4 == "none" and ww.parse_ideas(p4) == ([], None), "\"none\" is no cards and no pick")


def b_parse():
    cards, pick = ww.parse_ideas(IDEAS_PART)
    check(len(cards) == 3, f"three cards are read ({len(cards)})")
    c = cards[0]
    check(c["line"] == "An allergen checker for restaurant menus" and c["who_pays"] == "restaurants"
          and c["how_much"].startswith("$49") and c["first_step"].startswith("build"),
          f"each field is read ({c})")
    check(pick and pick[0] == 1 and pick[1].startswith("restaurants already pay"),
          f"the pick and its why are read ({pick})")
    bold = IDEAS_PART.replace("- Who pays:", "- **Who pays:**").replace("### Idea 1:", "## Idea 1:")
    check(ww.parse_ideas(bold)[0][0]["who_pays"] == "restaurants", "bold labels read the same")


def c_figures():
    cards, _p = ww.parse_ideas(IDEAS_PART)
    ok = ww.check_idea_card(cards[0], TRANSCRIPT, MINUTES)
    check(ok["mark"] == "said" and ok["how_much"].startswith("$49"),
          f"a figure the transcript says is kept, marked said ({ok['how_much']}, {ok['mark']})")
    bad = ww.check_idea_card(cards[2], TRANSCRIPT, MINUTES)
    check(bad["how_much"].startswith("unknown") and bad["mark"] is None
          and any("not in the transcript" in n for n in bad["notes"]),
          f"a figure it does not say reads unknown, and the card says why ({bad['how_much']})")
    k = ww.check_idea_card(dict(cards[0], how_much="$14.7k a month (said)"), TRANSCRIPT, MINUTES)
    check(k["mark"] == "said", "14.7k matches the transcript's 14,700")
    none = ww.check_idea_card(dict(cards[0], how_much="a monthly fee (said)"), TRANSCRIPT, MINUTES)
    check(none["how_much"] == "a monthly fee (said)",
          "CONTROL: a field with no number has nothing to invent and is kept as written")
    check(ww.figure_in_transcript("$49 a month", TRANSCRIPT)
          and not ww.figure_in_transcript("$48 a month", TRANSCRIPT),
          "CONTROL: 49 is in the transcript and 48 is not")


def d_time():
    cards, _p = ww.parse_ideas(IDEAS_PART)
    ok = ww.check_idea_card(cards[0], TRANSCRIPT, MINUTES)
    words = ww.canon_tokens(TRANSCRIPT)
    at = ww.canon_tokens("We charge restaurants 49 dollars a month")
    index = next(i for i in range(len(words)) if words[i:i + len(at)] == at)
    want = round(index / len(words) * MINUTES * 60)
    check(ok["seconds"] == want, f"the quote's place among the words gives its time ({ok['seconds']} s, "
                                 f"{want} s worked out by hand)")
    check(ww.timed_link(URL1, ok["seconds"]) == f"{URL1}&t={want}s", "the link carries &t=")
    bad = ww.check_idea_card(cards[2], TRANSCRIPT, MINUTES)
    check(bad["seconds"] is None and any("quote was not found" in n for n in bad["notes"]),
          "CONTROL: a quote that is not in the transcript gives no time, and says so")
    check(ww.quote_seconds("We charge restaurants 49 dollars a month", TRANSCRIPT, None) is None,
          "CONTROL: an unknown length gives no time either")


def e_no_gate():
    reset_store()
    cards, pick = ww.parse_ideas(IDEAS_PART)
    checked = [dict(ww.check_idea_card(c, TRANSCRIPT, MINUTES), n=c["n"]) for c in cards]
    gated = checked[2]
    check(gated is not None and not gated["seen_before"]
          and gated["line"].startswith("A licensed tax-filing"),
          "a card about consumers, a licence, $50,000, hired help and a teenager is kept whole")
    out = ww.record_video_ideas(meta_for(), checked, pick=pick, day=DAY)
    hows = [h for _c, _i, h in out]
    check(hows == ["new", "seen_before", "new"], f"it goes on the board as a new card ({hows})")
    store = ww.load_ideas()
    lines = [i["line"] for i in store["ideas"].values()]
    check(any(l.startswith("A licensed tax-filing") for l in lines), "and the board keeps it")
    board = ww.ideas_board_path().read_text(encoding="utf-8")
    check("A licensed tax-filing" in board and "needs a licence" in board,
          "IDEAS.md shows it with its catch as a note, not as a reason to drop it")


def f_killed():
    reset_store()
    cards, _p = ww.parse_ideas(IDEAS_PART)
    rec = ww.check_idea_card(cards[1], TRANSCRIPT, MINUTES)
    check(rec["seen_before"] and rec["killed"]["name"] == "dog-walking apps",
          f"a killed idea with no new evidence is seen before ({rec['killed']})")
    check(rec["killed"]["date"] == "2 March 2026"
          and rec["killed"]["because"] == "three free apps already do it well",
          f"with the date and the reason it was killed, as rules.toml has them "
          f"({rec['killed']['date']}: {rec['killed']['because']})")
    code_only = dict(cards[1])
    code_only.pop("killed")
    code_only["line"] = "A dog walking service app for apartment buildings"
    rec2 = ww.check_idea_card(code_only, TRANSCRIPT, MINUTES)
    check(rec2["seen_before"], "the code finds it too when the card forgot its Killed line")
    newer = dict(cards[1], killed="dog-walking apps | new evidence: 300 offices already pay for it "
                                  "at 49 dollars a month, so the free apps did not win")
    rec3 = ww.check_idea_card(newer, TRANSCRIPT, MINUTES)
    check(not rec3["seen_before"] and rec3["killed"]["new_evidence"].startswith("300 offices"),
          "new evidence against the reason makes it a full card, marked as a killed idea")
    lines = ww.idea_card_lines(rec3, URL1)
    check(any(l.startswith("- Killed idea, new evidence:") for l in lines),
          "and the card says so in its own line")
    out = ww.record_video_ideas(meta_for(), [rec, rec3], day=DAY)
    check([h for _c, _i, h in out] == ["seen_before", "new"], "one line, then one full card")
    text = ww.ideas_section_text(meta_for(), out)
    check("Seen before: A dog-walking app" in text and "Killed on" in text,
          "the write-up's line reads seen before, killed on a date because a reason")
    plain = ww.check_idea_card(dict(cards[0]), TRANSCRIPT, MINUTES)
    check(plain["killed"] is None and not plain["seen_before"],
          "CONTROL: an idea on no killed list is not touched")
    # A word a killed idea shares is not the killed idea: a walk-in cooler is not dog walking.
    cooler = dict(cards[0], line="A walk-in cooler log that tells a cafe when the fridge drifts")
    check(not ww.check_idea_card(cooler, TRANSCRIPT, MINUTES)["seen_before"],
          "a walk-in cooler log is not the killed dog-walking apps")
    walkers = dict(cards[0], line="An app that books dog walkers for apartment buildings")
    check(ww.check_idea_card(walkers, TRANSCRIPT, MINUTES)["seen_before"],
          "CONTROL: an app that books dog walkers still is")
    names = [name for name, _pattern in ww.KILLED_IDEAS]
    undated = [n for n in names if n not in ww.KILLED_IDEA_NOTES]
    check(not undated, f"every killed name has a note, with its date and reason or with neither "
                       f"(missing: {undated})")
    check(ww.killed_note("crypto tipping for creators")["date"] == "30 June 2026",
          "a dated kill reads its own date")
    check(ww.killed_note("a checklist app") == {"name": "a checklist app",
                                                "date": "date not recorded",
                                                "because": "the reason is not recorded"},
          "CONTROL: a kill the rules do not date says so, never a guessed date or reason")


def g_dedupe():
    reset_store()
    cards, pick = ww.parse_ideas(IDEAS_PART)
    first = [dict(ww.check_idea_card(c, TRANSCRIPT, MINUTES), n=c["n"]) for c in cards]
    ww.record_video_ideas(meta_for(), first, pick=pick, day=DAY)
    store = ww.load_ideas()
    allergen = next(i for i in store["ideas"].values() if i["line"].startswith("An allergen"))
    again = dict(cards[0], line="Menu allergen scanning for cafes", same_as=allergen["id"])
    later = "2026-03-05"
    out = ww.record_video_ideas(meta_for(URL2, "Another allergen story"),
                                [dict(ww.check_idea_card(again, TRANSCRIPT, MINUTES), n=1)], day=later)
    store = ww.load_ideas()
    check(out[0][1:] == (allergen["id"], "merged") and len(store["ideas"]) == 2,
          f"\"Same as\" adds the new video to the card already on the board ({out[0][1:]})")
    card = store["ideas"][allergen["id"]]
    check(ww.idea_backing(card) == 2 and card["last_day"] == later,
          "two videos back it now, and it was last seen on the later day")
    twin = dict(cards[0], line="An allergen checker for restaurant menus.")
    out2 = ww.record_video_ideas(meta_for("https://www.youtube.com/watch?v=ideavideo03", "Third"),
                                 [dict(ww.check_idea_card(twin, TRANSCRIPT, MINUTES), n=1)], day=later)
    check(out2[0][2] == "merged", "the same line written again is joined without a Same as")
    other = dict(cards[0], line="A plant-watering sensor kit for office buildings")
    out3 = ww.record_video_ideas(meta_for("https://www.youtube.com/watch?v=ideavideo04", "Fourth"),
                                 [dict(ww.check_idea_card(other, TRANSCRIPT, MINUTES), n=1)], day=later)
    check(out3[0][2] == "new", "CONTROL: a different idea is its own card")
    ww.record_video_ideas(meta_for(), first, pick=pick, day=DAY)
    store = ww.load_ideas()
    check(ww.idea_backing(store["ideas"][allergen["id"]]) == 3,
          "CONTROL: the first video written up again replaces its evidence, never doubles it")
    check(not ww.same_idea("An allergen checker for restaurant menus",
                           "A plant-watering sensor kit for office buildings"),
          "CONTROL: same_idea tells them apart")
    # A model writes "Same as" for ideas that only share a topic, and the joined idea's own words
    # were lost. So a "Same as" between two lines with no word in common is its own card, and a
    # joined idea keeps its own line, on the board and in the day's list.
    reset_store()
    ww.record_video_ideas(meta_for(), [dict(ww.check_idea_card(cards[0], TRANSCRIPT, MINUTES), n=1)],
                          day=DAY)
    first_id = next(iter(ww.load_ideas()["ideas"]))
    loose = dict(cards[0], line="A plant-watering sensor kit for office buildings", same_as=first_id)
    out4 = ww.record_video_ideas(meta_for(URL2, "Plants"),
                                 [dict(ww.check_idea_card(loose, TRANSCRIPT, MINUTES), n=1)], day=DAY)
    check(out4[0][2] == "new" and any("share no word" in n for n in out4[0][0]["notes"]),
          "a Same as between lines with no word in common is its own card, and says why")
    close = dict(cards[0], line="Menu allergen scanning for cafes", same_as=first_id)
    out5 = ww.record_video_ideas(meta_for("https://www.youtube.com/watch?v=ideavideo05", "Cafes"),
                                 [dict(ww.check_idea_card(close, TRANSCRIPT, MINUTES), n=1)],
                                 day="2026-03-05")
    check(out5[0][1:] == (first_id, "merged"), "CONTROL: a Same as that shares words still joins")
    board = ww.ideas_board_path().read_text(encoding="utf-8")
    check("seen as: Menu allergen scanning for cafes" in board,
          "the board keeps the joined idea's own line")
    real_day_records = ww.todays_day_records
    ww.todays_day_records = lambda day=None: []
    try:
        block = "\n".join(ww.every_idea_lines("2026-03-05"))
    finally:
        ww.todays_day_records = real_day_records
    check("Menu allergen scanning for cafes" in block and f"joins {first_id}" in block,
          "and the day's list shows it in its own words, joined to the card")


def h_board_and_taste():
    reset_store()
    cards, pick = ww.parse_ideas(IDEAS_PART)
    checked = [dict(ww.check_idea_card(c, TRANSCRIPT, MINUTES), n=c["n"]) for c in cards]
    ww.record_video_ideas(meta_for(), checked, pick=pick, day=DAY)
    board = ww.ideas_board_path().read_text(encoding="utf-8")
    check(board.startswith("# Ideas") and "## I-0001: An allergen checker" in board
          and "Backed by 1 video" in board, "IDEAS.md lists each card with its id and backing")
    check("&t=" in board and "Seen before" in board, "with a timed link, and the seen-before list")
    check(ww.ideas_taste_path().exists(), "IDEAS-TASTE.md is made the first time")
    check(ww.cmd_taste("like", ["1", "boring", "and", "paid"]) == 0, "`worthwatch like 1 ...` works")
    check(ww.cmd_taste("nah", ["I-0002"]) == 0, "`worthwatch nah I-0002` works")
    check(ww.cmd_taste("like", ["I-0999"]) == 1, "CONTROL: an id not on the board is refused")
    with open(ww.ideas_taste_path(), "a", encoding="utf-8") as f:
        f.write("I-0002 like: changed my mind\n")
    taste = ww.read_taste()
    check(taste == [("I-0001", "like", taste[0][2]), ("I-0002", "like", "changed my mind")],
          f"a line written by hand counts, and the newest per id wins ({taste})")
    prompt = ww.ideas_instructions()
    check("LIKE I-0001: An allergen checker" in prompt and "LIKE I-0002" in prompt,
          "the idea prompt carries the taste with each idea's line")
    check("I-0001: An allergen checker" in prompt.split("IDEAS ALREADY ON THE BOARD")[1],
          "and the board, for Same as")
    check("dog-walking apps: killed" in prompt, "and the killed list with dates and reasons")
    check("never because it is on the same topic" in prompt,
          "and says Same as is for the same idea, not the same topic")


def i_prompt():
    meta = meta_for()
    meta["verdict"], meta["verdict_reason"] = "MAYBE", "thin"
    plain = ww.build_prompt("CTX", meta, TRANSCRIPT)
    old = ww.SUMMARY_INSTRUCTIONS.format(reader=ww.READER, applies_choices=ww.applies_choices(),
                                         context="CTX", title=meta["title"],
                                         uploader=meta["uploader"], url=meta["url"],
                                         verdict_note=ww.verdict_note(meta), transcript=TRANSCRIPT)
    check(plain == old, "CONTROL: the prompt everyone else builds is exactly the write-up prompt")
    sent = []
    ww.claude_generate = lambda prompt, timeout=600: (sent.append(prompt), REPLY)[1]
    text, _chunked = ww.summarize_claude("CTX", meta, TRANSCRIPT, timeout=30)
    check(sent and sent[0].startswith(old) and "## Ideas" in sent[0][len(old):],
          "the writing brain's prompt is the write-up prompt, word for word, then the ideas part")
    check(text == WRITEUP and meta.get("ideas_part", "").startswith("### Idea 1"),
          "what comes back is the write-up alone; the ideas part waits on the meta")
    for line in ("What is the shovel in this gold rush?", "What is the boring version that makes money?",
                 "What would this look like for a different customer?",
                 "What could a tiny team build in a month that a big company won't?",
                 "What does this make newly possible?"):
        check(line in sent[0], f"the thinking prompt is there: {line}")
    ww.claude_generate = Spy("claude_generate")
    local = ww.build_prompt("CTX", meta, TRANSCRIPT)
    check("## Ideas" not in local[len(old) - 20:], "CONTROL: the local model is never asked for ideas")


def j_writeup_and_verdict():
    """The ideas ride along and change no verdict: the same write-up with and without them gives
    the same final verdict, and the saved file ends with the checked Ideas section."""
    reset_store()
    meta = meta_for()
    meta["ideas_part"] = IDEAS_PART
    path = ww.save_writeup(meta, WRITEUP, "claude:sonnet", 9.0, False)
    saved = path.read_text(encoding="utf-8")
    check("Pick: I-0001, because restaurants already pay" in saved,
          "the saved write-up ends with its Ideas section and its pick")
    check(saved.index("## 9. Header") < saved.index("## Ideas"), "after section 9")
    check("unknown (the figure is not in the transcript)" in saved,
          "and the checked card, not the model's, is what is saved")
    store = ww.load_ideas()
    check(len(store["ideas"]) == 2 and len(store["seen_before"]) == 1,
          "two cards and one seen-before line are on the board")
    plain = dict(meta)
    plain.pop("ideas_part")
    reset_store()
    p2 = ww.save_writeup(plain, WRITEUP, "claude:sonnet", 9.0, False)
    check("## Ideas" not in p2.read_text(encoding="utf-8") and not ww.IDEAS_PATH.exists(),
          "CONTROL: a write-up with no ideas part writes no ideas")
    applies, project, take = ww.section6_state(WRITEUP)
    a2, p2_, t2 = ww.section6_state(saved.split("---", 1)[1])
    check((applies, project, take) == (a2, p2_, t2),
          "section 6 reads the same with the Ideas section after it")
    check(ww.extract_try_this_week(WRITEUP) == ww.extract_try_this_week(saved.split("---", 1)[1])
          and ww.writeup_header(WRITEUP) == ww.writeup_header(saved.split("---", 1)[1]),
          "so do section 8 and the header")


def k_digest():
    reset_store()
    real_day_records = ww.todays_day_records
    ww.todays_day_records = lambda day=None: []
    try:
        before = ww.digest_blocks(DAY)
        cards, pick = ww.parse_ideas(IDEAS_PART)
        checked = [dict(ww.check_idea_card(c, TRANSCRIPT, MINUTES), n=c["n"]) for c in cards]
        ww.record_video_ideas(meta_for(), checked, pick=pick, day=DAY)
        after = ww.digest_blocks(DAY)
        other = ww.digest_blocks("2026-03-03")
    finally:
        ww.todays_day_records = real_day_records
    head = after[0]
    check("## Ideas today" in head and "## Project tips" in head
          and head.index("## Ideas today") < head.index("## Project tips"),
          "a day with ideas opens with Ideas today, then the project tips")
    check("1. **I-0001** An allergen checker" in head and "restaurants already pay" in head,
          "the video's own pick is there, with its why")
    check(head.index(ww.context_age_line(DAY)) < head.index("## Ideas today"),
          "after the title and the context file's line")
    check("<!-- IDEAS-START -->" in head and "## Every idea today (2)" in head
          and "Seen before: A dog-walking app" in head, "every card of the day is listed after")
    check("(said) (said)" not in head and "how much: $49 a month per restaurant (said)" in head,
          "a figure's mark is shown once, even when the model had already marked it")
    check(other == ww.digest_blocks("2026-03-03") and "Ideas today" not in other[0],
          "CONTROL: a day with no ideas is written exactly as before")
    reset_store()
    ww.todays_day_records = lambda day=None: []
    try:
        check(ww.digest_blocks(DAY) == before, "CONTROL: and so is this day once the cards are gone")
    finally:
        ww.todays_day_records = real_day_records


def l_remix():
    reset_store()
    cards, pick = ww.parse_ideas(IDEAS_PART)
    checked = [dict(ww.check_idea_card(c, TRANSCRIPT, MINUTES), n=c["n"]) for c in cards]
    ww.record_video_ideas(meta_for(), checked, pick=pick, day=DAY)
    check(ww.REMIX_LAST_HOUR == 18, f"a day's remix is due from 18:00 ({ww.REMIX_LAST_HOUR})")
    afternoon = dt.datetime.fromisoformat(DAY + "T13:10:00")
    evening = dt.datetime.fromisoformat(DAY + "T18:20:00")
    check(ww.remix_due_days(afternoon) == [], "before 18:00, nothing is due")
    check(ww.remix_due_days(evening) == [DAY], "after it, the day is due")
    next_morning = dt.datetime.fromisoformat("2026-03-05T09:30:00")
    check(ww.remix_due_days(next_morning) == [DAY], "a day that missed its remix is due the next day")
    calls = []

    def model(prompt, name, timeout=600):
        calls.append(name)
        if name == "opus":
            raise RuntimeError("claude CLI (opus) failed (exit 1): model not available")
        return ("TOP: I-0001 | restaurants already pay\nTOP: I-0999 | not a real card\n"
                "TOP: I-0002 | a second pick\n"
                "COMBO: I-0001 for school cafeterias | Allergen checks for school lunches | why: "
                "schools buy safety | first step: email one school kitchen manager\n"
                "COMBO: I-0001 + I-0999 | invented | why: none | first step: none\n")
    ww.claude_generate_with = model
    real_refresh = ww.refresh_digest
    ww.refresh_digest = lambda day, create=False: None
    try:
        entry = ww.remix_day(DAY, now=evening)
    finally:
        ww.refresh_digest = real_refresh
        ww.claude_generate_with = Spy("claude_generate_with")
    check(calls == ["opus", "sonnet"] and entry["model"] == "sonnet",
          f"the bigger model first, the smaller when it fails ({calls})")
    check([t["id"] for t in entry["top"]] == ["I-0001", "I-0002"],
          f"a pick that is not one of the day's cards is dropped ({entry['top']})")
    check(len(entry["combos"]) == 1 and entry["combos"][0]["ids"] == ["I-0001"],
          "so is a combo with an invented id")
    check(ww.remix_due_days(next_morning) == [], "a remixed day is not due again")
    head = ww.ideas_today_lines(DAY)
    check(any("the remix's picks" in l for l in head), "Ideas today now uses the remix's picks")
    block = ww.every_idea_lines(DAY)
    check(any(l.startswith("## Remix of the day") for l in block)
          and any("Allergen checks for school lunches" in l for l in block),
          "and the digest shows the combo")
    reset_store()
    calls.clear()
    ww.claude_generate_with = model
    try:
        empty = ww.remix_day("2026-03-01", now=evening)
    finally:
        ww.claude_generate_with = Spy("claude_generate_with")
    check(not calls and empty.get("none"), "CONTROL: a day with no cards makes no call")


def m_week_board():
    reset_store()
    cards, pick = ww.parse_ideas(IDEAS_PART)
    checked = [dict(ww.check_idea_card(c, TRANSCRIPT, MINUTES), n=c["n"]) for c in cards]
    ww.record_video_ideas(meta_for(), checked, pick=pick, day=DAY)
    ww.ensure_taste_file()
    with open(ww.ideas_taste_path(), "a", encoding="utf-8") as f:
        f.write("I-0002 like\n")
    lines = ww.week_board_lines("2026-03-06")
    text = "\n".join(lines)
    check("## Idea board" in text and text.index("**I-0002**") < text.index("**I-0001**"),
          "the Friday board lists the week's cards, a liked one first")
    check("No remix this week yet." in text, "and says when there is no remix")
    check("(said) (said)" not in text, "and shows a figure's mark once")
    week = ww.OUTPUT_DIR / "WEEK-2026-03-06.md"
    week.parent.mkdir(parents=True, exist_ok=True)
    week.write_text("# Week of 2026-03-06\n\n3 new videos this week.\n\n## YES, in full\n", encoding="utf-8")
    ww.refresh_week_board("2026-03-06")
    body = week.read_text(encoding="utf-8")
    check(body.startswith("# Week of 2026-03-06") and body.index("## Idea board") < body.index("## YES"),
          "a remix on a Friday puts the board near the top of the week's file")
    ww.refresh_week_board("2026-03-06")
    check(week.read_text(encoding="utf-8").count("## Idea board") == 1, "and replaces it, never twice")


def n_keep_rule():
    check("never NO" in ww.TRIAGE_INSTRUCTIONS and "founder" in ww.TRIAGE_INSTRUCTIONS,
          "the triage prompt carries the keep rule")
    story = {"title": "t", "transcript": "When I started my business I sold 40 mugs"}
    money = {"title": "t", "transcript": "they make $12,000 a month from it"}
    music = {"title": "Lofi beats to study to", "transcript": "la la la oh oh yeah baby"}
    listing = {"title": "I make $25K/month from one simple website", "description": ""}
    check(ww.ideas_keep_reason(story) and ww.ideas_keep_reason(money)
          and ww.ideas_keep_reason(listing), "a founder's story, a money figure, a money title keep")
    check(ww.ideas_keep_reason(music) is None, "CONTROL: a song keeps nothing")

    def triage_said(answer):
        ww.ollama_generate = lambda *a, **k: answer
        try:
            return ww.triage_video("CTX", "C", dict(story, duration=600), "model")
        finally:
            ww.ollama_generate = Spy("ollama_generate")
    v, reason, conf = triage_said("COPY: none\nVERDICT: NO - nothing usable\nCONFIDENCE: 80")
    check(v == "MAYBE" and "kept by the ideas rule" in reason,
          f"a NO with a founder's story is a MAYBE, and says why ({v}: {reason})")
    ww.ollama_generate = lambda *a, **k: "COPY: none\nVERDICT: NO - a song\nCONFIDENCE: 90"
    try:
        v2 = ww.triage_video("CTX", "C", dict(music, duration=600), "model")
    finally:
        ww.ollama_generate = Spy("ollama_generate")
    check(v2[0] == "NO", "CONTROL: a NO with neither stays NO")
    v3 = triage_said("COPY: a pricing page\nVERDICT: YES - a price to copy\nCONFIDENCE: 70")
    check(v3[0] == "YES" and "ideas rule" not in v3[1], "CONTROL: it never touches a YES")
    v4 = triage_said("COPY: unknown\nVERDICT: MAYBE - unclear\nCONFIDENCE: 40")
    check(v4[0] == "MAYBE" and "ideas rule" not in v4[1], "CONTROL: nor a MAYBE")
    # The triage gives the four project verdicts, which the ideas lane does not change, so it
    # reads the context file without a section about the lane, and without any sentence listed
    # in LANE_ONLY_SENTENCES: a small local model moves on any wording.
    lane_sentence = "Every idea goes on the board, even one I would never build."
    example = ww.CONTEXT_PATH.read_text(encoding="utf-8")
    full = (example.replace("## Who I am\n\n", "## Who I am\n\n" + lane_sentence + "\n", 1)
            + "\n## The ideas lane\n\nThe lane has no gates: list every idea, big or small.\n")
    seen_prompts = []
    saved = ww.LANE_ONLY_SENTENCES
    ww.LANE_ONLY_SENTENCES = (lane_sentence,)
    ww.ollama_generate = lambda model, prompt, **k: (seen_prompts.append(prompt),
                                                      "COPY: x\nVERDICT: MAYBE - y\nCONFIDENCE: 5")[1]
    try:
        ww.triage_video(full, "C", dict(music, duration=600), "model")
    finally:
        ww.ollama_generate = Spy("ollama_generate")
        ww.LANE_ONLY_SENTENCES = saved
    sent = seen_prompts[-1]
    check(lane_sentence in full and "## The ideas lane" in full, "the context under test has both")
    check("## The ideas lane" not in sent and "The lane has no gates" not in sent,
          "the triage is not shown the ideas lane's section")
    check(lane_sentence not in re.sub(r"\s+", " ", sent), "nor a sentence listed as the lane's own")
    flat_sent = re.sub(r"\s+", " ", sent)
    check("## What the verdicts mean" in sent and "## Decisions already made" in sent
          and "I'm Mara, a solo developer." in flat_sent
          and "never hold customer money: Ledgerleaf reads bank exports" in flat_sent,
          "CONTROL: it is shown the rest of the context file, the verdicts and the decisions included")


def o_context():
    """What the lane leans on is in the example context file: the killed ideas, with the dates the
    board prints, and the four verdicts."""
    flat = re.sub(r"\s+", " ", ww.CONTEXT_PATH.read_text(encoding="utf-8"))
    killed = flat.split("## Ideas I killed", 1)[1].split(" ## ", 1)[0]
    for name, _pattern in ww.KILLED_IDEAS:
        check(name in killed, f"the killed idea {name!r} is under 'Ideas I killed'")
        note = ww.killed_note(name)
        if note["date"] != "date not recorded":
            check(note["date"] in killed and note["because"] in killed,
                  f"  with the date and reason the board prints ({note['date']})")
    verdicts = flat.split("## What the verdicts mean", 1)[1]
    for verdict in ("YES: a mechanism I can use within two weeks on a live project",
                    "OPPORTUNITY: something a person could sell, judged on its own terms",
                    "MAYBE: the honest default", "NO: a pitch with no mechanism in it"):
        check(verdict in verdicts, f"the verdict is defined: {verdict[:40]!r}")


for title, fn in (("A. the ideas part is split off before any check", a_split),
                  ("B. the cards are read", b_parse),
                  ("C. a figure is shown or said only when the transcript says it", c_figures),
                  ("D. a timestamp from where the quote falls", d_time),
                  ("E. no gate: age, money, licences, hired help, consumers", e_no_gate),
                  ("F. a killed idea is one line, unless there is new evidence", f_killed),
                  ("G. the same idea on another day backs the card already there", g_dedupe),
                  ("H. the board, the taste file, like and nah", h_board_and_taste),
                  ("I. the prompt: the write-up prompt word for word, then the ideas part", i_prompt),
                  ("J. the saved write-up, and no verdict moves", j_writeup_and_verdict),
                  ("K. the digest opens with Ideas today", k_digest),
                  ("L. the daily remix", l_remix),
                  ("M. the Friday weekly's idea board", m_week_board),
                  ("N. the triage keep rule", n_keep_rule),
                  ("O. the context file holds what the lane leans on", o_context)):
    section(title, fn)

print()
if fails:
    print(f"{len(fails)} FAILURES: " + "; ".join(fails[:6]))
    sys.exit(1)
print("ALL PASS")
