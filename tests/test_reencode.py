"""FIX 2, 2026-09-25. Long videos are read: the size check is on 16 kHz mono audio.

Eight videos went NOT READ in one week because the smallest format carrying their audio was
over the 63 MB ceiling. They ran 48 to 109 minutes, and the smallest format was a muxed 144p
stream at 111 to 196 kbps, so the ceiling was a limit on length that moved with the video's
bitrate. The downloaded stream is now re-encoded to 16 kHz mono Opus at a constant 32 kbps
before the ceiling is checked, which is what Whisper reads anyway. The eight videos below are
invented, with the same lengths and bitrates as that week's.

Every audio file here is generated in this test with PyAV. Nothing is downloaded, no YouTube
request is made, Whisper is never loaded: the download and the transcriber are stubbed, and a
spy fails the test if the download runs when it must not. Without PyAV, sections A and C cannot
run: one check says so and fails, and sections B, D and E, which need no audio, still run.
Everything runs in a fresh folder under the OS temp folder.
"""
import importlib.util
import io
import json
import shutil
import struct
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "reencode")
for d in (ww.RAW_DIR, ww.INFO_CACHE_DIR, ww.AUDIO_DIR):
    d.mkdir(parents=True, exist_ok=True)
ww.wait_for_fetch_slot = lambda path="captions": None
ww.note_fetch = lambda path="captions": None
ww.AUDIO_PATH_ENABLED = True
HAVE_AV = importlib.util.find_spec("av") is not None

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def generate(path, seconds):
    """Stereo 44.1 kHz AAC in an MP4 container, the shape of the stream the audio path
    downloads, holding a slow sweep and a little noise."""
    import av
    import numpy as np
    rate, block = 44100, 1024
    rng = np.random.default_rng(0)
    with av.open(str(path), "w", format="mp4") as out:
        stream = out.add_stream("aac", rate=rate, layout="stereo")
        stream.bit_rate = 128000
        n = 0
        while n < rate * seconds:
            k = np.arange(n, n + block)
            tone = 0.3 * np.sin(2 * np.pi * (200 + 600 * k / (rate * seconds)) * k / rate)
            data = np.stack([tone, 0.8 * tone]) + 0.01 * rng.standard_normal((2, block))
            frame = av.AudioFrame.from_ndarray(data.astype(np.float32), format="fltp",
                                               layout="stereo")
            frame.sample_rate = rate
            frame.pts = n
            for packet in stream.encode(frame):
                out.mux(packet)
            n += block
        for packet in stream.encode(None):
            out.mux(packet)


def opus_head(path):
    """(channels, input sample rate) from the Ogg Opus header, which is where an Opus file
    says what rate it was made from: a decoder always hands Opus back at 48 kHz."""
    raw = Path(path).read_bytes()
    at = raw.find(b"OpusHead")
    if at < 0:
        return None, None
    _version, channels, _preskip, rate = struct.unpack("<BBHI", raw[at + 8:at + 16])
    return channels, rate


SECONDS = 60
SOURCE = TMP / "generated.m4a"
check(HAVE_AV, "PyAV is installed, so a stream can be generated and really re-encoded "
               "(sections A and C need it)")
if HAVE_AV:
    generate(SOURCE, SECONDS)

# ==========================================================================================
print("=== A. a generated stream is re-encoded to 16 kHz mono, and the download is gone ===")
reencode = getattr(ww, "reencode_audio", None)
check(reencode is not None, "one function re-encodes the downloaded audio")
if reencode and HAVE_AV:
    work = ww.AUDIO_DIR / "genvideo001.mp4"
    shutil.copy(SOURCE, work)
    out, stats = reencode(work)
    channels, rate = opus_head(out)
    check(Path(out).suffix == ".ogg" and channels == 1 and rate == 16000,
          f"Opus in Ogg, one channel, made from 16 kHz ({Path(out).name}, {channels}, {rate})")
    import av
    with av.open(str(out)) as f:
        seconds = float(f.duration or 0) / 1e6
    check(abs(seconds - SECONDS) < 0.5, f"the same length, {seconds:.2f} s of {SECONDS}")
    size = Path(out).stat().st_size
    per_second = size / SECONDS
    check(3900 <= per_second <= 4300,
          f"a constant 32 kbps plus the container, {per_second:.1f} bytes a second")
    check(not work.exists(), "and the downloaded stream is deleted as soon as it is re-encoded")
    check(stats.get("bytes") == size and stats.get("source_bytes") == SOURCE.stat().st_size,
          f"the stats say both sizes: {stats}")
    ww.delete_audio("genvideo001")

# ==========================================================================================
print("\n=== B. a long video: 97.0 MB of muxed stream for 75.7 minutes is not refused ===")
LONG = {"id": "MaraLong006", "title": "Pricing a Bakery App: The First Year in Numbers",
        "uploader": "Small Shop Stories", "duration": 4540,
        "formats": [{"format_id": "91-6", "ext": "mp4", "protocol": "m3u8_native",
                     "vcodec": "avc1.4D400C", "acodec": "mp4a.40.5", "tbr": 171.0,
                     "height": 128, "language": "en", "language_preference": 10,
                     "format": "91-6 - 128p (original)",
                     "url": "https://manifest.googlevideo.com/x.m3u8"}]}
best, err = ww.choose_audio_format(LONG)
check(best is not None and err is None,
      f"a format the old ceiling would have refused is chosen now ({err})")
check(best and abs(best["size"] / 1e6 - 97.0) < 0.1,
      f"and it is still the 97.0 MB stream: the download is what it was "
      f"({best['size'] / 1e6 if best else 0:.1f} MB)")

# ==========================================================================================
print("\n=== C. through the real audio path, with the download and Whisper stubbed ===")
downloads, handed = [], []


def fake_extract(url):
    info = dict(LONG, id=ww.video_id_from_url(url))
    ww.INFO_CACHE_DIR.mkdir(exist_ok=True)
    ww.media_info_cache_path(info["id"]).write_text(json.dumps(info), encoding="utf-8")
    return info, None


def fake_download(video_id, format_id):
    downloads.append((video_id, format_id))
    if SOURCE.exists():
        shutil.copy(SOURCE, ww.AUDIO_DIR / f"{video_id}.mp4")


def fake_transcribe(path, model_name=None, **kw):
    handed.append((Path(path).name, sorted(p.name for p in ww.AUDIO_DIR.glob("*"))))
    return "word " * (SECONDS * 3), {"model": "stub", "device": "cpu", "compute_type": "none",
                                     "beam_size": 1, "batch_size": 0, "vad": True,
                                     "audio_seconds": float(SECONDS), "transcribe_seconds": 1.0,
                                     "realtime_factor": 60.0, "segments": 1, "chars": 1,
                                     "repeats_collapsed": 0}


ww.extract_media_info = fake_extract
ww.download_audio_from_info = fake_download
ww.transcribe_audio = fake_transcribe
if HAVE_AV:
    meta, err = ww.fetch_transcript_via_audio("https://www.youtube.com/watch?v=MaraLong006")
    check(meta is not None and err is None, f"the video is read: {err}")
    check(handed and handed[0][0] == "MaraLong006.16k.ogg",
          f"Whisper is handed the re-encoded file ({handed[0][0] if handed else None})")
    check(handed and handed[0][1] == ["MaraLong006.16k.ogg"],
          f"and the downloaded stream is already gone when it reads it "
          f"({handed[0][1] if handed else None})")
    check(not list(ww.AUDIO_DIR.glob("*")), "and nothing of the audio is left afterwards")
    logged = [json.loads(l) for l in ww.REQUEST_LOG_PATH.read_text(encoding="utf-8").splitlines()
              if l.strip()] if ww.REQUEST_LOG_PATH.exists() else []
    entry = next((l for l in logged if l.get("kind") == "audio_transcribe"), {})
    check(entry.get("reencoded_bytes") and entry.get("audio_bytes") > entry["reencoded_bytes"],
          f"the request log keeps both sizes: {entry.get('audio_bytes')} downloaded, "
          f"{entry.get('reencoded_bytes')} read")

# ==========================================================================================
print("\n=== D. what is still refused, and refused before any download ===")
downloads.clear()
six_hours = dict(LONG, duration=6 * 3600)
ww.extract_media_info = lambda url: (dict(six_hours, id=ww.video_id_from_url(url)), None)
meta, err = ww.fetch_transcript_via_audio("https://www.youtube.com/watch?v=sixhours001")
check(meta is None and not downloads,
      f"six hours of talk is over the ceiling even at 16 kHz mono, and nothing is downloaded")
check(ww.ceiling_reason(err or "") is not None,
      f"and the row can still say why in its own words: {ww.ceiling_reason(err or '')}")
huge = dict(LONG, formats=[dict(LONG["formats"][0], tbr=4000.0)])
ww.extract_media_info = lambda url: (dict(huge, id=ww.video_id_from_url(url)), None)
meta, err = ww.fetch_transcript_via_audio("https://www.youtube.com/watch?v=hugestream1")
check(meta is None and not downloads and "AUDIO_DOWNLOAD_MAX_BYTES" in (err or ""),
      f"a stream too big to download is refused before the download: {err}")
normal = dict(LONG, duration=600, formats=[dict(LONG["formats"][0], tbr=114.0)])
best, err = ww.choose_audio_format(normal)
check(best is not None and err is None,
      "CONTROL: a ten-minute video at 114 kbps passes both checks, so neither is vacuous")

# ==========================================================================================
print("\n=== E. that week's eight refusals, their new size from their duration ===")
WEEK = [("MaraLong001", 4820, 66.9), ("MaraLong002", 3620, 79.2), ("MaraLong003", 6530, 122.4),
        ("MaraLong004", 4470, 80.5), ("MaraLong005", 2890, 65.7), ("MaraLong006", 4540, 97.0),
        ("MaraLong007", 3040, 74.5), ("MaraLong008", 3220, 75.7)]
size_of = getattr(ww, "reencoded_size", None)
check(size_of is not None, "one function says how big the re-encoded audio will be")
if size_of:
    for vid, seconds, old_mb in WEEK:
        new = size_of(seconds)
        print(f"    {vid}  {seconds / 60:5.1f} min  {old_mb:6.1f} MB downloaded  ->  "
              f"{new / 1e6:5.1f} MB read")
        check(new < ww.AUDIO_MAX_BYTES, f"{vid}: under the {ww.AUDIO_MAX_BYTES / 1e6:.0f} MB "
                                        f"ceiling at {new / 1e6:.1f} MB")

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
