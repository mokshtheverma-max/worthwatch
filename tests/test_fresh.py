"""2026-09-30, brief part 3: fresh inputs.

On 30 September the age rule dropped 29 videos before any triage, from searches that returned
years-old results. Measured that night with a dry run: a plain relevance search returned 7 of 10
results two or more years old, and the same search with YouTube's own "this year" filter
returned none.

Checked here: every search asks for this year's videos, with a count, and is logged as a search;
the plain ytsearch comes back when the filter is switched off; a topics.txt with two searches
retired and new ones added reads as the run reads it; a channels.txt with channels added holds
each once, in order; and a search's needed words keep the on-topic titles of a dry run and drop
the ones that only share a word with it. The owner's own topics.txt, channels.txt and dry-run
titles used to be read here; this suite's own invented ones stand in, written into the sandbox
over the example person's. No request: the yt-dlp runner is a stub.
"""
import io
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "fresh")
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


# --- this suite's own invented config, written over the example person's ------------------
PRIORITY = "https://www.youtube.com/@example-founders"
ww.FAVOURITE_CHANNELS = (PRIORITY,)
RETIRED = ("selling to bakeries by cold email", "how a cafe entered a market it had never sold to")
BEFORE = ("bakery bookkeeping software", "small bakery pricing", "indie game newsletter sponsorship",
          "newsletter welcome email examples")
NEW = ("small bakery cash flow | needs: bakery, bakeries, cafe, patisserie, bake shop",
       "bank export to spreadsheet", "newsletter second sponsor slot",
       "indie game devlog newsletter growth", "bookkeeping for food trucks")
IDEA_SEARCHES = ("founder case study with revenue numbers", "how I built my business revenue",
                 "bootstrapped SaaS founder interview", "small business teardown")
ww.TOPICS_PATH.write_text(
    "# Invented searches for tests/test_fresh.py.\n"
    "# Retired on 30 September, after 14 days that kept nothing:\n"
    + "".join(f"# {q}\n" for q in RETIRED)
    + "".join(f"{q}\n" for q in BEFORE)
    + "\n# Added on 30 September:\n" + "".join(f"{q}\n" for q in NEW)
    + "# Added on 8 October, for ideas:\n" + "".join(f"{q}\n" for q in IDEA_SEARCHES),
    encoding="utf-8")
CHANNELS_BEFORE = (PRIORITY, "https://www.youtube.com/@bakery-numbers",
                   "https://www.youtube.com/@indie-game-letters",
                   "https://www.youtube.com/@small-saas-diary")
ADDED = ("@ledger-basics", "@newsletter-craft", "@solo-founder-notes")
OCT8 = ("@pricing-experiments", "channel/UCexampleexampleexample0", "@first-customers")
OCT8_URLS = [f"https://www.youtube.com/{tail}" for tail in OCT8]
ww.CHANNELS_PATH.write_text(
    "# Invented channels for tests/test_fresh.py.\n" + "".join(f"{u}\n" for u in CHANNELS_BEFORE)
    + "# added 30 September\n" + "".join(f"https://www.youtube.com/{h}\n" for h in ADDED)
    + "\n# added 8 October\n" + "".join(f"{u}\n" for u in OCT8_URLS), encoding="utf-8")

LAUNCHED = []


def fake_run(cmd, timeout=120):
    LAUNCHED.append(list(cmd))
    return types.SimpleNamespace(stdout="", stderr="", returncode=0)


print("=== A. every search asks for this year's videos ===")
real_run = ww.run_yt_dlp
ww.run_yt_dlp = fake_run
ww.search_videos("bakery bookkeeping", count=10)
args = LAUNCHED[-1]
target = args[-1]
check(target.startswith("https://www.youtube.com/results?search_query=bakery+bookkeeping")
      and target.endswith("&sp=EgQIBRAB"),
      f"the search page, with the this-year and video-only filter ({target})")
check("--playlist-end" in args and args[args.index("--playlist-end") + 1] == "10",
      "with the count, which the search page does not carry itself")
check("--flat-playlist" in args and "youtubetab:approximate_date" in args,
      "and still one flat listing with approximate dates, as the age rule needs")
check(ww.request_kind_of(args)[:2] == ("search", "youtube.com/results"),
      f"logged as a search, not as a channel listing ({ww.request_kind_of(args)[:2]})")
saved = ww.TOPIC_SEARCH_FILTER
ww.TOPIC_SEARCH_FILTER = None
ww.search_videos("bakery bookkeeping", count=10)
ww.TOPIC_SEARCH_FILTER = saved
check(LAUNCHED[-1][-1] == "ytsearch10:bakery bookkeeping" and "--playlist-end" not in LAUNCHED[-1],
      "CONTROL: with the filter off it is the plain ytsearch of before")
check(ww.request_kind_of(LAUNCHED[-1])[0] == "search", "and that is logged as a search too")
ww.run_yt_dlp = real_run

print("\n=== B. topics.txt: two retired, five added ===")
# Read exactly as run_topics reads it: comment and blank lines skipped, each line parsed.
entries = [ww.parse_topic_line(l) for l in ww.TOPICS_PATH.read_text(encoding="utf-8").splitlines()
           if l.strip() and not l.strip().startswith("#")]
queries = [q for q, _n in entries]
needs = dict(entries)
for gone in RETIRED:
    check(gone not in queries, f"retired: {gone!r}")
for line in NEW:
    q = ww.parse_topic_line(line)[0]
    check(q in queries, f"added: {q!r}")
check(len(queries) == len(set(queries)) == 13, f"13 searches, none twice ({len(queries)})")

print("\n=== C. the cash-flow search keeps bakeries and drops cash flow for anyone else ===")
# Ten invented titles and channels of the kind its dry run returned, with the characters real
# titles carry (an emoji, a curly apostrophe, "|" and "&"); a title's em-dash is built from its
# code point, because this repository's files may not hold one.
EM = chr(0x2014)
DRY = (
    ("How one bakery fixed its cash flow in a month (spreadsheet, bank export, receipts)",
     "Small Shop Money", True),
    ("Cash flow for bakeries: a 3-column sheet that works (flour, wages & rent)",
     "Small Shop Money", True),
    ("Patisserie owner shows her weekly cash count | numbers, mistakes & what changed |",
     "Crumb and Ledger", True),
    ("The cash flow mistake that nearly closed my cafe (Part 1)",
     "Corner Cafe Diaries", True),
    ("Why Do Bakeries Run Out of Cash? \U0001F92F | Small Business Basics | Ep.4",
     "Main Street Basics | Money for Small Shops ", True),
    (f"Cash Flow Forecasting {EM} Build It, Then Check It Against the Bank", "Spreadsheet Hour",
     False),
    ("See how a freight company tracks cash flow across 40 trucks with one dashboard’s view",
     "Fleet Finance Co", False),
    ("See how a bake shop chain automates its end-of-day cash count and deposit slips",
     "Counter Systems", True),
    ("The cash flow mistake that nearly closed my cafe (Part 2)",
     "Corner Cafe Diaries", True),
    (f"Your Cash Flow Is Only As Good As Your Invoices {EM} A Talk for Agencies | 2026",
     "Agency Money Talks", False),
)
words = needs["small bakery cash flow"]
check(words == ("bakery", "bakeries", "cafe", "patisserie", "bake shop"),
      f"the search says what a result needs ({words})")
for title, channel, keep in DRY:
    why = ww.topic_result_problem({"title": title, "channel": channel, "description": ""}, words)
    check((why is None) == keep, f"{'kept' if keep else 'dropped'}: {title[:60]} ({why})")

print("\n=== D. channels.txt: three added, each once ===")
urls = ww.read_channel_urls()
for handle in ADDED:
    check(f"https://www.youtube.com/{handle}" in urls, f"added: {handle}")
# 8 October: and three more after them, named in section E.
check(len(urls) == len(set(urls)) == 10, f"10 channels, none twice ({len(urls)})")
check(urls[:4] == [u for u in urls if u.rsplit("/", 1)[-1] not in ADDED and u not in OCT8_URLS],
      "CONTROL: the four from before are all still there, in their order")

print("\n=== E. 8 October: idea-rich searches, three channels like the priority one, the pass ===")
for q in IDEA_SEARCHES:
    check(q in queries, f"added: {q!r}")
check(queries[-len(IDEA_SEARCHES):] == list(IDEA_SEARCHES)
      and all(ww.parse_topic_line(l)[0] in queries for l in NEW),
      "after the nine searches of before, every one of which is still there")
for url in OCT8_URLS:
    check(url in urls, f"added: {url.rsplit('youtube.com/', 1)[-1]}")
check(urls[-len(OCT8_URLS):] == OCT8_URLS, "after the seven channels of before")
check(urls[0] == PRIORITY and ww.favourite_channels(urls) == [PRIORITY],
      "the priority channel stays first, and the one favourite")
topics = " ".join(ww.DISCOVER_TOPICS).lower()
check("founder" in topics and "revenue" in topics and len(ww.DISCOVER_TOPICS) == 3,
      f"the discovery pass searches for founder interviews with numbers ({ww.DISCOVER_TOPICS})")
check("founder interviews" in ww.FIT_INSTRUCTIONS and "real numbers" in ww.FIT_INSTRUCTIONS,
      "and its fit question says that is the kind of channel wanted")
asked = []
ww.polite_delay = lambda *a, **k: None
ww.slot_may_proceed = lambda mode, skip=False: True
ww.search_videos = lambda q, count=8: [
    {"channel_url": PRIORITY},
    {"channel_url": "https://www.youtube.com/@newfounderchannel"}]
ww.get_channel_info = lambda url: (asked.append(url),
                                   {"name": "N", "followers": 10, "description": ""})[1]
ww.list_channel_videos = lambda url, max_items=5: []
ww.judge_channel_fit = lambda c, info, recent, model: "SELLS: nothing | FIT: high - founders"
ww.run_discover("CTX", "model", skip_canary=True)
check("https://www.youtube.com/@newfounderchannel" in asked,
      "the pass still checks a channel it has not got")
check(not any(u in asked for u in urls),
      f"and spends no request on a channel already in channels.txt ({[u for u in asked if u in urls]})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
