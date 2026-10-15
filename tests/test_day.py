"""Simulate whole days of hourly runs, yt-dlp stubbed, and check the backlog gate: the backlog
(this year's older videos of each channel) runs only once the day's pass over every channel is
complete, and at most once a day, however many runs the day has. With half the runs lost to a
cooldown, a full pass takes two days and the backlog waits for it. A video the listing calls NO
costs no caption request.

Watch it go red: make day_scan_complete always return True in a copy of
src/worthwatch/core.py and run this file against the copy."""
import io, sys, json, types, datetime as dt, subprocess, re
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "day")
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

ww.INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ww.AUDIO_DIR.mkdir(parents=True, exist_ok=True)

CH = [f"https://www.youtube.com/@ch{i:02d}" for i in range(1, 12)]
ww.read_channel_urls = lambda: CH
ww.CHANNELS_PATH = types.SimpleNamespace(exists=lambda: True)

NEW_PER_CHANNEL = [1]      # how many videos each channel has inside the --days 2 window
BACKLOG_SIZE = 40          # 2026 videos per channel
calls = []
TODAY = dt.date.today().strftime("%Y%m%d")

def fake_run_yt_dlp(cmd, timeout=120):
    tail = cmd[-1]
    # 2026-09-30: a search is the search page with this year's filter now, or ytsearch.
    kind = ("search" if "ytsearch" in tail or "youtube.com/results?" in tail
            else "list" if "--flat-playlist" in cmd
            else "channel_info" if "--playlist-items" in cmd
            else "captions" if "--write-sub" in cmd
            else "metadata")
    calls.append(kind)
    if kind == "channel_info":
        out = json.dumps({"channel": "Fake", "channel_follower_count": 1000})
    elif kind == "list":
        ch = re.search(r"@ch(\d\d)", tail).group(1)
        end = int(cmd[cmd.index("--playlist-end") + 1])
        n = BACKLOG_SIZE if end > 100 else 30
        out = "\n".join(json.dumps({
            "id": f"v{ch}{i:04d}abcde"[:11], "title": f"c{ch} v{i}", "duration": 900,
            "upload_date": TODAY if i < NEW_PER_CHANNEL[0] else "20260815",
        }) for i in range(n))
    elif kind == "metadata":
        out = json.dumps({"id": tail[-11:], "title": tail[-11:], "uploader": "U",
                          "description": "d", "duration": 900, "upload_date": TODAY})
    elif kind == "search":
        out = ""     # topics finds nothing new; --channels is what is under test
    else:
        out = ""
    return subprocess.CompletedProcess(cmd, 0, out, "")

ww.run_yt_dlp = fake_run_yt_dlp
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
    fake_run_yt_dlp([ww.YT_DLP, "--skip-download", "--dump-json", url])
    fake_run_yt_dlp([ww.YT_DLP, "--write-sub", url])
    return {"id": url[-11:], "title": "T", "uploader": "U", "url": url,
            "transcript": "w " * 400, "raw_path": ww.RAW_DIR / "x.txt", "lang": "en"}, None
ww.fetch_transcript = fake_ft

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def run_scenario(label, new_per_channel, days, live_slots=6):
    """Returns ([(channels covered, runs the backlog ran in), one per day], caption requests,
    requests per --channels run)."""
    for f in ww.DATA_DIR.glob("*.json"):
        f.unlink()
    NEW_PER_CHANNEL[0] = new_per_channel
    calls.clear()
    per_day = []
    print(f"\n{'=' * 74}\n{label}\n{'=' * 74}")
    for day in range(1, days + 1):
        dk = f"2026-10-{day:02d}"
        ww.today_key = lambda dk=dk: dk
        backlog_slots = 0
        for slot in range(live_slots):   # slots beyond this are eaten by a 429 cooldown
            buf = io.StringIO(); real = sys.stdout; sys.stdout = buf
            try:
                ww.run_channels("ctx", 2, "m", True)
            finally:
                sys.stdout = real
            txt = buf.getvalue()
            if "2026 backlog: the day's pass is complete" in txt:
                backlog_slots += 1
        _, cov = ww.rotation_state(CH)
        bl = json.loads(ww.BACKLOG_PATH.read_text()) if ww.BACKLOG_PATH.exists() else {}
        done = sum(s.get("total_2026_known", 0) - s.get("remaining", 0) for s in bl.values())
        print(f"  day {day}: {len(cov)}/11 channels covered, "
              f"backlog ran in {backlog_slots}/{live_slots} live slots, "
              f"{done} backlog video(s) processed in total")
        per_day.append((len(cov), backlog_slots))
    print(f"  requests over {days} day(s): {len(calls)}  "
          f"({len(calls) / (days * live_slots):.1f} per --channels run)")
    return per_day, calls.count("captions"), len(calls) / (days * live_slots)


for label, new in (("A. quiet channels: nothing new in the 2-day window", 0),
                   ("B. every channel has 1 new video every day", 1),
                   ("C. every channel has 3 new videos every day", 3)):
    per_day, captions, per_run = run_scenario(label, new, 3)
    check(all(cov == len(CH) for cov, _b in per_day),
          f"six runs a day cover all {len(CH)} channels every day ({[c for c, _b in per_day]})")
    check(all(b == 1 for _c, b in per_day),
          f"and the backlog runs once a day, after the pass, never twice ({[b for _c, b in per_day]})")
    check(captions == 0, f"a video the listing calls NO costs no caption request ({captions})")
    check(per_run <= 2.0, f"at most 2 requests a --channels run ({per_run:.1f})")

for label, new in (("D. 3 of 6 runs lost to cooldowns, quiet channels", 0),
                   ("E. 3 of 6 runs lost AND every channel has a new video daily", 1)):
    per_day, captions, per_run = run_scenario(label, new, 4, live_slots=3)
    check(all(b == 0 for cov, b in per_day if cov < len(CH)),
          f"on a day the pass did not finish, the backlog does not run "
          f"({[(c, b) for c, b in per_day]})")
    check(any(b > 0 for _c, b in per_day) and all(b <= 1 for _c, b in per_day),
          "and it still runs, once, on the days the pass finishes")
    check(captions == 0, f"a video the listing calls NO costs no caption request ({captions})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
