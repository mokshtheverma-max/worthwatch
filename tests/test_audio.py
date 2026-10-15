"""The audio path: the transcript that never touches the caption endpoint. Drives the real
request layer (run_yt_dlp, the 429 detection, the per-path cooldown, the request log) over
a fake yt-dlp and a fake transcriber, so nothing here touches the network or the GPU."""
import io, sys, json, subprocess, types, datetime as dt, time
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "audio")
ww.INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ww.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
ww.wait_for_request_slot = lambda: 12.5      # pretend 12.5s passed; no sleeping in tests
ww.wait_for_fetch_slot = lambda path="captions": None
ww.AUDIO_PATH_ENABLED = True
# Section F drives a captions-then-audio plan, which set_slot_plan() will not build
# unless the caption path is switched on; production ships it off (2026-09-06 evening).
ww.CAPTION_PATH_ENABLED = True

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
# The formats mirror what this address actually returned on 2026-09-06: no audio-only
# format at all, and one muxed HLS ladder in which the same stream appears once per
# dubbed language, identical in size, with only language_preference and the word
# "original" telling the real one apart.
MODE = {"download": "ok", "audio_bytes": 12_700_000}
launched = []

def make_formats():
    fmts = [{"format_id": f"sb{i}", "ext": "mhtml", "protocol": "mhtml",
             "vcodec": "none", "acodec": "none", "url": "u"} for i in range(4)]
    langs = ["fr-FR", "ar", "ko", "es-US", "en-US"]
    for group, (height, tbr) in enumerate([(144, 114.0), (480, 698.0)], start=91):
        for i, lang in enumerate(langs):
            original = lang == "en-US"
            fmts.append({
                "format_id": f"{group}-{i}", "ext": "mp4", "protocol": "m3u8_native",
                "vcodec": "avc1.4D400C", "acodec": "mp4a.40.5", "tbr": tbr,
                "height": height, "language": lang,
                "language_preference": 10 if original else -1,
                "format": f"{group}-{i} - {height}p [{lang}]" + (" (original)" if original else ""),
                "url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/x.m3u8"})
    # one SABR entry with no url, exactly as YouTube now returns for the web client
    fmts.append({"format_id": "140", "ext": "m4a", "protocol": "sabr", "vcodec": "none",
                 "acodec": "mp4a.40.2", "abr": 129.0, "url": None})
    return fmts

class FakeProc:
    def __init__(self, argv):
        self.argv = list(argv); launched.append(self.argv); self.pid = 1; self.returncode = 0
    def communicate(self, timeout=None):
        argv = self.argv
        if "--dump-json" in argv:
            vid = ww.video_id_from_url(argv[-1]) or "unknownvid0"
            return json.dumps({"id": vid, "title": "Audio video", "uploader": "U",
                               "duration": 1200, "formats": make_formats()}), "[debug] fake\n"
        if "--load-info-json" in argv:
            vid = Path(argv[argv.index("--load-info-json") + 1]).stem.replace(".media", "")
            if MODE["download"] == "429":
                self.returncode = 1
                return "", "ERROR: unable to download: HTTP Error 429: Too Many Requests\n"
            if MODE["download"] == "nothing":
                return "", "WARNING: nothing was written\n"
            (ww.AUDIO_DIR / f"{vid}.mp4").write_bytes(b"\0" * MODE["audio_bytes"])
            return "", "[debug] fake media download\n"
        return "", ""
    def wait(self, timeout=None):
        return 0

ww.subprocess = types.SimpleNamespace(
    Popen=lambda argv, **kw: FakeProc(argv), PIPE=subprocess.PIPE,
    TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess,
    run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))

transcribed = []
def fake_transcribe(path, **kw):
    transcribed.append(Path(path).name)
    return "hello from the audio path", {
        "model": "fake", "device": "cuda", "compute_type": "float16", "beam_size": 1,
        "vad": True, "audio_seconds": 1200.0, "transcribe_seconds": 60.0,
        "realtime_factor": 20.0, "segments": 3, "chars": 26}
ww.transcribe_audio = fake_transcribe

print("=== A. the smallest format that carries the audio, and never a dub ===")
info = {"id": "vid00000001", "duration": 1200, "formats": make_formats()}
ranked = ww.audio_bearing_formats(info)
check(ranked, "some format carries audio")
best, err = ww.choose_audio_format(info)
check(err is None and best is not None, f"one is chosen ({err})")
check(best["format_id"] == "91-4",
      f"the 144p ladder's ORIGINAL track, not a same-size dub (got {best['format_id']})")
check(best["language"] == "en-US" and best["original_score"] >= 100,
      f"and it is the en-US original (got {best['language']}, score {best['original_score']})")
check(best["dubs_rejected"] == 4, f"four dubs of that stream were rejected (got {best['dubs_rejected']})")
check(best["audio_only"] is False, "it is muxed, because no audio-only format exists here")
check(len(ranked) == 2, f"one survivor per dub group, not one per language (got {len(ranked)})")
check(all(r["protocol"] != "sabr" for r in ranked), "the SABR entry with no URL is not offered")

print("\n=== B. a whole fetch: two requests, then the GPU, then the audio is deleted ===")
launched.clear(); transcribed.clear()
meta, err = ww.fetch_transcript_via_audio("https://www.youtube.com/watch?v=vid00000001")
check(err is None and meta is not None, f"it produced a transcript ({err})")
check(len(launched) == 2, f"exactly two yt-dlp calls: extract then download (got {len(launched)})")
check("--dump-json" in launched[0] and "-f" in launched[1], "in that order, the second by format")
check("--hls-prefer-native" in launched[1], "the download uses the native HLS downloader (no ffmpeg here)")
check(launched[1][launched[1].index("-f") + 1] == "91-4", "and asks for the format that was chosen")
# FIX 2, 2026-09-25: the download is re-encoded to 16 kHz mono before Whisper reads it.
check(transcribed == ["vid00000001.16k.ogg"],
      f"the downloaded file went to the transcriber, re-encoded first ({transcribed})")
check(not list(ww.AUDIO_DIR.glob("*")), "the audio file is gone afterwards")
check((ww.RAW_DIR / "vid00000001.txt").read_text(encoding="utf-8") == "hello from the audio path",
      "the transcript is on disk")
check(not list(ww.INFO_CACHE_DIR.glob("*.media.json")), "and the media extraction is not kept either")

print("\n=== C. the request log tells the two paths apart ===")
rows = ww.load_request_log()
kinds = [(e["kind"], e["endpoint"]) for e in rows]
check(("audio", "googlevideo.com/videoplayback") in kinds,
      f"the media download is logged as audio/googlevideo, not as a caption ({kinds})")
check(all("timedtext" not in e["endpoint"] for e in rows),
      "nothing in this run touched the caption endpoint")
dl = [e for e in rows if e["kind"] == "audio"][0]
check(dl["client"]["path"] == "audio",
      f"and records that the audio path made it, not the caption path ({dl['client']['path']})")
check(any(e["kind"] == "audio_transcribe" and e["endpoint"] == "local/faster-whisper" for e in rows),
      "the local transcription is logged too, with its own endpoint")
tr = [e for e in rows if e["kind"] == "audio_transcribe"][0]
check(tr.get("audio_mb") and tr.get("realtime_factor") and tr.get("audio_seconds"),
      f"carrying the measurement: {tr.get('audio_mb')} MB, {tr.get('realtime_factor')}x realtime")

print("\n=== D. the audio path spends its own budget, not the caption path's ===")
check(ww.used_today("audio") == 1, f"one audio fetch counted (got {ww.used_today('audio')})")
check(ww.used_today("captions") == 0, f"and no caption fetch (got {ww.used_today('captions')})")
check(ww.daily_cap_for("audio") == ww.CAPTION_CAP_START,
      f"the audio path starts at its own cap ({ww.daily_cap_for('audio')})")

print("\n=== E. a 429 on the audio path stops audio, and leaves captions alone ===")
ww.RATE_STATE_PATH.unlink(missing_ok=True)
MODE["download"] = "429"
launched.clear()
buf = io.StringIO(); real = sys.stdout; sys.stdout = buf
try:
    try:
        meta, err = ww.fetch_transcript_via_audio("https://www.youtube.com/watch?v=vid00000002")
        raised = False
    except ww.RateLimitError:
        raised = True
finally:
    sys.stdout = real
check(raised, "a refused media download raises RateLimitError like any other 429")
check(ww.cooldown_remaining("audio") > 0,
      f"the AUDIO cooldown is armed ({ww.format_duration(ww.cooldown_remaining('audio'))})")
check(ww.cooldown_remaining("captions") == 0,
      "and the caption path is not put in cooldown by it")
check("audio path" in buf.getvalue(), f"the message names the path: {buf.getvalue().strip()[:80]!r}")
check(not list(ww.AUDIO_DIR.glob("*")), "and no half-downloaded audio is left behind")

print("\n=== F. fetch_transcript_by_plan follows the slot's plan ===")
ww.RATE_STATE_PATH.unlink(missing_ok=True)
MODE["download"] = "ok"
calls = []
ww.fetch_transcript = lambda url, settle=True: (calls.append("captions"), (None, "no captions here"))[1]
ww.set_slot_plan("captions", "audio", "test")
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    meta, err = ww.fetch_transcript_by_plan("https://www.youtube.com/watch?v=vid00000003")
finally:
    sys.stdout = real
check(calls == ["captions"], "captions were tried first, as the plan said")
check(meta is not None and err is None, "and the audio path picked up what they could not deliver")
check(ww.used_today("captions") == 1 and ww.used_today("audio") == 1,
      "each path spent exactly one of its own budget")

print("\n=== G. with captions off, the plan makes no caption call at all ===")
ww.RATE_STATE_PATH.unlink(missing_ok=True)
calls.clear()
ww.set_slot_plan("audio", None, "canary refused")
buf = io.StringIO(); sys.stdout = buf
try:
    meta, err = ww.fetch_transcript_by_plan("https://www.youtube.com/watch?v=vid00000004")
finally:
    sys.stdout = real
check(calls == [], "fetch_transcript was never called")
check(meta is not None, "and the transcript still arrived")

print("\n=== H. a transcriber that fails does not leave the audio on disk ===")
ww.RATE_STATE_PATH.unlink(missing_ok=True)
def boom(path, **kw):
    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
ww.transcribe_audio = boom
meta, err = ww.fetch_transcript_via_audio("https://www.youtube.com/watch?v=vid00000005")
check(meta is None and "cublas" in (err or ""), f"the failure is reported, not raised ({err})")
check(not list(ww.AUDIO_DIR.glob("*")), "and the audio file is deleted anyway")
ww.transcribe_audio = fake_transcribe

print("\n=== I. a format bigger than the ceiling is refused, not downloaded ===")
big = {"id": "v", "duration": 1200,
       "formats": [{"format_id": "999", "ext": "mp4", "protocol": "https", "vcodec": "none",
                    "acodec": "opus", "abr": 8000.0, "url": "u", "language": None}]}
best, err = ww.choose_audio_format(big)
check(best is None and "ceiling" in (err or ""), f"over AUDIO_MAX_BYTES -> refused ({err})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
