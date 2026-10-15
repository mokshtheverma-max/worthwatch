"""Count the YouTube requests one --channels slot makes, old shape vs new, with yt-dlp
stubbed out. Nothing here touches the network or the real state files."""
import io, sys, json, types, datetime as dt, subprocess
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "requests")
# The real request gate, kept before the stub below replaces it, so the last section can time
# the real one rather than a copy of its source.
REAL_WAIT_FOR_REQUEST_SLOT = ww.wait_for_request_slot
fails = []
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

CH = [f"https://www.youtube.com/@ch{i:02d}" for i in range(1, 12)]
ww.read_channel_urls = lambda: CH
ww.CHANNELS_PATH = types.SimpleNamespace(exists=lambda: True)

# --- stub every outbound call ----------------------------------------------
calls = []
TODAY = dt.date.today().strftime("%Y%m%d")

def fake_run_yt_dlp(cmd, timeout=120):
    # 2026-09-30: a search is the search page with this year's filter now, or ytsearch.
    is_search = "ytsearch" in cmd[-1] or "youtube.com/results?" in cmd[-1]
    kind = ("list" if "--flat-playlist" in cmd and not is_search
            else "search" if is_search
            else "channel_info" if "--playlist-items" in cmd
            else "captions" if "--write-sub" in cmd
            else "metadata")
    calls.append(kind)
    if kind == "channel_info":
        out = json.dumps({"channel": "Fake Channel", "channel_follower_count": 1000})
    elif kind == "list":
        # 30 videos, 3 of them from today (i.e. inside --days 2). Ids are 11 characters, like a
        # real video's: since 25 September a listing drops any entry whose id is not.
        ch = cmd[-1].split("@")[1].split("/")[0]
        out = "\n".join(json.dumps({
            "id": f"{ch}vid{i:04d}", "title": f"Video {i}", "duration": 900,
            "upload_date": TODAY if i < 3 else "20260101",
        }) for i in range(30))
    elif kind == "search":
        out = chr(10).join(json.dumps({
            "id": f"s{abs(hash(cmd[-1])) % 10000:04d}res{i:03d}", "title": f"Result {i}",
            "duration": 900, "upload_date": TODAY, "channel": "Someone",
            "channel_url": "https://www.youtube.com/@someone",
        }) for i in range(15))
    elif kind == "metadata":
        out = json.dumps({"id": cmd[-1][-11:], "title": "T", "uploader": "U",
                          "description": "d", "duration": 900, "upload_date": TODAY})
    else:
        out = ""
    return subprocess.CompletedProcess(cmd, 0, out, "")

ww.run_yt_dlp = fake_run_yt_dlp
ww.polite_delay = lambda *a, **k: None
ww.wait_for_caption_slot = lambda: None
ww.wait_for_fetch_slot = lambda path="captions": None
ww.wait_for_request_slot = lambda: None      # timing tested separately; count requests here
ww.triage_video = lambda *a, **k: ("NO", "stub", 80)
ww.ensure_local_model = lambda *a, **k: None
ww.write_digest = lambda *a, **k: None
_orig_ft = ww.fetch_transcript
def fake_fetch_transcript(url):
    # the two real yt-dlp calls a caption fetch makes: a --dump-json probe, then the
    # --write-sub download
    fake_run_yt_dlp([ww.YT_DLP, "--skip-download", "--dump-json", url])
    fake_run_yt_dlp([ww.YT_DLP, "--write-sub", "--sub-langs", "en", url])
    return {"id": url[-11:], "title": "T", "uploader": "U", "url": url,
            "transcript": "word " * 400, "raw_path": ww.RAW_DIR / "x.txt", "lang": "en"}, None
ww.fetch_transcript = fake_fetch_transcript
ww.summarize_local = lambda *a, **k: ("## Top takeaway" + chr(10) + "nothing" + chr(10), False)
ww.summarize_claude = lambda *a, **k: ("## Top takeaway" + chr(10) + "nothing" + chr(10), False)
ww.ensure_one_sentence = lambda t, m: t
ww.save_writeup = lambda *a, **k: None

print("=== one full slot, new shape: --channels then --topics ===")
ww.run_channels("ctx", 2, "stub-model", True)
n_ch = len(calls)
ww.TOPICS_PATH = types.SimpleNamespace(exists=lambda: True,
    read_text=lambda **k: chr(10).join(f"query {i}" for i in range(10)))
ww.run_topics("ctx", "stub-model", True)
print(f"  --channels: {n_ch} requests   --topics: {len(calls)-n_ch} requests")
from collections import Counter
c = Counter(calls)
print(f"  requests: {dict(c)}  TOTAL={len(calls)}")
new_total = len(calls)

# --- what the old shape did, reconstructed from a log of one old-shape slot ----
# The real log this used to read is not part of this repository. This invented one has the
# same shape: every video in the window triaged from its own metadata request, the verdicts the
# budget could not pay for thrown away, two write-ups, then a 429.
OLD_SHAPE_LOG = """\
One slot, --channels --days 2 (the old shape)
https://www.youtube.com/@ch01: 30 videos listed, 12 inside the window
  [YES] Pricing a bookkeeping add-on for small bakeries
      -> summarized
  [MAYBE] One year of newsletter sponsorship numbers
      -> summarized
  [NO] A desk tour
  [MAYBE] How an indie game studio sells its soundtrack
      left for next run
  [YES] Three ways to price a monthly plan
      left for next run
  [NO] Reacting to a keyboard launch
  [MAYBE] What a solo founder learned from forty customer calls
      left for next run
  [MAYBE] Writing release notes people read
      left for next run
  [NO] A travel vlog
  [MAYBE] Bank exports and the bookkeeping they save
      left for next run
  [YES] Growing a newsletter to its first paid sponsor
      left for next run
  [MAYBE] A small game's launch week in numbers
      left for next run
ERROR: HTTP Error 429: Too Many Requests
"""
print("\n=== the same slot under the old shape, from an invented log of one such slot ===")
txt = OLD_SHAPE_LOG
import re
triaged = len(re.findall(r"^\s+\[(?:YES|MAYBE|NO)\]", txt, re.M))
deferred = len(re.findall(r"left for next run", txt))
summarized = len(re.findall(r"-> summarized", txt))
# 1 channel_info + 1 listing per channel reached, 1 metadata per triaged video,
# 2 requests per caption attempt (probe + subtitle fetch)
old_total = 2 + triaged + 2 * (summarized + 1)   # +1 for the attempt that took the 429
print(f"  {triaged} videos triaged, {deferred} of them triaged and then discarded "
      f"for want of budget, {summarized} summarized, then a 429")
print(f"  requests: 2 listing + {triaged} metadata + {2 * (summarized + 1)} caption "
      f"= TOTAL={old_total}")
print(f"  of those, {deferred} metadata requests bought nothing: the verdict was "
      f"computed, printed, and thrown away unsaved")

print("\n=== result ===")
print(f"  old: ~{old_total} requests/slot at REQUEST_DELAY_SECONDS=4s between them")
print(f"  new: ~{new_total} requests/slot at MIN_REQUEST_GAP_SECONDS="
      f"{ww.MIN_REQUEST_GAP_SECONDS:.0f}s between them")
print(f"  requests per slot:  {old_total} -> {new_total}  "
      f"({(1 - new_total / old_total) * 100:.0f}% fewer)")
old_rate = old_total / (old_total * 4 / 60)
new_rate = new_total / (new_total * ww.MIN_REQUEST_GAP_SECONDS / 60)
print(f"  peak request rate:  {old_rate:.1f}/min -> {new_rate:.1f}/min  "
      f"({old_rate / new_rate:.1f}x slower)")
print(f"  slot wall-clock:    ~{old_total * 4 / 60:.0f} min -> "
      f"~{new_total * ww.MIN_REQUEST_GAP_SECONDS / 60:.0f} min (fits the hourly slot)")

# --- the gate itself, timed for real ---------------------------------------
print("\n=== MIN_REQUEST_GAP_SECONDS actually holds ===")
import time
ww.MIN_REQUEST_GAP_SECONDS = 3.0
# The real gate, put back. This section used to rebuild a copy of the gate from its source text,
# and a copy cannot go red when the real one breaks.
ww.wait_for_request_slot = REAL_WAIT_FOR_REQUEST_SLOT
ww.note_request()
t0 = time.monotonic(); ww.wait_for_request_slot(); waited = time.monotonic() - t0
held = 2.8 < waited < 3.3
print(f"  slept {waited:.2f}s for a 3.0s floor -> {'PASS' if held else 'FAIL'}")
if not held:
    fails.append(f"the gate slept {waited:.2f}s for a 3.0s floor")
ww.note_request()
time.sleep(3.2)
t0 = time.monotonic(); ww.wait_for_request_slot(); waited2 = time.monotonic() - t0
free = waited2 < 0.2
print(f"  slept {waited2:.2f}s when the gap had already passed -> "
      f"{'PASS' if free else 'FAIL'}")
if not free:
    fails.append(f"the gate slept {waited2:.2f}s when the gap had already passed")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
