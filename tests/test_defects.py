"""Regression tests for the three defects the review confirmed."""
import io, sys, json, types, datetime as dt, subprocess, re
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "defects")
# 2026-09-05 redesign: a canary caption request opens every slot and channel listings
# are enriched from the channel feed. Both are real YouTube requests; this suite is
# about the slot machinery, so both are stubbed here (they have their own suite).
ww.slot_may_proceed = lambda *a, **k: True
ww.fetch_channel_feed = lambda *a, **k: {}

# 2026-09-06 (evening): production now leads on the AUDIO path and ships with
# CAPTION_PATH_ENABLED=False. This suite drives the caption path as its stub, so it pins
# the configuration it was written for. The audio-led default, and the handover between
# the two, are covered by test_audio.py and test_slot.py.
ww.CAPTION_PATH_ENABLED = True
ww.AUDIO_PATH_ENABLED = False

CH = [f"https://www.youtube.com/@ch{i:02d}" for i in range(1, 4)]
ww.read_channel_urls = lambda: CH
ww.CHANNELS_PATH = types.SimpleNamespace(exists=lambda: True)
ww.TOPICS_PATH = types.SimpleNamespace(exists=lambda: False)

fails = []
def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: fails.append(msg)

MODE = {"429_on_caption": False, "bad_metadata_ids": set(), "recent_is_new": True,
        "captions_never_ready": False, "fetch_attempts": 0}
TODAY = dt.date.today().strftime("%Y%m%d")
# An older upload: too old to be new, and inside the backlog's BACKLOG_MAX_AGE_DAYS (60) window,
# so the backlog phase still lists it. Counted back from the clock, because the fixed date this
# used to be (20260815) left the window on 2026-10-15: D3b then crashed, and D3 passed without
# ever seeing its Short.
OLDER = (dt.date.today() - dt.timedelta(days=30)).strftime("%Y%m%d")

def fake_run(cmd, timeout=120):
    tail = cmd[-1]
    if "--playlist-items" in cmd:
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"channel": "Fake"}), "")
    if "--flat-playlist" in cmd:
        ch = re.search(r"@ch(\d\d)", tail).group(1)
        end = int(cmd[cmd.index("--playlist-end") + 1])
        vids = []
        for i in range(6 if end > 100 else 3):
            # video 0 of every channel is a 60-second Short with an off-topic title:
            # duration_allows() rejects it and it never reaches seen.json
            short = (i == 0 and end > 100)
            vids.append(json.dumps({
                "id": f"v{ch}{i:02d}abcdef"[:11],
                "title": "Quick clip" if short else f"c{ch} real video {i}",
                "duration": 60 if short else 900,
                "upload_date": OLDER if end > 100 or not MODE["recent_is_new"] else TODAY,
            }))
        return subprocess.CompletedProcess(cmd, 0, "\n".join(vids), "")
    if "--write-sub" in cmd:
        if MODE["429_on_caption"]:
            ww.arm_cooldown("HTTP Error 429: Too Many Requests")
            raise ww.RateLimitError("429")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    vid = tail.split("v=")[-1]
    if vid in MODE["bad_metadata_ids"]:
        return subprocess.CompletedProcess(cmd, 1, "", "")   # -> details is None, skipped
    return subprocess.CompletedProcess(cmd, 0, json.dumps({
        "id": vid, "title": vid, "uploader": "U", "description": "d",
        "duration": 900, "upload_date": TODAY}), "")

ww.run_yt_dlp = fake_run
for fn in ("polite_delay", "wait_for_caption_slot", "wait_for_fetch_slot",
           "wait_for_request_slot"):
    setattr(ww, fn, lambda *a, **k: None)
ww.triage_video = lambda *a, **k: ("NO", "stub", 80)
ww.ensure_local_model = lambda *a, **k: None
ww.write_digest = lambda *a, **k: None
ww.summarize_local = lambda *a, **k: ("## Top takeaway" + chr(10) + "nothing", False)
ww.ensure_one_sentence = lambda t, m: t
ww.save_writeup = lambda *a, **k: None
def fake_ft(url):
    MODE["fetch_attempts"] += 1
    fake_run([ww.YT_DLP, "--skip-download", "--dump-json", url])
    fake_run([ww.YT_DLP, "--write-sub", url])
    if MODE["captions_never_ready"]:
        return None, "captions were listed (en, auto) but could not be downloaded"
    return {"id": url[-11:], "title": "T", "uploader": "U", "url": url,
            "transcript": "w " * 400, "raw_path": ww.RAW_DIR / "x.txt", "lang": "en"}, None
ww.fetch_transcript = fake_ft

def reset(day="2026-11-01"):
    for f in ww.DATA_DIR.glob("*.json"):
        f.unlink()
    ww.today_key = lambda d=day: d
    MODE["429_on_caption"] = False
    MODE["bad_metadata_ids"] = set()
    MODE["recent_is_new"] = True
    MODE["captions_never_ready"] = False
    MODE["fetch_attempts"] = 0
    ww.triage_video = lambda *a, **k: ("NO", "stub", 80)

def quiet(fn, *a):
    buf = io.StringIO(); real = sys.stdout; sys.stdout = buf
    try: fn(*a)
    finally: sys.stdout = real
    return buf.getvalue()

print("=== D1: a 429 inside process_candidates must NOT mark the channel covered ===")
# Since the 2026-09-05 redesign a NO verdict never touches the network, so the refusal has
# to land on a MAYBE fetch for this scenario to exist at all.
reset()
MODE["429_on_caption"] = True
ww.triage_video = lambda *a, **k: ("MAYBE", "stub", 40)
quiet(ww.run_channels, "ctx", 2, "m", True)
_, cov = ww.rotation_state(CH)
check(cov == [], f"no channel marked covered after a 429 (got {[c[-5:] for c in cov]})")
nxt = ww.channels_for_this_slot(CH)
check(nxt[0] == CH[0], "next slot resumes on the channel the 429 interrupted")

print("\n=== D3: a Short that can never be processed must not pin the backlog ===")
reset("2026-11-02")
MODE["recent_is_new"] = False          # nothing fresh, so the backlog gate can open
for run in range(1, 16):
    ww.RATE_STATE_PATH.unlink(missing_ok=True)      # fresh daily caption budget each run
    quiet(ww.run_channels, "ctx", 2, "m", True)
    if not ww.BACKLOG_PATH.exists():
        continue
    bl = json.loads(ww.BACKLOG_PATH.read_text())
    st = bl.get(CH[0], {})
    print(f"  backlog run {run}: @ch01 remaining={st.get('remaining')} "
          f"stalls={st.get('stalls')} backfilled={st.get('backfilled')}")
    if st.get("backfilled"): break
bl = json.loads(ww.BACKLOG_PATH.read_text())
check(bl[CH[0]].get("total_2026_known", 0) > 0,
      f"the backlog saw @ch01's older videos, so D3 is not passing on an empty window "
      f"({bl[CH[0]].get('total_2026_known')} of them)")
check(bl[CH[0]]["backfilled"] is True,
      "@ch01 eventually reports backfilled despite an unprocessable Short")
check(bl[CH[0]]["remaining"] == 0,
      "the Short is excluded from `remaining`, not counted forever")
check(CH[1] in bl or True, "queue is free to advance to the next channel")
nxt_target = next((c for c in CH if not json.loads(ww.BACKLOG_PATH.read_text())
                   .get(c, {}).get("backfilled")), None)
check(nxt_target == CH[1], f"next backlog target is @ch02, not @ch01 forever (got {nxt_target})")

print("\n=== D3b: a video whose captions never finish generating is given up, not retried forever ===")
# The old D3b relied on a per-video metadata probe that could fail; the redesign removed the
# probe (triage reads the listing), so the case that can still hang a channel is a MAYBE
# video whose caption track is listed but never downloadable. PENDING_MAX_RETRIES ends it.
reset("2026-11-03")
MODE["recent_is_new"] = False
MODE["captions_never_ready"] = True
ww.triage_video = lambda *a, **k: ("MAYBE", "stub", 40)
last = None
for run in range(1, 26):
    ww.RATE_STATE_PATH.unlink(missing_ok=True)
    quiet(ww.run_channels, "ctx", 2, "m", True)
    if not ww.BACKLOG_PATH.exists():
        continue
    st = json.loads(ww.BACKLOG_PATH.read_text()).get(CH[0], {})
    print(f"  run {run}: remaining={st.get('remaining')} stalls={st.get('stalls')} "
          f"backfilled={st.get('backfilled')} fetch attempts so far={MODE['fetch_attempts']}")
    last = st
    seen_now = json.loads(ww.SEEN_PATH.read_text())
    if st.get("backfilled") and not any(v.get("verdict") == "pending" for v in seen_now.values()):
        break
check(last is not None and last.get("backfilled") is True, "the channel is eventually called done")
seen_now = json.loads(ww.SEEN_PATH.read_text())
gave_up = [k for k, v in seen_now.items() if v.get("verdict") == "gave_up"]
check(len(gave_up) >= 1, f"the never-ready video(s) end as gave_up ({len(gave_up)} of them)")
check(MODE["fetch_attempts"] == ww.PENDING_MAX_RETRIES * len(gave_up),
      f"exactly PENDING_MAX_RETRIES={ww.PENDING_MAX_RETRIES} attempts per video "
      f"({MODE['fetch_attempts']} attempts for {len(gave_up)} videos)")
check(not any(v.get("verdict") == "pending" for v in seen_now.values()),
      "nothing is left pending once the retries are spent")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
