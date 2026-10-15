"""FIX 4, 2026-09-22. No speech, no write-up.

On 2026-09-22 three music-only videos came out of Whisper as no text at all (rightly NOT READ),
as "Oh", and as "Subtitles by the Amara.org community", a line Whisper is known to make up on
silence. The last two went through the transcript triage and a Sonnet write-up of nothing, and
were counted as read and skipped.

The threshold is the data's, not a guess. Over every transcript on disk with a recorded length,
five sat between 0.0 and 4.4 words a minute, the next was 101.9, and nothing lay between. So a
transcript under 20 words a minute of audio is no speech, and so is one whose whole text is one
line from a short list of known Whisper phrases.

The transcripts and the request log this suite used to read were the owner's own. Small
invented ones stand in, written into the sandbox: the five kinds of junk at the lengths that
were measured, and a transcript of real speech at the slowest rate that was measured. No YouTube
request, no model call: the fetch, the triage and the CLI are all stubbed, and spies raise if a
no-speech video reaches either.
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "speech")
from worthwatch import scoring  # noqa: E402

ww.load_context = lambda: "CONTEXT for the test."
ww.ensure_local_model = lambda *a, **k: None
ww.slot_plan = lambda: {"primary": "audio", "fallback": None, "why": ""}
ww._RUN_CLOCK["deadline"] = None

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
    if not cond:
        fails.append(msg)


def reset():
    for pattern in ("*.json", "*.jsonl"):
        for f in ww.DATA_DIR.glob(pattern):
            f.unlink()
    for d in (ww.RAW_DIR, ww.TRANSCRIPTS_DIR, ww.OUTPUT_DIR):
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    f.unlink()


check_fn = getattr(ww, "speech_check", None)
check(check_fn is not None, "there is one check that says whether a transcript holds speech")


def verdict_of(text, seconds):
    if check_fn is None:
        return None, None
    return check_fn(text, None if seconds is None else seconds / 60.0)


# --- the invented transcripts, written where the tool keeps them ---------------------------
JUNK = [("MaraJunk001", "Oh", 20.1),
        ("MaraJunk002", "Subtitles by the Amara.org community", 168.5),
        ("MaraJunk003", ".", 83.7),
        ("MaraJunk004", ".", 82.8),
        ("MaraJunk005", "Thank you for watching.", 54.9)]
# Real speech at the slowest rate measured: 1,928 words in 1,135.6 s, 101.9 a minute.
_TALK = ("We open at seven, and the first hour is mostly regulars who want the same loaf every "
         "day. I keep the books at the counter between customers, one line per sale, and at "
         "the end of the week I read the totals out loud to my partner. ").split()
SLOW_SPEECH = ("MaraSlow001", " ".join((_TALK * (1928 // len(_TALK) + 1))[:1928]), 1135.6)


def write_transcripts():
    for vid, text, _seconds in JUNK + [SLOW_SPEECH]:
        (ww.RAW_DIR / f"{vid}.txt").write_text(text + "\n", encoding="utf-8")


write_transcripts()

# ==========================================================================================
print("\n=== A. the five junk transcripts, at their measured lengths, are no speech ===")
for vid, text, seconds in JUNK:
    on_disk = (ww.RAW_DIR / f"{vid}.txt").read_text(encoding="utf-8").strip()
    check(on_disk == text, f"{vid}: the fixture is the transcript on disk, {on_disk!r}")
    kind, words = verdict_of(text, seconds)
    check(kind is not None, f"{vid} ({text!r}, {seconds} s): no speech ({kind}, {words} words)")
check(verdict_of(".", 83.7)[1] == 0, "a full stop is not a word: '.' is zero words")

print("\n  and the slowest real speech is speech")
slowest = (ww.RAW_DIR / f"{SLOW_SPEECH[0]}.txt").read_text(encoding="utf-8")
kind, words = verdict_of(slowest, 1135.6)
check(kind is None and words == 1928,
      f"CONTROL: {SLOW_SPEECH[0]}, 1,928 words in 1,135.6 s (101.9 a minute), is speech ({kind})")

print("\n  the threshold sits inside the measured gap")
wpm = getattr(ww, "NO_SPEECH_WPM", None)
check(wpm is not None and 4.4 < wpm < 101.9,
      f"NO_SPEECH_WPM = {wpm}, between the fastest junk (4.4) and the slowest speech (101.9)")

print("\n  the known phrases are matched only when they are the whole transcript")
kind, _w = verdict_of("Thank you for watching.", 10.0)
check(kind == "phrase",
      f"the phrase in a 10 s clip, 24 words a minute, is still no speech ({kind})")
kind, _w = verdict_of("Subtitles by the Amara.org community", None)
check(kind == "phrase", f"and with no recorded length it is still that phrase ({kind})")
kind, _w = verdict_of("In this video I set the whole thing up from scratch, step by step. "
                      "Thank you for watching.", 12.0)
check(kind is None, "CONTROL: a transcript that merely ends with the phrase is speech")
kind, _w = verdict_of("A short spoken line about the product.", None)
check(kind is None, "CONTROL: with no recorded length a real transcript is never condemned")
kind, words = verdict_of("", 55.0)
check(kind == "no_text" and words == 0, f"no text at all is no speech too ({kind})")

# ==========================================================================================
print("\n=== B. THE DEFECT. a no-speech video gets no triage call and no write-up ===")
reset()
triage_calls, cli_calls = [], []


def stub_triage(context_text, channel, details, model, sells_something=None):
    triage_calls.append("transcript" in details)
    return "MAYBE", "Title promises a launch but description offers no mechanism to copy", 10


def stub_cli(prompt, timeout=600):
    cli_calls.append(prompt)
    return "## 6. What applies to me\n\nnothing\n"


def fake_fetch_oh(url, plan=None):
    return {"id": "MaraMusic01", "title": "Our bakery's opening day, set to music",
            "uploader": "Invented Bakery", "url": url, "transcript": "Oh",
            "raw_path": ww.RAW_DIR / "MaraMusic01.txt", "lang": "en", "track_kind": "audio",
            "audio_stats": {"audio_seconds": 20.1, "model": "large-v3", "device": "cuda",
                            "transcribe_seconds": 0.7}}, None


ww.triage_video = stub_triage
ww.claude_generate = stub_cli
ww.summarize_local = lambda *a, **k: (_ for _ in ()).throw(AssertionError("local summary"))
ww.fetch_transcript_by_plan = fake_fetch_oh
MUSIC_VIDEO = [{"id": "MaraMusic01", "title": "Our bakery's opening day, set to music",
                "channel": "https://www.youtube.com/@invented-bakery",
                "url": "https://www.youtube.com/watch?v=MaraMusic01", "description": "",
                "duration": None, "upload_date": "20260922"}]
seen = {}
ww.process_candidates(MUSIC_VIDEO, "CONTEXT", "qwen3.5:9b", False, seen,
                      budget={"remaining": 1}, source="channels")
check(triage_calls == [False],
      f"the listing was triaged and the transcript was not ({triage_calls})")
check(not cli_calls, "no summary was asked for")
record = ww.load_records().get("MaraMusic01", {})
check(record.get("summary_state") == "NOT_READ" and record.get("reason_code") == "no_speech",
      f"the record is NOT_READ, no_speech ({record.get('summary_state')}, "
      f"{record.get('reason_code')})")
verdict, state_text, tags = ww.final_verdict(record) if record else (None, "", frozenset())
check(f"{verdict} ({state_text})" == "NOT READ (no speech: 1 word in 0.3 min of audio)",
      f"its row reads: {verdict} ({state_text})")
check("not_read" in tags and "row" in tags and "header" not in tags,
      f"it counts as not read, with a row and no place in the header ({sorted(tags)})")
check(ww.day_counts([record])["not_read"] == 1 if record else False, "in the day's counts too")
check("MaraMusic01" in seen, "it is done, so no later run fetches it again")
check(ww.retry_queue() == [], "and it is not a failure to retry")
history = [json.loads(l) for l in ww.HISTORY_PATH.read_text(encoding="utf-8").splitlines()
           if l.strip()] if ww.HISTORY_PATH.exists() else []
check(history and "no speech" in history[-1]["reason"] and not history[-1].get("summary"),
      "history.jsonl says why, and holds no summary")

# ==========================================================================================
print("\n=== C. 'transcribed but produced no text' is the same state ===")
reset()
triage_calls.clear()
cli_calls.clear()
ww.fetch_transcript_by_plan = lambda url, plan=None: (
    None, "audio: the audio was transcribed but produced no text (0.9 min of audio)")
NIGHT_BAKE = [dict(MUSIC_VIDEO[0], id="MaraMusic02",
                   title="Timelapse: a whole night of baking, no talking",
                   url="https://www.youtube.com/watch?v=MaraMusic02")]
ww.process_candidates(NIGHT_BAKE, "CONTEXT", "qwen3.5:9b", False, {},
                      budget={"remaining": 1}, source="channels")
record = ww.load_records().get("MaraMusic02", {})
verdict, state_text, tags = ww.final_verdict(record) if record else (None, "", frozenset())
check(f"{verdict} ({state_text})" == "NOT READ (no speech: 0 words in 0.9 min of audio)",
      f"its row reads: {verdict} ({state_text})")
check(record.get("no_speech") == "no_text" and triage_calls == [False] and not cli_calls,
      "one state for both, and nothing spent after the listing")

print("\n  the audio path puts the length in that error, so the row can say it")
# The real audio fetch, which ships switched off; switched on here as a user would.
ww.AUDIO_PATH_ENABLED = True
ww.extract_media_info = lambda url: ({"id": "MaraMusic02", "title": "t", "uploader": "u"}, None)
ww.choose_audio_format = lambda info: ({"format_id": "91", "audio_only": False, "height": 144,
                                         "ext": "mp4", "acodec": "mp4a.40.5", "abr": 115.1,
                                         "tbr": None, "size": 800000, "size_basis": "from bitrate",
                                         "protocol": "m3u8_native", "language": "en",
                                         "original_score": 5, "dubs_rejected": 0}, None)
ww.wait_for_fetch_slot = lambda path="captions": None
ww.download_audio_from_info = lambda vid, fid: None
ww.note_fetch = lambda path="captions": None
ww.downloaded_audio_path = lambda vid: TMP / "fake-audio.mp4"
(TMP / "fake-audio.mp4").write_bytes(b"not really audio")
ww.transcribe_audio = lambda path, model_name=None: ("", {"audio_seconds": 55.6,
                                                          "transcribe_seconds": 0.4})
ww.delete_audio = lambda vid: 0
meta, err = ww.fetch_transcript_via_audio("https://www.youtube.com/watch?v=MaraMusic02")
check(meta is None and err and "produced no text" in err and "(0.9 min of audio)" in err,
      f"the error carries the audio's length: {err!r}")
(TMP / "fake-audio.mp4").unlink(missing_ok=True)

print("\n  a record written before this fix reads the same, once it carries the field")
legacy = {"id": "MaraLegacy1", "summary_state": "NOT_READ", "reason_code": "no_transcript",
          "state_reason": "audio: the audio was transcribed but produced no text",
          "no_speech": "no_text", "words": 0, "listing_verdict": "MAYBE"}
verdict, state_text, tags = ww.final_verdict(legacy)
check(f"{verdict} ({state_text})" ==
      "NOT READ (no speech: 0 words; the length of the audio was not recorded)",
      f"with no length recorded it says so rather than guess: {verdict} ({state_text})")

# ==========================================================================================
print("\n=== D. the two junk write-ups of 2026-09-22 read NOT READ, and are not 'read' ===")
CLIP = {"id": "MaraMusic03", "summary_state": "WRITTEN", "reason_code": "written",
        "listing_verdict": "MAYBE", "read_verdict": "MAYBE", "settled_verdict": "MAYBE",
        "applies": "nothing", "project": None, "words": 5, "minutes": 2.8, "no_speech": "rate"}
verdict, state_text, tags = ww.final_verdict(CLIP)
check(f"{verdict} ({state_text})" == "NOT READ (no speech: 5 words in 2.8 min of audio)",
      f"a music-only clip that was written up: {verdict} ({state_text})")
check("not_read" in tags and "read_and_skipped" not in tags,
      "counted not read, where it was read and skipped")
check(ww.final_verdict(dict(CLIP, no_speech=None))[0] == "MAYBE",
      "CONTROL: without the field the same record is the MAYBE it was")
counts = ww.first_guess_counts([CLIP])
check(counts["read"] == 0 and counts["never_read"] == 1,
      "and the first-guess footer does not count it as a video that was read")

# ==========================================================================================
print("\n=== E. the retry path checks before it summarises ===")
reset()
cli_calls.clear()
(ww.RAW_DIR / "MaraMusic03.txt").write_text("Subtitles by the Amara.org community",
                                            encoding="utf-8")
ww.update_record("MaraMusic03", url="https://www.youtube.com/watch?v=MaraMusic03",
                 title="A music-only clip about sourdough", channel="c", read_verdict="MAYBE",
                 summary_state="READ_NOT_WRITTEN", reason_code="summary_timeout",
                 state_reason="summary timed out after 600 s", attempts=1, minutes=2.8)
ww.run_retry_phase("CONTEXT", "qwen3.5:9b", local_only=False)
record = ww.load_records()["MaraMusic03"]
check(not cli_calls and record.get("reason_code") == "no_speech",
      f"no summary, and the record says why ({record.get('reason_code')})")
check(ww.retry_queue() == [], "and it leaves the retry queue for good")

# ==========================================================================================
print("\n=== F. the labels' rules score sees it ===")
# This read the owner's own golden set, transcripts and request log through the old rescore
# script. The scorer is src/worthwatch/scoring.py now, and a small invented labels file stands
# in: the two "." videos as the tool once wrote them up (MAYBE, section 6 nothing), their
# transcripts in raw/, and the request log's measured lengths.
reset()
write_transcripts()
JUNK_WRITEUP = "## 6. What applies to me\n\nnothing\n\n## 8. Try this week\n\nnothing\n"
labels = {"source": ["DIGEST-2026-09-22.md"], "videos": []}
with open(ww.HISTORY_PATH, "w", encoding="utf-8") as f:
    for vid, _text, seconds in JUNK[2:4]:
        url = f"https://www.youtube.com/watch?v={vid}"
        f.write(json.dumps({"date": "2026-09-22", "verdict": "MAYBE", "channel": "c",
                            "title": f"music only, {vid}", "url": url, "reason": "r",
                            "summary": JUNK_WRITEUP}) + "\n")
        labels["videos"].append({"id": vid, "date": "2026-09-22", "channel": "c",
                                 "title": f"music only, {vid}", "url": url,
                                 "tool_verdict": "MAYBE", "listing_verdict": "MAYBE",
                                 "tool_reason": "r", "applies": "nothing", "has_summary": True,
                                 "your_verdict": "SKIP"})
ww.REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
ww.REQUEST_LOG_PATH.write_text("".join(json.dumps({
    "ts": "2026-09-22T10:00:00", "kind": "audio_transcribe", "endpoint": "local/faster-whisper",
    "status": "ok", "video_id": vid, "audio_seconds": seconds}) + "\n"
    for vid, _text, seconds in JUNK[2:4]), encoding="utf-8")
ww.LABELS_PATH.write_text(json.dumps(labels, indent=2), encoding="utf-8")
golden = scoring.load_labels(ww.LABELS_PATH)
history = scoring.history_by_id(ww.HISTORY_PATH)
record, _notes = scoring.labelled_record(ww, golden["MaraJunk004"], history)
check(ww.final_verdict(record)[0] == "NOT READ",
      f"MaraJunk004 ('.', 82.8 s) is NOT READ in the rules row, where it was MAYBE "
      f"({ww.final_verdict(record)[0]})")
record, _notes = scoring.labelled_record(ww, golden["MaraJunk003"], history)
check("no speech" in ww.final_verdict(record)[1],
      f"MaraJunk003 says why it was not read: {ww.final_verdict(record)[1]!r}")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
