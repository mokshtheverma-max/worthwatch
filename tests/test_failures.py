"""2026-09-26 night, brief part 2: one section per way a run can break.

Each section simulates one failure inside a run of the real pipeline, with the outside world
(YouTube, the transcriber, Ollama, the claude CLI) stubbed, and proves three things: the run
carries on (it does not raise, the other videos are handled, and a video it cannot handle is
left waiting with the reason), the digest says what happened in plain words (checked on the
rendered text), and nothing is lost or counted twice (no video marked done without a record a
later run will finish, no second history row, the day's counts add up), with a retry queued
where one makes sense.

Sections are numbered as the brief lists the failures and were added one fix at a time, each
run red before its change. No YouTube request, no model call: a spy raises on any of them.
Everything is written inside one fresh folder under the OS temp folder.
"""
import builtins
import io
import json
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "failures")
# The topics run of section 1E reads topics.txt; this suite writes its own, so nothing it
# checks moves when the example person's searches change.
ww.TOPICS_PATH.write_text("bakery bookkeeping software\nindie game newsletter sponsorship\n",
                          encoding="utf-8")
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


class Spy:
    """Anything that would leave this machine or wake a model fails the test."""

    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **k):
        raise AssertionError(f"the test called {self.name}; it must make no request")


REAL = {_name: getattr(ww, _name) for _name in ("transcribe_audio", "fetch_transcript_via_audio")}
for _name in ("run_yt_dlp", "extract_media_info", "download_audio_from_info", "transcribe_audio",
              "claude_generate", "fetch_channel_feed", "load_whisper"):
    setattr(ww, _name, Spy(_name))
ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}
ww.polite_delay = lambda *a, **k: None


def fresh():
    """An empty sandbox for the next section: no records, no seen, no history, no digest."""
    for path in (ww.RECORDS_PATH, ww.SEEN_PATH, ww.HISTORY_PATH, ww.RATE_STATE_PATH,
                 ww.ROTATION_PATH, ww.LAST_RUN_PATH, ww.BACKLOG_PATH):
        Path(path).unlink(missing_ok=True)
    if ww.OUTPUT_DIR.exists():
        for f in ww.OUTPUT_DIR.iterdir():
            if f.is_file():
                f.unlink()
    if hasattr(ww, "start_this_run"):
        ww.start_this_run()


def digest_text(day=None):
    path = ww.OUTPUT_DIR / f"DIGEST-{day or ww.today_key()}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def history_rows():
    if not Path(ww.HISTORY_PATH).exists():
        return []
    return [json.loads(l) for l in Path(ww.HISTORY_PATH).read_text(encoding="utf-8").splitlines()
            if l.strip()]


# ==========================================================================================
print("=== 10. the power goes off while a file is being written ===")


class PowerLost(BaseException):
    """The process dies: nothing after it runs, no handler catches it."""


class HalfWriter:
    """A file that takes half of what it is given, gets it to the disk, and then the power goes."""

    def __init__(self, f):
        self.f = f

    def write(self, text):
        self.f.write(text[: len(text) // 2])
        self.f.flush()
        self.f.close()
        raise PowerLost("the power went off half-way through the write")

    def __getattr__(self, name):
        return getattr(self.f, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self.f.close()
        except Exception:  # noqa: BLE001 - already closed by the power cut
            pass
        return False


def power_cut_while_writing(match, action):
    """Run action() with every write of a file whose name matches dying half-way. Returns
    whether the power cut happened."""
    real_open, real_io_open = builtins.open, io.open

    def dying_open(file, mode="r", *a, **k):
        f = real_open(file, mode, *a, **k)
        if ("w" in mode or "x" in mode) and match(Path(str(file)).name):
            return HalfWriter(f)
        return f

    builtins.open = dying_open
    io.open = dying_open
    try:
        action()
    except PowerLost:
        return True
    finally:
        builtins.open, io.open = real_open, real_io_open
    return False


fresh()
DAY = ww.today_key()
ww.update_record("aaaaaaaaaa1", url="https://www.youtube.com/watch?v=aaaaaaaaaa1",
                 title="The first video of the day", channel="C", summary_state="WAITING",
                 reason_code="rolled", state_reason="rolled to the next run; nothing spent on it")
ww.refresh_digest(DAY, create=True)
before = digest_text(DAY)
check("The first video of the day" in before, "a digest for the day exists before the power cut")
ww.update_record("bbbbbbbbbb2", url="https://www.youtube.com/watch?v=bbbbbbbbbb2",
                 title="The second video of the day", channel="C", summary_state="WAITING",
                 reason_code="rolled", state_reason="rolled to the next run; nothing spent on it")
cut = power_cut_while_writing(lambda name: "DIGEST-" in name,
                              lambda: ww.refresh_digest(DAY))
after_cut = digest_text(DAY)
check(cut, "the power cut happened in the middle of writing the digest")
check(after_cut == before,
      f"the digest on disk is the whole old one, not half of the new one "
      f"({len(after_cut)} characters, {len(before)} before)")
ww.refresh_digest(DAY)
healed = digest_text(DAY)
check("The first video of the day" in healed and "The second video of the day" in healed
      and healed.count("<!-- TABLE-END -->") == 1,
      "the next write carries on: both videos, one table, nothing counted twice")
check(not [p.name for p in ww.OUTPUT_DIR.iterdir() if p.name.endswith(".tmp")],
      "and the half-written temporary file the power cut left is gone")

print("\n  records.json torn in the middle")
fresh()
ww.update_record("cccccccccc3", url="https://www.youtube.com/watch?v=cccccccccc3",
                 title="A record that must not be lost", summary_state="WAITING",
                 reason_code="rolled", state_reason="rolled")
good = Path(ww.RECORDS_PATH).read_bytes()
torn = good[: len(good) // 2]
Path(ww.RECORDS_PATH).write_bytes(torn)
damaged = getattr(ww, "StateFileDamaged", None)
raised = None
try:
    ww.update_record("dddddddddd4", title="A later update", summary_state="WAITING",
                     reason_code="rolled", state_reason="rolled")
except BaseException as e:  # noqa: BLE001 - the class is what is being checked
    raised = e
check(damaged is not None and isinstance(raised, damaged),
      f"a torn records.json stops the write with its own error, not an empty store "
      f"({raised.__class__.__name__ if raised else 'nothing raised'})")
check(Path(ww.RECORDS_PATH).read_bytes() == torn,
      "and the torn file is left exactly as it was, for a person to mend, never overwritten")
check(raised is not None and "records.json" in str(raised),
      f"the error names the file: {str(raised)[:120]}")
Path(ww.RECORDS_PATH).write_bytes(good)

print("\n  seen.json and rate-state.json torn in the middle")
for path, loader in ((ww.SEEN_PATH, "load_seen"), (ww.RATE_STATE_PATH, "load_rate_state")):
    Path(path).write_text('{"abc": {"verdict": "Y', encoding="utf-8")
    raised = None
    try:
        getattr(ww, loader)()
    except BaseException as e:  # noqa: BLE001
        raised = e
    check(damaged is not None and isinstance(raised, damaged),
          f"{Path(path).name}: a torn file is refused by name "
          f"({raised.__class__.__name__ if raised else 'read back as empty'})")
    Path(path).unlink()

print("\n  every state file and every page is written whole or not at all")
written = []
real_atomic = getattr(ww, "atomic_write_text", None)
if real_atomic:
    ww.atomic_write_text = lambda path, text, **k: (written.append(Path(path).name),
                                                    real_atomic(path, text, **k))[1]
fresh()
ww.save_records({})
ww.save_seen({})
ww.save_rate_state({})
ww.save_rotation({})
ww.save_backlog({})
ww.write_last_run("channels", new_videos=0)
ww.update_record("eeeeeeeeee5", url="https://www.youtube.com/watch?v=eeeeeeeeee5", title="E",
                 summary_state="WAITING", reason_code="rolled", state_reason="rolled")
ww.refresh_digest(DAY, create=True)
ww.save_transcript({"id": "eeeeeeeeee5", "title": "E", "url": "u", "transcript": "words " * 50,
                    "audio_stats": {"audio_seconds": 60.0}}, source="topics")
ww.save_writeup({"id": "eeeeeeeeee5", "title": "E", "url": "u", "uploader": "U"},
                        "## 1. What it is\nA talk.\n", "claude:sonnet", 1.0, False)
if real_atomic:
    ww.atomic_write_text = real_atomic
for name in ("records.json", "seen.json", "rate-state.json", "rotation.json", "backlog.json",
             "last-run.json", f"DIGEST-{DAY}.md", "eeeeeeeeee5.txt", "eeeeeeeeee5.json"):
    check(name in written, f"{name} is written through atomic_write_text")
check(any(n.endswith("-e.md") for n in written), "and so is the write-up's own file")

# ==========================================================================================
# The shared pieces of a run for the sections below: a listing, a fetch that hands back a
# transcript of real speech, a triage and a write-up, each a stub a section can swap.
import urllib.error

REAL_TRIAGE = ww.triage_video     # the real one, which asks Ollama through ollama_generate
TALK = ("read the bank export then match each receipt twice and compare the two totals before "
        "the month closes ")


def speech(minutes):
    words = TALK.split()
    need = int(minutes * 150)
    return " ".join((words * (need // len(words) + 1))[:need])


def listing(*entries):
    return [{"id": vid, "title": title, "channel": "C",
             "url": f"https://www.youtube.com/watch?v={vid}", "description": "",
             "duration": 600, "upload_date": "20260925"} for vid, title in entries]


def good_fetch(url, plan=None):
    vid = ww.video_id_from_url(url)
    return {"id": vid, "title": f"title of {vid}", "uploader": "U", "url": url,
            "transcript": speech(10), "raw_path": ww.RAW_DIR / f"{vid}.txt", "lang": "en",
            "track_kind": "audio", "audio_stats": {"audio_seconds": 600.0, "model": "stub"}}, None


def good_triage(context_text, channel, details, model, sells_something=None):
    if "transcript" in details:
        return "YES", "It teaches a mechanism [copy: match each receipt twice]", 95
    return "MAYBE", "Description missing", 10


WRITEUP = ("## 1. What it is\nA talk.\n\n## 6. What applies to me\n\nMost applicable: \"match "
           "each receipt twice\" - match each Ledgerleaf bakery's receipts twice before the month "
           "closes. Applies to: Ledgerleaf.\n\n## 7. What contradicts what I believe\n\nnothing in "
           "this video touches the beliefs list\n\n## 8. Try this week\n\nMatch one Ledgerleaf "
           "bakery's receipts twice and compare the two totals.\n\n## 9. Header\n\nMatch one "
           "Ledgerleaf bakery's receipts twice and compare the two totals.\n")
WRITTEN = []


def good_summarise(context_text, meta, model, local_only, verdict, attempt=1):
    WRITTEN.append(meta["id"])
    return WRITEUP, "claude:sonnet", "YES gets Sonnet", 12.0, False, "YES", []


def standard_run():
    ww.fetch_transcript_by_plan = good_fetch
    ww.triage_video = good_triage
    ww.summarise_one = good_summarise
    ww.may_start_a_summary = lambda: (True, "")
    ww.local_most_useful = lambda *a, **k: None


def run_once(candidates, seen=None, label="topics", budget=3):
    """process_candidates, then write_digest, as run_topics does. Returns (seen, raised)."""
    seen = {} if seen is None else seen
    raised = None
    try:
        rows, no, gave_up, summaries, _rl = ww.process_candidates(
            candidates, "CONTEXT", "model", False, seen, budget={"remaining": budget},
            source="topics")
        ww.write_digest(rows, no, summaries, gave_up=gave_up, label=label)
    except Exception as e:  # noqa: BLE001 - an exception escaping here is the defect
        raised = e
    return seen, raised


def row_of(vid):
    record = ww.load_records().get(vid) or {}
    if record.get("reason_code") not in ww.REASON_CODES:
        return None, "", frozenset(), record
    return (*ww.final_verdict(record), record)


def writeups_in_history(vid):
    return sum(1 for r in history_rows() if vid in (r.get("url") or "") and r.get("summary"))


# ==========================================================================================
print("\n=== 1. Ollama down or slow ===")
calls = []
real_urlopen = ww.urllib.request.urlopen


def refused(req, timeout=None, *a, **k):
    calls.append(timeout)
    raise urllib.error.URLError(ConnectionRefusedError(10061, "No connection could be made "
                                                              "because the target machine "
                                                              "actively refused it"))


def too_slow(req, timeout=None, *a, **k):
    calls.append(timeout)
    raise TimeoutError("timed out")


print("  A. down while the listing is triaged: three videos, the real triage and ollama_generate")
fresh()
standard_run()
ww.triage_video = REAL_TRIAGE
ww.urllib.request.urlopen = refused
seen, raised = run_once(listing(("ollamaaaaa1", "First"), ("ollamaaaaa2", "Second"),
                                ("ollamaaaaa3", "Third")))
ww.urllib.request.urlopen = real_urlopen
check(raised is None, f"the run does not crash when Ollama refuses the connection ({raised!r})")
check(len(calls) == 1, f"after the first refusal it stops asking, so the run does not pay for "
                       f"it again ({len(calls)} calls)")
states = [row_of(v)[3].get("summary_state") for v in ("ollamaaaaa1", "ollamaaaaa2", "ollamaaaaa3")]
check(states == ["WAITING"] * 3, f"each of the three is on the books as waiting ({states})")
check(all(seen.get(v, {}).get("verdict") == "pending" and seen[v].get("needs_triage")
          for v in ("ollamaaaaa1", "ollamaaaaa2", "ollamaaaaa3")),
      "and queued in seen.json to be triaged later, so none is lost when no listing returns it")
page = digest_text()
check("Ollama" in page and "not triaged" in page,
      "the digest says they were not triaged, and that Ollama did not answer")
check(re.search(r"What went wrong in this run:.*Ollama", page) is not None,
      "and the run section names the outage")

print("  B. back again: the next run triages them from the queue and reads them")
calls.clear()
standard_run()
due = ww.pending_due(seen)
check({d["id"] for d in due} >= {"ollamaaaaa1", "ollamaaaaa2", "ollamaaaaa3"},
      "the pending queue hands all three back")
ww.start_this_run()
seen, raised = run_once(due, seen=seen)
check(raised is None and all(row_of(v)[3].get("summary_state") == "WRITTEN"
                             for v in ("ollamaaaaa1", "ollamaaaaa2", "ollamaaaaa3")),
      "all three are read and written once Ollama answers")
check(all(writeups_in_history(v) == 1 for v in ("ollamaaaaa1", "ollamaaaaa2", "ollamaaaaa3")),
      "each has exactly one write-up in history.jsonl, nothing counted twice")

print("  C. slow: a timeout is an outage too, and the wait is held to the run's time left")
fresh()
standard_run()
ww.triage_video = REAL_TRIAGE
ww.urllib.request.urlopen = too_slow
ww.start_run_clock(50)
seen, raised = run_once(listing(("slowslowsl1", "Slow one"), ("slowslowsl2", "Slow two")))
ww._RUN_CLOCK["deadline"] = None
ww.urllib.request.urlopen = real_urlopen
check(raised is None, f"a hung Ollama does not crash the run ({raised!r})")
check(calls and calls[0] <= 50, f"and the triage waits no longer than the run has left "
                                f"({calls[:1]} s against 50 s left)")
check(len(calls) == 1, f"one timeout, and it is not paid again ({len(calls)} calls)")
check(all(seen.get(v, {}).get("needs_triage") for v in ("slowslowsl1", "slowslowsl2")),
      "both are queued to be triaged later")

print("  D. down when the transcript is judged: saved, not written, judged by the next run")
fresh()
standard_run()


def listing_only(context_text, channel, details, model, sells_something=None):
    if "transcript" in details:
        raise ww.OllamaDown("the local model (Ollama) did not answer: refused")
    return "MAYBE", "Description missing", 10


ww.triage_video = listing_only
WRITTEN.clear()
seen, raised = run_once(listing(("transcrip01", "Judged later")))
verdict, text, tags, record = row_of("transcrip01")
check(raised is None and not WRITTEN, "no crash, and no write-up paid for without a verdict")
check(record.get("summary_state") == "READ_NOT_WRITTEN" and record.get("reason_code")
      == "triage_failed" and "Ollama" in text,
      f"the row says it was read and not judged, and why: {verdict} ({text[:110]})")
check(record.get("read_verdict") in (None, ""), "it is not shown as a judged MAYBE")
check((ww.TRANSCRIPTS_DIR / "transcrip01.txt").exists(), "its transcript is saved")
check(any(r["id"] == "transcrip01" for r in ww.retry_queue()), "and it is in the retry queue")
standard_run()
ww.start_this_run()
rows, sums = ww.run_retry_phase("CONTEXT", "model", False)
verdict, text, tags, record = row_of("transcrip01")
check(record.get("summary_state") == "WRITTEN" and record.get("read_verdict") == "YES",
      f"the next run judges it from the saved transcript and writes it up ({verdict}, "
      f"read {record.get('read_verdict')})")
check(writeups_in_history("transcrip01") == 1, "with one write-up in history.jsonl")

print("  E. down before the run starts: nothing listed, no canary spent, and the digest says why")
fresh()
standard_run()
real_running, real_search, real_gate = ww.ollama_is_running, ww.search_videos, ww.slot_may_proceed
ww.ollama_is_running = lambda: False
ww.search_videos = Spy("search_videos")
ww.slot_may_proceed = Spy("slot_may_proceed")
real_budget, real_count = ww.new_budget, ww.todays_source_count
ww.new_budget = lambda: {"remaining": 3}
ww.todays_source_count = lambda source: 0
ww.start_this_run()
raised = None
try:
    try:
        ww.ensure_local_model("model")
    except ww.OllamaDown:
        pass
    ww.run_topics("CONTEXT", "model", False)
except Exception as e:  # noqa: BLE001
    raised = e
ww.ollama_is_running, ww.search_videos, ww.slot_may_proceed = real_running, real_search, real_gate
ww.new_budget, ww.todays_source_count = real_budget, real_count
check(raised is None, f"the topics run returns cleanly ({raised!r})")
page = digest_text()
check("## Run at" in page and "What went wrong in this run: The local model (Ollama)" in page,
      "a run section says the local model did not answer, though the run met no video")
_last = (Path(ww.LAST_RUN_PATH).read_text(encoding="utf-8")
         if Path(ww.LAST_RUN_PATH).exists() else "")
check("Ollama" in _last, "and last-run.json records why the run did nothing")

# ==========================================================================================
import errno
import subprocess

OOM = ("local transcription failed: RuntimeError: CUDA failed with error out of memory")
NO_NETWORK = ("yt-dlp produced no metadata for the media client (exit 1): ERROR: [youtube] "
              "netnetnet01: Unable to download API page: <urlopen error [Errno 11001] "
              "getaddrinfo failed> (caused by TransportError)")


def fetch_failing(fails_for):
    """A fetch that fails, the way the real one does, for the ids in fails_for: {id: what},
    where what is an error string it returns or an exception it raises."""
    def fetch(url, plan=None):
        vid = ww.video_id_from_url(url)
        what = fails_for.get(vid)
        if isinstance(what, BaseException):
            raise what
        if what:
            return None, what
        return good_fetch(url, plan)
    return fetch


def waiting_line(page):
    return next((l for l in page.splitlines() if "another try" in l), "")


print("\n=== 2. the transcriber runs out of GPU memory ===")
fresh()
standard_run()
ww.fetch_transcript_by_plan = fetch_failing({"gpugpugpu01": OOM})
WRITTEN.clear()
seen, raised = run_once(listing(("gpugpugpu01", "Out of memory"), ("gpugpugpu02", "Fine")))
verdict, text, tags, record = row_of("gpugpugpu01")
check(raised is None and "gpugpugpu02" in WRITTEN,
      "the run carries on: the next video is read and written")
check(record.get("summary_state") == "WAITING" and seen.get("gpugpugpu01", {}).get("verdict")
      == "pending", f"the one that failed is not marked done; it waits, queued for another try "
                    f"({record.get('summary_state')}, seen {seen.get('gpugpugpu01', {}).get('verdict')})")
page = digest_text()
check("GPU memory" in waiting_line(page) and "Out of memory" in waiting_line(page),
      f"the digest names it and says the transcriber ran out of GPU memory: "
      f"{waiting_line(page)[:150]}")
check(re.search(r"What went wrong in this run:.*GPU memory", page) is not None,
      "and the run section says it")
check(writeups_in_history("gpugpugpu01") == 0 and writeups_in_history("gpugpugpu02") == 1,
      "nothing is written twice or for the video that failed")
print("  the next run tries it again from the queue, and it is read")
standard_run()
ww.start_this_run()
seen, raised = run_once(ww.pending_due(seen), seen=seen)
check(row_of("gpugpugpu01")[3].get("summary_state") == "WRITTEN"
      and writeups_in_history("gpugpugpu01") == 1, "written once, on the second try")
print("  three failures in a row, and it stops trying")
fresh()
standard_run()
ww.fetch_transcript_by_plan = fetch_failing({"gpugpugpu03": OOM})
seen = {}
for n in range(3):
    ww.start_this_run()
    seen, raised = run_once(listing(("gpugpugpu03", "Never fits")) if n == 0
                            else ww.pending_due(seen), seen=seen)
verdict, text, tags, record = row_of("gpugpugpu03")
check(record.get("summary_state") == "NOT_READ" and seen.get("gpugpugpu03", {}).get("verdict")
      not in (None, "pending") and "3 tries" in text,
      f"after the third failure it is NOT READ, done, and says so: {verdict} ({text[:100]})")
print("  two GPU failures in one run, and the run stops fetching")
fresh()
standard_run()
ww.fetch_transcript_by_plan = fetch_failing({"gpugpugpu04": OOM, "gpugpugpu05": OOM})
fetched = []
_inner = ww.fetch_transcript_by_plan
ww.fetch_transcript_by_plan = lambda url, plan=None: (fetched.append(url), _inner(url, plan))[1]
seen, raised = run_once(listing(("gpugpugpu04", "A"), ("gpugpugpu05", "B"), ("gpugpugpu06", "C")))
check(raised is None and len(fetched) == 2,
      f"after two GPU failures the third is not fetched ({len(fetched)} fetches)")
check(row_of("gpugpugpu06")[3].get("summary_state") == "WAITING"
      and seen.get("gpugpugpu06", {}).get("verdict") == "pending",
      "it waits, queued for the next run, with nothing spent on it")

print("\n=== 5. the network drops in the middle of a run ===")
fresh()
standard_run()
ww.fetch_transcript_by_plan = fetch_failing({
    "netnetnet01": NO_NETWORK,
    "netnetnet02": RuntimeError("yt-dlp timed out after 180s and was killed: --dump-json "
                                "https://www.youtube.com/watch?v=netnetnet02")})
WRITTEN.clear()
seen, raised = run_once(listing(("netnetnet01", "Offline"), ("netnetnet02", "Hung"),
                                ("netnetnet03", "Back")))
check(raised is None and "netnetnet03" in WRITTEN, "the run carries on to the video after")
for vid in ("netnetnet01", "netnetnet02"):
    check(row_of(vid)[3].get("summary_state") == "WAITING"
          and seen.get(vid, {}).get("verdict") == "pending",
          f"{vid} waits, queued for another try, not marked done")
page = digest_text()
check("network" in waiting_line(page), f"the digest says the network failed: "
                                       f"{waiting_line(page)[:150]}")
print("  a channel listing that fails: the channel is not marked as looked at")
fresh()
standard_run()


def fake_yt_dlp(cmd, timeout=120):
    url = cmd[-1]
    if "@offline" in url:
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: [youtube:tab] Unable to download "
                                           "webpage: <urlopen error [Errno 11001] getaddrinfo "
                                           "failed> (caused by TransportError)")
    # Today's date, so the two-day window never ages the fixture out.
    return subprocess.CompletedProcess(cmd, 0, json.dumps(
        {"id": "chanvideo01", "title": "A video", "duration": 600,
         "upload_date": __import__("datetime").date.today().strftime("%Y%m%d"),
         "channel": "Online", "url": "https://www.youtube.com/watch?v=chanvideo01"}) + "\n", "")


met = []
saved = {k: getattr(ww, k) for k in ("run_yt_dlp", "read_channel_urls", "new_budget",
                                     "slot_may_proceed", "enrich_with_feed", "process_candidates")}
ww.run_yt_dlp = fake_yt_dlp
urls = ["https://www.youtube.com/@offline", "https://www.youtube.com/@online"]
ww.read_channel_urls = lambda: urls
ww.new_budget = lambda: {"remaining": 3}
ww.slot_may_proceed = lambda *a, **k: True
ww.enrich_with_feed = lambda *a, **k: None
ww.process_candidates = lambda videos, *a, **k: (met.extend(v["id"] for v in videos),
                                                 ([], [], [], [], False))[1]
ww.CHANNELS_PATH.write_text("\n".join(urls) + "\n", encoding="utf-8")
raised = None
try:
    ww.start_this_run()
    ww.run_channels("CONTEXT", 2, "model", False)
except Exception as e:  # noqa: BLE001
    raised = e
for k, v in saved.items():
    setattr(ww, k, v)
_, done = ww.rotation_state(urls)
check(raised is None and met == ["chanvideo01"],
      f"the run carries on to the next channel ({raised!r}, {met})")
check(urls[0] not in done and urls[1] in done,
      f"the channel whose listing failed is not marked as looked at, so the next slot lists it "
      f"again; the other is ({sorted(done)})")
check(re.search(r"What went wrong in this run:.*@offline", digest_text()) is not None,
      "and the run section names the channel whose listing failed")

print("\n=== 6. the disk is full ===")
fresh()
standard_run()
real_free = getattr(ww, "disk_free_bytes", None)
ww.disk_free_bytes = lambda path=None: 400 * 1024 * 1024
fetched = []
ww.fetch_transcript_by_plan = lambda url, plan=None: (fetched.append(url), good_fetch(url))[1]
seen, raised = run_once(listing(("diskdisk001", "No room"), ("diskdisk002", "No room either")))
if real_free:
    ww.disk_free_bytes = real_free
check(raised is None and not fetched, f"with 0.4 GB free nothing is downloaded "
                                      f"({len(fetched)} fetches), and the run ends cleanly")
check(all(seen.get(v, {}).get("verdict") == "pending" for v in ("diskdisk001", "diskdisk002")),
      "both are queued in seen.json for the next run, nothing spent on them")
check(re.search(r"What went wrong in this run:.*disk", digest_text()) is not None,
      "and the run section says the disk had too little room")
print("  a write that fails with the disk full in the middle of a video")
fresh()
standard_run()
ww.fetch_transcript_by_plan = fetch_failing({
    "diskdisk003": OSError(errno.ENOSPC, "No space left on device")})
seen, raised = run_once(listing(("diskdisk003", "Full mid-way"), ("diskdisk004", "After")))
check(raised is None, f"no crash ({raised!r})")
check(seen.get("diskdisk003", {}).get("verdict") == "pending"
      and row_of("diskdisk003")[3].get("summary_state") == "WAITING",
      "the video waits for another try, not marked done")
check(seen.get("diskdisk004", {}).get("verdict") == "pending",
      "and the run stops fetching: the next one is queued, not downloaded onto a full disk")

# ==========================================================================================
print("\n=== 8. a video removed between the listing and the fetch ===")
fresh()
standard_run()
NO_META = "yt-dlp produced no metadata for the media client (exit 1): ERROR: [youtube] {}: {}"
ww.fetch_transcript_by_plan = fetch_failing({
    "removedvid1": NO_META.format("removedvid1", "Video unavailable. This video has been removed "
                                                 "by the uploader"),
    "privatevid1": NO_META.format("privatevid1", "Private video. Sign in if you've been granted "
                                                 "access to this video"),
    "membersvid1": NO_META.format("membersvid1", "Join this channel to get access to "
                                                 "members-only content like this video, and "
                                                 "other exclusive perks."),
    "throttled01": NO_META.format("throttled01", "Video unavailable. This content isn't "
                                                 "available, try again later."),
})
WRITTEN.clear()
seen, raised = run_once(listing(("removedvid1", "Gone"), ("privatevid1", "Made private"),
                                ("membersvid1", "Members only"), ("goodvideo01", "Still there"),
                                ("throttled01", "Throttled")), budget=5)
check(raised is None and "goodvideo01" in WRITTEN, "the run carries on and reads the video "
                                                   "that is still there")
for vid, words in (("removedvid1", "removed or made private after it was listed"),
                   ("privatevid1", "removed or made private after it was listed"),
                   ("membersvid1", "needs a signed-in account")):
    verdict, text, tags, record = row_of(vid)
    check(verdict == "NOT READ" and record.get("reason_code") == "removed" and words in text,
          f"{vid}: NOT READ, in plain words ({text[:90]})")
    check(seen.get(vid, {}).get("verdict") not in (None, "pending"),
          f"{vid}: done, because a gone video will not come back")
page = digest_text()
check("removed or made private after it was listed" in page and "yt-dlp produced no metadata"
      not in page, "the digest says it in those words, not in yt-dlp's")
check(row_of("throttled01")[3].get("summary_state") == "WAITING"
      and seen.get("throttled01", {}).get("verdict") == "pending",
      "CONTROL: 'Video unavailable ... try again later' is YouTube throttling this address, not a "
      "removed video: it waits for another try")

# ==========================================================================================
print("\n=== 3. an empty or garbled transcript ===")
fresh()
standard_run()


def fetch_text(texts):
    def fetch(url, plan=None):
        vid = ww.video_id_from_url(url)
        if vid in texts and texts[vid] is None:
            return None, f"{ww.NO_TEXT_ERROR} (0.9 min of audio)"
        meta, err = good_fetch(url, plan)
        if vid in texts:
            meta["transcript"] = texts[vid]
        return meta, err
    return fetch


LOOP = "Thank you so much for watching this video today. " * 800
UNREADABLE = ((chr(0xFFFD) * 5 + " receipt ") * 1500)


class FakeWhisper:
    """Stands in for the model on the GPU: hands back segments, as faster-whisper does."""

    def __init__(self, text):
        self.text = text

    def transcribe(self, path, **kw):
        class Info:
            duration = 600.0
        return iter(type("Seg", (), {"text": s})() for s in self.text.split(". ")), Info()


def audio_path_with(text):
    """The real fetch_transcript_via_audio and the real transcribe_audio, with the extraction,
    the download and the re-encode stubbed and a fake Whisper model saying `text`."""
    ww.extract_media_info = lambda url: ({"id": ww.video_id_from_url(url), "title": "t",
                                          "duration": 600, "formats": []}, None)
    ww.choose_audio_format = lambda info: ({"format_id": "91", "audio_only": False, "height": 144,
                                            "ext": "mp4", "acodec": "mp4a", "abr": 100, "tbr": 100,
                                            "size": 1000, "size_basis": "stated",
                                            "protocol": "https", "language": "en",
                                            "original_score": 5, "dubs_rejected": 0}, None)
    ww.wait_for_fetch_slot = lambda path="captions": None
    ww.note_fetch = lambda path="captions": None

    def download(video_id, format_id):
        ww.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        (ww.AUDIO_DIR / f"{video_id}.mp4").write_bytes(b"not really audio")
    ww.download_audio_from_info = download
    ww.reencode_audio = lambda path: (Path(path), {"bytes": 16, "source_bytes": 16, "seconds": 0.1})
    ww.load_whisper = lambda *a, **k: FakeWhisper(text)
    ww.transcribe_audio = REAL["transcribe_audio"]
    return lambda url, plan=None: REAL["fetch_transcript_via_audio"](url)


texts = fetch_text({"emptyvid001": None, "unreadable1": UNREADABLE})
through_audio = audio_path_with(LOOP)
ww.fetch_transcript_by_plan = lambda url, plan=None: (
    through_audio(url) if "loopvid0001" in url else texts(url, plan))
WRITTEN.clear()
judged = []
ww.triage_video = lambda c, ch, d, m, s=None: (judged.append(d["title"]) or good_triage(c, ch, d, m))
seen, raised = run_once(listing(("emptyvid001", "Nothing said"), ("loopvid0001", "Looping"),
                                ("unreadable1", "Unreadable"), ("fineaudio01", "Fine")), budget=4)
check(raised is None and WRITTEN == ["fineaudio01"],
      f"the run carries on, and only the readable video is written ({WRITTEN})")
v, text, tags, record = row_of("emptyvid001")
check(v == "NOT READ" and "no speech" in text, f"an empty transcript is NOT READ, no speech ({text})")
v, text, tags, record = row_of("loopvid0001")
check(v == "NOT READ" and record.get("reason_code") == "garbled" and "repeated" in text,
      f"a transcript that is one phrase looping is NOT READ, in plain words ({text[:100]})")
v, text, tags, record = row_of("unreadable1")
check(v == "NOT READ" and record.get("reason_code") == "garbled" and "unreadable" in text,
      f"a transcript of unreadable characters is NOT READ, in plain words ({text[:100]})")
check(sum(1 for t in judged if t in ("Looping", "Unreadable")) == 2,
      "neither garbled one is judged from its transcript, and neither is written up")
check(writeups_in_history("loopvid0001") == 0 and writeups_in_history("unreadable1") == 0,
      "no write-up is paid for either")
check(not list(ww.AUDIO_DIR.glob("loopvid0001*")) if ww.AUDIO_DIR.exists() else True,
      "and the looping one's audio is not left behind")
for _name in ("extract_media_info", "download_audio_from_info", "transcribe_audio", "load_whisper"):
    setattr(ww, _name, Spy(_name))

print("  a saved transcript that is not text any more, met by the retry")
fresh()
standard_run()
ww.update_record("corrupted01", url="https://www.youtube.com/watch?v=corrupted01",
                 title="Corrupted on disk", channel="C", summary_state="READ_NOT_WRITTEN",
                 reason_code="summary_timeout", state_reason="summary timed out after 600 s",
                 attempts=1, read_verdict="YES", read_reason="a mechanism", read_day=DAY)
ww.TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
(ww.TRANSCRIPTS_DIR / "corrupted01.txt").write_bytes(bytes(range(128, 256)) * 60)
raised = None
try:
    ww.run_retry_phase("CONTEXT", "model", False)
except Exception as e:  # noqa: BLE001 - the defect is exactly an exception escaping here
    raised = e
record = ww.load_records().get("corrupted01", {})
seen_now = ww.load_seen()
check(raised is None, f"the retry does not crash on a file that is not text ({raised!r})")
check(record.get("summary_state") == "WAITING" and record.get("refetch")
      and "garbled" in (record.get("state_reason") or ""),
      f"it is sent back for one more fetch, and says why ({record.get('state_reason')})")
check(seen_now.get("corrupted01", {}).get("verdict") == "pending",
      "and it really is queued: seen.json hands it to the next channels run")

# ==========================================================================================
print("\n=== 4. the write-up model times out or errors ===")
fresh()
standard_run()


def summarise_failing(fails_for):
    def summarise(context_text, meta, model, local_only, verdict, attempt=1):
        what = fails_for.get(meta["id"])
        if what is not None:
            raise what
        return good_summarise(context_text, meta, model, local_only, verdict, attempt)
    return summarise


ww.summarise_one = summarise_failing({
    "writeup0001": RuntimeError("claude CLI timed out after 600s and was killed."),
    "writeup0002": FileNotFoundError(2, "The system cannot find the file specified")})
WRITTEN.clear()
seen, raised = run_once(listing(("writeup0001", "Times out"), ("writeup0002", "CLI missing"),
                                ("writeup0003", "Fine")))
check(raised is None and WRITTEN == ["writeup0003"],
      f"the run carries on past both, and the third is written ({raised!r}, {WRITTEN})")
for vid, words in (("writeup0001", "timed out after 600 s"),
                   ("writeup0002", "FileNotFoundError")):
    verdict, text, tags, record = row_of(vid)
    check(verdict == "NOT WRITTEN" and words in text and "attempt 1 of 3" in text,
          f"{vid}: NOT WRITTEN, saying why and which attempt ({text[:110]})")
    check(any(r["id"] == vid for r in ww.retry_queue())
          and (ww.TRANSCRIPTS_DIR / f"{vid}.txt").exists(),
          f"{vid}: in the retry queue, with its transcript saved")
section = digest_text().split("## Run at ")[-1]
check("Written in this run: 1" in section and "Read, not written in this run: 2" in section,
      "the run section counts the one written and says where the two went")
print("  the next run writes both from their transcripts, with no request")
standard_run()
ww.start_this_run()
ww.run_retry_phase("CONTEXT", "model", False)
check(all(row_of(v)[3].get("summary_state") == "WRITTEN" and writeups_in_history(v) == 1
          for v in ("writeup0001", "writeup0002")),
      "both written on the retry, one write-up each in history.jsonl")
print("  three failures, and no further attempt")
fresh()
standard_run()
ww.summarise_one = summarise_failing({"writeup0004": RuntimeError("claude CLI failed (exit 1)")})
seen, raised = run_once(listing(("writeup0004", "Always fails")))
for _ in range(2):
    ww.start_this_run()
    ww.run_retry_phase("CONTEXT", "model", False)
verdict, text, tags, record = row_of("writeup0004")
check(record.get("terminal") and "no further attempt" in text and not ww.retry_queue(),
      f"after the third failure it stops, and the row says so ({text[:110]})")

# ==========================================================================================
print("\n=== 7. a malformed listing or search result ===")
fresh()
standard_run()
MALFORMED = [
    "[1, 2, 3]",
    json.dumps({"id": "malformed01", "title": 12345, "duration": "12:34",
                "upload_date": "2026-09-25", "description": ["not", "text"], "channel": None,
                "url": "https://www.youtube.com/watch?v=malformed01", "ie_key": "Youtube"}),
    json.dumps({"id": "short", "title": "An id that is not a video's"}),
    json.dumps({"id": None, "title": "No id at all"}),
    json.dumps({"id": "wellformed1", "title": "A good one", "duration": 600,
                "upload_date": "20260925", "channel": "C", "ie_key": "Youtube",
                "url": "https://www.youtube.com/watch?v=wellformed1"}),
    '{"id": "truncated01", "title": "cut off in the midd',
]
ww.run_yt_dlp = lambda cmd, timeout=120: subprocess.CompletedProcess(cmd, 0, "\n".join(MALFORMED), "")
raised = None
try:
    found = ww.search_videos("any query", count=10)
    listed = ww.list_channel_videos("https://www.youtube.com/@somechannel")
except Exception as e:  # noqa: BLE001
    raised, found, listed = e, [], []
ww.run_yt_dlp = Spy("run_yt_dlp")
check(raised is None, f"neither listing raises on what it cannot read ({raised!r})")
check([v["id"] for v in found] == ["malformed01", "wellformed1"]
      and [v["id"] for v in listed] == ["malformed01", "wellformed1"],
      f"the search and the channel listing keep the two videos and nothing else "
      f"({[v['id'] for v in found]}, {[v['id'] for v in listed]})")
odd = next((v for v in found if v["id"] == "malformed01"), {})
check(odd.get("duration") == 754 and odd.get("title") == "12345"
      and isinstance(odd.get("description"), str),
      f"a duration of 12:34 is read as 754 s, and text fields are text ({odd.get('duration')!r}, "
      f"{odd.get('title')!r})")
WRITTEN.clear()
seen, raised = run_once(found)
check(raised is None and sorted(WRITTEN) == ["malformed01", "wellformed1"],
      f"both go through the triage and are written ({raised!r}, {WRITTEN})")
check(re.search(r"could not be read, skipped: [1-9]", digest_text()) is not None,
      "the run section says how many listing lines could not be read")

# ==========================================================================================
print("\n=== 9. the same video listed twice ===")
fresh()
standard_run()
fetched = []
ww.fetch_transcript_by_plan = lambda url, plan=None: (fetched.append(ww.video_id_from_url(url)),
                                                      good_fetch(url))[1]
triaged = []
ww.triage_video = lambda c, ch, d, m, s=None: (triaged.append(d["title"]) or good_triage(c, ch, d, m))
WRITTEN.clear()
seen, raised = run_once(listing(("twicetwice1", "Listed twice"), ("twicetwice1", "Listed twice"),
                                ("oncelisted1", "Listed once")))
check(raised is None and fetched.count("twicetwice1") == 1 and WRITTEN.count("twicetwice1") == 1,
      f"inside one listing it is fetched once and written once ({fetched}, {WRITTEN})")
check(triaged.count("Listed twice") == 2 * 1,
      f"and judged once from the listing and once from its transcript, not twice each "
      f"({triaged.count('Listed twice')} triage calls)")
check(writeups_in_history("twicetwice1") == 1 and "oncelisted1" in WRITTEN,
      "one write-up in history.jsonl, and the other video is still read")
page = digest_text()
rows = re.findall(r"^\|[^\n]*watch\?v=twicetwice1\)", page, re.M)
check(len(rows) == 1, f"one row in the day's table ({len(rows)})")
check(page.count("#### [YES] title of twicetwice1") == 1, "and one write-up in the run section")
print("  from the queue and from a listing in the same run")
fresh()
standard_run()
fetched.clear()
ww.fetch_transcript_by_plan = lambda url, plan=None: (fetched.append(ww.video_id_from_url(url)),
                                                      fetch_failing({"queuedtwice": NO_NETWORK})(url))[1]
seen = {"queuedtwice": {"date": DAY, "verdict": "pending", "title": "Queued", "channel": "C",
                        "url": "https://www.youtube.com/watch?v=queuedtwice",
                        "pending_verdict": "MAYBE", "pending_reason": "queued", "retry_count": 1,
                        "note": "the network failed"}}
ww.start_this_run()
raised = None
try:
    ww.process_candidates(ww.pending_due(seen), "CONTEXT", "model", False, seen,
                          budget={"remaining": 3}, source="pending")
    ww.process_candidates(listing(("queuedtwice", "Queued"), ("fresh000001", "Fresh")),
                          "CONTEXT", "model", False, seen, budget={"remaining": 3},
                          source="channels")
except Exception as e:  # noqa: BLE001
    raised = e
check(raised is None and fetched.count("queuedtwice") == 1 and "fresh000001" in fetched,
      f"a video the queue already tried in this run is not fetched again by the listing "
      f"({fetched})")

# ==========================================================================================
print("\n=== 11. a run that overlaps the next scheduled run ===")
import os
import time as _time
fresh()


def lock_as(pid, started, age_seconds=0):
    """A lock file as a run with this process id, started at `started` (epoch seconds), would
    have left it, last touched `age_seconds` ago."""
    Path(ww.LOCK_PATH).write_text(json.dumps({"pid": pid, "started": started}), encoding="utf-8")
    then = _time.time() - age_seconds
    os.utime(ww.LOCK_PATH, (then, then))


def try_lock():
    try:
        ww.acquire_lock()
        return "taken"
    except SystemExit:
        return "refused"


print("  A. the other run is alive: this one does nothing")
me = os.getpid()
started = getattr(ww, "process_started", lambda pid: None)(me) or (_time.time() - 60)
lock_as(me, started + 1, age_seconds=5)
before = Path(ww.LOCK_PATH).read_bytes()
records_before = Path(ww.RECORDS_PATH).exists()
check(try_lock() == "refused" and Path(ww.LOCK_PATH).read_bytes() == before,
      "a lock held by a live run is refused, and left as it is")
check(Path(ww.RECORDS_PATH).exists() == records_before, "and the refused run wrote nothing")

print("  B. the run that took the lock died with the power: the next run is not blocked")
dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                      capture_output=True, text=True).stdout.strip()
lock_as(int(dead), _time.time() - 600, age_seconds=600)
check(try_lock() == "taken", "a lock from a process that has gone is cleared at once, not after "
                             "an hour")
ww.release_lock()

print("  C. a long run is still alive after an hour: its lock is not taken from it")
lock_as(me, started + 1, age_seconds=2 * 3600)
check(try_lock() == "refused", "a lock older than an hour whose run is alive is refused")
Path(ww.LOCK_PATH).unlink(missing_ok=True)

print("  D. the day's table says which scheduled starts did not run")
fresh()
# No schedule is known by default now (SCHEDULED_HOURS is empty); this section is about a run
# scheduled hourly from 09:00 to 14:00, the old default, so it sets that schedule itself.
_saved_hours = ww.SCHEDULED_HOURS
ww.SCHEDULED_HOURS = tuple(range(9, 15))
LOGS = getattr(ww, "LOGS_DIR", ROOT / "logs")
LOGS.mkdir(parents=True, exist_ok=True)
PAST = "2026-09-20"
for hhmm in ("0900", "1000", "1100", "1200", "1300"):
    (LOGS / f"hourly-{PAST}_{hhmm}.log").write_text(
        f"run start {PAST} {hhmm[:2]}:{hhmm[2:]}:01\nrun end {PAST} {hhmm[:2]}:08:00; exit codes "
        f"cooldown 0, channels 0, topics 0\n", encoding="utf-8")
(LOGS / f"hourly-{PAST}_1100.log").write_text(
    f"run start {PAST} 11:00:01\nOllama still not reachable at {PAST} 11:00:25; skipping this "
    f"slot\n", encoding="utf-8")
ww.update_record("pastday0001", url="https://www.youtube.com/watch?v=pastday0001",
                 title="A video of that day", channel="C", summary_state="WAITING",
                 reason_code="rolled", state_reason="rolled", day=PAST)
ww.refresh_digest(PAST, create=True)
page = digest_text(PAST)
line = next((l for l in page.splitlines() if "Scheduled" in l), "")
check("14:00" in line and "did not start" in line,
      f"the start that left no log is named: {line[:160]}")
check("11:00" in line and "Ollama" in line, "and the one the wrapper skipped says why")
ww.SCHEDULED_HOURS = _saved_hours

# ==========================================================================================
print("\n=== 12. the clock passes midnight in the middle of a run ===")
import datetime as real_dt
import types


class Clock:
    now = real_dt.datetime(2026, 9, 26, 23, 59, 50)


class FakeDate(real_dt.date):
    @classmethod
    def today(cls):
        return Clock.now.date()


class FakeDateTime(real_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return Clock.now if tz is None else Clock.now.replace(tzinfo=tz)


fresh()
standard_run()
real_module = ww.dt
ww.dt = types.SimpleNamespace(date=FakeDate, datetime=FakeDateTime, timedelta=real_dt.timedelta,
                              timezone=real_dt.timezone)


def midnight_fetch(url, plan=None):
    Clock.now = Clock.now + real_dt.timedelta(seconds=30)      # 23:59:50 becomes 00:00:20
    return good_fetch(url, plan)


ww.fetch_transcript_by_plan = midnight_fetch
raised = None
try:
    ww.start_this_run()
    seen, raised = run_once(listing(("midnight001", "Before midnight"),
                                    ("midnight002", "After midnight")))
finally:
    after = Clock.now
    ww.dt = real_module
check(raised is None and after.date() == real_dt.date(2026, 9, 27),
      f"the clock really did pass midnight during the run ({after})")
days = {v: row_of(v)[3].get("day") for v in ("midnight001", "midnight002")}
check(set(days.values()) == {"2026-09-26"},
      f"both videos belong to the day the run started on ({days})")
dates = sorted({r.get("date") for r in history_rows()})
check(dates == ["2026-09-26"], f"so do their history rows ({dates})")
check((ww.OUTPUT_DIR / "DIGEST-2026-09-26.md").exists()
      and not (ww.OUTPUT_DIR / "DIGEST-2026-09-27.md").exists(),
      "and the run section goes into that day's digest, not half into the next one's")
page = digest_text("2026-09-26")
check(len(re.findall(r"^\|[^\n]*watch\?v=midnight00", page, re.M)) == 2,
      "where both are rows of the same table")
check(all(p.name.startswith("2026-09-26-") for p in ww.OUTPUT_DIR.glob("20*.md")),
      "and both write-up files carry that day's date")

# ==========================================================================================
print("\n=== 10b. the power goes off in the middle of a video ===")


def die_during(name, only_if=lambda *a, **k: True):
    """Make ww.<name> lose the power when only_if(its arguments) says so."""
    real = getattr(ww, name)

    def dying(*a, **k):
        if only_if(*a, **k):
            raise PowerLost(f"the power went off during {name}")
        return real(*a, **k)
    setattr(ww, name, dying)
    return real


def run_until_power_cut(candidates):
    seen = {}
    try:
        ww.process_candidates(candidates, "CONTEXT", "model", False, seen,
                              budget={"remaining": 3}, source="topics")
    except PowerLost:
        return seen, True
    return seen, False


def next_run_finishes(vid, expect_model=True):
    standard_run()
    calls = []
    real_summarise = ww.summarise_one
    ww.summarise_one = lambda *a, **k: (calls.append(1), real_summarise(*a, **k))[1]
    ww.start_this_run()
    ww.run_retry_phase("CONTEXT", "model", False)
    record = ww.load_records().get(vid, {})
    return record, len(calls)


for where, name, cond in (
        ("while the write-up was being written", "summarise_one", lambda *a, **k: True),
        ("while the transcript was being judged", "triage_video",
         lambda c, ch, d, *a, **k: "transcript" in d),
        ("after the write-up reached history.jsonl, before the record said so",
         "save_writeup", lambda *a, **k: True)):
    print(f"  {where}")
    fresh()
    standard_run()
    real = die_during(name, cond)
    seen, cut = run_until_power_cut(listing(("powercut001", "Cut mid-video")))
    setattr(ww, name, real)
    record = ww.load_records().get("powercut001", {})
    check(cut and seen.get("powercut001", {}).get("verdict") not in (None, "pending"),
          "the power went, with the video already marked done in seen.json")
    check(record.get("summary_state") == "READ_NOT_WRITTEN"
          and any(r["id"] == "powercut001" for r in ww.retry_queue()),
          f"its record says it was read and not finished, and it is in the retry queue "
          f"({record.get('summary_state')} / {record.get('reason_code')})")
    before = writeups_in_history("powercut001")
    record, model_calls = next_run_finishes("powercut001")
    check(record.get("summary_state") == "WRITTEN" and writeups_in_history("powercut001") == 1,
          f"the next run finishes it: written, one write-up in history.jsonl "
          f"({record.get('summary_state')}, {writeups_in_history('powercut001')})")
    if before:
        check(model_calls == 0, "and the write-up the cut left in history.jsonl is taken as it "
                                "is, not paid for a second time")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
