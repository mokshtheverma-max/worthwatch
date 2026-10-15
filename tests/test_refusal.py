"""What happens to a video whose caption request is refused: it waits until tomorrow, nothing
is tried another way, and nothing is retried again today, whatever its verdict. Also the rule
that NO never touches the network, and that YES is fetched before MAYBE.

No YouTube request and no model call; everything runs on the invented example person in a temp
folder.

Watch it go red: make mark_pending ignore its retry date (retry_after = today) in a copy of
src/worthwatch/core.py and run this file against the copy.
"""
import datetime as dt
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "refusal")

# The caption path on and the audio path off, so a refused caption request is the only way a
# transcript could be had and nothing else can step in.
ww.AUDIO_PATH_ENABLED = False
ww.CAPTION_PATH_ENABLED = True

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


CALLS = {"fetch": [], "summaries": []}
MODE = {"refuse": True, "verdict": "YES"}


def fake_fetch_transcript(url, settle=True):
    CALLS["fetch"].append(url)
    if MODE["refuse"]:
        ww.arm_cooldown("ERROR: Unable to download video subtitles for 'en': HTTP Error 429: "
                        "Too Many Requests")
        raise ww.RateLimitError("HTTP Error 429: Too Many Requests")
    return {"id": url[-11:], "title": "T", "uploader": "U", "url": url, "transcript": "w " * 300,
            "raw_path": ww.RAW_DIR / "x.txt", "lang": "en", "track_kind": "auto"}, None


ww.fetch_transcript = fake_fetch_transcript
ww.triage_video = lambda *a, **k: (MODE["verdict"], "stub", 70)
ww.wait_for_caption_slot = lambda: None
ww.wait_for_fetch_slot = lambda path="captions": None
ww.ensure_local_model = lambda *a, **k: None


def fake_summary(*a, **k):
    CALLS["summaries"].append(1)
    return ("## 6. What applies to me\nMost applicable: nothing\nIgnore: x\n\n## 8. Try this "
            "week\nnothing\n", False)


ww.summarize_claude = fake_summary
ww.summarize_local = fake_summary
ww.save_writeup = lambda *a, **k: None


def reset():
    for f in ww.RECORDS_PATH.parent.glob("*.json*"):
        f.unlink()
    ww.HISTORY_PATH.unlink(missing_ok=True)
    for k in CALLS:
        CALLS[k].clear()
    MODE.update(refuse=True, verdict="YES")


def candidates(n=2):
    return [{"id": f"video{i:06d}", "title": f"Video {i}",
             "url": f"https://www.youtube.com/watch?v=video{i:06d}",
             "duration": 900, "upload_date": dt.date.today().strftime("%Y%m%d"),
             "channel": "Chan", "description": "d"} for i in range(n)]


def run(cands, seen=None):
    seen = {} if seen is None else seen
    buf = io.StringIO()
    real = sys.stdout
    sys.stdout = buf
    try:
        out = ww.process_candidates(cands, "ctx", "m", True, seen, budget={"remaining": 3},
                                    source="test")
    finally:
        sys.stdout = real
    return out, seen, buf.getvalue()


tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()

print("=== A. YES refused: it waits until tomorrow, and nothing is tried another way ===")
reset()
(pass_rows, no_entries, gave_up, summaries, rate_limited), seen, out = run(candidates(2))
check(rate_limited is True, "process_candidates reports the refusal")
check(len(CALLS["fetch"]) == 1, f"exactly one request in total (got {len(CALLS['fetch'])})")
check(not summaries, "no transcript, so nothing is written up")
e = seen.get("video000000", {})
check(e.get("verdict") == "pending" and e.get("retry_after") == tomorrow,
      f"pending with retry_after={e.get('retry_after')} (tomorrow is {tomorrow})")
check(e.get("pending_verdict") == "YES" and "429" in e.get("note", ""),
      "the verdict and the reason are carried with it")
check("video000001" not in seen, "the second candidate was never touched after the 429")
check(ww.cooldown_remaining() > 0, "the cooldown from the 429 stands")
check(ww.pending_due(seen) == [], "pending_due() does not offer it again today")
real_today = ww.today_key
ww.today_key = lambda: tomorrow
check([c["id"] for c in ww.pending_due(seen)] == ["video000000"], "but does offer it tomorrow")
ww.today_key = real_today

print("\n=== B. MAYBE refused: the same, pending until tomorrow ===")
reset()
MODE["verdict"] = "MAYBE"
(_, _, _, summaries, rate_limited), seen, out = run(candidates(1))
check(len(CALLS["fetch"]) == 1, "exactly one request")
check(seen.get("video000000", {}).get("retry_after") == tomorrow, "pending until tomorrow")

print("\n=== C. a pending-for-tomorrow video is skipped even when it is listed again today ===")
reset()
(_, _, _, _, _), seen, _ = run(candidates(1))
MODE["refuse"] = False
CALLS["fetch"].clear()
(_, _, _, summaries, _), seen, _ = run(candidates(1), seen)
check(len(CALLS["fetch"]) == 0, "not fetched again today, however many times it is listed")
ww.today_key = lambda: tomorrow
# Moving the day has to move the clock with it: the 429 in the first run armed a caption
# cooldown, and fetch_transcript_by_plan honours it on every call. A real tomorrow is past it;
# a faked one has to say so. And a real tomorrow is a new run.
st = ww.load_rate_state()
st.pop("cooldown_until", None)
ww.save_rate_state(st)
ww.start_this_run()
(_, _, _, summaries, _), seen, _ = run(candidates(1), seen)
check(len(CALLS["fetch"]) == 1 and seen.get("video000000", {}).get("verdict") == "MAYBE",
      "fetched tomorrow, on the cached verdict, without a new triage (MAYBE: the stub write-up "
      "applies to nothing, so the YES is demoted)")
ww.today_key = real_today

print("\n=== D. NO is decided from the listing: no request, recorded in seen and history ===")
reset()
MODE["verdict"] = "NO"
MODE["refuse"] = False
(pass_rows, no_entries, _, summaries, _), seen, out = run(candidates(3))
check(len(CALLS["fetch"]) == 0, "zero requests for three NO videos")
check(len(no_entries) == 3 and all(r.get("fetched") is False for r in no_entries),
      "three screened-out entries, marked not fetched")
check(all(seen.get(c["id"], {}).get("verdict") == "NO" for c in candidates(3)),
      "all three in seen.json as NO")
hist = [json.loads(l) for l in ww.HISTORY_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
check(len(hist) == 3 and all("not fetched" in h["reason"] for h in hist),
      "history says they were not fetched")
check(not summaries, "and nothing was written up")

print("\n=== E. YES is fetched before MAYBE when the budget cannot cover both ===")
reset()
MODE["refuse"] = False
order = []
verdicts = {"video000000": "MAYBE", "video000001": "YES", "video000002": "MAYBE"}
ww.triage_video = lambda ctx, ch, d, m, s=None: (verdicts[ww.video_id_from_url(d["url"])], "stub", 70)


def ordered_fetch(url, settle=True):
    order.append(ww.video_id_from_url(url))
    return fake_fetch_transcript(url)


ww.fetch_transcript = ordered_fetch
buf = io.StringIO()
real = sys.stdout
sys.stdout = buf
try:
    ww.process_candidates(candidates(3), "ctx", "m", True, {}, budget={"remaining": 1},
                          source="test")
finally:
    sys.stdout = real
check(order == ["video000001"], f"the single fetch went to the YES video (got {order})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
