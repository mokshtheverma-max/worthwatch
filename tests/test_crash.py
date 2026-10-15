"""FIX 5, 2026-09-25. One bad search result is that result's failure, not the run's.

Twice in one week the topics run ended with exit 1. A search had returned a channel, not a
video: its "id" is the 24-character channel id and its url is /channel/UC... The tool queued it
as a video, the media client's extraction of a whole channel timed out after 180 s, and the
RuntimeError that run_yt_dlp raises went up through process_candidates and ended the
invocation, and every query after it that run. It stayed WAITING in records.json, and the next
topics run started at the same query.

Two changes, each checked here: search_videos keeps only results that are videos, and a fetch
that raises for one video leaves that video NOT READ with the error and goes on to the next.
And the line under the day's table stops saying "Rolled to the next run": 15 real videos were
still waiting at the end of the week, because nothing brings a rolled video back except a later
listing that returns it.

No YouTube request, no model call: yt-dlp, the fetch and the triage are stubbed, and every video
and channel here is invented. Everything runs in a fresh folder under the OS temp folder.
"""
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "crash")
ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}
ww._RUN_CLOCK["deadline"] = None

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


# ==========================================================================================
print("=== A. a search keeps only the results that are videos ===")
LISTING = [
    {"_type": "url", "ie_key": "Youtube", "id": "MaraVid0001",
     "url": "https://www.youtube.com/watch?v=MaraVid0001",
     "title": "Reading a bakery's bank export into its books", "duration": 900,
     "channel": "Small Shop Software"},
    {"_type": "url", "ie_key": "YoutubeTab", "id": "UCmaraExampleChannel0001",
     "url": "https://www.youtube.com/channel/UCmaraExampleChannel0001", "title": "BAKERYBOOKS4wd",
     "channel_id": "UCmaraExampleChannel0001"},
    {"_type": "url", "ie_key": "YoutubeTab", "id": "PLabcdefghijklmnopqrstuvwxyz012345",
     "url": "https://www.youtube.com/playlist?list=PLabcdefghijklmnopqrstuvwxyz012345",
     "title": "A bakery bookkeeping playlist"},
    {"_type": "url", "ie_key": "Youtube", "id": "shortsvid01",
     "url": "https://www.youtube.com/shorts/shortsvid01", "title": "A short", "duration": 50},
]


def fake_search(cmd, timeout=120):
    return subprocess.CompletedProcess(cmd, 0, "\n".join(json.dumps(d) for d in LISTING), "")


ww.run_yt_dlp = fake_search
captured = io.StringIO()
real_stdout, sys.stdout = sys.stdout, captured
try:
    videos = ww.search_videos("bakery bookkeeping bank export", count=10)
finally:
    sys.stdout = real_stdout
ids = [v["id"] for v in videos]
check(ids == ["MaraVid0001", "shortsvid01"],
      f"the two videos come back, the channel and the playlist do not ({ids})")
check("BAKERYBOOKS4wd" in captured.getvalue() and "not a video" in captured.getvalue(),
      f"and the drop is said, by title: {captured.getvalue().strip()[:140]}")
check(ww.is_video_entry({"id": "MaraVid0001", "url": "https://www.youtube.com/watch?v=MaraVid0001"}),
      "CONTROL: an ordinary video entry is a video")

# ==========================================================================================
print("\n=== B. a fetch that fails for one video is that video's failure ===")
triaged = []


def stub_triage(context_text, channel, details, model, sells_something=None):
    triaged.append((details["title"], "transcript" in details))
    if "transcript" in details:
        return "NO", "a real talk, but nothing to copy", 95
    return "MAYBE", "the listing says little", 10


def fake_fetch(url, plan=None):
    vid = ww.video_id_from_url(url)
    if vid == "timeout0001":
        raise RuntimeError("yt-dlp timed out after 180s and was killed: --dump-json "
                           "https://www.youtube.com/channel/UCmaraExampleChannel0001")
    if vid == "oddfail0001":
        raise RuntimeError("yt-dlp returned something nobody here recognises")
    return {"id": vid, "title": "A real talk", "uploader": "U", "url": url,
            "transcript": "a real talk about pricing a bakery's bookkeeping " * 400,
            "raw_path": ww.RAW_DIR / f"{vid}.txt", "lang": "en", "track_kind": "audio",
            "audio_stats": {"audio_seconds": 900.0, "model": "stub", "device": "cpu",
                            "transcribe_seconds": 1.0}}, None


ww.triage_video = stub_triage
ww.fetch_transcript_by_plan = fake_fetch
ww.local_most_useful = lambda *a, **k: "nothing"
TWO = [{"id": "timeout0001", "title": "The one that times out", "channel": "C",
        "url": "https://www.youtube.com/watch?v=timeout0001", "description": "",
        "duration": 900, "upload_date": "20260924"},
       {"id": "realtalk001", "title": "A real talk", "channel": "C",
        "url": "https://www.youtube.com/watch?v=realtalk001", "description": "",
        "duration": 900, "upload_date": "20260924"}]
seen = {}
raised = None
try:
    ww.process_candidates(TWO, "CONTEXT", "model", False, seen, budget={"remaining": 3},
                          source="topics")
except Exception as e:  # noqa: BLE001 - the defect is exactly an exception escaping here
    raised = e
check(raised is None, f"the run goes on past the failure ({raised!r})")
records = ww.load_records()
bad = records.get("timeout0001", {})
verdict, text, tags = ww.final_verdict(bad) if bad.get("reason_code") in ww.REASON_CODES \
    else (None, "", frozenset())
# 2026-09-26 night, brief part 2.5, named: these two assertions encoded the rule that a fetch
# that times out is done and NOT READ. A timeout is the network's failure, not the video's, so
# the video now waits in seen.json for another try, at most three in all; the channel entry that
# caused 21 and 24 September's timeouts never reaches the fetch any more (section A).
check(bad.get("summary_state") == "WAITING" and bad.get("reason_code") == "fetch_failed"
      and "network" in text,
      f"the timed-out video waits for another try, with why in its row ({verdict}: {text[:100]})")
check(seen.get("timeout0001", {}).get("verdict") == "pending",
      "and it is queued in seen.json, not marked done")
seen_odd = {}
ww.process_candidates([dict(TWO[0], id="oddfail0001", url="https://www.youtube.com/watch?v=oddfail0001")],
                      "CONTEXT", "model", False, seen_odd, budget={"remaining": 3}, source="topics")
odd = ww.load_records().get("oddfail0001", {})
check(odd.get("summary_state") == "NOT_READ" and odd.get("reason_code") == "transcribe_failed"
      and "oddfail0001" in seen_odd and seen_odd["oddfail0001"].get("verdict") != "pending",
      "CONTROL: an error nobody recognises is still the video's own: NOT READ, done")
good = records.get("realtalk001", {})
check(good.get("read_verdict") == "NO" and ("A real talk", True) in triaged,
      f"and the next video is still fetched and read ({good.get('read_verdict')})")

print("\n  CONTROL: a refusal still stops the run, as it always has")


def refused(url, plan=None):
    raise ww.RateLimitError("HTTP Error 429: Too Many Requests")


ww.fetch_transcript_by_plan = refused
fetched_after = []
ww.save_transcript = lambda *a, **k: fetched_after.append(1) or {"minutes": 1, "words": 1}
rate_limited = ww.process_candidates(
    [dict(TWO[0], id="refused0001", url="https://www.youtube.com/watch?v=refused0001"),
     dict(TWO[1], id="notreached1", url="https://www.youtube.com/watch?v=notreached1")],
    "CONTEXT", "model", False, {}, budget={"remaining": 3}, source="topics")[4]
check(rate_limited and not fetched_after and
      ww.load_records().get("refused0001", {}).get("reason_code") == "refused_429",
      "a 429 is still a refusal of the address that ends the slot, not a failed video")

# ==========================================================================================
print("\n=== C. the line under the table says what waiting means ===")
ww.update_record("waiting0001", url="https://www.youtube.com/watch?v=waiting0001",
                 title="A video that waits", summary_state="WAITING", reason_code="rolled",
                 state_reason="rolled to the next run; nothing spent on it")
under = "\n".join(ww.day_table_lines([ww.load_records()["waiting0001"]]))
check("Rolled to the next run" not in under,
      "it no longer promises the next run, which on this week's evidence is not true")
check("only if" in under and "returns it again" in under and "A video that waits" in under,
      f"it says when one is read: {under.strip()[:160]}")

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
