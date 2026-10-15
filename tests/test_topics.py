"""FIX 3, 2026-09-25. Topic queries that name the thing, and a cheap filter before triage.

A one-word query for a tool whose name is also two ordinary words returns songs, lyrics, a
playlist, a poem, a movie clip and products of a brand with the same name, against the few real
videos about the tool. The listing triage called every piece of junk MAYBE, so the only thing
standing between it and a download was the day's cap.

The filter judges a result on its title, its channel and the search's own snippet, before any
model call: a song, lyrics, a music playlist or poetry by the title, a music channel, or, for a
query that says what it needs, none of the words it needs. Every drop is logged with its
reason and recorded, so the digest can name it.

The week's results come from tests/fixtures/topics-week.json, an invented week of 21 to 26
September for an invented tool, OpenOven, whose name reads as "open oven": it stands in for the
two files of real search results this suite used to read, and has the same kinds of row. Since
2026-09-26 night every filter matches in any case. No YouTube request, no model call: the
search and the triage are stubbed, and a spy fails the test if a dropped result reaches the
triage. Everything runs in one fresh folder under the OS temp folder.
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "topics")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# This suite's own invented topics.txt, written over the example one: one search that names the
# tool and says what it needs.
ww.TOPICS_PATH.write_text(
    "# Invented searches for tests/test_topics.py.\n"
    "bakery bookkeeping software\n"
    "small bakery pricing\n"
    "OpenOven bake schedule planner | needs: OpenOven, bake schedule, bakery\n"
    "indie game newsletter sponsorship\n"
    "\n"
    "newsletter welcome email examples\n"
    "solo founder first customers\n", encoding="utf-8")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


parse = getattr(ww, "parse_topic_line", None)
problem = getattr(ww, "topic_result_problem", None)
check(parse is not None and problem is not None,
      "one function reads a topics.txt line, one judges a result")
if parse is None or problem is None:
    print("\n" + f"{len(fails)} FAILURES: " + "; ".join(fails))
    sys.exit(1)

# ==========================================================================================
print("\n=== A. a query line can say what a result needs ===")
query, needs = parse("OpenOven bake schedule planner | needs: OpenOven, bake schedule, bakery")
check(query == "OpenOven bake schedule planner" and needs == ("OpenOven", "bake schedule", "bakery"),
      f"the query and its needed words: {query!r} {needs}")
check(parse("selling to bakeries") == ("selling to bakeries", ()),
      "CONTROL: a plain line is a query that needs nothing")
lines = [l.strip() for l in ww.TOPICS_PATH.read_text(encoding="utf-8").splitlines()
         if l.strip() and not l.strip().startswith("#")]
oven_query = [parse(l) for l in lines if l.lower().startswith("openoven")]
check(len(oven_query) == 1 and oven_query[0][0] != "OpenOven" and oven_query[0][1],
      f"topics.txt: the OpenOven query names the thing and says what it needs: {oven_query}")
# The count is pinned, so a query lost or doubled by an edit to the file above shows here.
check(len(lines) == 6, f"and topics.txt holds 6 queries ({len(lines)})")

# ==========================================================================================
print("\n=== B. the week's OpenOven results ===")
ALL = json.loads((FIXTURES / "topics-week.json").read_text(encoding="ascii"))["results"]
WEEK = [r for r in ALL if r["day"] <= "2026-09-25"]
needs = oven_query[0][1] if oven_query else ()
junk = ["Open Oven (I Love Her) -Marlo Venn LYRICS", "Fern Ashby- Open Oven (Lyric Video)",
        "Marlo Venn - Open Oven (I Love Her) [Lyrics]", "A Cartoon Movie - On the Open Oven",
        "OPENOVEN4camp",
        "Song of the Open Oven - a Reading (Powerful Life Poetry)"]
# 2026-09-26 night: every filter matches in any case now, because matching the tool's name as
# written dropped a real video, "Installation of Openoven". The cost, said here rather than
# hidden: the camping brand spelled Openoven and OPENOVEN now carries the needed word, so these
# two pass the needs check. Both came from the old one-word query of 21 and 24 September.
brand = ["Openoven 22-26 Camping Stove Cover/Topper",
         "OPENOVEN 270 degree pizza stone REVIEW for Overlanding"]
real = ["Using the OpenOven GUI to plan your bakery's bake schedule",
        "OpenOven: The journey so far and roadmap (maintainers' talk)"]
by_title = {r["title"]: r for r in WEEK if r["topic"] == "OpenOven"}
for title in junk:
    r = by_title.get(title, {"title": title, "channel": ""})
    why = problem({"title": r["title"], "channel": r.get("channel") or "", "description": ""},
                  needs)
    check(bool(why), f"dropped: {title[:58]} ({why})")
playlist = next((r for r in WEEK if r["topic"] == "OpenOven" and "Playlist" in r["title"]), None)
check(playlist is not None and problem({"title": playlist["title"],
                                        "channel": playlist.get("channel") or "",
                                        "description": ""}, needs),
      "dropped: the indie playlist for a long drive")
for title in real:
    why = problem({"title": title, "channel": "", "description": ""}, needs)
    check(why is None, f"kept: {title} ({why})")
for title in brand:
    why = problem({"title": title, "channel": "", "description": ""}, needs)
    check(why is None, f"the known cost, passed by the needs check now: {title[:50]} ({why})")

# ==========================================================================================
print("\n=== B2. 26 September: the real video the case-sensitive match dropped ===")
WEEK26 = ALL
install = next((r for r in WEEK26 if r["title"] == "Installation of Openoven"), None)
check(install is not None and install["stage"] == "filtered" and install["day"] == "2026-09-26",
      "the fixture holds it, as the filter's own drop of 26 September")
if install:
    why = problem({"title": install["title"], "channel": install.get("channel") or "",
                   "description": ""}, needs)
    check(why is None, f"kept now: Installation of Openoven, {install.get('channel')} ({why})")
for spelling in ("OpenOVEN", "OpenOven", "openoven", "OPENOVEN"):
    check(problem({"title": f"Installation of {spelling}", "channel": "", "description": ""},
                  needs) is None, f"'{spelling}' satisfies the need for OpenOven")

# ==========================================================================================
print("\n=== C. no real video of the week is lost ===")
# Every result of the week that was read, kept, or is still waiting as a real video, judged
# with the needs of its own query. Only the OpenOven query needs anything, so for the others
# this is the title and channel patterns alone.
lost = []
for r in WEEK:
    if r["topic"] == "OpenOven" and r["title"] not in real:
        continue
    q_needs = needs if r["topic"] == "OpenOven" else ()
    if (r["read"] or r["kept"] or r["now"] == "WAITING") and problem(
            {"title": r["title"], "channel": r.get("channel") or "", "description": ""}, q_needs):
        lost.append(r["title"])
check(not lost, f"none of the week's read, kept or waiting videos is dropped: {lost[:3]}")
check(sum(1 for r in WEEK if r["read"]) >= 9,
      "CONTROL: the fixture really holds the week's read videos, so that check had something "
      "to lose")
# 2026-09-26 night, the whole week, 21 to 26 September, each result judged with the needs of its
# own query as topics.txt holds it now (21 and 24 September searched the one word "OpenOven").
_needs_now = dict(parse(l) for l in lines)
_renamed = {"OpenOven": oven_query[0][0] if oven_query else "OpenOven"}
lost26 = [r["title"] for r in WEEK26
          if (r["read"] or r["kept"] or (r["now"] == "WAITING" and r["id"]
                                          and not str(r["id"]).startswith("title:")))
          and problem({"title": r["title"], "channel": r.get("channel") or "", "description": ""},
                      _needs_now.get(_renamed.get(r["topic"], r["topic"]), ()))]
check(not lost26, f"21 to 26 September: none of the read, kept or waiting videos is dropped "
                  f"({len(WEEK26)} results; {lost26[:3]})")
check(len(WEEK26) > len(WEEK) and sum(1 for r in WEEK26 if r["day"] == "2026-09-26") >= 5,
      f"CONTROL: the fixture holds 26 September as well ({len(WEEK26)} results)")

# ==========================================================================================
print("\n=== D. controls: business titles that only look like songs stay ===")
for title in ("How I grew a Spotify playlist to 10,000 followers and sold it",
              "The poem generator that made $10k in a month",
              "Lyrical writing for landing pages that convert"):
    check(problem({"title": title, "channel": "Some Founder", "description": ""}, ()) is None,
          f"kept: {title}")
check(problem({"title": "Official Video", "channel": "Some Band - Topic", "description": ""}, ())
      is not None, "and a YouTube music channel is dropped by its name")
check(problem({"title": "Sourdough starter basics", "channel": "", "description": ""},
              ("OpenOven",)) is not None,
      "a query that needs its proper noun drops a result without it")
check(problem({"title": "Planning a week of bakes with an open tool", "channel": "",
               "description": "a walk through OpenOven"}, ("OpenOven",)) is None,
      "and keeps one whose search snippet has it")
# 2026-09-26 night: this assertion encoded the case-sensitive match, which the brief reverses:
# "a needed word with a capital in it is matched as written". Now any case satisfies the need,
# and a result without the word at all is still dropped (above).
check(problem({"title": "Openoven camping stove cover", "channel": "", "description": ""},
              ("OpenOven",)) is None,
      "a needed word matches in any case: Openoven satisfies OpenOven")
check(problem({"title": "Camping stove cover for overlanding", "channel": "", "description": ""},
              ("OpenOven",)) is not None,
      "CONTROL: and a result that has the word in no case at all is still dropped")

# ==========================================================================================
print("\n=== D2. every filter on the way to a triage ignores case ===")
for title, channel in (("OPEN OVEN - LYRIC VIDEO", "x"), ("open oven (official audio)", "x"),
                       ("Road trip PLAYLIST FOR driving", "x"), ("A Song And POETRY", "x"),
                       ("A song", "Some Band - TOPIC"), ("A song", "SOMEBANDVEVO")):
    check(problem({"title": title, "channel": channel, "description": ""}, ()) is not None,
          f"junk in capitals is still junk: {title} / {channel}")
check(all(p.flags & __import__("re").IGNORECASE for _w, p, _f in ww.TOPIC_JUNK),
      "every junk pattern is compiled to ignore case")
check(ww.title_obviously_on_topic("BAKERY BOOKKEEPING IN 90 MINUTES")
      and ww.title_obviously_on_topic("Pricing For Bakeries, A Walkthrough"),
      "the duration rule's on-topic words match in any case")
check(ww.is_technical_video({"title": "MCP SERVERS FOR A CODING AGENT"}),
      "and so do the words that send a video to Sonnet")

# ==========================================================================================
print("\n=== E. in the topics run: dropped before triage, logged, recorded ===")
listing = [
    {"id": "songsong001", "title": "Marlo Venn - Open Oven (I Love Her) [Lyrics]",
     "url": "https://www.youtube.com/watch?v=songsong001", "duration": 250, "upload_date": None,
     "description": "", "channel": "Lyric Channel", "channel_url": None},
    {"id": "realvid0001", "title": "Using the OpenOven GUI to plan your bakery's bake schedule",
     "url": "https://www.youtube.com/watch?v=realvid0001", "duration": 900, "upload_date": None,
     "description": "", "channel": "The OpenOven Project", "channel_url": None},
]
triaged = []
ww.search_videos = lambda q, count=10: [dict(v) for v in listing]
ww.slot_may_proceed = lambda *a, **k: True
ww.new_budget = lambda: {"remaining": 3}
ww.todays_source_count = lambda source: 0
ww.polite_delay = lambda *a, **k: None
ww.note_topic_searched = lambda *a, **k: None
ww.write_digest = lambda *a, **k: None
# The cursor starts at the OpenOven search, the third line of topics.txt.
ww.load_rotation = lambda: {"topics_index": 2}


def spy(videos, *a, **k):
    triaged.extend(v["id"] for v in videos)
    return [], [], [], [], False


ww.process_candidates = spy
captured = io.StringIO()
real_stdout = sys.stdout
sys.stdout = captured
try:
    ww.run_topics("context", "model", True)
finally:
    sys.stdout = real_stdout
log = captured.getvalue()
check("songsong001" not in triaged and "realvid0001" in triaged,
      f"the song never reaches the triage and the real video does ({triaged})")
check("(topic filter: dropped 'Marlo Venn - Open Oven (I Love Her) [Lyrics]'" in log,
      "and the drop is logged with the title")
record = ww.load_records().get("songsong001") or {}
verdict, text, tags = ww.final_verdict(record) if record else (None, "", frozenset())
check(record.get("reason_code") == "off_topic" and "lyrics" in text,
      f"and recorded with its reason: {record.get('reason_code')} / {text}")
check("off_topic" in tags and "row" not in tags and ww.count_bucket(tags) is None,
      "it gets no row and joins none of the five counts, like an age-filtered result")
table = "\n".join(ww.day_table_lines(ww.todays_day_records()))
check("Dropped as off-topic before any triage" in table and "Marlo Venn" in table,
      "and the day's table names it on one line under the table")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
