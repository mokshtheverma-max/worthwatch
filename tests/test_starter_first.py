"""A priority channel first, a download that failed, and a cap per channel.

Section A drives the real audio fetch (run_yt_dlp, the format choice, the download, the re-encode
and the transcriber's call) over a fake yt-dlp whose extraction is invented here, in the shape
yt-dlp gives a video under two days old: HLS formats that yt-dlp's own test refused, some it
flagged and never reached, and the format it chose after the test. Section B drives
process_candidates with a fetch that brings nothing back. Sections C to F drive run_channels with
one priority channel (settings.toml [channels] priority); section G is the cap per channel.

No YouTube request and no model call: the fakes stand in for yt-dlp and the transcriber, and a
spy raises on anything else that would leave this machine. Everything is written inside one fresh
folder under the OS temp folder, on the invented example person.

Watch it go red: make formats_to_try return the plain size order (drop the two lines that
remove refused formats) in a copy of src/worthwatch/core.py and run this file against the copy.
"""
import datetime as _dt
import io
import json
import subprocess
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "starter-first")

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


for _name in ("claude_generate", "fetch_channel_feed", "load_whisper", "ollama_generate"):
    setattr(ww, _name, Spy(_name))
ww.wait_for_request_slot = lambda: 25.0
ww.wait_for_fetch_slot = lambda path="captions": None
ww.polite_delay = lambda *a, **k: None


def invented_extraction():
    """An extraction in the shape yt-dlp gives a video under two days old, every value invented.

    Four HLS streams (91 to 94, 144p to 480p), each as the original English track (-4) and two
    machine dubs (-0 Spanish, -1 German); format 18, a 360p MP4 that is not HLS and so needs no
    test; and a storyboard that carries no audio. yt-dlp tested the HLS formats while it
    extracted: it refused every dub and 94-4, and flagged 91-4, 92-4 and 93-4 without reaching
    them. After its test it chose 18."""
    duration = 1010
    formats = []
    for group, height, tbr in (("91", 144, 120), ("92", 240, 250), ("93", 360, 650),
                               ("94", 480, 1100)):
        for suffix, language, words, pref in (("4", "en", "original, default", 10),
                                              ("0", "es", "Spanish", -1),
                                              ("1", "de", "German", -1)):
            fid = f"{group}-{suffix}"
            fmt = {"format_id": fid, "format": f"{fid} - {height}p ({words})",
                   "protocol": "m3u8_native", "ext": "mp4", "vcodec": "avc1.4D400C",
                   "acodec": "mp4a.40.5", "height": height, "tbr": tbr, "language": language,
                   "language_preference": pref, "url": f"https://example.invalid/hls/{fid}.m3u8",
                   "__needs_testing": True}
            if suffix != "4" or group == "94":
                fmt["__working"] = False
            formats.append(fmt)
    formats.append({"format_id": "18", "format": "18 - 640x360 (360p)", "protocol": "https",
                    "ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "height": 360,
                    "filesize_approx": 40_000_000, "language": "en",
                    "url": "https://example.invalid/media/18.mp4"})
    formats.append({"format_id": "sb0", "format": "sb0 - storyboard", "protocol": "mhtml",
                    "ext": "mhtml", "vcodec": "none", "acodec": "none",
                    "url": "https://example.invalid/sb/0"})
    return {"id": "invented001", "title": "An invented founder interview", "duration": duration,
            "uploader": "An example channel", "format_id": "18", "formats": formats}


FIXTURE = invented_extraction()
VID = FIXTURE["id"]
URL = f"https://www.youtube.com/watch?v={VID}"
OLD_ERROR = "audio: the audio format was listed (91-4) but nothing was downloaded"


def without_test_marks(info):
    """The same extraction as a video two days old or more gets it: yt-dlp tests nothing."""
    clean = json.loads(json.dumps(info))
    clean["format_id"] = None
    for f in clean["formats"]:
        f.pop("__working", None)
        f.pop("__needs_testing", None)
    return clean


# --- a fake yt-dlp -------------------------------------------------------------------------
# The extraction prints the fixture. A download writes the file for a format that works, and for
# one that does not it answers the way yt-dlp does when YouTube refuses the pieces: stdout full of
# refused pieces, "ERROR: The downloaded file is empty", exit 1, and no file.
FAKE = {"info": FIXTURE, "works": {"18"}, "refuse_429": False}
downloads = []
launched = []


class FakeProc:
    def __init__(self, argv):
        self.argv = list(argv)
        launched.append(self.argv)
        self.pid = 1
        self.returncode = 0

    def communicate(self, timeout=None):
        argv = self.argv
        if "--dump-json" in argv:
            return json.dumps(FAKE["info"]) + "\n", "[debug] fake extraction\n"
        if "--load-info-json" in argv:
            fid = argv[argv.index("-f") + 1]
            vid = Path(argv[argv.index("--load-info-json") + 1]).stem.replace(".media", "")
            downloads.append(fid)
            if FAKE["refuse_429"]:
                self.returncode = 1
                return "", "ERROR: unable to download: HTTP Error 429: Too Many Requests\n"
            if fid in FAKE["works"]:
                ww.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
                (ww.AUDIO_DIR / f"{vid}.mp4").write_bytes(b"\0" * 4096)
                return "[download] 100%\n", "[debug] fake media download\n"
            self.returncode = 1
            return ("[download] Got error: HTTP Error 403: Forbidden. Retrying fragment 1 (1/10)...\n"
                    "[download] fragment not found; Skipping fragment 1 ...\n",
                    "ERROR: The downloaded file is empty\n")
        return "", ""

    def wait(self, timeout=None):
        return 0


ww.subprocess = types.SimpleNamespace(
    Popen=lambda argv, **kw: FakeProc(argv), PIPE=subprocess.PIPE,
    TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess,
    run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))


def fake_reencode(path):
    path = Path(path)
    out = path.with_name(path.name.split(".")[0] + ".16k.ogg")
    size = path.stat().st_size
    path.replace(out)
    return out, {"bytes": size, "source_bytes": size, "seconds": 0.0}


def fake_transcribe(path, **kw):
    return "the founder built the app in a weekend and charged fifty dollars a month", {
        "model": "fake", "device": "cpu", "compute_type": "int8", "beam_size": 1,
        "vad": True, "audio_seconds": 1010.0, "transcribe_seconds": 30.0,
        "realtime_factor": 33.7, "segments": 3, "chars": 70}


ww.reencode_audio = fake_reencode
ww.transcribe_audio = fake_transcribe


def fetch(info, works, refuse_429=False):
    """One real fetch_transcript_via_audio over the fake. Returns (meta, err, raised)."""
    FAKE.update(info=info, works=set(works), refuse_429=refuse_429)
    downloads.clear()
    launched.clear()
    for f in list(ww.AUDIO_DIR.glob("*")) if ww.AUDIO_DIR.exists() else []:
        f.unlink()
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    raised = None
    try:
        meta, err = ww.fetch_transcript_via_audio(URL)
    except Exception as e:  # noqa: BLE001 - which exception is part of what is checked
        meta, err, raised = None, None, e
    finally:
        sys.stdout = real
    return meta, err, raised


# ==========================================================================================
print("=== A. a format yt-dlp's test refused, or that brings nothing back, falls through ===")
refused_by_ytdlp = {f["format_id"] for f in FIXTURE["formats"] if f.get("__working") is False}
check(len(refused_by_ytdlp) == 9 and FIXTURE["format_id"] == "18",
      f"the fixture is a new video's extraction: yt-dlp's own test refused "
      f"{len(refused_by_ytdlp)} formats and it chose {FIXTURE['format_id']}")

print("  A1. YouTube refuses the new video's HLS formats; format 18 works")
meta, err, raised = fetch(FIXTURE, works={"18"})
check(raised is None and meta is not None and err is None,
      f"the video is read ({err or raised})")
check(downloads[:1] == ["18"],
      f"the first download is the format yt-dlp chose after its own test, not an untested HLS "
      f"one ({downloads})")
check(not set(downloads) & refused_by_ytdlp,
      "no format yt-dlp's test refused is ever asked for")
check(not list(ww.AUDIO_DIR.glob("*")), "and no audio is left behind")
if meta:
    check(meta["audio_stats"].get("format_id") == "18"
          and meta["audio_stats"].get("language") == "en",
          f"the record keeps which format was read and its label "
          f"({meta['audio_stats'].get('format_id')}, {meta['audio_stats'].get('language')})")

print("  A1b. a format yt-dlp's test refused is never asked for, even when it is the smallest")
marked = json.loads(json.dumps(FIXTURE))
for f in marked["formats"]:
    if f.get("__needs_testing"):
        f["__working"] = f["format_id"] != "91-4"
meta, err, raised = fetch(marked, works={"91-4", "92-4"})
check(meta is not None and downloads == ["92-4"],
      f"91-4 was refused by yt-dlp's test, so the next smallest original track is read and 91-4 "
      f"is never downloaded ({downloads}, {err})")

print("  A2. a format that brings nothing back falls through to the next one")
old = without_test_marks(FIXTURE)
meta, err, raised = fetch(old, works={"92-4"})
check(downloads[:1] == ["91-4"],
      f"CONTROL: a video with no test marks still starts at the smallest original track "
      f"({downloads[:1]})")
check(meta is not None and downloads == ["91-4", "92-4"],
      f"91-4 brought nothing, so 92-4 was tried and read ({downloads}, {err})")
meta, err, raised = fetch(old, works={"91-4"})
check(meta is not None and downloads == ["91-4"],
      f"CONTROL: when the first works, exactly one download ({downloads})")
check(sum(1 for a in launched if "--dump-json" in a) == 1,
      "and one extraction: the fallthrough never extracts again")

print("  A3. nothing comes back from any format")
meta, err, raised = fetch(FIXTURE, works=set())
check(meta is None and raised is None, f"no transcript, and no crash ({raised!r})")
check(len(downloads) == getattr(ww, "AUDIO_FORMAT_TRIES", None) == 3,
      f"at most {getattr(ww, 'AUDIO_FORMAT_TRIES', '?')} downloads for one video ({downloads})")
check(err is not None and all(f in err for f in downloads)
      and "The downloaded file is empty" in err,
      f"the error names every format tried and yt-dlp's own reason ({err})")
check(ww.fetch_failure_kind(err or "") == "download",
      f"and it is a download failure, which passes ({ww.fetch_failure_kind(err or '')})")
check(not list(ww.AUDIO_DIR.glob("*")), "and no audio is left behind")
check(ww.fetch_failure_kind(OLD_ERROR) == "download",
      "an older sentence for the same failure is the same kind, so a record carrying it is too")

print("  A4. a refusal (HTTP 429) still stops at once")
meta, err, raised = fetch(FIXTURE, works={"18"}, refuse_429=True)
check(isinstance(raised, ww.RateLimitError) and len(downloads) == 1,
      f"RateLimitError, and no second download into a refusal ({downloads}, {raised!r})")
ww.RATE_STATE_PATH.unlink(missing_ok=True)

print("  A5. the format choice itself is unchanged")
fmt, err = ww.choose_audio_format(FIXTURE)
check(fmt is not None and fmt["format_id"] == "91-4",
      f"choose_audio_format still names the smallest original track ({fmt and fmt['format_id']})")
check(fmt is not None and fmt["dubs_rejected"] == 2,
      f"and never one of its two dubs ({fmt and fmt['dubs_rejected']} rejected)")

# ==========================================================================================
# The shared pieces of a run for the sections below: a listing, a fetch, a triage and a
# write-up, each a stub a section can swap.
print("\n=== B. a download that failed is tried again, at most 3 tries ===")
WRITEUP = ("## 1. What it is\nA case study.\n\n## 6. What applies to me\n\nMost applicable: \"charge "
           "for the second month\" - Applies to: Ledgerleaf.\n\n## 8. Try this week\n\nnothing\n\n"
           "## 9. Header\n\nnothing\n")
WRITTEN = []
TALK = ("the founder built the app in a weekend and charged fifty dollars a month for it and "
        "then grew it by posting the build every day ")


def speech(minutes):
    words = TALK.split()
    need = int(minutes * 150)
    return " ".join((words * (need // len(words) + 1))[:need])


def listing(*entries, channel="C", key=None):
    rows = []
    for vid, title in entries:
        row = {"id": vid, "title": title, "channel": channel,
               "url": f"https://www.youtube.com/watch?v={vid}", "description": "",
               "duration": 600, "upload_date": "20260304"}
        if key:
            row["channel_key"] = key
        rows.append(row)
    return rows


def good_fetch(url, plan=None):
    vid = ww.video_id_from_url(url)
    return {"id": vid, "title": f"title of {vid}", "uploader": "U", "url": url,
            "transcript": speech(10), "raw_path": ww.RAW_DIR / f"{vid}.txt", "lang": "en",
            "track_kind": "audio", "audio_stats": {"audio_seconds": 600.0, "model": "stub"}}, None


def fetch_failing(fails_for):
    def fetch_one(url, plan=None):
        what = fails_for.get(ww.video_id_from_url(url))
        if isinstance(what, BaseException):
            raise what
        if what:
            return None, what
        return good_fetch(url, plan)
    return fetch_one


def triage(context_text, channel, details, model, sells_something=None):
    return ("YES", "It shows a way to charge [copy: charge for the second month]", 95) \
        if "transcript" in details else ("MAYBE", "Description missing", 10)


def summarise(context_text, meta, model, local_only, verdict, attempt=1):
    WRITTEN.append(meta["id"])
    return WRITEUP, "claude:sonnet", "Sonnet", 12.0, False, verdict, []


def standard_run():
    ww.fetch_transcript_by_plan = good_fetch
    ww.triage_video = triage
    ww.summarise_one = summarise
    ww.may_start_a_summary = lambda: (True, "")
    ww.local_most_useful = lambda *a, **k: None
    ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}


def fresh():
    for path in (ww.RECORDS_PATH, ww.SEEN_PATH, ww.HISTORY_PATH, ww.RATE_STATE_PATH,
                 ww.ROTATION_PATH, ww.LAST_RUN_PATH, ww.BACKLOG_PATH):
        Path(path).unlink(missing_ok=True)
    if ww.OUTPUT_DIR.exists():
        for f in ww.OUTPUT_DIR.iterdir():
            if f.is_file():
                f.unlink()
    ww.start_this_run()
    WRITTEN.clear()


def run_once(candidates, seen=None, budget=3, source="topics"):
    """process_candidates, then write_digest, as a run does. Returns (seen, raised)."""
    seen = {} if seen is None else seen
    raised = None
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        rows, no, gave_up, summaries, _rl = ww.process_candidates(
            candidates, "CONTEXT", "model", False, seen, budget={"remaining": budget},
            source=source)
        ww.write_digest(rows, no, summaries, gave_up=gave_up, label=source)
    except Exception as e:  # noqa: BLE001 - an exception escaping here is the defect
        raised = e
    finally:
        sys.stdout = real
    return seen, raised


def record(vid):
    return ww.load_records().get(vid) or {}


def row_of(vid):
    r = record(vid)
    if r.get("reason_code") not in ww.REASON_CODES:
        return None, "", frozenset(), r
    return (*ww.final_verdict(r), r)


def digest_text():
    path = ww.OUTPUT_DIR / f"DIGEST-{ww.today_key()}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


fresh()
standard_run()
ww.fetch_transcript_by_plan = fetch_failing({"emptyaudio1": OLD_ERROR})
seen, raised = run_once(listing(("emptyaudio1", "This unbelievable app idea"),
                                ("otheraudio1", "Another video")))
r = record("emptyaudio1")
check(raised is None and "otheraudio1" in WRITTEN, f"the run carries on to the next video "
                                                  f"({raised!r})")
check(r.get("summary_state") == "WAITING" and seen.get("emptyaudio1", {}).get("verdict")
      == "pending", f"the video that brought no audio is not marked done: it waits, queued for "
                    f"another try ({r.get('summary_state')}, "
                    f"seen {seen.get('emptyaudio1', {}).get('verdict')})")
check("try 1 of 3" in (r.get("state_reason") or ""),
      f"and its row says which try it was ({r.get('state_reason')})")
check("This unbelievable app idea" in digest_text().split("Queued for another try")[-1]
      if "Queued for another try" in digest_text() else False,
      "the digest lists it under the videos queued for another try")
for n in (2, 3):
    ww.start_this_run()
    seen, raised = run_once(ww.pending_due(seen), seen=seen)
verdict, text, tags, r = row_of("emptyaudio1")
check(r.get("summary_state") == "NOT_READ" and "3 tries" in text
      and seen.get("emptyaudio1", {}).get("verdict") not in (None, "pending"),
      f"after the third try it is NOT READ, done, and says so: {verdict} ({text[:110]})")

# ==========================================================================================
# Sections C to F drive the real run_channels over a fake listing: channels.txt has the priority
# channel second, where the rotation alone would reach it in the first slot of a pass and then
# not again until the next pass. Fetch, triage and write-up are the stubs above.
PRIO = "https://www.youtube.com/@founder-stories-example"
CH = ["https://www.youtube.com/@ch01", PRIO, "https://www.youtube.com/@ch02",
      "https://www.youtube.com/@ch03"]
ww.FAVOURITE_CHANNELS = (PRIO,)
LISTED = {}          # channel url -> [(video id, title)], what its Videos tab shows now
listings = []        # the order channels were listed in, across the runs of a section
fetched = []         # the order videos were fetched in
TODAY = _dt.date.today().strftime("%Y%m%d")


def fake_yt_dlp(cmd, timeout=120):
    url = str(cmd[-1])
    curl = url[:-len("/videos")] if url.endswith("/videos") else url
    listings.append(curl)
    lines = [json.dumps({"id": vid, "title": title, "duration": 600, "upload_date": TODAY,
                         "url": f"https://www.youtube.com/watch?v={vid}"})
             for vid, title in LISTED.get(curl, [])]
    return subprocess.CompletedProcess(cmd, 0, "\n".join(lines) + "\n", "")


def tracking_fetch(fails_for=None):
    inner = fetch_failing(fails_for or {})

    def fetch_one(url, plan=None):
        fetched.append(ww.video_id_from_url(url))
        return inner(url, plan)
    return fetch_one


CHANNELS_FILE = ROOT / "channels.txt"
CHANNELS_FILE.write_text("\n".join(CH) + "\n", encoding="utf-8")
ww.CHANNELS_PATH = CHANNELS_FILE
ww.read_channel_urls = lambda: list(CH)
ww.run_yt_dlp = fake_yt_dlp
ww.slot_may_proceed = lambda *a, **k: True
ww.enrich_with_feed = lambda *a, **k: None
backlog_calls = []
ww.run_backlog_phase = lambda *a, **k: (backlog_calls.append(1), ([], [], [], []))[1]


def channels_run(days=2):
    """One --channels run, as main() starts it. Returns what it raised, or None."""
    ww.start_this_run()
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    raised = None
    try:
        ww.run_channels("CONTEXT", days, "model", False)
    except Exception as e:  # noqa: BLE001 - an exception escaping here is the defect
        raised = e
    finally:
        sys.stdout = real
    return raised


def set_audio_used(used, cap=8):
    state = ww.load_rate_state()
    state.update({"audio_daily_cap": cap, "audio_cap_day": ww.today_key(),
                  "audio_day": ww.today_key(), "audio_count": used})
    ww.save_rate_state(state)


def fresh_channels():
    fresh()
    standard_run()
    ww.fetch_transcript_by_plan = tracking_fetch()
    LISTED.clear()
    listings.clear()
    fetched.clear()
    backlog_calls.clear()


print("\n=== C. the priority channel is listed first in every run, and read before any other ===")
fresh_channels()
LISTED.update({CH[0]: [("ch01video01", "One"), ("ch01video02", "Two")],
               PRIO: [("priovideo01", "This unbelievable app idea")],
               CH[2]: [("ch02video01", "Three")], CH[3]: [("ch03video01", "Four")]})
raised = channels_run()
check(raised is None, f"the run finishes ({raised!r})")
check(listings[:1] == [PRIO], f"the priority channel is the first channel listed ({listings})")
check(fetched[:1] == ["priovideo01"], f"and its new video is the first one fetched ({fetched})")
check(listings.count(PRIO) == 1, f"it is listed once in the run, not again by the rotation "
                                 f"({listings})")
check("priovideo01" in WRITTEN, "and it is written up")
print("  the next run: the priority channel has posted again")
LISTED[PRIO] = [("priovideo02", "Another case study"), ("priovideo01", "This unbelievable app idea")]
listings.clear()
fetched.clear()
raised = channels_run()
check(listings[:1] == [PRIO], f"it is listed first again, in every run ({listings})")
check(fetched[:1] == ["priovideo02"], f"and the new one is read before any other channel's "
                                      f"({fetched})")
listings.clear()
raised = channels_run()
check(listings[:1] == [PRIO], f"and in the run after that, whatever the rotation is doing "
                              f"({listings})")
check(record("priovideo01").get("favourite") is True and record("priovideo02").get("favourite"),
      "its records say it is from a priority channel")
listed_dates = (ww.load_rotation().get("listed") or {})
check(listed_dates.get(PRIO) == ww.today_key(),
      f"and rotation.json still records the day it was last listed ({listed_dates.get(PRIO)})")

print("\n=== D. never rolled to waiting by a cap ===")
print("  D1. the day's cap is all but spent: the priority channel's video is still read")
fresh_channels()
set_audio_used(7)
LISTED.update({CH[0]: [("ch01video01", "One")], PRIO: [("priovideo03", "Late in the day")]})
raised = channels_run()
check(raised is None and fetched == ["priovideo03"],
      f"the one fetch left goes to the priority channel, and no other channel spends what is held "
      f"back for it ({fetched}, {raised!r})")
check(record("ch01video01").get("summary_state") in (None, "WAITING"),
      "the other channel's video waits")
print("  D2. the priority channel posts four at once: all four are read in the run, not three")
fresh_channels()
LISTED.update({PRIO: [(f"prioburst0{i}", f"Burst {i}") for i in range(1, 5)]})
raised = channels_run()
check(sorted(fetched) == [f"prioburst0{i}" for i in range(1, 5)],
      f"four fetched, none rolled to the next run ({fetched})")
check(all(record(f"prioburst0{i}").get("summary_state") == "WRITTEN" for i in range(1, 5)),
      "and all four written")
print("  D3. the room held back for the priority channels")
fresh_channels()
set_audio_used(5)
budget = ww.new_budget()["remaining"]
check(budget == 0, f"with 3 of the day's 8 left, the queue, other channels and searches get none "
                   f"of them (new_budget {budget})")
set_audio_used(2)
check(ww.new_budget()["remaining"] == 3, "CONTROL: with 6 left they get the run's usual 3")
fav_budget = getattr(ww, "favourite_budget", lambda: {"remaining": None})()["remaining"]
check(fav_budget == 6, f"and the priority channels' step may use all 6 ({fav_budget})")
print("  D4. the adaptive cap itself is not touched")
fresh_channels()
cap_before = ww.daily_cap_for("audio")
history_before = ww.load_rate_state().get("audio_cap_history")
LISTED.update({PRIO: [("priovideo04", "One more")]})
channels_run()
check(ww.daily_cap_for("audio") == cap_before == ww.CAPTION_CAP_START
      and ww.load_rate_state().get("audio_cap_history") == history_before,
      f"the day's cap and its history are the same before and after a priority channel's run "
      f"({cap_before})")
YESTERDAY = (_dt.date.fromisoformat(ww.today_key()) - _dt.timedelta(days=1)).isoformat()
state = {"audio_daily_cap": 8, "audio_cap_day": YESTERDAY, "audio_day": YESTERDAY,
         "audio_count": 3}
rolled = ww.roll_daily_cap(dict(state), "audio")
check(rolled["audio_daily_cap"] == 8 + ww.CAPTION_CAP_STEP,
      f"and a clean day still raises it by {ww.CAPTION_CAP_STEP} the next morning "
      f"({rolled['audio_daily_cap']})")

print("\n=== E. always the full write-up ===")
print("  E1. the listing says NO: the priority channel's video is still read")
fresh_channels()


def triage_no(context_text, channel, details, model, sells_something=None):
    return "NO", "Description missing; nothing to copy", 5


ww.triage_video = triage_no
LISTED.update({PRIO: [("priovideono", "A video the listing calls NO")],
               CH[0]: [("ch01videono", "Another NO")]})
raised = channels_run()
check("priovideono" in fetched, f"the priority channel's video is fetched whatever the listing "
                                f"says ({fetched})")
check("ch01videono" not in fetched, "CONTROL: another channel's NO is screened out, as before")
check("priovideono" in WRITTEN and record("priovideono").get("summary_state") == "WRITTEN",
      "and written up even though the transcript was read as NO too")
v, text, tags, r = row_of("priovideono")
check(v == "NO" and r.get("settled_verdict") == "NO",
      f"the verdict stays what the rules make it, NO; only the write-up is new ({v})")
print("  E2. the backend: the writing brain for a priority channel, whatever the verdict, even "
      "under --local-only")
meta = {"title": "t", "favourite": True}
check(ww.choose_backend("NO", "t", meta, False, "m")[0] == "claude",
      f"a priority channel's NO goes to the writing brain "
      f"({ww.choose_backend('NO', 't', meta, False, 'm')})")
check(ww.choose_backend("MAYBE", "t", meta, True, "m")[0] == "claude",
      "and so does a priority channel's video under --local-only")
check(ww.choose_backend("NO", "t", {"title": "t"}, False, "m")[0] == "local"
      and ww.choose_backend("MAYBE", "t", {"title": "t"}, True, "m")[0] == "local",
      "CONTROL: any other channel's NO, and --local-only, stay local, as before")

print("\n=== F. retried until read ===")
print("  F1. a fetch that keeps failing: still queued after the third try, then once a day")
fresh_channels()
ww.fetch_transcript_by_plan = tracking_fetch({"priovidfail": OLD_ERROR, "ch01videofl": OLD_ERROR})
LISTED.update({PRIO: [("priovidfail", "Refused today")], CH[0]: [("ch01videofl", "Refused too")]})
for n in range(3):
    channels_run()
seen = ww.load_seen()
entry = seen.get("priovidfail", {})
tomorrow = (_dt.date.fromisoformat(ww.today_key()) + _dt.timedelta(days=1)).isoformat()
check(entry.get("verdict") == "pending" and record("priovidfail").get("summary_state") == "WAITING",
      f"after three tries the priority channel's video is still queued, not given up "
      f"({entry.get('verdict')}, {record('priovidfail').get('summary_state')})")
check(entry.get("retry_after") == tomorrow,
      f"and from now on it is tried once a day ({entry.get('retry_after')})")
check(fetched.count("priovidfail") == 3, f"one try a run ({fetched.count('priovidfail')})")
v, text, tags, r = row_of("ch01videofl")
check(r.get("summary_state") == "NOT_READ" and "3 tries" in text,
      f"CONTROL: another channel's video with the same failure is done after 3 tries "
      f"({text[:90]})")
line = next((l for l in digest_text().splitlines() if "Queued for another try" in l), "")
check("except a priority channel's, which is tried until it is read." in line,
      f"the digest says a priority channel is the exception to the 3 tries: {line[:170]}")
print("  F2. a failure nobody recognises: a priority video waits, any other is done")
fresh_channels()
ODD = "audio: the extraction listed no downloadable format carrying audio (protocols seen: mhtml)"
ww.fetch_transcript_by_plan = tracking_fetch({"priovidodd1": ODD, "ch01videood": ODD})
LISTED.update({PRIO: [("priovidodd1", "Odd")], CH[0]: [("ch01videood", "Odd too")]})
channels_run()
seen = ww.load_seen()
check(seen.get("priovidodd1", {}).get("verdict") == "pending",
      f"the priority channel's video is queued for another try ({seen.get('priovidodd1')})")
check(record("ch01videood").get("summary_state") == "NOT_READ",
      "CONTROL: another channel's video with the same failure is NOT READ, as before")
print("  F3. a removed video is done, priority or not")
fresh_channels()
GONE = ("yt-dlp produced no metadata for the media client (exit 1): ERROR: [youtube] priovidgone: "
        "Video unavailable. This video has been removed by the uploader")
ww.fetch_transcript_by_plan = tracking_fetch({"priovidgone": GONE})
LISTED.update({PRIO: [("priovidgone", "Gone")]})
channels_run()
check(record("priovidgone").get("reason_code") == "removed"
      and ww.load_seen().get("priovidgone", {}).get("verdict") not in (None, "pending"),
      "CONTROL: a removed video will not come back, so it is not queued")
print("  F4. a write-up that keeps failing is never given up; past the third, once a day")
fresh_channels()
ww.update_record("priowrite01", url="https://www.youtube.com/watch?v=priowrite01",
                 title="Write-up keeps failing", channel=PRIO, favourite=True,
                 summary_state="READ_NOT_WRITTEN", reason_code="summary_pending",
                 state_reason="read", read_verdict="MAYBE", read_day=ww.today_key())
ww.update_record("otherwrite1", url="https://www.youtube.com/watch?v=otherwrite1",
                 title="Older, another channel", channel="C",
                 summary_state="READ_NOT_WRITTEN", reason_code="summary_pending",
                 state_reason="read", read_verdict="MAYBE", read_day=ww.today_key())
# The other channel's record is older, so without the priority rule it would go first.
records = ww.load_records()
records["otherwrite1"]["created"] = "2026-03-01T09:00:00"
ww.save_records(records)
queue = [r["id"] for r in ww.retry_queue()]
check(queue[:1] == ["priowrite01"], f"a priority channel's write-up is first in the retry queue "
                                    f"({queue})")
for attempt in (1, 2, 3):
    ww.summary_failed("priowrite01", RuntimeError("claude timed out after 600s"), attempts=attempt)
r = record("priowrite01")
check(not r.get("terminal") and "priowrite01" not in [x["id"] for x in ww.retry_queue()],
      f"after the third failure it is not given up, and not tried again the same day "
      f"(terminal {r.get('terminal')})")
records = ww.load_records()
for f in records["priowrite01"]["failures"]:
    f["day"] = (_dt.date.fromisoformat(ww.today_key()) - _dt.timedelta(days=1)).isoformat()
ww.save_records(records)
check("priowrite01" in [x["id"] for x in ww.retry_queue()],
      "and the next day it is tried again")
ww.update_record("otherwrite2", url="https://www.youtube.com/watch?v=otherwrite2",
                 title="Another channel, failing", channel="C",
                 summary_state="READ_NOT_WRITTEN", reason_code="summary_pending",
                 state_reason="read", read_verdict="MAYBE", read_day=ww.today_key())
for attempt in (1, 2, 3):
    ww.summary_failed("otherwrite2", RuntimeError("claude timed out after 600s"), attempts=attempt)
check(record("otherwrite2").get("terminal") is True,
      "CONTROL: any other channel's write-up is given up after the third, as before")

# ==========================================================================================
print("\n=== G. at most 3 videos a channel a day, a priority channel exempt ===")
print("  G1. one channel's five videos in one run: three read, two wait")
fresh_channels()
seen, raised = run_once(listing(*[(f"burstvid00{i}", f"Talk {i}") for i in range(1, 6)],
                                channel="Conference Talks", key="@conferencetalksexample"),
                        budget=5)
read = [f"burstvid00{i}" for i in range(1, 6) if record(f"burstvid00{i}").get("read_day")]
waiting = [f"burstvid00{i}" for i in range(1, 6)
           if record(f"burstvid00{i}").get("reason_code") == "channel_cap"]
check(raised is None and len(read) == 3, f"three of the channel's videos are read ({read}, "
                                         f"{raised!r})")
check(len(waiting) == 2 and all(record(v).get("summary_state") == "WAITING" for v in waiting),
      f"the other two wait, with the reason on their records ({waiting})")
check(len(fetched) == 3 and len(WRITTEN) == 3,
      f"no fetch was spent on the two that wait ({len(fetched)} fetched, {len(WRITTEN)} written)")
check(bool(waiting) and all("3 videos read today" in (record(v).get("state_reason") or "")
                           for v in waiting),
      f"and their rows say why ({record(waiting[0]).get('state_reason') if waiting else None})")
page = digest_text()
cap_line = next((l for l in page.splitlines() if l.startswith("Waiting because its channel")), "")
check("Talk 4" in cap_line and "Talk 5" in cap_line,
      f"the digest lists them on a line of their own: {cap_line[:140]}")
print("  G2. CONTROL: five channels' videos in one run are all read")
fresh_channels()
spread = []
for i in range(1, 6):
    spread += listing((f"spreadvid0{i}", f"Video {i}"), channel=f"Channel {i}")
seen, raised = run_once(spread, budget=5)
check(len(WRITTEN) == 5, f"all five read and written ({WRITTEN})")
print("  G3. one channel, two ways in: a listing and a search count against the same day")
fresh_channels()
seen, raised = run_once(listing(("viafirst001", "First"), ("viafirst002", "Second"),
                                channel="https://www.youtube.com/@ConferenceTalksExample",
                                key="@conferencetalksexample"), budget=3, source="channels")
found = [dict(v, uploader_id="@ConferenceTalksExample", channel="Conference Talks")
         for v in listing(("viasearch01", "Third"), ("viasearch02", "Fourth"))]
seen, raised = run_once(found, seen=seen, budget=3, source="topics")
check(record("viasearch01").get("read_day") and record("viasearch02").get("reason_code")
      == "channel_cap", f"a search result from the same channel is the third, and the fourth waits "
                        f"({record('viasearch02').get('reason_code')})")
print("  G4. a priority channel is exempt")
fresh_channels()
fav = [dict(v, favourite=True) for v in
       listing(*[(f"favcapvid0{i}", f"Case study {i}") for i in range(1, 5)],
               channel=PRIO, key="@founder-stories-example")]
seen, raised = run_once(fav, budget=6, source="channels")
check(len(WRITTEN) == 4, f"all four of the priority channel's read in one day ({WRITTEN})")
print("  G5. a queued video of a channel at its cap stays queued, and no try is used")
fresh_channels()
seen = {}
ww.fetch_transcript_by_plan = tracking_fetch({"queuedcap01": OLD_ERROR})
seen, raised = run_once(listing(("queuedcap01", "Queued")), seen=seen, budget=3)
seen, raised = run_once(listing(*[(f"capfill00{i}", f"Fill {i}") for i in range(1, 4)]),
                        seen=seen, budget=3)
ww.start_this_run()
fetched.clear()
seen, raised = run_once(ww.pending_due(seen), seen=seen, budget=3)
check(fetched == [] and seen.get("queuedcap01", {}).get("verdict") == "pending"
      and seen["queuedcap01"].get("retry_count") == 1,
      f"not fetched today, still queued, still on its first try ({fetched}, "
      f"{seen.get('queuedcap01')})")

print("  G6. a conference channel's burst no longer holds the rotation")
fresh_channels()
CONF = "https://www.youtube.com/@ConferenceTalksExample"
POD = "https://www.youtube.com/@InterviewPodExample"
CH4 = [CONF, POD, "https://www.youtube.com/@chx", "https://www.youtube.com/@chy"]
ww.read_channel_urls = lambda: list(CH4)
CHANNELS_FILE.write_text("\n".join(CH4) + "\n", encoding="utf-8")
LISTED.update({CONF: [(f"conftalk00{i}", f"Conference talk {i}") for i in range(1, 10)],
               POD: [("podcast0001", "Interview")], CH4[2]: [("chxvideo001", "X")],
               CH4[3]: [("chyvideo001", "Y")]})
for n in range(3):
    channels_run()
conf_read = [v for v in fetched if v.startswith("conftalk")]
check(len(conf_read) == 3, f"three of the conference channel's nine are read that day, not "
                           f"nine ({len(conf_read)})")
check({"podcast0001", "chxvideo001", "chyvideo001"} <= set(fetched),
      f"and the other three channels are reached within the same three runs ({fetched})")
ww.read_channel_urls = lambda: list(CH)
CHANNELS_FILE.write_text("\n".join(CH) + "\n", encoding="utf-8")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
