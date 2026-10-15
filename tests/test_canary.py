"""The canary: one caption request opens every slot, and its outcome decides whether the
slot is spent. Drives the real request layer (run_yt_dlp, the 429 detection, the request
log) over a fake yt-dlp process, so nothing here touches the network."""
import io, sys, json, subprocess, types, datetime as dt, time
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "canary")
ww.INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
# The paired probe ships off now (a refusal means wait). Section D2 is about the probe, so this
# suite switches it on, the way it was when the suite was written.
ww.CANARY_PROBE_ON_REFUSAL = True
ww.CANARY_SEED_VIDEO_IDS = ["canaryvid01", "canaryvid02"]
# The pool normally comes from history.jsonl plus raw/; in here it is these two, so the
# rotation has something to rotate between and the assertions can name the videos.
ww.canary_pool = lambda limit=None: ["canaryvid01", "canaryvid02"]
ww.AUDIO_PATH_ENABLED = False   # sections D-F are about the canary alone; H covers handover
# Production leads on audio with CAPTION_PATH_ENABLED=False since 2026-09-06 (evening).
# This suite is the CAPTION canary's, so it switches that path on and puts it in front.
ww.CAPTION_PATH_ENABLED = True
ww.PATH_PREFERENCE = ("captions", "audio")
ww.wait_to_settle = lambda *a, **k: 0.0
ww.wait_for_request_slot = lambda: 12.5          # pretend 12.5s passed; no sleeping in tests

fails = []
def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: fails.append(msg)

# --- a fake yt-dlp process -----------------------------------------------------------
MODE = {"captions": "ok", "expire_in": 3600}
launched = []

def caption_url(vid, expire_in):
    return (f"https://www.youtube.com/api/timedtext?v={vid}&caps=asr&lang=en"
            f"&expire={int(time.time()) + expire_in}&signature=abc")

class FakeProc:
    def __init__(self, argv):
        self.argv = list(argv); launched.append(self.argv); self.pid = 1; self.returncode = 0
    def communicate(self, timeout=None):
        argv = self.argv
        if "--dump-json" in argv:
            vid = ww.video_id_from_url(argv[-1]) or "unknownvid0"
            info = {"id": vid, "title": "Canary video", "uploader": "U",
                    "duration": 900,
                    "formats": [{"format_id": "91-0", "ext": "mp4", "protocol": "m3u8_native",
                                 "vcodec": "avc1", "acodec": "mp4a.40.5", "tbr": 114.0,
                                 "height": 144, "language": "en-US",
                                 "language_preference": 10,
                                 "format": "91-0 - 144p [en-US] (original)",
                                 "url": "https://manifest.googlevideo.com/x.m3u8"}],
                    "subtitles": {}, "automatic_captions": {
                        "en": [{"ext": "vtt", "url": caption_url(vid, MODE["expire_in"]), "impersonate": True}],
                        "en-orig": [{"ext": "vtt", "url": caption_url(vid, MODE["expire_in"])}]}}
            return json.dumps(info), "[debug] fake extraction\n"
        if "--load-info-json" in argv:
            vid = Path(argv[argv.index("--load-info-json") + 1]).stem
            if MODE["captions"] == "ok":
                (ww.RAW_DIR / f"{vid}.en.vtt").write_text(
                    "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello from the canary\n", encoding="utf-8")
                return "", "[debug] fake caption download\n"
            if MODE["captions"] == "429":
                self.returncode = 1
                return "", ("WARNING: Unable to download video subtitles for 'en': "
                            "HTTP Error 429: Too Many Requests\n")
            return "", "WARNING: something else went wrong\n"   # inconclusive: no file, no 429
        return "", ""
    def wait(self, timeout=None):
        return 0

ww.subprocess = types.SimpleNamespace(
    Popen=lambda argv, **kw: FakeProc(argv), PIPE=subprocess.PIPE,
    TimeoutExpired=subprocess.TimeoutExpired, CompletedProcess=subprocess.CompletedProcess,
    run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))

def requests_logged():
    return ww.load_request_log()

print("=== A. first canary of the day: no cache, so extract then download (two requests) ===")
launched.clear()
outcome, detail, vid = ww.run_canary()
print(f"  -> {outcome} [{vid}]: {detail}")
check(outcome == "ok", "outcome is ok")
check(vid == "canaryvid01", f"and it names the video it asked for (got {vid})")
check(len(launched) == 2, f"two yt-dlp calls: extraction then caption download (got {len(launched)})")
check("--dump-json" in launched[0] and "--load-info-json" in launched[1], "in that order")
check(ww.info_cache_path("canaryvid01").exists(), "the extraction is cached for later canaries")
check(not list(ww.RAW_DIR.glob("canaryvid01*")), "the canary's own caption file is not kept")
kinds = [e["kind"] for e in requests_logged()]
check(kinds == ["canary", "canary"], f"both requests are logged as kind=canary (got {kinds})")
eps = [e["endpoint"] for e in requests_logged()]
check(eps == ["youtube.com/watch", "youtube.com/api/timedtext"], f"with their endpoints ({eps})")
check(all(e.get("gap_s") == 12.5 for e in requests_logged()), "and the gap since the previous request")
# PLAYER_CLIENT is gone (the caption path presents yt-dlp's own client), so the configuration
# of the moment is what client_config() says outside the media client's block.
check(all("client" in e and e["client"] == ww.client_config() for e in requests_logged()),
      f"and the client configuration of the moment ({ww.client_config()})")

print("\n=== B. the next canary takes a DIFFERENT video, and the one after comes back ===")
# This is the whole point of the 2026-09-06 change. Before it, every fresh canary asked
# for CANARY_VIDEO_IDS[0], so four refusals in the log all named one video and none of
# them could say whether the address or that video was the thing being refused.
ww.note_canary(outcome, detail, video_id=vid)
launched.clear()
outcome2, detail2, vid2 = ww.run_canary()
check(vid2 == "canaryvid02", f"a different video than last time (got {vid2})")
check(outcome2 == "ok" and len(launched) == 2,
      f"never canaried before, so it extracts first (got {len(launched)} call(s))")
ww.note_canary(outcome2, detail2, video_id=vid2)
launched.clear()
outcome3, detail3, vid3 = ww.run_canary()
check(vid3 == "canaryvid01", f"the rotation wraps to the least recently used (got {vid3})")
check(outcome3 == "ok" and len(launched) == 1 and "--load-info-json" in launched[0],
      f"and its cached URL is still valid, so that one is a single request "
      f"(got {len(launched)})")
ww.note_canary(outcome3, detail3, video_id=vid3)

print("\n=== C. an expired cached URL is not trusted: extract again ===")
cached = json.loads(ww.info_cache_path("canaryvid01").read_text(encoding="utf-8"))
for key in ("en", "en-orig"):
    for tr in cached["automatic_captions"].get(key, []):
        tr["url"] = caption_url("canaryvid01", -5)
ww.info_cache_path("canaryvid01").write_text(json.dumps(cached), encoding="utf-8")
launched.clear()
outcome, detail, vid = ww.run_canary()
check(outcome == "ok" and len(launched) == 2, f"expired cache -> two requests again (got {len(launched)})")
exp = ww.caption_url_expiry(caption_url("x", 100))
check(exp is not None and 95 <= (exp - dt.datetime.now()).total_seconds() <= 100,
      "caption_url_expiry reads YouTube's expire stamp")
check(ww.caption_url_expiry("https://x/y?v=1") is None, "and is None when there is no stamp")

print("\n=== D. refused: HTTP 429 on the canary ends the slot, arms the cooldown, halves the cap ===")
ww.RATE_STATE_PATH.unlink(missing_ok=True)
cap_before = ww.daily_caption_cap()
MODE["captions"] = "429"
launched.clear()
buf = io.StringIO(); real = sys.stdout; sys.stdout = buf
try:
    proceed = ww.slot_may_proceed("--channels")
finally:
    sys.stdout = real
print("  " + buf.getvalue().strip().replace("\n", "\n  "))
check(proceed is False, "slot_may_proceed() is False (the audio path is off in this suite)")
st = ww.load_rate_state()
check(ww.cooldown_remaining() > 0, f"cooldown armed ({ww.format_duration(ww.cooldown_remaining())})")
check(st.get("last_canary", {}).get("outcome") == "refused", "rate-state records the refused canary")
check(int(st.get("daily_cap")) == max(ww.CAPTION_CAP_MIN, cap_before // 2),
      f"daily cap halved {cap_before} -> {st.get('daily_cap')} with reason {st.get('cap_reason')!r}")
logged = requests_logged()
check(any(e["status"] == "429" and e["kind"] == "canary" and "stderr_tail" in e for e in logged),
      "the 429 is in the request log with its stderr tail")

print("\n=== D2. the refusal is followed by ONE paired probe of a different video ===")
probe_calls = [e for e in logged if e["kind"] == "canary_probe"]
check(probe_calls, f"a probe was fired (kinds seen: {sorted({e['kind'] for e in logged})})")
probe_vids = {e.get("video_id") for e in probe_calls}
check(probe_vids and "canaryvid01" not in probe_vids or len(probe_vids) == 1,
      f"the probe asked for a different video ({probe_vids})")
check(all(e.get("measuring") for e in probe_calls if e["status"] == "429"),
      "a probe that is itself refused is logged as a measurement")
check(int(ww.load_rate_state().get("strikes", 0)) == 1,
      f"and adds no second strike for the same refusal (strikes={ww.load_rate_state().get('strikes')})")
verdict, sentence, ev = ww.canary_attribution()
check(verdict == "address", f"two videos refused a minute apart -> ADDRESS (got {verdict}: {sentence})")
check(len(ev["refused"]) == 2, f"and the evidence names both ({ev['refused']})")

print("\n=== D3. a probe that is SERVED says the refusal followed the video ===")
ww.RATE_STATE_PATH.unlink(missing_ok=True)
for f in list(ww.REQUEST_LOG_PATH.parent.glob(ww.REQUEST_LOG_PATH.name)):
    f.unlink()
st = ww.load_rate_state()
st["canary_log"] = [
    {"at": (dt.datetime.now() - dt.timedelta(seconds=90)).isoformat(timespec="seconds"),
     "outcome": "refused", "video_id": "canaryvid01", "role": "canary", "detail": "x"},
    {"at": dt.datetime.now().isoformat(timespec="seconds"),
     "outcome": "ok", "video_id": "canaryvid02", "role": "probe", "after": "canaryvid01",
     "detail": "x"}]
ww.save_rate_state(st)
verdict, sentence, _ = ww.canary_attribution()
check(verdict == "video", f"refused then a different video served a minute later -> VIDEO "
                          f"(got {verdict}: {sentence})")

print("\n=== D4. successes hours away are NOT evidence about a refusal ===")
st = ww.load_rate_state()
st["canary_log"] = [
    {"at": (dt.datetime.now() - dt.timedelta(hours=17)).isoformat(timespec="seconds"),
     "outcome": "ok", "video_id": "canaryvid02", "role": "fetch", "detail": "x"},
    {"at": dt.datetime.now().isoformat(timespec="seconds"),
     "outcome": "refused", "video_id": "canaryvid01", "role": "canary", "detail": "x"}]
ww.save_rate_state(st)
verdict, sentence, _ = ww.canary_attribution()
check(verdict == "unknown",
      f"a success 17h earlier is a different moment, not a different video (got {verdict})")
check("different moment" in sentence, f"and it says so: {sentence!r}")
ww.RATE_STATE_PATH.unlink(missing_ok=True)

print("\n=== E. a refused canary means --channels makes no listing request at all ===")
ww.read_channel_urls = lambda: ["https://www.youtube.com/@ch01", "https://www.youtube.com/@ch02"]
ww.CHANNELS_PATH = types.SimpleNamespace(exists=lambda: True)
ww.ensure_local_model = lambda *a, **k: None
ww.write_digest = lambda *a, **k: None
st = ww.load_rate_state(); st.pop("cooldown_until", None); st.pop("last_canary", None); ww.save_rate_state(st)
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    ww.run_channels("ctx", 2, "m", True)
finally:
    sys.stdout = real
check(not any("--flat-playlist" in a for a in launched), "no channel listing was requested")
check(all(("--load-info-json" in a) or ("--dump-json" in a) for a in launched),
      f"only canary/probe traffic went out (calls: {len(launched)})")
lr = json.loads(ww.LAST_RUN_PATH.read_text(encoding="utf-8"))
check("canary" in (lr.get("note") or "") and "slot gate" in (lr.get("note") or ""),
      f"last-run.json quotes the gate's own reason: {lr.get('note')!r}")

print("\n=== F. inconclusive: no 429 but no caption either -> slot ends, no cooldown ===")
MODE["captions"] = "nothing"
st = ww.load_rate_state(); st.pop("cooldown_until", None); st.pop("last_canary", None); ww.save_rate_state(st)
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    proceed = ww.slot_may_proceed("--topics")
finally:
    sys.stdout = real
check(proceed is False, "slot_may_proceed() is False when the canary cannot be measured")
check(ww.cooldown_remaining() == 0, "and no cooldown is armed for it")
check(ww.load_rate_state().get("last_canary", {}).get("outcome") == "inconclusive",
      "rate-state records it as inconclusive")

print("\n=== G. a canary that passed recently covers the rest of the slot ===")
MODE["captions"] = "ok"
st = ww.load_rate_state(); st.pop("last_canary", None); ww.save_rate_state(st)
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    first = ww.slot_may_proceed("--channels")
    n_first = len(launched)
    second = ww.slot_may_proceed("--topics")
finally:
    sys.stdout = real
check(first and second, "both gates open")
check(len(launched) == n_first, f"the second gate made no request (reused the canary {n_first} call(s) ago)")
st = ww.load_rate_state()
st["last_canary"]["at"] = (dt.datetime.now() - dt.timedelta(seconds=ww.CANARY_REUSE_SECONDS + 5)).isoformat(timespec="seconds")
ww.save_rate_state(st)
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    ww.slot_may_proceed("--channels")
finally:
    sys.stdout = real
check(len(launched) >= 1, "once the reuse window has passed, a fresh canary goes out")

print("\n=== H. --no-canary skips it, deliberately and visibly ===")
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    proceed = ww.slot_may_proceed("this run", skip_canary=True)
finally:
    sys.stdout = real
check(proceed is True and len(launched) == 0 and "skipped" in buf.getvalue(), "no request, and it says so")

print("\n=== H2. with the audio path available, a refused canary hands the slot over ===")
ww.AUDIO_PATH_ENABLED = True
ww.fetch_transcript_via_audio = lambda url, **kw: (None, "not called in this suite")
MODE["captions"] = "429"
ww.RATE_STATE_PATH.unlink(missing_ok=True)
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    proceed = ww.slot_may_proceed("--channels")
finally:
    sys.stdout = real
out = buf.getvalue()
check(proceed is True, "the slot proceeds instead of ending")
plan = ww.slot_plan()
check(plan["primary"] == "audio" and plan["fallback"] is None,
      f"on the audio path only, with captions off for the slot ({plan})")
check("no caption request at all" in out, "and it says the caption endpoint is not touched")
check(ww.cooldown_remaining("captions") > 0 and ww.cooldown_remaining("audio") == 0,
      "the caption cooldown is armed; the audio path is untouched by it")

print("\n=== H3. a caption cooldown already in force spends no CAPTION request at all ===")
# The next slot leads on audio, so its canary is the media probe - one --dump-json with
# the media client. What must never happen is a second caption request to re-prove a
# refusal this tool has already measured and recorded.
launched.clear()
buf = io.StringIO(); sys.stdout = buf
try:
    proceed = ww.slot_may_proceed("--topics")
finally:
    sys.stdout = real
out3 = buf.getvalue()
check(proceed is True, "the slot proceeds")
check(not any("--write-auto-sub" in a or "--write-sub" in a for a in launched),
      f"no caption request was made to re-prove a refusal already measured "
      f"(calls: {len(launched)})")
check(ww.slot_plan()["primary"] == "audio", "and the slot is on the audio path")
check("captions (in cooldown" in out3, f"and it says why captions were skipped: {out3.strip()[:90]!r}")
ww.AUDIO_PATH_ENABLED = False
MODE["captions"] = "ok"
ww.RATE_STATE_PATH.unlink(missing_ok=True)

print("\n=== I. the caption download never lets yt-dlp re-extract on failure ===")
launched.clear()
ww.download_captions_from_info("canaryvid01", "en")
check("--ignore-errors" in launched[0] and "--load-info-json" in launched[0],
      "--load-info-json runs with --ignore-errors, so a failed subtitle is not retried via the URL")
# The impersonation flag that came first here was removed for good: the tool presents yt-dlp's
# own client and never tries to get around a refusal.
check("-v" in launched[0], "and carries verbose stderr for the log")
check(not any("impersonate" in a for a in launched[0]),
      "and no impersonation flag of any kind")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
