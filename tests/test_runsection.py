"""2026-09-26 night, brief part 1.3. A run section counts only what it wrote, and says where the
rest went.

On 26 September the 11:26 topics run read three videos as YES, found the run's time budget
spent before each summary, and wrote none of them. Its section in the digest still opened "New
videos: 3 - YES 3, OPPORTUNITY 0, MAYBE 0, NO 0" and showed no write-up, because the count came
from the triage's verdicts; the write-ups arrived at 12:00, on retry, in another section.

Replayed here through the real process_candidates and write_digest, with the triage, the fetch
and the model stubbed and the run's clock spent. No YouTube request, no model call; everything
is written inside a fresh folder under the OS temp folder, with the invented example person's
profile. The three videos are invented with the real ones' lengths.
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "runsection")
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


class Spy:
    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **k):
        raise AssertionError(f"the test called {self.name}; it must make no request")


for _name in ("run_yt_dlp", "extract_media_info", "download_audio_from_info", "transcribe_audio",
              "claude_generate", "ollama_generate"):
    setattr(ww, _name, Spy(_name))

THREE = [("MaraVid0011", "Bakery Bookkeeping Full Course 2026 | How Small Shops Actually Close "
          "the Month", "Bakery Owners Weekly", 133.7),
         ("MaraVid0012", "My Newsletter Sponsor Setup (copy my pricing sheet!)",
          "Indie Game Letters", 43.4),
         ("MaraVid0013", "Spreadsheets Are Making Some Bakers Miserable", "Small Shop Talks", 8.9)]
MINUTES = {vid: m for vid, _t, _c, m in THREE}
TITLES = {vid: t for vid, t, _c, _m in THREE}
TALK = ("match the bank export to the ledger then read the report and fix the entry "
        "before the month closes ") * 60


def listing(entries):
    return [{"id": vid, "title": title, "channel": channel,
             "url": f"https://www.youtube.com/watch?v={vid}", "description": "",
             "duration": int(minutes * 60), "upload_date": "20260925"}
            for vid, title, channel, minutes in entries]


def stub_triage(context_text, channel, details, model, sells_something=None):
    if "transcript" in details:
        return "YES", "It teaches a mechanism [copy: send the welcome checklist on day one]", 95
    return "MAYBE", "Description missing prevents identifying any copyable mechanism", 10


def speech(minutes):
    """A transcript at about 150 words a minute, so the no-speech check sees real speech."""
    words = TALK.split()
    need = int(minutes * 150)
    return " ".join((words * (need // len(words) + 1))[:need])


def stub_fetch(url, plan=None):
    vid = ww.video_id_from_url(url)
    return {"id": vid, "title": TITLES.get(vid, vid), "uploader": "U", "url": url,
            "transcript": speech(MINUTES.get(vid, 10)), "raw_path": ww.RAW_DIR / f"{vid}.txt",
            "lang": "en",
            "track_kind": "audio",
            "audio_stats": {"audio_seconds": MINUTES.get(vid, 10) * 60, "model": "stub"}}, None


SPENT = "the run's time budget is spent: 510 s left of 1350 s, under the 600 s floor"
WRITEUP = ("## 1. What it is\nA talk.\n\n## 6. What applies to me\n\nMost applicable: \"match the "
           "bank export twice\" - match each Ledgerleaf bakery's bank export twice before the "
           "month closes. Applies to: Ledgerleaf.\n\n## 7. What contradicts what I believe\n\n"
           "nothing in this video touches the beliefs list\n\n## 8. Try this week\n\nMatch one "
           "Ledgerleaf bakery's bank export twice and compare the two totals.\n\n## 9. Header\n\n"
           "Match one Ledgerleaf bakery's bank export twice and compare the two totals.\n")


def stub_summarise(context_text, meta, model, local_only, verdict, attempt=1):
    return WRITEUP, "claude:sonnet", "YES gets Sonnet", 12.0, False, "YES", []


ww.triage_video = stub_triage
ww.fetch_transcript_by_plan = stub_fetch
ww.summarise_one = stub_summarise
ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}


def run(entries, allow_summaries):
    """One run of the topics mode's work: process_candidates, then write_digest. Returns the
    run section it appended to today's digest."""
    starts = iter(allow_summaries)
    ww.may_start_a_summary = lambda: ((True, "") if next(starts, False) else (False, SPENT))
    if hasattr(ww, "start_this_run"):
        ww.start_this_run()
    path = ww.OUTPUT_DIR / f"DIGEST-{ww.today_key()}.md"
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    rows, no, gave_up, summaries, _rl = ww.process_candidates(
        listing(entries), "CONTEXT", "model", False, {}, budget={"remaining": 3}, source="topics")
    ww.write_digest(rows, no, summaries, gave_up=gave_up, label="topics")
    after = path.read_text(encoding="utf-8")
    check(after.startswith(before[:40]) or not before, "CONTROL: the digest is rewritten, not replaced")
    sections = re.split(r"(?m)^## Run at ", after)
    return "## Run at " + sections[-1], after


# ==========================================================================================
print("=== A. the 11:26 run of 26 September: three read as YES, none written ===")
section, digest = run(THREE, [False, False, False])
first = section.splitlines()[2] if len(section.splitlines()) > 2 else ""
print("\n    the section as written:\n" + "\n".join("    | " + l for l in section.splitlines()[:6]))
check("YES 3" not in section, f"the section no longer says YES 3 ({first})")
check(re.search(r"^Written in this run: 0\b", section, re.M) is not None,
      f"it says it wrote nothing: {first}")
check(all(TITLES[vid][:40] in section for vid, *_ in THREE),
      "and names each of the three, where it went")
check("retry queue" in section and "Written on retry" in section and "saved" in section,
      "saying they wait in the retry queue with their transcripts saved, to be written later "
      "under Written on retry")
check("time budget" in section, "and why they were not written here")
counts = ww.day_count_line(ww.todays_day_records())
check(counts.startswith("0 kept, 0 read and skipped, 3 read but not written"),
      f"CONTROL: the day's counts are the records', unchanged by this: {counts}")

# ==========================================================================================
print("\n=== B. a run that writes one and leaves one ===")
TWO = [("aaaaaaaaaa1", "The one that gets written", "C1", 12.0),
       ("bbbbbbbbbb2", "The one the clock stops", "C2", 12.0)]
TITLES.update({vid: t for vid, t, _c, _m in TWO})
MINUTES.update({vid: m for vid, _t, _c, m in TWO})
section, digest = run(TWO, [True, False])
check(re.search(r"^Written in this run: 1 - YES 1, OPPORTUNITY 0, MAYBE 0, NO 0\.", section, re.M)
      is not None, f"one written, and its verdict counted: {section.splitlines()[2]}")
check("The one the clock stops" in section and "retry queue" in section,
      "the other has its own line, with where it went")
check(section.count("#### [YES] The one that gets written") == 1,
      "and the write-up that was written is in the section once")

# ==========================================================================================
print("\n=== C. controls ===")
BOTH = [("cccccccccc3", "First of two written", "C3", 12.0),
        ("dddddddddd4", "Second of two written", "C4", 12.0)]
TITLES.update({vid: t for vid, t, _c, _m in BOTH})
MINUTES.update({vid: m for vid, _t, _c, m in BOTH})
section, digest = run(BOTH, [True, True])
check(re.search(r"^Written in this run: 2 - YES 2, OPPORTUNITY 0, MAYBE 0, NO 0\.", section, re.M)
      is not None, f"a run that writes both says 2: {section.splitlines()[2]}")
check("Read, not written" not in section, "and has no line for videos it did not write")
check(digest.count("## Run at ") == 3, "three runs, three sections, none replaced")
counts = ww.day_count_line(ww.todays_day_records())
check(counts.startswith("3 kept, 0 read and skipped, 4 read but not written"),
      f"the day's counts: {counts}")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
