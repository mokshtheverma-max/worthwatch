"""A whole simulated slot, end to end, over a fake yt-dlp: which path leads it, what the
canary asks, and what a refusal does to the rest of it.

This is the suite the 2026-09-06 (evening) rewrite was written against. The shape it
proves is the one that could not happen before it:

    the caption canary is refused  ->  channels are still listed
                                   ->  the videos are still transcribed, through audio

Before the rewrite that slot died three times over - at the scheduler script's
`worthwatch cooldown`, at main()'s cooldown check and at slot_may_proceed() - all three keyed
on the caption path, and none of them able to see that a second path existed. Section C runs
the old shape on the same fake so the difference is measured rather than asserted.

Nothing here touches the network, the GPU or the real state files. It drives the real
request layer (run_yt_dlp, the 429 detection, the per-path cooldowns, the request log),
the real slot gate and the real run_channels."""
import io, sys, json, subprocess, types, datetime as dt, time
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "slot")
ww.INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ww.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
# worthwatch never names a YouTube player client, so the fake below tells the media client's
# calls from the caption client's by the one flag that differs (see is_media_call), and the
# request log says which path asked.
# The transcriber is faked below, so as far as a run started from main() can tell, the whisper
# extra is installed; without this main() would drop the audio path for the run.
ww.whisper_installed = lambda: True

fails = []
def fake_reencode(path):
    """FIX 2, 2026-09-25: the fake download here is zero bytes, not audio, so the re-encode is
    stubbed beside the transcriber. It keeps the size and does what the real one does to the
    file: <id>.16k.ogg beside it, and the download gone. tests/test_reencode.py drives the
    real one on audio it generates."""
    path = Path(path)
    out = path.with_name(path.name.split(".")[0] + ".16k.ogg")
    size = path.stat().st_size
    path.replace(out)
    return out, {"bytes": size, "source_bytes": size, "seconds": 0.0}


ww.reencode_audio = fake_reencode

def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: fails.append(msg)


# --- a fake yt-dlp -------------------------------------------------------------------
# It answers four kinds of call, and tells the caption client from the media one by the
# caption client's --extractor-args (see is_media_call).
MODE = {"captions": "ok", "media_extract": "ok", "media_download": "ok", "listing": "ok"}
launched = []
CHANNELS = ["https://www.youtube.com/@ch01", "https://www.youtube.com/@ch02"]
TODAY = dt.date.today().strftime("%Y%m%d")


def is_media_call(argv):
    """The caption client's calls carry youtube:skip=dash,hls and the media client's skip
    nothing: the one difference between the two in a command line."""
    return not any(str(a).startswith("youtube:skip=") for a in argv)


def caption_url(vid, expire_in=3600):
    return (f"https://www.youtube.com/api/timedtext?v={vid}&caps=asr&lang=en"
            f"&expire={int(time.time()) + expire_in}&signature=abc")


def audio_formats():
    # Shaped like what this address really returned on 2026-09-06: no audio-only format,
    # one muxed HLS ladder, the original track marked by language_preference and the word.
    return [{"format_id": "91-0", "ext": "mp4", "protocol": "m3u8_native", "vcodec": "avc1",
             "acodec": "mp4a.40.5", "tbr": 114.0, "height": 144, "language": "en-US",
             "language_preference": 10, "format": "91-0 - 144p [en-US] (original)",
             "url": "https://manifest.googlevideo.com/x.m3u8"}]


class FakeProc:
    def __init__(self, argv):
        self.argv = list(argv); launched.append(self.argv); self.pid = 1; self.returncode = 0

    def communicate(self, timeout=None):
        argv = self.argv
        tail = argv[-1]
        if "--flat-playlist" in argv:                       # a channel listing
            if MODE["listing"] == "429":
                self.returncode = 1
                return "", "ERROR: unable to list: HTTP Error 429: Too Many Requests\n"
            ch = tail.split("@")[1].split("/")[0]
            rows = [json.dumps({"id": f"{ch}vid{i:04d}", "title": f"{ch} video {i}",
                                "duration": 900, "upload_date": TODAY, "channel": ch,
                                "channel_id": f"UC{ch}", "description": "a description"})
                    for i in range(2)]
            return "\n".join(rows), ""
        if "--dump-json" in argv:                           # one video's extraction
            vid = ww.video_id_from_url(tail) or "unknownvid0"
            if is_media_call(argv):
                if MODE["media_extract"] == "429":
                    self.returncode = 1
                    return "", "ERROR: unable to extract: HTTP Error 429: Too Many Requests\n"
                if MODE["media_extract"] == "no_formats":
                    return json.dumps({"id": vid, "title": f"Video {vid}", "uploader": "U",
                                       "duration": 900, "formats": []}), ""
                return json.dumps({"id": vid, "title": f"Video {vid}", "uploader": "U",
                                   "duration": 900, "formats": audio_formats()}), ""
            return json.dumps({"id": vid, "title": f"Video {vid}", "uploader": "U",
                               "duration": 900, "subtitles": {},
                               "automatic_captions": {
                                   "en": [{"ext": "vtt", "url": caption_url(vid)}],
                                   "en-orig": [{"ext": "vtt", "url": caption_url(vid)}]}}), ""
        if "--load-info-json" in argv:
            stem = Path(argv[argv.index("--load-info-json") + 1]).stem
            vid = stem[:-len(".media")] if stem.endswith(".media") else stem
            if "-f" in argv:                                # the media download
                if MODE["media_download"] == "429":
                    self.returncode = 1
                    return "", "ERROR: unable to download: HTTP Error 429: Too Many Requests\n"
                (ww.AUDIO_DIR / f"{vid}.mp4").write_bytes(b"\0" * 12_700_000)
                return "", "[debug] fake media download\n"
            if MODE["captions"] == "429":                   # the caption download
                self.returncode = 1
                return "", ("WARNING: Unable to download video subtitles for 'en': "
                            "HTTP Error 429: Too Many Requests\n")
            if MODE["captions"] == "nothing":
                return "", "WARNING: something else went wrong\n"
            (ww.RAW_DIR / f"{vid}.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello from the caption path\n",
                encoding="utf-8")
            return "", "[debug] fake caption download\n"
        return "", ""

    def wait(self, timeout=None):
        return 0


ww.subprocess = types.SimpleNamespace(
    Popen=lambda argv, **kw: FakeProc(argv), PIPE=subprocess.PIPE,
    TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess,
    run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))

# no sleeping, no GPU, no model, no digest files
ww.wait_for_request_slot = lambda: 12.5
ww.wait_for_fetch_slot = lambda path="captions": None
ww.wait_to_settle = lambda *a, **k: 0.0
ww.polite_delay = lambda *a, **k: None
# Fix 4, 2026-09-22: 2,880 words for the fixture's 15 minutes of audio, 192 a minute. It was
# 180 words, 12 a minute, which fix 4 rightly calls no speech: the real transcripts on disk
# with a recorded length run from 101.9 to 264.6 words a minute.
ww.transcribe_audio = lambda path, **kw: ("a transcript from the audio path " * 480, {
    "model": "fake", "device": "cuda", "compute_type": "float16", "beam_size": 1,
    "vad": True, "audio_seconds": 900.0, "transcribe_seconds": 30.0,
    "realtime_factor": 30.0, "segments": 3, "chars": 900})
ww.read_channel_urls = lambda: CHANNELS
ww.CHANNELS_PATH = types.SimpleNamespace(exists=lambda: True)
ww.fetch_channel_feed = lambda *a, **k: {}
ww.triage_video = lambda *a, **k: ("YES", "on topic", 90)
# 2026-09-30: main() runs the opportunity lane for the scheduled modes, and its screen is one
# more question for the local model; stubbed here like the triage, so section D's real main()
# asks no model. It answers as a screen that could not be asked, which changes no verdict.
ww.screen_opportunity = lambda *a, **k: {"error": "this suite asks no model"}
ww.ensure_local_model = lambda *a, **k: None
ww.summarize_local = lambda *a, **k: ("## 6. What applies to me\nMost applicable: nothing\n"
                                      "Ignore: x\n\n## 8. Try this week\nnothing\n", False)
ww.summarize_claude = ww.summarize_local
ww.save_writeup = lambda *a, **k: None
ww.write_digest = lambda *a, **k: None
ww.canary_pool = lambda limit=None: ["canaryvid01", "canaryvid02"]


def reset(captions_enabled, audio_enabled, preference=("audio", "captions"),
          captions="ok", media_extract="ok", media_download="ok", listing="ok"):
    for f in list(ww.DATA_DIR.glob("*.json*")) + list(ww.LOGS_DIR.glob("*.json*")) + \
             list(ww.RAW_DIR.glob("*")) + list(ww.INFO_CACHE_DIR.glob("*")) + \
             list(ww.AUDIO_DIR.glob("*")):
        f.unlink()
    launched.clear()
    ww.CAPTION_PATH_ENABLED = captions_enabled
    ww.AUDIO_PATH_ENABLED = audio_enabled
    ww.PATH_PREFERENCE = preference
    ww._SLOT_PLAN.update({"primary": None, "fallback": None, "why": None})
    ww._IGNORE_COOLDOWN["on"] = False
    MODE.update(captions=captions, media_extract=media_extract,
                media_download=media_download, listing=listing)


class Captured(io.StringIO):
    # main() reconfigures stdout for the scheduled task's log file; a plain StringIO has
    # no such method and the whole point of section D is to drive the real main().
    def reconfigure(self, **kw):
        return None


def quiet(fn, *a, **kw):
    buf = Captured(); real = sys.stdout; sys.stdout = buf
    try:
        out = fn(*a, **kw)
    finally:
        sys.stdout = real
    return out, buf.getvalue()


def caption_requests():
    """Every call that actually asks /api/timedtext for a subtitle."""
    return [a for a in launched if "--write-auto-sub" in a or "--write-sub" in a]


def listings():
    return [a for a in launched if "--flat-playlist" in a]


def media_downloads():
    return [a for a in launched if "--load-info-json" in a and "-f" in a]


def history_rows():
    if not ww.HISTORY_PATH.exists():
        return []
    return [json.loads(l) for l in ww.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# =====================================================================================
print("=== A. what worthwatch ships: captions lead, the audio path is off ===")
# Read off the module as shipped, before any section moves the switches. The example settings
# leave the audio path off too ([transcripts] audio), so the sandbox's profile agrees.
import importlib
shipped = importlib.import_module("worthwatch.core")
check(shipped.AUDIO_PATH_ENABLED is False, "AUDIO_PATH_ENABLED is False")
check(shipped.CAPTION_PATH_ENABLED is True,
      f"CAPTION_PATH_ENABLED is True (got {shipped.CAPTION_PATH_ENABLED})")
check(tuple(shipped.PATH_PREFERENCE)[0] == "captions",
      f"and captions come first in PATH_PREFERENCE ({shipped.PATH_PREFERENCE})")
# Everything below drives the audio path, switched on the way a user switches it on.
reset(captions_enabled=False, audio_enabled=True)
check(ww.path_order() == ["audio"], f"so the only path is audio (got {ww.path_order()})")
plan = ww.slot_plan()
check(plan["primary"] == "audio" and plan["fallback"] is None,
      f"and a run that never reached the gate still plans audio-only ({plan})")
check(ww.path_blocked("captions") == "switched off", "captions report themselves switched off")

print("\n=== A2. an audio-only slot: the canary is a MEDIA probe, and no caption request "
      "is made anywhere in it ===")
reset(captions_enabled=False, audio_enabled=True)
proceed, out = quiet(ww.slot_may_proceed, "--channels")
check(proceed is True, "the gate opens")
check(len(launched) == 1 and "--dump-json" in launched[0] and is_media_call(launched[0]),
      f"on exactly one request, and it is the media client's extraction (got {len(launched)})")
check(not caption_requests(), "not one caption request")
rows = ww.load_request_log()
check([e["kind"] for e in rows] == ["canary_media"], f"logged as canary_media ({[e['kind'] for e in rows]})")
check(rows[0]["endpoint"] == "youtube.com/watch" and "timedtext" not in rows[0]["endpoint"],
      f"against the watch endpoint, never timedtext ({rows[0]['endpoint']})")
check(rows[0]["client"]["path"] == "audio",
      f"logged as the audio path's request ({rows[0]['client']['path']})")
check(not list(ww.INFO_CACHE_DIR.glob("*.media.json")),
      "and it does not leave its extraction behind - it was a probe, not a fetch")
check("no media byte fetched" in ww.last_canary("audio").get("detail", ""),
      f"the ledger records what it did and did not measure: "
      f"{ww.last_canary('audio').get('detail', '')!r}")
check(ww.last_canary("captions") == {}, "and nothing was written to the caption path's ledger")

# =====================================================================================
print("\n=== B. THE PROOF: a refused caption canary still lists channels and transcribes "
      "through audio ===")
# The slot the tool could not run before this rewrite. Captions lead, so the canary is a
# caption request; it is refused; and the slot has to carry on through the audio path.
reset(captions_enabled=True, audio_enabled=True, preference=("captions", "audio"),
      captions="429")
_, out = quiet(ww.run_channels, "ctx", 2, "m", True)
print("  --- what the slot printed ---")
for line in out.strip().splitlines():
    print("  | " + line)
print("  --- end ---")

canary_rows = [e for e in ww.load_request_log() if e["kind"].startswith("canary")]
check(any(e["kind"] == "canary" and e["status"] == "429" for e in canary_rows),
      f"the slot opened with a caption canary and it was REFUSED "
      f"({[(e['kind'], e['status']) for e in canary_rows]})")
check(ww.last_canary("captions").get("outcome") == "refused",
      "the ledger records the refusal against the caption path")

check(len(listings()) >= 1,
      f"channels were still listed - {len(listings())} listing request(s) went out")
check(sorted({a[-1] for a in listings()}) == sorted(f"{c}/videos" for c in CHANNELS),
      f"both channels in the slot's rotation ({sorted({a[-1] for a in listings()})})")

check(len(media_downloads()) >= 1,
      f"and the videos were fetched through the audio path "
      f"({len(media_downloads())} media download(s))")
transcripts = sorted(p.name for p in ww.RAW_DIR.glob("*.txt"))
check(transcripts, f"transcripts landed on disk: {transcripts}")
check(all("audio path" in (ww.RAW_DIR / t).read_text(encoding="utf-8") for t in transcripts),
      "every one of them came out of the transcriber, not the caption track")

rows = history_rows()
# 2026-09-20, rule 1: this suite's stub summary has section 6 saying "nothing", so the
# YES it triages as is demoted to MAYBE before anything records it. That is the rule
# working, and the assertion says so rather than being loosened to accept either: what
# this suite is about is that the verdict is SETTLED and the video is not left pending.
check(rows and all(r["verdict"] == "MAYBE" for r in rows),
      f"summarised and in history, demoted to MAYBE by rule 1 ({len(rows)} row(s))")
seen = json.loads(ww.SEEN_PATH.read_text(encoding="utf-8"))
check(seen and all(v.get("verdict") == "MAYBE" for v in seen.values()),
      f"seen.json settles them rather than leaving them pending ({len(seen)})")

# The canary's caption request, and its paired probe's when that is switched on: it ships off
# now (CANARY_PROBE_ON_REFUSAL), so only the canary's own may come before the slice.
canary_caption_requests = 2 if ww.CANARY_PROBE_ON_REFUSAL else 1
after_canary = caption_requests()[canary_caption_requests:]
check(not after_canary,
      f"no caption request after the canary (and its paired probe, when that is on) "
      f"({len(after_canary)})")
check(ww.cooldown_remaining("captions") > 0 and ww.cooldown_remaining("audio") == 0,
      "the caption cooldown is armed; the audio path is untouched by it")
check(ww.used_today("captions") == 0 and ww.used_today("audio") >= 1,
      f"the audio path spent its own budget and captions spent none "
      f"(captions {ww.used_today('captions')}, audio {ww.used_today('audio')})")
lr = json.loads(ww.LAST_RUN_PATH.read_text(encoding="utf-8"))
check(lr.get("maybe", 0) >= 1 and not lr.get("note"),
      f"last-run.json records real work, not a skipped slot ({lr})")

# =====================================================================================
print("\n=== C. the same refusal on the OLD shape (no audio path) ends the slot before "
      "any listing - which is what this rewrite is for ===")
reset(captions_enabled=True, audio_enabled=False, preference=("captions", "audio"),
      captions="429")
_, out = quiet(ww.run_channels, "ctx", 2, "m", True)
check(not listings(), f"no channel listing was requested at all ({len(listings())})")
check(not list(ww.RAW_DIR.glob("*.txt")), "and nothing was transcribed")
lr = json.loads(ww.LAST_RUN_PATH.read_text(encoding="utf-8"))
check("canary" in (lr.get("note") or ""), f"last-run.json says why: {lr.get('note')!r}")
check(caption_requests(), "the only traffic was the caption canary and its probe")

# =====================================================================================
print("\n=== D. a caption cooldown blocks the caption path and nothing else ===")
# Three gates used to read the caption cooldown and stop the whole slot. Each is checked
# here on the state that actually exists on this address: captions in cooldown, audio free.
reset(captions_enabled=True, audio_enabled=True, preference=("captions", "audio"))
ww.arm_cooldown("HTTP Error 429: Too Many Requests", path="captions")
check(ww.cooldown_remaining("captions") > 0 and ww.cooldown_remaining("audio") == 0,
      "set up: captions in cooldown, audio free")

# gate 1 - a scheduler script calls `worthwatch cooldown` and skips the slot on exit 1
code, out = quiet(ww.cmd_cooldown)
check(code == 0, f"`worthwatch cooldown` exits 0, so the scheduled slot runs (got {code})")
check("audio" in out, f"and names the path it will run on: {out.strip()[-60:]!r}")

# gate 2 - main()'s own check, driven through main() so it is the real one
launched.clear()
argv = sys.argv
# --canary: main() skips the canary unless asked now (CANARY_ENABLED is False); this gate was
# written with the canary opening the run, and "canary included" below needs it to.
sys.argv = ["worthwatch", "--channels", "--days", "2", "--local-only", "--captions", "--canary"]
ww.acquire_lock = lambda: None
ww.release_lock = lambda: None
# main() starts by reading the settings and pointing every path at the real folders
# (configure()). Here every path is already the sandbox's, so that step is stubbed like the lock.
ww.configure = lambda env=None: {}
try:
    _, out = quiet(ww.main)
finally:
    sys.argv = argv
check(len(listings()) >= 1,
      f"main() ran the mode instead of returning at the cooldown ({len(listings())} listing(s))")
check(any(e["kind"] == "canary_media" for e in ww.load_request_log()),
      "the run opened with its canary, on the media path")
check(not caption_requests(),
      f"and made no caption request of any kind, canary included ({len(caption_requests())})")
check(len(media_downloads()) >= 1, "the videos came through the audio path")

# gate 3 - the slot gate itself
reset(captions_enabled=True, audio_enabled=True, preference=("captions", "audio"))
ww.arm_cooldown("HTTP Error 429: Too Many Requests", path="captions")
proceed, out = quiet(ww.slot_may_proceed, "--topics")
check(proceed is True, "slot_may_proceed() opens the slot")
check(not caption_requests(),
      "spending no caption request to re-prove a refusal it already measured")
check(ww.slot_plan()["primary"] == "audio", f"on the audio path ({ww.slot_plan()})")

# gate 4 - and per video, where a stale caption cooldown must not cost the audio path
reset(captions_enabled=True, audio_enabled=True, preference=("captions", "audio"))
ww.arm_cooldown("HTTP Error 429: Too Many Requests", path="captions")
ww.set_slot_plan("captions", "audio", "deliberately stale plan")
meta, err = ww.fetch_transcript_by_plan("https://www.youtube.com/watch?v=aaaaaaaaaaa")
check(meta is not None and err is None, f"the transcript still arrives ({err})")
check(meta["track_kind"] == "audio", f"through the audio path ({meta.get('track_kind')})")
check(not caption_requests(), "with no caption request attempted")

# =====================================================================================
print("\n=== E. the media canary is a measurement of the media path, and only that ===")
reset(captions_enabled=True, audio_enabled=True, media_extract="429")
proceed, out = quiet(ww.slot_may_proceed, "--channels")
check(ww.cooldown_remaining("audio") > 0,
      f"a 429 on the media canary arms the AUDIO cooldown "
      f"({ww.format_duration(ww.cooldown_remaining('audio'))})")
check(ww.cooldown_remaining("captions") == 0, "and leaves the caption path alone")
check(proceed is True and ww.slot_plan()["primary"] == "captions",
      f"the slot is routed to captions rather than ended ({ww.slot_plan()})")
check("no media request at all" in out, f"and it says so: {out.strip()[-90:]!r}")
verdict, sentence, ev = ww.canary_attribution(path="audio")
check(ev["path"] == "audio" and ev["refused"],
      f"the attribution reads the audio ledger, not the caption one ({ev['refused']})")
v2, _, ev2 = ww.canary_attribution(path="captions")
check(v2 == "none" and not ev2["refused"],
      f"and the caption ledger is separately clean ({v2})")

print("\n=== E2. a media canary refused with nowhere to go ends the slot, as it should ===")
reset(captions_enabled=False, audio_enabled=True, media_extract="429")
proceed, out = quiet(ww.run_channels, "ctx", 2, "m", True)
check(ww.slot_may_proceed is not None and not listings(),
      f"no listing was spent once the only path was refused ({len(listings())})")

# =====================================================================================
print("\n=== F. inconclusive still ends the slot: a guard that cannot measure must not "
      "quietly pass ===")
reset(captions_enabled=False, audio_enabled=True, media_extract="no_formats")
proceed, out = quiet(ww.slot_may_proceed, "--channels")
check(proceed is False, "the gate closes")
check(ww.cooldown_remaining("audio") == 0, "without arming a cooldown for it")
check(ww.last_canary("audio").get("outcome") == "inconclusive",
      f"and records it as inconclusive ({ww.last_canary('audio').get('outcome')})")
check("no downloadable format carrying audio" in ww.last_canary("audio").get("detail", ""),
      f"naming what it could not measure: {ww.last_canary('audio').get('detail', '')!r}")

print("\n=== F2. and when no path is switched on at all, it says that rather than "
      "pretending ===")
reset(captions_enabled=False, audio_enabled=False)
proceed, out = quiet(ww.slot_may_proceed, "--channels")
check(proceed is False, "the gate closes")
check("switched off" in out, f"and names the reason: {out.strip()[:120]!r}")
code, cout = quiet(ww.cmd_cooldown)
check(code == 1, "`worthwatch cooldown` reports the slot cannot run")

# =====================================================================================
print("\n=== G. a hand-run URL follows the same plan as a scheduled slot ===")
# process_video() used to call fetch_transcript() directly, so `worthwatch <url>` and
# `worthwatch --file` asked /api/timedtext whatever the gate had just decided one frame up.
reset(captions_enabled=False, audio_enabled=True)
args = types.SimpleNamespace(claude=False, compare=False, model=None, no_canary=False)
ww.copy_to_clipboard = lambda *a, **k: (False, "not copied inside a test")
_, out = quiet(ww.run_urls, ["https://www.youtube.com/watch?v=urlvid00001"], "ctx", args)
check(not caption_requests(),
      f"no caption request was made for a hand-run URL ({len(caption_requests())})")
check(len(media_downloads()) == 1,
      f"it came through the audio path, like the slot said ({len(media_downloads())})")
check((ww.RAW_DIR / "urlvid00001.txt").exists(), "and the transcript is on disk")
check(ww.used_today("audio") >= 1 and ww.used_today("captions") == 0,
      f"charged to the audio path's budget, not the caption path's "
      f"(audio {ww.used_today('audio')}, captions {ww.used_today('captions')})")
seen = json.loads(ww.SEEN_PATH.read_text(encoding="utf-8"))
check(seen.get("urlvid00001", {}).get("verdict") == "manual", f"and recorded in seen.json ({seen})")

# =====================================================================================
print("\n=== H. a refusal on a shared surface counts against every path, because it is "
      "evidence about the address ===")
# A channel listing belongs to neither path: both need one to find a video at all. Filing
# that refusal under captions (which is what classifying by endpoint did) would arm a
# cooldown on a path that is switched off, and stop nothing.
reset(captions_enabled=True, audio_enabled=True, listing="429")
_, out = quiet(ww.run_channels, "ctx", 2, "m", True)
check(ww.cooldown_remaining("audio") > 0 and ww.cooldown_remaining("captions") > 0,
      f"both paths are put in cooldown (audio {ww.cooldown_remaining('audio'):.0f}s, "
      f"captions {ww.cooldown_remaining('captions'):.0f}s)")
row = [e for e in ww.load_request_log() if e["status"] == "429"][-1]
check(row["kind"] == "listing" and set(row["path"].split(",")) == {"audio", "captions"},
      f"and the log says which paths it counted against ({row.get('path')!r})")
check(not media_downloads(), "nothing was fetched after it")

reset(captions_enabled=False, audio_enabled=True)
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
