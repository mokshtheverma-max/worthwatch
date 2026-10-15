import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__
from . import settings as _settings

# --- where things live ----------------------------------------------------------------------
# Three folders, each a setting (settings.resolve_dirs): CONFIG_DIR holds what you write
# (settings.toml, rules.toml, context.md, channels.txt, topics.txt), DATA_DIR what the tool keeps
# (state, cache, transcripts, the request log) and OUTPUT_DIR what you read (digests, write-ups,
# the idea board). apply_paths() points every path below at them; the command line and the MCP
# server call it when they start, and every test suite calls it with a temporary folder.
_CONFIG_FILES = {"CONTEXT_PATH": "context.md", "CHANNELS_PATH": "channels.txt",
                 "TOPICS_PATH": "topics.txt", "SETTINGS_PATH": "settings.toml",
                 "RULES_PATH": "rules.toml", "LABELS_PATH": "labels.json"}
_DATA_FILES = {"RAW_DIR": "raw", "SEEN_PATH": "seen.json", "HISTORY_PATH": "history.jsonl",
               "RECORDS_PATH": "records.json", "TRANSCRIPTS_DIR": "transcripts",
               "LAST_RUN_PATH": "last-run.json", "JEV_SPEND_PATH": "jev-spend.json",
               "IDEAS_PATH": "ideas.json", "BACKLOG_PATH": "backlog.json",
               "ROTATION_PATH": "rotation.json", "RATE_STATE_PATH": "rate-state.json",
               "PAUSE_PATH": "PAUSED", "LOCK_PATH": ".lock",
               "REQUEST_LOG_PATH": "logs/requests.jsonl", "LOGS_DIR": "logs",
               "INFO_CACHE_DIR": "cache/info", "AUDIO_DIR": "cache/audio",
               "WHISPER_MODEL_DIR": "cache/whisper"}
_OUTPUT_FILES = {"INDEX_PATH": "INDEX.md", "TRYNEXT_PATH": "TRY-NEXT.md",
                 "CANDIDATES_PATH": "CHANNEL-CANDIDATES.md"}


def apply_paths(config_dir, data_dir, output_dir):
    """Point every path this tool reads or writes at the three folders."""
    g = globals()
    g["CONFIG_DIR"], g["DATA_DIR"], g["OUTPUT_DIR"] = (Path(config_dir), Path(data_dir),
                                                        Path(output_dir))
    for table, base in ((_CONFIG_FILES, g["CONFIG_DIR"]), (_DATA_FILES, g["DATA_DIR"]),
                        (_OUTPUT_FILES, g["OUTPUT_DIR"])):
        for name, rel in table.items():
            g[name] = base / rel


# The defaults, before configure() reads the settings. apply_paths() re-points every one; they
# are written out here so a reader can see each path and what it is for.
CONFIG_DIR, DATA_DIR, OUTPUT_DIR = _settings.resolve_dirs()
CONTEXT_PATH = CONFIG_DIR / "context.md"       # your context file: every verdict is judged against it
CHANNELS_PATH = CONFIG_DIR / "channels.txt"
TOPICS_PATH = CONFIG_DIR / "topics.txt"
SETTINGS_PATH = CONFIG_DIR / "settings.toml"
RULES_PATH = CONFIG_DIR / "rules.toml"
LABELS_PATH = CONFIG_DIR / "labels.json"       # your own labels, for `worthwatch score`
RAW_DIR = DATA_DIR / "raw"
SEEN_PATH = DATA_DIR / "seen.json"
HISTORY_PATH = DATA_DIR / "history.jsonl"
RECORDS_PATH = DATA_DIR / "records.json"       # one record per video: see "the record store"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"     # the retry path's own copy of every transcript
LAST_RUN_PATH = DATA_DIR / "last-run.json"
JEV_SPEND_PATH = DATA_DIR / "jev-spend.json"   # what the optional Jev screen spent, by day
IDEAS_PATH = DATA_DIR / "ideas.json"           # the ideas lane's cards and remixes
BACKLOG_PATH = DATA_DIR / "backlog.json"
ROTATION_PATH = DATA_DIR / "rotation.json"
RATE_STATE_PATH = DATA_DIR / "rate-state.json"
PAUSE_PATH = DATA_DIR / "PAUSED"
LOCK_PATH = DATA_DIR / ".lock"
REQUEST_LOG_PATH = DATA_DIR / "logs" / "requests.jsonl"
LOGS_DIR = DATA_DIR / "logs"
INFO_CACHE_DIR = DATA_DIR / "cache" / "info"
AUDIO_DIR = DATA_DIR / "cache" / "audio"       # emptied after every transcription
WHISPER_MODEL_DIR = DATA_DIR / "cache" / "whisper"
INDEX_PATH = OUTPUT_DIR / "INDEX.md"
TRYNEXT_PATH = OUTPUT_DIR / "TRY-NEXT.md"
CANDIDATES_PATH = OUTPUT_DIR / "CHANNEL-CANDIDATES.md"

# Every file this tool keeps state in, by the name of its constant. A suite that runs the tool
# redirects all of these into its own sandbox through this one list, so a store added later
# cannot leak out of a suite written before it existed. Directories belong in it too.
STATE_PATHS = ("SEEN_PATH", "HISTORY_PATH", "RECORDS_PATH", "LAST_RUN_PATH",
               "BACKLOG_PATH", "RATE_STATE_PATH", "ROTATION_PATH", "LOCK_PATH",
               "REQUEST_LOG_PATH", "TRANSCRIPTS_DIR", "JEV_SPEND_PATH", "IDEAS_PATH")
LOCK_STALE_SECONDS = 3600
# yt-dlp is the installed Python package, run with the same Python as this tool. Command lists
# start with this name; run_yt_dlp() puts yt_dlp_argv() in its place at launch.
YT_DLP = "yt-dlp"


def yt_dlp_argv():
    return [sys.executable, "-m", "yt_dlp"]

# --- what every YouTube request looks like ------------------------------------------
# yt-dlp's own client, on purpose: no browser fingerprint, no account, no token provider and
# no player client it would not pick itself. When YouTube refuses this network the tool waits
# and says so; it never tries to get around a refusal.
# Manifests are separate requests (one each for DASH and HLS) made only to describe formats the
# caption path never downloads, so that path skips them: fewer requests, the same captions.
EXTRACTOR_SKIP = "dash,hls"
# yt-dlp needs a JavaScript runtime to solve the player's challenges. Its README recommends Deno,
# the only runtime it switches on by default, and worthwatch installs Deno through yt-dlp's own
# `deno` extra, into the scripts folder yt-dlp looks in first. So the default names nothing.
# settings.toml [youtube] js_runtime can name node, bun or quickjs instead.
JS_RUNTIME = ""


def js_runtime_flags(runtime=None):
    """The command-line flags for a JavaScript runtime other than yt-dlp's default, the way its
    README says: --no-js-runtimes first, or a lower-priority runtime is ignored while Deno is
    there."""
    runtime = (JS_RUNTIME if runtime is None else runtime or "").strip()
    if not runtime or runtime == "deno":
        return []
    return ["--no-js-runtimes", "--js-runtimes", runtime]

# --- rate-limit policy --------------------------------------------------------------
# Polite by default, and measured rather than guessed. Every request is logged with its type,
# endpoint, gap and status (logs/requests.jsonl in the data folder), a floor holds between any
# two requests across runs, the day's ceiling adapts to what happened the day before, and an
# HTTP 429 stops that path for at least an hour (longer if it keeps happening). The tool never
# answers a refusal by trying another way in: it waits, and says so.
MAX_CAPTION_FETCHES_PER_RUN = 3    # per-invocation budget; the rest rolls to the next run
# The day's caption ceiling is not a constant. It lives in rate-state.json as daily_cap and
# moves: a clean day (no 429, at least one caption fetched) raises the next day's cap by
# CAPTION_CAP_STEP; any 429 halves it on the spot. These four numbers bound it.
CAPTION_CAP_START = 8              # first day's cap, and what a missing rate-state.json means
CAPTION_CAP_MIN = 2                # a halving never goes below this
CAPTION_CAP_MAX = 30               # a raise never goes above this
CAPTION_CAP_STEP = 2               # how much a clean day earns
CAPTION_DELAY_SECONDS = 90.0       # minimum gap between caption downloads, ACROSS runs
# Gap between a video's extraction (the watch page, which is where the caption URL comes from)
# and the caption download itself: a pause, so two requests about one video never land a
# second apart.
CAPTION_SETTLE_SECONDS = 60.0
# A hard floor under EVERY YouTube request, kept in rate-state.json so it holds across
# runs, measured from the END of the last request, not the start.
MIN_REQUEST_GAP_SECONDS = 25.0
COOLDOWN_BASE_SECONDS = 3600       # after any 429: one hour of no fetching at all
COOLDOWN_MAX_SECONDS = 6 * 3600    # ceiling for repeat offences
STRIKE_DECAY_HOURS = 24            # a 429 older than this stops escalating the next one
# The canary, off by default: one request for a video that is already summarised, made before
# a run spends anything else, on the path that is about to lead the run. A refused canary takes
# that path out for the run and hands the run to the next one; "inconclusive" ends the run,
# because a guard that cannot measure must not pass. It costs a request, so it is for someone
# measuring whether their network is refused, and it rotates through videos this tool has
# already transcribed, least recently asked first, keeping a per-video ledger that says
# whether refusals follow the network or the video.
CANARY_ENABLED = False
CANARY_SEED_VIDEO_IDS = []         # videos to ask for before anything has been transcribed
CANARY_POOL_SIZE = 25              # how many recently transcribed videos the rotation walks
CANARY_LOG_KEEP = 60               # canary ledger entries kept in rate-state.json
CANARY_ATTRIBUTION_WINDOW_HOURS = 24
# How close in time a success has to be to a refusal before it counts as evidence that the
# refusal belongs to the video rather than to the network. Two videos minutes apart are a
# comparison; two videos hours apart are two different moments.
CANARY_CONTRAST_MINUTES = 90
CANARY_URL_MARGIN_SECONDS = 600    # treat a cached caption URL as expired this long before it is
# The paired probe: on a refusal, one further request for a DIFFERENT video, at once, to tell
# "this network is refused" from "this video is refused" at the same minute. It is off: a
# refusal means wait. Inside measuring_a_refusal() a 429 it meets is logged but adds no
# strike, because it measures the refusal that just happened.
CANARY_PROBE_ON_REFUSAL = False
# Whether the paired probe may extract a video whose cached caption URL has expired (two
# requests instead of one, plus the settle).
CANARY_PROBE_MAY_EXTRACT = True
# A caption track that is a machine translation from another language is a worse transcript.
# Off: such videos are recorded as "no usable captions" rather than fetched.
ALLOW_TRANSLATED_CAPTIONS = False
# Channel listings do not carry descriptions; the channel's public RSS feed does, for its
# last 15 uploads, in one request that touches neither the player nor the caption endpoint.
FEED_ENRICHMENT = True


# --- the audio path -------------------------------------------------------------------
# A transcript for a video with no captions: download the smallest format that carries the
# audio and transcribe it on this machine with faster-whisper. Downloading may break YouTube's
# terms, so it is off unless switched on (settings.toml [transcripts] audio = true), and it
# needs the optional worthwatch[whisper] extra.
AUDIO_PATH_ENABLED = False
AUDIO_TERMS_WARNING = ("Downloading a video's audio may break YouTube's terms of service. "
                       "worthwatch does it only because you switched it on ([transcripts] audio "
                       "= true in settings.toml); switch it off to use captions alone.")
WHISPER_EXTRA_FIX = "uv tool install --reinstall \"worthwatch[whisper]\""


def whisper_installed():
    """True when the optional transcriber, faster-whisper, can be imported. Imports nothing."""
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def audio_path_ready():
    """(ready, why not): the audio path is switched on and its transcriber is installed."""
    if not AUDIO_PATH_ENABLED:
        return False, "switched off ([transcripts] audio = true in settings.toml switches it on)"
    if not whisper_installed():
        return False, f"it needs the whisper extra: {WHISPER_EXTRA_FIX}"
    return True, ""
# The caption path skips dash and hls because they cost requests and carry no subtitles.
# The audio path needs formats, and on some videos they only exist in those manifests, so
# it skips nothing and lets the extraction list everything it can.
AUDIO_EXTRACTOR_SKIP = ""
AUDIO_MAX_BYTES = 60 * 1024 * 1024   # refuse audio larger than this, once it is 16 kHz mono
# The ceiling is checked on the audio Whisper reads, not on the stream that is downloaded: the
# smallest stream that carries the audio is often a muxed low-resolution video, so a ceiling
# on the download would be a limit on length that moved with the video's bitrate. The download
# is re-encoded to 16 kHz mono Opus at a constant 32 kbps first, with PyAV (no ffmpeg needed),
# which is what Whisper resamples to anyway: about 4,100 bytes a second, so 60 MiB is about four
# hours of talk.
AUDIO_REENCODE_RATE = 16000
AUDIO_REENCODE_BITRATE = 32000       # bits a second, constant (libopus "vbr" off)
AUDIO_REENCODE_BYTES_PER_SECOND = 4100   # measured on a generated file, rounded up
# What the download itself may be: only a guard against a video whose smallest stream is far
# bigger than a low-resolution stream of the longest video the listing lets through.
AUDIO_DOWNLOAD_MAX_BYTES = 150 * 1024 * 1024
# How many formats one fetch may download, one after another, before it gives the video up for
# this run. A format that brings nothing back falls through to the next; each download waits
# its own AUDIO_DELAY_SECONDS and counts as one audio fetch, like the first.
AUDIO_FORMAT_TRIES = 3
AUDIO_DELAY_SECONDS = 90.0           # minimum gap between media downloads, ACROSS runs
PATH_DELAY_SECONDS = {"captions": CAPTION_DELAY_SECONDS, "audio": AUDIO_DELAY_SECONDS}


# --- which path leads -------------------------------------------------------------------
# Two transcript paths, each with its own switch, cooldown, adaptive cap, gap and ledger: the
# caption path (YouTube's own captions) and the audio path (a download transcribed here). A
# refusal on one is a fact about that path only, so a cooldown on one never stops the other.
CAPTION_PATH_ENABLED = True
# The order the enabled paths are tried in: the first usable one leads the slot and is the
# one the canary probes, the next is its per-video fallback. Nothing here decides whether
# a path is on - CAPTION_PATH_ENABLED and AUDIO_PATH_ENABLED do that - only what comes
# first when both are. Captions first: they need no download and no transcriber.
PATH_PREFERENCE = ("captions", "audio")

# --- local transcription ---------------------------------------------------------------
# faster-whisper (CTranslate2), only with the optional worthwatch[whisper] extra. No ffmpeg
# binary is needed: PyAV decodes the m4a/webm yt-dlp downloads. The model is cached under
# cache/whisper and loaded once per process. large-v3 is the default because the thing this
# stands in for, YouTube's own captions, is good, and a worse transcript is not a fallback.
WHISPER_MODEL = "large-v3"
WHISPER_DEVICE = "cuda"
WHISPER_COMPUTE_TYPE = "float16"
WHISPER_BEAM_SIZE = 1              # greedy; beam 5 costs ~2x the time for a small WER gain
WHISPER_VAD = True                 # drop silence before decoding: fewer hallucinated lines
# faster-whisper's BatchedInferencePipeline decodes several VAD-split windows at once and
# is about three times faster. It is OFF, and that is a deliberate, measured choice.
#
# Measured 2026-09-06 on one 13.2-minute file, the same bytes through every configuration:
#   batch 16   2819 words   8.7s   87.6% agreement with the caption track
#   batch 8    2821 words  11.2s   87.7%
#   sequential 2956 words  44.6s   92.7%
# The gap is not noise and it is not phrasing: two contiguous blocks of about ninety words
# each - whole answers in an interview - are simply absent from both batched runs and
# present in both sequential ones. A transcript that quietly drops five percent of what
# was said is worse than a slow one, because nothing downstream can tell that it happened;
# the summary just never mentions it. Two batched runs of the same file also disagreed
# with each other by 188 words, which is its own reason not to trust it here.
#
# The price is 45 seconds instead of 9 per video. At two to eight videos a day that buys
# nothing worth having. Set it back to 16 if that ever stops being true.
WHISPER_BATCH_SIZE = 0
# Whisper decodes what it hears, and a product name it has never heard comes out as the
# nearest common word ("cloud code" for Claude Code). An initial_prompt is the documented way
# to put vocabulary in front of the decoder, so the words your videos use go in it: the
# [whisper] vocabulary setting, which apply_profile() puts here.
WHISPER_INITIAL_PROMPT = ""
# A decoder that loses its place can emit the same word tens of times. One such run showed
# up in the first three videos ("no" fourteen times). condition_on_previous_text=False
# already prevents the long-range version; this catches the within-segment one, and caps a
# repeat at three rather than deleting it, because people do say "no no no".
WHISPER_MAX_WORD_REPEAT = 3

PENDING_MAX_RETRIES = 3            # "captions listed but empty" retries before giving up
TOPICS_DAILY_CAP = 12              # new topics-sourced videos per calendar day, across all queries
MIN_BACKFILL_DATE = "20260101"     # nothing older than this ever enters the backlog
BACKFILL_FETCH_SIZE = 500          # how many of a channel's videos to list when checking its 2026 backlog
# The catch-up of older videos is capped to the last BACKLOG_MAX_AGE_DAYS rather than run to
# the end: the backlog phase only spends what a day leaves over, so a full catch-up across many
# channels is a job measured in years. Set to None for the full catch-up back to
# MIN_BACKFILL_DATE; nothing else needs editing either way.
BACKLOG_MAX_AGE_DAYS = 60
BACKLOG_MAX_STALLS = 3             # passes with no progress before a channel is called done anyway
# How far back to list when BACKLOG_MAX_AGE_DAYS is set. A listing is a single yt-dlp
# call, so MIN_REQUEST_GAP_SECONDS cannot break it up - it is the largest burst this tool
# emits. Asking for 500 entries when the window can only hold two months of uploads pays
# that burst in full for nothing, so a capped backlog asks for a capped listing.
BACKLOG_WINDOW_FETCH_SIZE = 120

WEEK_LOOKBACK_DAYS = 7


def opportunities_path():
    """OPPORTUNITIES.md in the output folder, resolved at call time rather than import time
    so a suite that redirects OUTPUT_DIR into a sandbox redirects this with it."""
    return OUTPUT_DIR / "OPPORTUNITIES.md"


# --- the verdicts --------------------------------------------------------------------
# Four. A video showing a new way people get paid is not a project tip, and a road never
# looked down is not a NO nobody reads, so OPPORTUNITY is a verdict of its own. Both gates are
# enforced in code as well as asked for in the prompt (see opportunity_gate and
# demote_if_nothing_applies).
#   YES          a mechanism usable WITHIN TWO WEEKS on a live project, and the summary
#                names the mechanism and the project or it is not YES; or one that changes
#                how the reader works, or this tool
#   OPPORTUNITY  a documented way A PERSON IN THE READER'S POSITION gets paid, with numbers
#                shown, that is not already killed. A big company, a merger, a funding round
#                or a hiring war is NEVER one, however large its number.
#   MAYBE        the default, and the honest answer far more often than either above
#   NO           off topic: a mechanism-free pitch, a reaction video, nothing usable
# YES, OPPORTUNITY and MAYBE are written up by the writing brain; NO gets its two-line entry
# and no write-up.
VERDICTS = ("YES", "OPPORTUNITY", "MAYBE", "NO")
FETCH_PRIORITY = {"YES": 0, "OPPORTUNITY": 1, "MAYBE": 2}   # who gets the budget first
# A video is triaged twice: from its listing, which decides whether it is fetched, and then
# from its transcript, which decides what it actually is. The second call reads the whole
# transcript up to this many characters; longer ones are cut to the head and the tail.
TRIAGE_TRANSCRIPT_CHARS = 60_000
# How many recorded opportunities the triage prompt is reminded of, newest last, so a
# video that only repeats a road already recorded is not called a new one.
ALREADY_IN_FILES_LIMIT = 60

OLLAMA_HOST = "http://localhost:11434"
NUM_CTX = 32768
RESPONSE_RESERVE_TOKENS = 2000
CHARS_PER_TOKEN = 4
# Kept, but it is no longer the thing holding the rate down: MIN_REQUEST_GAP_SECONDS is
# a floor under every call and is always the larger of the two, so these sleeps now just
# mean a caller waits a little of the gap up front instead of all of it inside run_yt_dlp.
REQUEST_DELAY_SECONDS = 4.0   # courtesy pause a caller takes on its own (was 1.5)

MIN_VIDEO_SECONDS = 180
MAX_VIDEO_SECONDS = 90 * 60
CHANNEL_LOOKBACK_VIDEOS = 30
# How many channels one --channels run looks at, out of all of channels.txt. Walking the file
# from the top every run sounds like more coverage and is less: the first channel's backlog can
# spend the whole per-run budget before the loop reaches the second. A small rotating window
# per run spreads the requests over the day and reaches the bottom of the file. See
# channels_for_this_slot().
CHANNELS_PER_SLOT = 2
# Priority channels: the [channels] priority setting, the channels you most want read. A channel
# of channels.txt named there is a priority channel, and that is the whole rule:
#   - every --channels run lists it first, before the queue and the rotation, and reads its new
#     videos before any other channel's (run_favourites); it is not in the rotation;
#   - no cap rolls its videos to waiting: the run's 3 does not apply to it, and
#     FAVOURITE_DAILY_RESERVE of the day's adaptive cap is held back for it;
#   - it always gets the full write-up: never screened out at its listing, and written up even
#     when its transcript is read as NO (the verdict stays what the rules make it);
#   - it is tried again until it is read: a fetch or a write-up that fails is never given up,
#     only spaced out to once a day after the third try;
#   - it is exempt from CHANNEL_DAILY_CAP.
# apply_profile() sets these three from settings.toml.
FAVOURITE_CHANNELS = ()
# How much of the day's cap is held back for each priority channel: the queue, the other
# channels, the searches and the backlog stop this many short of the cap, less what the
# priority channels have read that day.
FAVOURITE_DAILY_RESERVE = 3
# At most this many of one channel's videos are read in a day, however they were found (its
# listing, a search, the queue, the backlog), so one channel's burst cannot fill the day. A
# video over the cap waits (reason channel_cap) with nothing spent on it; a priority channel is
# exempt.
CHANNEL_DAILY_CAP = 3

# What makes a title obviously on topic, which is the only thing that lets a video outside
# the MIN/MAX_VIDEO_SECONDS window through (see duration_allows). The [topics]
# on_topic_keywords setting; apply_profile() sets it.
TOPIC_KEYWORDS = []

# Videos whose accurate summary depends on current product names and jargon that a small local
# model does not reliably know. These never get a local write-up, whatever --local-only says.
# The [topics] technical_keywords setting; apply_profile() sets it.
TECHNICAL_KEYWORDS = []


def is_technical_video(info):
    text = " ".join(str(info.get(k, "")) for k in ("title", "channel", "uploader", "description")).lower()
    return any(k in text for k in TECHNICAL_KEYWORDS)


# What `--discover` searches for to propose new channels, and channels to check besides. The
# [discover] queries and candidates settings; apply_profile() sets them.
DISCOVER_TOPICS = []
KNOWN_DISCOVER_CANDIDATES = []


class RateLimitError(Exception):
    pass


# --- Jev, optional ------------------------------------------------------------------------------
# Jev is a small decision model on OpenRouter, used here only as the opportunity screen
# (OPPORTUNITY_SCREEN_BACKEND) and only when switched on; it is a paid call. Any call this tool
# makes goes through jev_ledger(), which refuses a call that could take the day past
# JEV_DAILY_CAP_USD before it is sent (jev.py prices every call from its size first).
JEV_DAILY_CAP_USD = 1.00


def jev_ledger():
    """The tool's own Jev ledger: one cap a calendar day, kept in JEV_SPEND_PATH."""
    from . import jev
    return jev.Ledger(JEV_SPEND_PATH, JEV_DAILY_CAP_USD, per_day=True)

# The default model for a local (Ollama) brain. It is a hybrid-thinking model, so every call
# here sets think:false (see ollama_generate), which makes it several times faster for the same
# answer. Override with --model or the brain settings.
DEFAULT_LOCAL_MODEL = "qwen3.5:9b"

SMART_PUNCT = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...",
}

SUMMARY_INSTRUCTIONS = """You are writing a video summary for {reader}, the person the CONTEXT below describes, who is the one building and the one reading this. Write to them. The CONTEXT tells you who they are, what they are building, decisions they have already made, and beliefs a video might contradict. Use it: make the summary specific to them, not generic, and check the video's claims against it.

=== CONTEXT ===
{context}
=== END CONTEXT ===

VIDEO: {title}
CHANNEL: {uploader}
URL: {url}
VERDICT: {verdict_note}

=== TRANSCRIPT ===
{transcript}
=== END TRANSCRIPT ===

Write the summary under 600 words, plain prose, no mannered writing, in exactly this order, with these headings:

## 1. What it is
The video, who made it, and every conflict of interest: a sponsor, a course, a lead magnet, an affiliate link, or the presenter selling the thing recommended.

## 2. The claim
One paragraph.

## 3. Evidenced versus asserted
Which numbers were shown on screen or sourced, and which were only said.
If the VERDICT above is OPPORTUNITY, end this section with one more line, all on one line, in exactly this form:
Opportunity line: works: <the way people are getting paid, one clause> | for: <who pays, or who does it> | evidence: <"shown: " and what was on screen or sourced, or "claimed: " and what was only said>
Write that line ONLY when the VERDICT above is OPPORTUNITY, and only when the video fills all three fields. Never write N/A, UNKNOWN, or an empty field in it. If you cannot fill all three from the video, do not write the line at all; the line is deleted afterwards if you do.

## 4. The method
Numbered steps, filler removed.

## 5. Worth keeping verbatim
Any prompt, command, checklist, tool name, book title, or exact phrase worth having the exact words of. Quote those exactly and nothing else from the transcript. Say "None." if there is nothing worth quoting.

## 6. What applies to me
This section is what THIS VIDEO gave me, not what you already know about me from CONTEXT. An item belongs here only if it passes all three of these tests. Every one of them is checked after you write it, and an item that fails any of them is deleted.

1. THE QUOTE. The item is anchored to the video's own words, quoted: double quotes, three to fifteen words, verbatim from the transcript. Do not paraphrase inside the quote marks, do not tidy up the speaker's grammar, and never quote the CONTEXT. Quote marks mean the video said those words.

2. THE PROJECT. The item ends by naming exactly one project, in exactly this form: {applies_choices}. If the VERDICT above is OPPORTUNITY, write "Applies to: a road not taken": it is a way people get paid that I have not tried, not a change to a project I run. That list is closed. Never a person's name. Never anything not on the list. Never two of them on one item: an item that applies to four projects has not been mapped to any of them.

3. THE MAPPING TEST. The quote would still make sense to a stranger who was told the project and the quote and nothing else. "The younger you are, the easier it is" does not tell anyone anything about how to run a project. If the only thing connecting the quote to the project is what you read in CONTEXT, the item fails this test and does not belong here.

Do not build a bridge between the video and the CONTEXT. A video about someone's app revenue is not applicable merely because I also build software, and advice you could have written without watching the video is not applicable at all. Most videos honestly score nothing here, and a plausible invention is far worse than nothing.

If nothing passes all three tests, this whole section is the single word "nothing", and it stops there. Do not explain why there is nothing. Do not write out what you considered and rejected. Do not write an Ignore line. Just the word.

Otherwise: write the line "Most applicable: " followed by the single most useful concrete takeaway, then its quote, then the specific thing in my setup it would change, then its "Applies to:" label. Then, only if there is genuinely more, a short ranked list, each item carrying its own quote and its own "Applies to:" label. Then one line, "Ignore: ", saying what to ignore and why, including anything that would break a decision already made in CONTEXT's "Decisions already made" - in your own words, with no quote marks around anything the video did not say.

## 7. What contradicts what I believe
Checked against CONTEXT's "Beliefs a video should test me on". A contradiction is something the video actually said that pushes against one of those beliefs. If there is none, this whole section is exactly this line and nothing else:
nothing in this video touches the beliefs list
No explanation. No paragraph about why there is no contradiction. No note that the video is about a different subject, or that it happens to agree with me.

## 8. Try this week
Exactly one sentence: one thing a person could do in a single afternoon with what they already have, drawn from what survived section 6 - or the single word "nothing" if the video earned nothing. Not a project, not a plan, not two things joined by "and"; if it needs more than an afternoon, cut it down to the first afternoon of it. If section 6 came out as "nothing" then this is "nothing" too - there is nothing from this video to try.

## 9. Header
The line that goes at the top of my day's digest for this video: exactly one plain sentence, under 30 words, that tells me what to do and names the one project from section 6 it is for, in the words of its "Applies to:" label. It says the same action as section 8, so that it stands on its own without the rest of the summary. Start with a word, not a quote; no quote marks anywhere in it, no dash, no second sentence. If section 8 is "nothing", this is "nothing" too.

Rules: never invent a number the video did not give; write UNKNOWN rather than guess; keep the presenter's claims as claims, never restate them as settled fact; do not reproduce the transcript beyond section 5. A double-quoted span anywhere in this summary is a claim that the video said those words: every quoted line is checked against the transcript afterwards and deleted if the words are not there, so never put quote marks around anything from the CONTEXT and never attribute my own beliefs or my own numbers to the video. And never think aloud: no "Actually", no "re-reading", no "let me", no "I should", no paragraph working out whether something qualifies. Write the finished answer only."""

EXTRACTION_INSTRUCTIONS = """This is part {i} of {n} of a longer video transcript. Extract, tersely, as a bullet list:
- Concrete claims and numbers, each marked SHOWN ON SCREEN, SOURCED, or ONLY SAID
- Any method or steps described
- Any exact phrase, prompt, command, checklist, tool name, or book title worth quoting verbatim (quote it exactly)
Do not summarize opinions or filler, and do not write a conclusion. Just the extracted list.

=== TRANSCRIPT PART {i} of {n} ===
{chunk}
=== END PART ==="""


def normalize_punct(text):
    for k, v in SMART_PUNCT.items():
        text = text.replace(k, v)
    return text


# --- files written whole ---------------------------------------------------------------------
# A state file or a page written in place with write_text is half a file after a power cut in
# the middle of the write. Half a digest keeps half a table for good, because the next write
# only replaces the blocks it can find; half a records.json read back as an empty store and
# saved over loses every record in it. So every write goes through atomic_write_text.
class StateFileDamaged(BaseException):
    """A state file that is there and cannot be read. The run stops, and nothing is written
    over the file. A BaseException, like a keyboard interrupt, so that no handler for one
    video's failure can mistake it for that video's problem and carry on writing."""


def atomic_write_text(path, text, encoding="utf-8"):
    """Write a whole file so that it is the old one or the new one, never half of each.

    The text goes to a temporary file beside the target, is flushed to the disk, and then
    replaces the target in one step (os.replace, which Windows does atomically on one volume).
    Line endings are the ones write_text wrote, so no file changes shape. A temporary file that
    a power cut left behind is removed the next time the same file is written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows will not replace a file another program holds open without delete
                # sharing; a reader never holds it for long.
                if attempt == 4:
                    raise
                time.sleep(0.2)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    for stale in path.parent.glob(f".{path.name}.*.tmp"):
        try:
            stale.unlink()
        except OSError:
            pass


def read_state_json(path):
    """A state file's contents: {} when the file does not exist, and StateFileDamaged when it
    is there and cannot be read. Never {} for a file that is there, because an empty store read
    back and then saved is how a torn file becomes a lost one."""
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise StateFileDamaged(
            f"{path.name} cannot be read ({e.msg} at character {e.pos} of {len(text)}), so this "
            f"run stopped before writing anything over it. The file is at {path}, exactly as it "
            f"was found; mend it by hand, or put back a good copy, before the next run.") from None


def load_context():
    if not CONTEXT_PATH.exists():
        print(f"ERROR: {CONTEXT_PATH} does not exist. Run `worthwatch init` to write an example "
              f"you can change, or point the context_file setting at yours.")
        sys.exit(1)
    return CONTEXT_PATH.read_text(encoding="utf-8")


YOUTUBE_ID_RE = re.compile(r"(?:v=|/shorts/|/live/|/embed/|youtu\.be/)([A-Za-z0-9_-]{11})")


def video_id_from_url(url):
    """The 11-character video id, so a URL can be checked against seen.json
    before spending a request to resolve it."""
    m = YOUTUBE_ID_RE.search(url or "")
    return m.group(1) if m else None


def slugify(text, max_len=60):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:max_len].rstrip("-") or "video"


def clean_vtt(raw_text):
    lines = raw_text.splitlines()
    text_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line == "WEBVTT":
            continue
        if line.startswith(("Kind:", "Language:", "NOTE", "STYLE", "::cue")):
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line:
            continue
        text_lines.append(line)

    deduped = []
    for line in text_lines:
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)

    final = []
    for line in deduped:
        if line in final[-2:]:
            continue
        final.append(line)

    prose = " ".join(final)
    return re.sub(r"\s+", " ", prose).strip()


REGIONAL_EN = {"en-US", "en-GB", "en-CA", "en-AU", "en-IN", "en-IE", "en-NZ", "en-ZA", "en-orig"}


def collapse_repeats(text, limit=None):
    """(text, how many runs were shortened). A run of the same word repeated more than
    `limit` times is cut back to `limit`. Whisper does this when it loses its place; a
    human does not say the same word fourteen times, but does say it three."""
    limit = WHISPER_MAX_WORD_REPEAT if limit is None else limit
    if limit <= 0:
        return text, 0
    out, run, collapsed = [], [], 0
    for token in text.split(" "):
        key = re.sub(r"[^a-z0-9']", "", token.lower())
        if run and key and key == run[0][0]:
            run.append((key, token))
            continue
        if len(run) > limit:
            out.extend(t for _, t in run[:limit])
            collapsed += 1
        else:
            out.extend(t for _, t in run)
        run = [(key, token)] if key else []
        if not key:
            out.append(token)
    if len(run) > limit:
        out.extend(t for _, t in run[:limit])
        collapsed += 1
    else:
        out.extend(t for _, t in run)
    return " ".join(out), collapsed


def pick_language(subs, auto):
    if "en" in subs:
        return "en"
    if "en" in auto:
        return "en"
    for d in (subs, auto):
        for k in d:
            if k in REGIONAL_EN:
                return k
    for d in (subs, auto):
        for k in d:
            if k.startswith("en-"):
                return k
    if subs:
        return next(iter(subs))
    if auto:
        return next(iter(auto))
    return None


def package_version(name):
    """The installed version of a Python package, or None."""
    try:
        import importlib.metadata as metadata
        return metadata.version(name)
    except Exception:  # noqa: BLE001 - not installed, or metadata unreadable: unknown
        return None


def ejs_status():
    """(ok, detail): is the challenge-solver package there for yt-dlp?"""
    version = package_version("yt-dlp-ejs")
    if not version:
        return False, "yt-dlp-ejs is not installed (it comes with yt-dlp's default extras)"
    return True, f"yt-dlp-ejs {version} with {JS_RUNTIME or 'deno'}"


_CLIENT_PATH = {"path": "captions"}


class media_client:
    """`with media_client():` - every yt-dlp call inside is the audio path's: it lists the
    formats the caption path skips, because the audio path needs formats where the caption
    path needed none. Done as a block rather than a per-call flag for the same reason the
    client flags live in one function: a call site that sets it by hand can forget."""
    def __enter__(self):
        self.prev = _CLIENT_PATH["path"]
        _CLIENT_PATH["path"] = "audio"
    def __exit__(self, *exc):
        _CLIENT_PATH["path"] = self.prev
        return False


def client_flags():
    """The flags every yt-dlp call carries, in one place, so a call site cannot forget one
    and so the request log can record exactly what the client looked like."""
    # --ignore-no-formats-error: a video whose formats yt-dlp cannot list as downloadable
    # still has captions, and without this flag yt-dlp aborts the metadata dump with
    # "Requested format is not available" before printing anything. The caption path never
    # downloads media, so a missing format is not an error for it; the audio path checks for
    # one itself, and says so. yt-dlp picks YouTube's client itself; worthwatch never names one.
    on_audio = _CLIENT_PATH["path"] == "audio"
    skip = AUDIO_EXTRACTOR_SKIP if on_audio else EXTRACTOR_SKIP
    flags = ["--ignore-no-formats-error"] + js_runtime_flags()
    if skip:
        flags += ["--extractor-args", f"youtube:skip={skip}"]
    return flags


def client_config():
    """A short, loggable description of the client every request presents: which path asked,
    the JavaScript runtime and the yt-dlp version."""
    return {
        "path": _CLIENT_PATH["path"],
        "js": JS_RUNTIME or "deno", "yt_dlp": package_version("yt-dlp") or "unknown",
    }


# What yt-dlp's stderr tells us about the request, reduced to a few flags the log can carry.
# Each is (name, substring that proves it). Only the presence is recorded.
STDERR_SIGNALS = [
    ("pot_subs_missing", "subtitles require a PO Token"),
    ("ejs_missing", "Remote component"),
    ("n_challenge_failed", "n challenge solving failed"),
    ("no_js_runtime", "No supported JavaScript runtime"),
    ("bot_check", "Sign in to confirm"),
    ("http_403", "HTTP Error 403"),
]


def stderr_signals(stderr):
    return [name for name, needle in STDERR_SIGNALS if needle in (stderr or "")]


def log_request(kind, endpoint, status, started, ended, gap, **extra):
    """One line per YouTube request in logs/requests.jsonl: what kind of request, which
    endpoint family, how long since the previous request ended, what came back. This is
    the record the 2026-09-04 and 09-05 strikes did not have, and the reason the cause had
    to be reconstructed from file mtimes."""
    entry = {
        "ts": started.isoformat(timespec="seconds"), "kind": kind, "endpoint": endpoint,
        "gap_s": None if gap is None else round(gap, 1),
        "duration_s": round((ended - started).total_seconds(), 1), "status": status,
        "client": client_config(),
    }
    entry.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
    try:
        REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REQUEST_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"  (could not write the request log: {e})")
    return entry


def load_request_log(since=None):
    if not REQUEST_LOG_PATH.exists():
        return []
    rows = []
    for line in REQUEST_LOG_PATH.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if since is None or e.get("ts", "") >= since:
            rows.append(e)
    return rows


def request_kind_of(cmd):
    """(kind, endpoint, video_id) for a yt-dlp command line, for the log. Endpoint names
    are the YouTube surface the call actually touches, so a refusal can be read off the
    log by endpoint. The kind can be relabelled by the caller (the canary does) through
    request_label()."""
    tail = cmd[-1] if cmd else ""
    vid = None
    if "--load-info-json" in cmd:
        vid = Path(cmd[cmd.index("--load-info-json") + 1]).stem
        if vid.endswith(".media"):
            vid = vid[:-len(".media")]
        if "--write-sub" in cmd or "--write-auto-sub" in cmd:
            kind, endpoint = "captions", "youtube.com/api/timedtext"
        else:
            # the same --load-info-json shape, but downloading a format instead of a
            # subtitle: a different host, a different limit, and it must not be logged
            # or counted as a caption request
            kind, endpoint = "audio", "googlevideo.com/videoplayback"
    elif "--write-sub" in cmd or "--write-auto-sub" in cmd:
        kind, endpoint = "captions", "watch+timedtext"
    elif isinstance(tail, str) and (tail.startswith("ytsearch")
                                    or tail.startswith("https://www.youtube.com/results?")):
        kind, endpoint = "search", "youtube.com/results"
    elif "--playlist-items" in cmd:
        kind, endpoint = "channel_info", "youtube.com/@channel"
    elif "--flat-playlist" in cmd:
        kind, endpoint = "listing", "youtube.com/@channel/videos"
    else:
        kind, endpoint = "extract", "youtube.com/watch"
    if vid is None and isinstance(tail, str):
        vid = video_id_from_url(tail)
    label = _REQUEST_LABEL.get("kind")
    return (label or kind), endpoint, vid


_REQUEST_LABEL = {"kind": None}


class request_label:
    """`with request_label("canary"):` relabels every request made inside the block."""
    def __init__(self, kind):
        self.kind = kind
    def __enter__(self):
        self.prev = _REQUEST_LABEL.get("kind")
        _REQUEST_LABEL["kind"] = self.kind
    def __exit__(self, *exc):
        _REQUEST_LABEL["kind"] = self.prev
        return False


_MEASURING = {"on": False}


class measuring_a_refusal:
    """`with measuring_a_refusal():` - inside this block a 429 is logged and raised as
    usual, but adds no strike, halves no cap and arms no cooldown. It is for the paired
    probe, whose whole job is to measure the refusal that just happened: the cooldown is
    already in force, and counting one refusal twice would double the wait and halve the
    budget for a fact the tool went looking for on purpose."""
    def __enter__(self):
        self.prev = _MEASURING["on"]
        _MEASURING["on"] = True
    def __exit__(self, *exc):
        _MEASURING["on"] = self.prev
        return False


def request_paths(kind, endpoint):
    """Which transcript path(s) a refusal on this request is evidence about, and therefore
    whose cooldown it arms. The paths are limited separately because they are different
    surfaces: every 429 this tool has ever been given came from timedtext, and nothing yet
    says a refusal there says anything about googlevideo.

    The endpoint alone cannot answer this, which is the bug the media canary exposed on
    2026-09-06: BOTH clients ask youtube.com/watch, so classifying an extraction by its
    endpoint filed every media-path refusal under captions - arming a cooldown on a path
    that had not been asked for anything, and leaving the path that actually got refused
    free to be refused again. An extraction belongs to the client that made it, and
    _CLIENT_PATH is exactly that: "audio" inside media_client(), "captions" outside it.

    Listings, searches and channel-info calls belong to neither path - both need them to
    find videos at all. A refusal there is a fact about the address, so it counts against
    every path that is switched on: none of them has evidence it is still welcome."""
    if kind == "audio" or "googlevideo" in (endpoint or ""):
        return ["audio"]
    if kind == "captions" or "timedtext" in (endpoint or ""):
        return ["captions"]
    if endpoint == "youtube.com/watch":
        return [_CLIENT_PATH["path"]]
    return path_order() or ["captions"]


def polite_delay(seconds=REQUEST_DELAY_SECONDS):
    time.sleep(seconds)


def group_kwargs(os_name=None):
    """Popen keywords that let kill_tree() take a child and everything it starts. On macOS and
    Linux the child gets its own process group; Windows needs nothing, kill_tree() walks the
    tree there."""
    return {} if (os_name or os.name) == "nt" else {"start_new_session": True}


def kill_tree(proc, os_name=None, run=None, os_module=None):
    """Kill a child process and every process it started.

    A JavaScript runtime that yt-dlp starts for the player's challenges, or a model CLI's own
    helpers, can outlive the child and hold its output pipe open; killing only the child then
    leaves communicate() waiting forever (a run once sat idle for minutes past its timeout). So
    the whole tree goes: taskkill /T on Windows, the child's process group elsewhere (started
    with group_kwargs())."""
    run = run or subprocess.run
    os_module = os_module or os
    if (os_name or os.name) == "nt":
        run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        return
    import signal
    try:
        os_module.killpg(os_module.getpgid(proc.pid), getattr(signal, "SIGKILL", signal.SIGTERM))
    except (OSError, AttributeError):
        try:
            proc.kill()
        except OSError:
            pass


def run_yt_dlp(cmd, timeout=120):
    # The client flags are applied here, not at the call sites, for the same reason the
    # 429 is detected here: a call site that forgets one is a request nothing downstream can
    # describe.
    if "--ignore-no-formats-error" not in cmd:
        cmd = [cmd[0]] + client_flags() + cmd[1:]
    if "-v" not in cmd:
        # verbose stderr is where the challenge-solver facts are
        cmd = [cmd[0], "-v"] + cmd[1:]
    kind, endpoint, video_id = request_kind_of(cmd)
    argv = (yt_dlp_argv() if cmd[0] == YT_DLP else [cmd[0]]) + list(cmd[1:])

    # Every yt-dlp call goes through here, so this is the one place that can honestly
    # promise a minimum gap between requests. Doing it per-caller was the old design and
    # it missed the metadata probes entirely, which were most of the traffic.
    gap = wait_for_request_slot()
    started = dt.datetime.now()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace", **group_kwargs())
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        note_request()  # it still went out; a timeout is not a free request
        log_request(kind, endpoint, "timeout", started, dt.datetime.now(), gap, video_id=video_id,
                    timeout_s=timeout)
        raise RuntimeError(f"yt-dlp timed out after {timeout}s and was killed: {' '.join(cmd[-2:])}")
    note_request()
    ended = dt.datetime.now()
    signals = stderr_signals(stderr)
    if "429" in stderr and ("Too Many Requests" in stderr or "HTTP Error 429" in stderr):
        # Arm the cooldown here, at the one place a 429 is ever detected, so no
        # caller can catch RateLimitError and forget to record it.
        detail = stderr.strip()[-500:]
        paths = request_paths(kind, endpoint)
        log_request(kind, endpoint, "429", started, ended, gap, video_id=video_id,
                    signals=signals, stderr_tail=detail[-300:], path=",".join(paths),
                    measuring=True if _MEASURING["on"] else None)
        if _MEASURING["on"]:
            print(f"  HTTP 429 from YouTube, inside a measurement: logged, but no strike, "
                  f"no cap change and no new cooldown - it measures the refusal already "
                  f"being served.")
        else:
            for path in paths:
                until, wait, strikes = arm_cooldown(detail, path=path)
                print(f"  HTTP 429 from YouTube on the {path} path. Cooldown armed: no "
                      f"{path} request until {until.strftime('%Y-%m-%d %H:%M')} "
                      f"({format_duration(wait)} from now, strike {strikes}). "
                      f"worthwatch waits: later runs skip that path until then, and nothing "
                      f"tries another way in.")
        raise RateLimitError(detail)
    status = "ok" if proc.returncode == 0 else f"exit{proc.returncode}"
    errors = [l.strip() for l in (stderr or "").splitlines() if l.startswith("ERROR")]
    log_request(kind, endpoint, status, started, ended, gap, video_id=video_id, signals=signals,
                errors=errors[:3])
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


# --- one video: extract once, settle, download the caption from the saved extraction ----
# Until 2026-09-05 a caption fetch was two full extractions (a --dump-json probe, then a
# second yt-dlp run that extracted the same video again just to download its subtitle),
# with the subtitle request fired within a second of the second extraction. Now the
# extraction happens once, its result is saved to cache/info/<id>.json, and the caption is
# downloaded from that file with --load-info-json after CAPTION_SETTLE_SECONDS. That is one
# watch-page request plus one timedtext request per video, and the gap between them is the
# documented workaround for the 429 rather than the shape that kept earning it.


def info_cache_path(video_id):
    return INFO_CACHE_DIR / f"{video_id}.json"


def extract_video_info(url):
    """One extraction: the watch page for the web client. Returns (info dict, None) or
    (None, error). The full info dict is saved to cache/info/ so the caption download can
    load it instead of extracting again."""
    cmd = [YT_DLP, "--skip-download", "--no-playlist", "--dump-json", url]
    res = run_yt_dlp(cmd)
    lines = [l for l in res.stdout.splitlines() if l.strip().startswith("{")]
    if not lines:
        return None, f"yt-dlp produced no metadata (exit {res.returncode}): {res.stderr.strip()[-500:]}"
    try:
        info = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None, f"yt-dlp returned no valid metadata (exit {res.returncode}): {res.stderr.strip()[-500:]}"
    if not info.get("id"):
        return None, "yt-dlp metadata carried no video id"
    INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(info_cache_path(info["id"]), json.dumps(info, ensure_ascii=False))
    return info, None


def caption_url_expiry(track_url):
    """YouTube stamps every caption URL with `expire=<unix seconds>`; None if absent."""
    m = re.search(r"[?&]expire=(\d{9,11})", track_url or "")
    return dt.datetime.fromtimestamp(int(m.group(1))) if m else None


def is_translated_track(track):
    return bool(re.search(r"[?&]tlang=", track.get("url") or ""))


def choose_caption_track(info):
    """(lang, kind, url, note) for the one track worth downloading, or (None, ...) with the
    reason. kind is "manual", "auto" or "translated".

    A video with auto-dubbed audio tracks can carry captions in a hundred languages, and its
    "en" entry can be a machine translation of another language's track (lang=xx&tlang=en)
    while the real English track sits under "en-orig". A tool that asks for "en" on such a
    video downloads the translated track, which is also a worse transcript. So: manual
    English first, then "en-orig", then an "en" entry that is not a translation; and any
    translated entries are pruned out of the info dict here, so the download that later
    loads it cannot pick one by accident."""
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    english = ["en", *sorted(k for k in REGIONAL_EN if k != "en-orig")]

    for lang in english:
        tracks = [t for t in subs.get(lang, []) if t.get("url")]
        if tracks:
            return lang, "manual", tracks[0]["url"], None

    if any(t.get("url") for t in auto.get("en-orig", [])):
        originals = [t for t in auto["en-orig"] if t.get("url") and not is_translated_track(t)]
        if originals:
            auto["en-orig"] = originals
            return "en-orig", "auto", originals[0]["url"], None

    saw_translation = None
    for lang in english:
        tracks = [t for t in auto.get(lang, []) if t.get("url")]
        if not tracks:
            continue
        originals = [t for t in tracks if not is_translated_track(t)]
        if originals:
            auto[lang] = originals      # prune the translated entries under this key
            return lang, "auto", originals[0]["url"], None
        saw_translation = saw_translation or (lang, tracks[0]["url"])

    if saw_translation:
        lang, url = saw_translation
        if ALLOW_TRANSLATED_CAPTIONS:
            return lang, "translated", url, None
        return None, "translated", url, (f"only a machine translation into {lang} is available; "
                                         f"translated tracks are not fetched (ALLOW_TRANSLATED_CAPTIONS)")

    # not an English video at all: the first track of any language, as before
    lang = pick_language(subs, auto)
    if lang is None:
        return None, None, None, "no captions available (manual or auto-generated) in any language"
    tracks = [t for t in (subs.get(lang) or auto.get(lang) or []) if t.get("url")]
    if not tracks:
        return None, None, None, f"caption track {lang} carries no URL"
    kind = "manual" if lang in subs else ("translated" if is_translated_track(tracks[0]) else "auto")
    if kind == "translated" and not ALLOW_TRANSLATED_CAPTIONS:
        return None, kind, tracks[0]["url"], (f"only a machine translation into {lang} is available; "
                                              f"translated tracks are not fetched (ALLOW_TRANSLATED_CAPTIONS)")
    return lang, kind, tracks[0]["url"], None


def save_info_cache(info):
    INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(info_cache_path(info["id"]), json.dumps(info, ensure_ascii=False))


def wait_to_settle(extracted_at, seconds=None):
    """Hold until CAPTION_SETTLE_SECONDS have passed since the extraction ended."""
    seconds = CAPTION_SETTLE_SECONDS if seconds is None else seconds
    if extracted_at is None:
        return 0.0
    left = seconds - (dt.datetime.now() - extracted_at).total_seconds()
    if left > 0:
        time.sleep(left)
    return max(0.0, left)


def download_captions_from_info(video_id, lang):
    """The timedtext request, and nothing else: yt-dlp loads cache/info/<id>.json and
    downloads the chosen subtitle from it. --ignore-errors matters: without it a failed
    subtitle download makes yt-dlp fall back to re-extracting the URL and trying again,
    which is exactly one more request than a refused address should get."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(RAW_DIR / "%(id)s.%(ext)s")
    cmd = [YT_DLP, "--load-info-json", str(info_cache_path(video_id)), "--skip-download",
           "--ignore-errors", "--write-sub", "--write-auto-sub", "--sub-langs", lang,
           "--sub-format", "vtt", "-o", outtmpl]
    return run_yt_dlp(cmd)


def read_downloaded_vtt(video_id):
    """(cleaned text, error) from the .vtt yt-dlp wrote for this id, which is deleted."""
    vtt_files = sorted(RAW_DIR.glob(f"{video_id}*.vtt"))
    if not vtt_files:
        return None, "could not be downloaded"
    vtt_path = vtt_files[0]
    cleaned = clean_vtt(vtt_path.read_text(encoding="utf-8", errors="replace"))
    vtt_path.unlink()
    if not cleaned:
        return None, "caption track downloaded but was empty after cleaning"
    return cleaned, None


def fetch_transcript(url, settle=True):
    """Returns (meta, None) or (None, error). One extraction, one caption download."""
    info, err = extract_video_info(url)
    if err:
        return None, err
    extracted_at = dt.datetime.now()
    vid = info["id"]
    title = info.get("title") or vid
    uploader = info.get("uploader") or info.get("channel") or "UNKNOWN"

    lang, kind, track_url, err = choose_caption_track(info)
    if err:
        return None, err
    save_info_cache(info)   # with any translated entries pruned, so the download cannot pick one

    if settle:
        wait_to_settle(extracted_at)
    download_captions_from_info(vid, lang)

    cleaned, err = read_downloaded_vtt(vid)
    if err == "could not be downloaded":
        return None, f"captions were listed ({lang}, {kind}) but could not be downloaded"
    if err:
        return None, err

    raw_txt_path = RAW_DIR / f"{vid}.txt"
    atomic_write_text(raw_txt_path, cleaned)
    try:
        info_cache_path(vid).unlink()   # done with it; the canary keeps its own
    except OSError:
        pass
    return {
        "id": vid, "title": title, "uploader": uploader, "url": url,
        "transcript": cleaned, "raw_path": raw_txt_path, "lang": lang, "track_kind": kind,
    }, None


# --- the audio path -------------------------------------------------------------------
# One extraction with the media client, one media request for the smallest audio-only
# format, then faster-whisper on this machine. Two YouTube requests per video, the
# same two-step shape as the caption path and the same budget, log and gap discipline -
# but the second request goes to googlevideo.com, not /api/timedtext, so it is limited
# separately (see arm_cooldown's path argument) and can run on a day when captions cannot.


def media_info_cache_path(video_id):
    """The media client's extraction is cached beside the caption client's, not over it:
    they are different player responses for the same video and the canary reads the other
    one."""
    return INFO_CACHE_DIR / f"{video_id}.media.json"


def extract_media_info(url):
    """One extraction with the media client. Returns (info, None) or (None, error)."""
    with media_client():
        cmd = [YT_DLP, "--skip-download", "--no-playlist", "--dump-json", url]
        res = run_yt_dlp(cmd, timeout=180)
    lines = [l for l in res.stdout.splitlines() if l.strip().startswith("{")]
    if not lines:
        return None, (f"yt-dlp produced no metadata for the media client (exit "
                      f"{res.returncode}): {res.stderr.strip()[-400:]}")
    try:
        info = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None, f"the media client returned no valid metadata (exit {res.returncode})"
    if not info.get("id"):
        return None, "the media client's metadata carried no video id"
    INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(media_info_cache_path(info["id"]), json.dumps(info, ensure_ascii=False))
    return info, None


def original_language_score(f):
    """How strongly a format looks like the video's OWN audio rather than one of
    YouTube's machine dubs. The caption path already learned this trap - it is why
    choose_caption_track() prefers "en-orig" over "en" - and the media side has exactly
    the same one: the first video measured on 2026-09-06 offered twenty-one copies of the
    same 114 kbps stream, identical in size to the byte, differing only in which language
    had been dubbed over it. Picking by size alone chose the Arabic one.

    yt-dlp marks the real track two ways: language_preference 10, and the word "original"
    in the format description. Both are checked, and a named non-English language counts
    against, so a tie never lands on a dub by accident."""
    score = 0
    if "original" in (f.get("format") or "").lower():
        score += 100
    pref = f.get("language_preference")
    if isinstance(pref, (int, float)):
        score += int(pref)
    lang = (f.get("language") or "").lower()
    if lang.startswith("en"):
        score += 5
    elif lang:
        score -= 20
    return score


def variant_group(f):
    """The key that identifies "the same stream, dubbed into a different language".
    YouTube's HLS ids for those are 91-0 ... 91-20, so the part before the dash is the
    group; anything without that shape is its own group."""
    fid = str(f.get("format_id") or "")
    return fid.split("-")[0] if "-" in fid else fid


def format_size(f, duration):
    """(bytes, how we know) for a format. Real bytes where YouTube states them, its own
    estimate where it only estimates, bitrate x duration where it gives neither - in that
    order, so a stated size always beats a guess."""
    if f.get("filesize"):
        return int(f["filesize"]), "stated"
    if f.get("filesize_approx"):
        return int(f["filesize_approx"]), "estimated"
    rate = f.get("abr") or f.get("tbr")
    if rate and duration:
        return int(rate * 1000 / 8 * duration), "from bitrate"
    return 1 << 62, "unknown"


def audio_bearing_formats(info):
    """Every downloadable format that carries this video's own audio, smallest first.

    It is not called audio_only_formats because YouTube often serves no audio-only format
    a client can download ("YouTube is forcing SABR streaming for this client", yt-dlp
    #12482). What is left is usually HLS, whose smallest variant (a 144p stream) is
    smaller than an audio-only format used to be. So "smallest format carrying the audio"
    is the honest reading of "audio only, smallest format", and that is what this returns:
    audio-only formats first when there are any, otherwise the smallest muxed stream,
    original-language only, never a dub."""
    duration = info.get("duration") or 0
    best_of_group = {}
    for f in info.get("formats") or []:
        if f.get("acodec") in (None, "none") or not f.get("url"):
            continue
        if (f.get("protocol") or "").startswith("sabr") or f.get("ext") == "mhtml":
            continue
        size, basis = format_size(f, duration)
        row = {"format_id": f.get("format_id"), "ext": f.get("ext"),
               "acodec": f.get("acodec"), "vcodec": f.get("vcodec"),
               "abr": f.get("abr"), "tbr": f.get("tbr"), "size": size,
               "size_basis": basis, "protocol": f.get("protocol"),
               "language": f.get("language"), "height": f.get("height"),
               "audio_only": f.get("vcodec") in (None, "none"),
               "original_score": original_language_score(f),
               "dubs_rejected": 0}
        group = variant_group(f)
        current = best_of_group.get(group)
        if current is None:
            best_of_group[group] = row
        elif row["original_score"] > current["original_score"]:
            row["dubs_rejected"] = current["dubs_rejected"] + 1
            best_of_group[group] = row
        else:
            current["dubs_rejected"] += 1
    rows = list(best_of_group.values())
    audio_only = [r for r in rows if r["audio_only"]]
    muxed = [r for r in rows if not r["audio_only"]]
    ranked = sorted(audio_only, key=lambda r: (r["size"], r.get("abr") or 0))
    ranked += sorted(muxed, key=lambda r: (r["size"], r.get("height") or 0))
    return ranked


def reencoded_size(seconds):
    """How big the audio Whisper reads will be for this many seconds of video: 16 kHz mono
    Opus at a constant 32 kbps, with the container's share measured and rounded up."""
    return int(float(seconds or 0) * AUDIO_REENCODE_BYTES_PER_SECOND)


def choose_audio_format(info):
    """(format, note) for the one format worth downloading, or (None, reason).

    FIX 2, 2026-09-25: refused before the download only for what could not pass after it, the
    audio at 16 kHz mono over AUDIO_MAX_BYTES (predicted from the duration), or a download over
    AUDIO_DOWNLOAD_MAX_BYTES. Both sentences keep the shape ceiling_reason reads."""
    formats = audio_bearing_formats(info)
    if not formats:
        protos = sorted({(f.get("protocol") or "?") for f in (info.get("formats") or [])})
        return None, (f"the extraction listed no downloadable format carrying audio "
                      f"(protocols seen: {', '.join(protos) or 'none at all'})")
    best = formats[0]
    predicted = reencoded_size(info.get("duration"))
    if predicted > AUDIO_MAX_BYTES:
        return None, (f"the audio re-encoded to 16 kHz mono would be {predicted / 1e6:.1f} MB, "
                      f"over the {AUDIO_MAX_BYTES / 1e6:.0f} MB ceiling (AUDIO_MAX_BYTES)")
    if best["size"] > AUDIO_DOWNLOAD_MAX_BYTES:
        return None, (f"the smallest audio-bearing format is {best['size'] / 1e6:.1f} MB, over "
                      f"the {AUDIO_DOWNLOAD_MAX_BYTES / 1e6:.0f} MB ceiling "
                      f"(AUDIO_DOWNLOAD_MAX_BYTES)")
    return best, None


# 2026-10-05: what a fetch says when no format it tried brought any audio back. The words are
# what fetch_failure_kind() knows a download failure by, so keep them in step with "download"
# in FAILURE_PATTERNS.
DOWNLOAD_FAILED_ERROR = "no audio came back"


def formats_to_try(info):
    """(formats, None): the formats one fetch downloads in turn until one brings audio back, at
    most AUDIO_FORMAT_TRIES; or (None, reason) when none can be read at all.

    The first is the one choose_audio_format() picks, with two exceptions, both read from
    yt-dlp's own test. yt-dlp tests every HLS format of a video under two days old while it
    extracts, because, in its own words, "Newly uploaded videos' HLS formats are potentially
    problematic", and it leaves the answer on each format (`__working`). A format its test
    refused is never offered. And when its test refused any, the formats it flagged and never
    reached go last: they are the same stream, and taking one untested from a refused stream
    brings nothing back. A video with no test marks, which is any video two days old or more,
    keeps the plain order."""
    best, err = choose_audio_format(info)
    if err:
        return None, err
    # choose_audio_format() stays the one place the first format is decided; the ranking only
    # supplies what comes after it (it is the ranking's own first, unless a caller swaps it).
    ranked = [best] + [f for f in audio_bearing_formats(info)
                       if f["format_id"] != best["format_id"]]
    marks = {f.get("format_id"): f for f in info.get("formats") or []}
    refused = {fid for fid, f in marks.items() if f.get("__working") is False}
    ranked = [f for f in ranked
              if f["format_id"] not in refused and f["size"] <= AUDIO_DOWNLOAD_MAX_BYTES]
    if refused:
        untested = {fid for fid, f in marks.items()
                    if f.get("__needs_testing") and "__working" not in f}
        ranked = ([f for f in ranked if f["format_id"] not in untested]
                  + [f for f in ranked if f["format_id"] in untested])
    if not ranked:
        return None, (f"{DOWNLOAD_FAILED_ERROR}: yt-dlp's own test refused every format that "
                      f"carries the audio ({len(refused)} refused)")
    return ranked[:AUDIO_FORMAT_TRIES], None


def download_failure_reason(result):
    """yt-dlp's own last word on a download that brought nothing back. Its retries of refused
    pieces go to stdout, not stderr, so an HTTP 403 is looked for in both."""
    if result is None:
        return "nothing was written"
    errors = [re.sub(r"^ERROR:\s*", "", l.strip()) for l in (result.stderr or "").splitlines()
              if l.startswith("ERROR")]
    errors = [e for e in errors if e]
    why = errors[-1] if errors else f"exit {result.returncode}, nothing was written"
    if "HTTP Error 403" in f"{result.stdout or ''}\n{result.stderr or ''}" and "403" not in why:
        why += " (HTTP 403 on its pieces)"
    return one_line(why)[:160]


def reencode_audio(path):
    """(new path, stats): the downloaded stream as 16 kHz mono Opus at a constant 32 kbps,
    written beside it as <id>.16k.ogg, and the download deleted at once. PyAV reads only the
    audio stream of a muxed file, so the 144p picture is never decoded. Raises on a file it
    cannot read; the caller turns that into the video's error."""
    import av
    path = Path(path)
    video_id = path.name.split(".")[0]
    out = path.with_name(f"{video_id}.16k.ogg")
    source_bytes = path.stat().st_size
    started = time.monotonic()
    with av.open(str(path)) as source, av.open(str(out), "w", format="ogg") as target:
        audio = next(s for s in source.streams if s.type == "audio")
        stream = target.add_stream("libopus", rate=AUDIO_REENCODE_RATE, layout="mono",
                                   options={"vbr": "off"})
        stream.bit_rate = AUDIO_REENCODE_BITRATE
        resampler = av.AudioResampler(format="s16", layout="mono", rate=AUDIO_REENCODE_RATE)
        for frame in source.decode(audio):
            for piece in resampler.resample(frame):
                for packet in stream.encode(piece):
                    target.mux(packet)
        for piece in resampler.resample(None):
            for packet in stream.encode(piece):
                target.mux(packet)
        for packet in stream.encode(None):
            target.mux(packet)
    path.unlink()
    return out, {"bytes": out.stat().st_size, "source_bytes": source_bytes,
                 "seconds": round(time.monotonic() - started, 1)}


def download_audio_from_info(video_id, format_id):
    """The media request, and nothing else: yt-dlp loads the saved media extraction and
    downloads one format from it. No second extraction, exactly as the caption path
    downloads a subtitle from its own saved info."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    outtmpl = str(AUDIO_DIR / "%(id)s.%(ext)s")
    # --hls-prefer-native: this machine has no ffmpeg, and yt-dlp's other HLS downloader
    # is ffmpeg. The native one fetches the fragments itself and writes an mp4 PyAV can
    # decode, which is all the transcriber needs.
    cmd = [YT_DLP, "--load-info-json", str(media_info_cache_path(video_id)),
           "-f", str(format_id), "--no-part", "--no-continue", "--no-progress",
           "--hls-prefer-native", "-o", outtmpl]
    with media_client():
        return run_yt_dlp(cmd, timeout=600)


def downloaded_audio_path(video_id):
    files = [f for f in AUDIO_DIR.glob(f"{video_id}.*") if f.suffix != ".json"]
    return files[0] if files else None


def delete_audio(video_id):
    """The audio is a means, not an artefact: nothing keeps it after the transcript
    exists. Returns the bytes reclaimed."""
    freed = 0
    for f in AUDIO_DIR.glob(f"{video_id}.*"):
        try:
            freed += f.stat().st_size
            f.unlink()
        except OSError:
            pass
    return freed


# faster-whisper is loaded lazily and once: importing ctranslate2 pulls ~2 GB of CUDA
# libraries into the process, which a run that never transcribes should not pay for.
_WHISPER = {"model": None, "key": None}


_CUDA_DLL_DIRS = []      # the add_dll_directory handles, kept alive: see below


def _add_cuda_dll_dirs(os_name=None):
    """Windows only: make CTranslate2 able to find cuBLAS and cuDNN. pip puts them in
    site-packages/nvidia/*/bin, which is on no search path Windows uses by default, and
    the failure is a bare "Library cublas64_12.dll is not found or cannot be loaded" at
    model-construction time - not at import, which is what makes it look like a GPU
    problem rather than a path one.

    Three things, because on 2026-09-06 the obvious one was measured to be unreliable on
    its own: os.add_dll_directory worked when called from a short script and failed inside
    the tool, taking the first video of the proof run with it.

      1. os.add_dll_directory, with the handles kept in a module-level list. The object it
         returns REMOVES the directory again when it is garbage collected, so throwing the
         return value away makes the fix depend on when the collector runs.
      2. PATH. add_dll_directory only helps a LoadLibrary call that asked for the user
         search path; a dependency pulled in by another DLL often does not, and falls back
         to PATH.
      3. Loading the three libraries by absolute path up front. Once a module is in the
         process, a later LoadLibrary("cublas64_12.dll") resolves to it however it was
         asked for. This is the one that cannot be defeated by search-path semantics.

    On macOS and Linux it does nothing: the libraries are found the usual way there."""
    if (os_name or os.name) != "nt":
        return []
    root = None
    try:
        import importlib.util
        spec = importlib.util.find_spec("nvidia")
        if spec and spec.submodule_search_locations:
            root = Path(list(spec.submodule_search_locations)[0])
    except (ImportError, ValueError):
        root = None
    if root is None:
        root = Path(sys.executable).parent.parent / "Lib" / "site-packages" / "nvidia"
    bins = sorted(root.glob("*/bin"))
    if not bins:
        return []
    if not _CUDA_DLL_DIRS:
        for d in bins:
            try:
                _CUDA_DLL_DIRS.append(os.add_dll_directory(str(d)))
            except OSError:
                pass
        os.environ["PATH"] = os.pathsep.join([str(d) for d in bins] +
                                             [os.environ.get("PATH", "")])
    loaded, missing = [], []
    import ctypes
    for stem in ("cublas64_12", "cublasLt64_12", "cudnn64_9"):
        for d in bins:
            dll = d / f"{stem}.dll"
            if dll.exists():
                try:
                    ctypes.WinDLL(str(dll))
                    loaded.append(stem)
                except OSError as e:
                    missing.append(f"{stem}: {e}")
                break
        else:
            missing.append(f"{stem}: not in {root}")
    if missing:
        print(f"  (CUDA libraries not preloaded: {'; '.join(missing)})")
    return loaded


def load_whisper(model_name=None, device=None, compute_type=None):
    """The transcriber, loaded once per process and kept."""
    model_name = model_name or WHISPER_MODEL
    device = device or WHISPER_DEVICE
    compute_type = compute_type or WHISPER_COMPUTE_TYPE
    key = (model_name, device, compute_type)
    if _WHISPER["key"] == key:
        return _WHISPER["model"]
    _add_cuda_dll_dirs()
    from faster_whisper import WhisperModel
    WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(model_name, device=device, compute_type=compute_type,
                         download_root=str(WHISPER_MODEL_DIR))
    _WHISPER.update({"model": model, "key": key})
    return model


def transcribe_audio(audio_path, model_name=None, device=None, compute_type=None,
                     beam_size=WHISPER_BEAM_SIZE, vad=WHISPER_VAD,
                     batch_size=WHISPER_BATCH_SIZE):
    """(text, stats) for one audio file. stats carries what the measurement needs: how
    long the audio was, how long this machine took, and the ratio between them."""
    model = load_whisper(model_name, device, compute_type)
    runner = model
    if batch_size:
        from faster_whisper import BatchedInferencePipeline
        runner = BatchedInferencePipeline(model=model)
    kwargs = dict(beam_size=beam_size, vad_filter=vad, language="en",
                  condition_on_previous_text=False,
                  initial_prompt=WHISPER_INITIAL_PROMPT or None)
    if batch_size:
        kwargs["batch_size"] = batch_size
    started = time.monotonic()
    segments, info = runner.transcribe(str(audio_path), **kwargs)
    pieces = [seg.text.strip() for seg in segments]   # a generator: the work happens here
    elapsed = time.monotonic() - started
    text, collapsed = collapse_repeats(normalize_punct(" ".join(t for t in pieces if t)))
    audio_seconds = float(getattr(info, "duration", 0) or 0)
    # 2026-09-26 night, brief part 2.3: a phrase Whisper looped on, or text it could not make
    # readable, is said here, where Whisper's words are made (fetch_transcript_via_audio acts).
    garbled = garbled_text(text)
    return text, {
        "garbled": garbled,
        "model": model_name or WHISPER_MODEL, "device": device or WHISPER_DEVICE,
        "compute_type": compute_type or WHISPER_COMPUTE_TYPE, "beam_size": beam_size,
        "batch_size": batch_size, "vad": vad, "audio_seconds": round(audio_seconds, 1),
        "transcribe_seconds": round(elapsed, 1),
        "realtime_factor": round(audio_seconds / elapsed, 1) if elapsed > 0 else None,
        "segments": len(pieces), "chars": len(text), "repeats_collapsed": collapsed,
    }


def fetch_transcript_via_audio(url, model_name=None, keep_audio=False):
    """The path that never touches the caption endpoint. Returns (meta, None) or
    (None, error). Two YouTube requests - the media client's extraction and the audio
    download - then the GPU, then the audio file is deleted."""
    if not AUDIO_PATH_ENABLED:
        return None, "the audio path is switched off (AUDIO_PATH_ENABLED)"
    info, err = extract_media_info(url)
    if err:
        return None, err
    vid = info["id"]
    title = info.get("title") or vid
    uploader = info.get("uploader") or info.get("channel") or "UNKNOWN"
    formats, err = formats_to_try(info)
    if err:
        return None, err
    # 2026-10-05: a format that brings nothing back falls through to the next, and what yt-dlp
    # said about each one is kept. Until today the first one was the only one, and its error was
    # thrown away: "the audio format was listed (91-21) but nothing was downloaded".
    tried, audio_path = [], None
    for n, fmt in enumerate(formats):
        kind = "audio-only" if fmt["audio_only"] else f"muxed {fmt.get('height') or '?'}p"
        print(f"  audio: format {fmt['format_id']} ({kind}, {fmt['ext']}, {fmt['acodec']}, "
              f"{fmt['abr'] or fmt['tbr'] or '?'} kbps, {fmt['size'] / 1e6:.1f} MB "
              f"{fmt['size_basis']}, {fmt['protocol']})")
        print(f"  audio: language {fmt.get('language') or 'unlabelled'} "
              f"(original-track score {fmt['original_score']}, "
              f"{fmt['dubs_rejected']} dub(s) of the same stream rejected)")

        wait_for_fetch_slot("audio")
        download_started = time.monotonic()
        result = download_audio_from_info(vid, fmt["format_id"])
        download_seconds = time.monotonic() - download_started
        note_fetch("audio")

        audio_path = downloaded_audio_path(vid)
        if audio_path is not None and audio_path.stat().st_size > 0:
            break
        why = download_failure_reason(result)
        delete_audio(vid)
        audio_path = None
        tried.append(f"{fmt['format_id']}: {why}")
        nxt = formats[n + 1]["format_id"] if n + 1 < len(formats) else None
        print(f"  audio: format {fmt['format_id']} brought nothing back ({why})"
              + (f"; trying format {nxt}" if nxt else ""))
    if audio_path is None:
        return None, (f"{DOWNLOAD_FAILED_ERROR} from the {len(tried)} format(s) tried: "
                      + "; ".join(tried))
    audio_bytes = audio_path.stat().st_size
    # FIX 2, 2026-09-25: re-encoded before the ceiling is checked and before Whisper reads it.
    try:
        audio_path, reencoded = reencode_audio(audio_path)
    except Exception as e:   # noqa: BLE001 - a file PyAV cannot read must not leave audio behind
        delete_audio(vid)
        return None, f"re-encoding the audio failed: {e.__class__.__name__}: {str(e)[:200]}"
    print(f"  audio: re-encoded to 16 kHz mono, {audio_bytes / 1e6:.1f} MB to "
          f"{reencoded['bytes'] / 1e6:.1f} MB in {reencoded['seconds']}s")
    if reencoded["bytes"] > AUDIO_MAX_BYTES:
        delete_audio(vid)
        return None, (f"the audio re-encoded to 16 kHz mono is {reencoded['bytes'] / 1e6:.1f} "
                      f"MB, over the {AUDIO_MAX_BYTES / 1e6:.0f} MB ceiling (AUDIO_MAX_BYTES)")

    try:
        text, stats = transcribe_audio(audio_path, model_name=model_name)
    except Exception as e:   # noqa: BLE001 - a GPU failure must not leave the audio behind
        delete_audio(vid)
        return None, f"local transcription failed: {e.__class__.__name__}: {str(e)[:200]}"
    finally:
        try:
            media_info_cache_path(vid).unlink()
        except OSError:
            pass
    freed = 0 if keep_audio else delete_audio(vid)
    if not text:
        # FIX 4, 2026-09-22: with the length of the audio, so the row can say "no speech:
        # 0 words in 0.9 min of audio" instead of only that something produced nothing.
        return None, (f"{NO_TEXT_ERROR} "
                      f"({float(stats.get('audio_seconds') or 0) / 60:.1f} min of audio)")
    if stats.get("garbled"):
        # 2026-09-26 night, brief part 2.3: not judged, not written, not kept.
        return None, f"{GARBLED_ERROR} {stats['garbled']}"

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_txt_path = RAW_DIR / f"{vid}.txt"
    atomic_write_text(raw_txt_path, text)
    stats.update({"format_id": fmt["format_id"], "ext": fmt["ext"],
                  "abr": fmt["abr"] or fmt["tbr"], "audio_only": fmt["audio_only"],
                  "protocol": fmt["protocol"], "language": fmt.get("language"),
                  "formats_failed": tried,
                  "audio_bytes": audio_bytes, "audio_mb": round(audio_bytes / 1e6, 2),
                  "reencoded_bytes": reencoded["bytes"],
                  "reencode_seconds": reencoded["seconds"],
                  "download_seconds": round(download_seconds, 1), "freed_bytes": freed})
    log_request("audio_transcribe", "local/faster-whisper", "ok",
                dt.datetime.now() - dt.timedelta(seconds=stats["transcribe_seconds"]),
                dt.datetime.now(), None, video_id=vid, **stats)
    print(f"  audio: {stats['audio_seconds'] / 60:.1f} min of audio, {stats['audio_mb']} MB, "
          f"transcribed in {stats['transcribe_seconds']}s ({stats['realtime_factor']}x realtime) "
          f"on {stats['device']}/{stats['model']}; "
          f"{'audio KEPT (keep_audio)' if keep_audio else 'audio deleted'}")
    return {
        "id": vid, "title": title, "uploader": uploader, "url": url,
        "transcript": text, "raw_path": raw_txt_path, "lang": "en", "track_kind": "audio",
        "audio_stats": stats,
    }, None


# --- the canary ---------------------------------------------------------------------
# One caption request for a video that is already summarised, before a slot spends
# anything else. Outcomes: "ok" (proceed), "refused" (HTTP 429: cooldown armed, budget
# halved, the slot ends), "inconclusive" (the canary could not be measured for some other
# reason: the slot ends too, loudly, without a cooldown, because a guard that cannot
# measure must not quietly pass).


def canary_cached_info(video_id):
    """The cached extraction for a canary video if its caption URL is still valid."""
    path = info_cache_path(video_id)
    if not path.exists():
        return None
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    lang, kind, url, err = choose_caption_track(info)
    if err:
        return None
    expiry = caption_url_expiry(url)
    if expiry is None or expiry - dt.timedelta(seconds=CANARY_URL_MARGIN_SECONDS) <= dt.datetime.now():
        return None
    return info


def canary_pool(limit=CANARY_POOL_SIZE):
    """The videos the canary rotates through: the most recently summarised ones whose
    transcript is still on disk, newest first. Asking for a caption this tool has already
    been given once is what keeps a refusal meaningful - the track exists and was served
    before, so a 429 is a fact about now, not about the video being unusual. Falls back to
    the seed ids when there is no history yet."""
    seen_ids, pool = set(), []
    if HISTORY_PATH.exists():
        for line in reversed(HISTORY_PATH.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                url = json.loads(line).get("url") or ""
            except json.JSONDecodeError:
                continue
            vid = video_id_from_url(url)
            if not vid or vid in seen_ids:
                continue
            seen_ids.add(vid)
            if (RAW_DIR / f"{vid}.txt").exists():
                pool.append(vid)
            if len(pool) >= limit:
                break
    for vid in CANARY_SEED_VIDEO_IDS:
        if vid not in pool and len(pool) < limit:
            pool.append(vid)
    return pool or list(CANARY_SEED_VIDEO_IDS)


def one_line(text):
    """Flatten a yt-dlp stderr tail to a single line. Details are printed inline by
    `worthwatch status` and `worthwatch paths`, and a raw newline in one of them wrecks everything under
    it - which is what a 429 detail, being several lines of stderr, always contains."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def canary_log(state):
    """The per-canary ledger in rate-state.json: one entry per canary request made, with
    the video it asked for and what came back. Before it existed there was only
    `last_canary`, which keeps one entry and so can never show a pattern."""
    return state.setdefault("canary_log", [])


def pick_canary_video(state, pool=None):
    """A different video each run: the least recently canaried member of the pool, so the
    rotation walks the whole pool before it repeats anything. Never-canaried videos come
    first, newest first among them. None when the pool is empty, which it is on a fresh
    machine until the tool has read a video."""
    pool = list(pool or canary_pool())
    if not pool:
        return None
    last_used = {}
    for entry in canary_log(state):
        vid = entry.get("video_id")
        if vid:
            last_used[vid] = max(entry.get("at", ""), last_used.get(vid, ""))
    return min(pool, key=lambda v: (last_used.get(v, ""), pool.index(v)))


def canary_request(video_id, label="canary"):
    """One canary caption request for one video: the cached caption URL if it is still
    valid (one request), otherwise a fresh extraction, the settle, and the caption (two).
    Returns (outcome, detail)."""
    with request_label(label):
        info = canary_cached_info(video_id)
        fresh = False
        if info is None:
            try:
                info, err = extract_video_info(f"https://www.youtube.com/watch?v={video_id}")
            except RateLimitError as e:
                return "refused", f"extraction of canary {video_id} refused: {str(e)[-160:]}"
            except RuntimeError as e:
                return "inconclusive", f"canary extraction failed: {e}"
            if err:
                return "inconclusive", f"canary extraction failed: {err}"
            fresh = True
        lang, kind, url, err = choose_caption_track(info)
        if err:
            return "inconclusive", f"canary {video_id}: {err}"
        save_info_cache(info)
        if fresh:
            wait_to_settle(dt.datetime.now(), CAPTION_SETTLE_SECONDS)
        try:
            download_captions_from_info(video_id, lang)
        except RateLimitError as e:
            return "refused", f"canary caption request for {video_id} refused: {str(e)[-160:]}"
        except RuntimeError as e:
            return "inconclusive", f"canary caption request failed: {e}"
        cleaned, err = read_downloaded_vtt(video_id)
        if err:
            return "inconclusive", f"canary {video_id}: caption {err}"
    # The canary's copy of the transcript is discarded; raw/ keeps the real one.
    return "ok", (f"canary {video_id} ({lang}, {kind}, "
                  f"{'fresh extraction' if fresh else 'cached URL'})")


def media_canary_request(video_id, label="canary_media"):
    """The media-path canary: one extraction with the media client for a video this tool
    has already transcribed, and the format check that decides whether the audio path
    could actually run. Returns (outcome, detail).

    What it measures, exactly: that this network is still being served a real, downloadable
    format ladder by the player, which is the thing that fails on this path (a ladder of
    storyboards and no audio-bearing format, "YouTube is forcing SABR streaming for this
    client", yt-dlp #12482). A ladder with nothing in it is that failure, and it is the one
    worth catching before the run spends a listing.

    What it does NOT measure: whether googlevideo will hand over the bytes. Only a real
    media download does that, and a real media download is 12 MB and a budget slot, which
    is not what a canary is for. So a refusal there is caught per video by
    fetch_transcript_by_plan(), where it arms the audio cooldown like any other 429, and
    this function says "the player is serving formats", never "the path is fine"."""
    with request_label(label):
        try:
            info, err = extract_media_info(f"https://www.youtube.com/watch?v={video_id}")
        except RateLimitError as e:
            return "refused", f"media extraction of canary {video_id} refused: {str(e)[-160:]}"
        except RuntimeError as e:
            return "inconclusive", f"media canary extraction failed: {e}"
    if err:
        return "inconclusive", f"media canary {video_id}: {err}"
    # The canary is not going to download this; its cached extraction is not an artefact.
    try:
        media_info_cache_path(video_id).unlink()
    except OSError:
        pass
    fmt, err = choose_audio_format(info)
    if err:
        return "inconclusive", f"media canary {video_id}: {err}"
    return "ok", (f"media canary {video_id}: the player served format "
                  f"{fmt['format_id']} ({'audio-only' if fmt['audio_only'] else 'muxed'}, "
                  f"{fmt['ext']}, {fmt['size'] / 1e6:.1f} MB {fmt['size_basis']}, "
                  f"{fmt['protocol']}); no media byte fetched")


# Which request each path's canary makes, and what it is called in logs/requests.jsonl.
# The caption one asks /api/timedtext for a subtitle; the media one asks the player for a
# format ladder. Both cost one request on a video already summarised, and both report the
# same three outcomes. The caption labels are unchanged from before the media canary
# existed, so `worthwatch requests` and the suites read the old rows and the new ones alike.
PATH_CANARY = {"captions": canary_request, "audio": media_canary_request}
PATH_CANARY_LABEL = {"captions": "canary", "audio": "canary_media"}
# What a request on this path is called in a sentence: "no caption request at all".
PATH_NOUN = {"captions": "caption", "audio": "media"}


def run_canary(video_ids=None, path="captions"):
    """Returns (outcome, detail, video_id). A different video each run.

    `path` chooses which endpoint is probed. The rotation, the pool and the ledger are
    shared between the paths on purpose: they are all questions about "is this address
    being refused", and the answer is only readable if every request that asked it is in
    one ledger, tagged with the path that asked."""
    pool = list(video_ids or canary_pool())
    if not pool:
        return "inconclusive", "no canary video configured", None
    chosen = pick_canary_video(load_rate_state(), pool)
    outcome, detail = PATH_CANARY[path](chosen, label=PATH_CANARY_LABEL[path])
    return outcome, detail, chosen


def run_canary_probe(refused_video_id, pool=None, path="captions"):
    """The paired test: one more request on the same path, immediately, for a DIFFERENT
    video, so the address and the video are told apart at the same minute instead of six
    hours apart. Returns (outcome, detail, video_id), or (None, why not, None) when it
    does not run. At most one probe per refusal - `probe_for` stamps the 429 it belongs
    to, per path."""
    if not CANARY_PROBE_ON_REFUSAL:
        return None, "disabled (CANARY_PROBE_ON_REFUSAL)", None
    state = load_rate_state()
    k_probe, k_429 = _path_key(path, "probe_for"), _path_key(path, "last_429")
    if state.get(k_probe) and state.get(k_probe) == state.get(k_429):
        return None, "this refusal has already been probed once", None
    pool = [v for v in (pool or canary_pool()) if v != refused_video_id]
    if not pool:
        return None, "no second video to compare against", None
    if path == "captions":
        # A video whose cached caption URL is still valid costs one request instead of two.
        cached = [v for v in pool if canary_cached_info(v)]
        if cached:
            other = cached[0]
        elif CANARY_PROBE_MAY_EXTRACT:
            other = pick_canary_video(state, pool)
        else:
            return None, "no second video with a still-valid cached caption URL", None
    else:
        # The media canary is one request either way; there is no cheaper second video.
        other = pick_canary_video(state, pool)
    state[k_probe] = state.get(k_429)
    save_rate_state(state)
    with measuring_a_refusal():
        outcome, detail = PATH_CANARY[path](other, label=f"{PATH_CANARY_LABEL[path]}_probe")
    return outcome, detail, other


def note_canary(outcome, detail, video_id=None, role="canary", path="captions", **extra):
    """Record one canary request: the summary line `worthwatch status` prints, and an entry in the
    ledger the attribution is computed from. `last_canary` stays the caption path's key
    (rate-state.json and `worthwatch status` have always read it as that); the audio path's is
    `audio_last_canary`, the same prefixing the cooldown and the cap use."""
    state = load_rate_state()
    entry = {"at": dt.datetime.now().isoformat(timespec="seconds"), "outcome": outcome,
             "video_id": video_id, "role": role, "path": path,
             "detail": one_line(detail)[:200]}
    entry.update({k: v for k, v in extra.items() if v is not None})
    log = canary_log(state)
    log.append(entry)
    del log[:-CANARY_LOG_KEEP]
    if role == "canary":
        state[_path_key(path, "last_canary")] = dict(entry)
    save_rate_state(state)
    return entry


def last_canary(path="captions", state=None):
    """The most recent canary on one path, or {}."""
    state = load_rate_state() if state is None else state
    return state.get(_path_key(path, "last_canary")) or {}


def canary_attribution(state=None, window_hours=CANARY_ATTRIBUTION_WINDOW_HOURS,
                       path="captions"):
    """Whether the recent refusals ON ONE PATH follow the address or the video, read off
    the ledger rather than assumed. Returns (verdict, sentence, evidence).

    The path filter matters: mixing a timedtext refusal with a googlevideo success would
    produce a "different video served 90 seconds later" contrast that is really a
    different *endpoint*, and the rule would launder that into a verdict about a video.
    Ledger entries written before 2026-09-06 carry no path and are read as captions,
    which is what they were.

      "address"  two or more DIFFERENT videos refused inside the window. No property of
                 any one video explains that; what those requests share is this IP.
      "video"    one video refused, and a DIFFERENT video served within
                 CANARY_CONTRAST_MINUTES of that refusal. Close enough in time to be a
                 comparison rather than two different moments.
      "unknown"  a refusal, but nothing to contrast it with: only ever one video asked
                 for (which is every day this tool ran before 2026-09-06, when the canary
                 was a constant), or successes too far away in time to mean anything.
      "none"     no refusal in the window.

    A paired probe - a second video asked for within a minute of a refusal, on purpose -
    outranks all of it and is checked first. Spread over hours, "the address recovered"
    and "that video was always fine" produce an identical ledger; only the pair separates
    them, which is why run_canary_probe() spends a request to create one."""
    state = load_rate_state() if state is None else state
    now = dt.datetime.now()
    cutoff = (now - dt.timedelta(hours=window_hours)).isoformat(timespec="seconds")
    rows = [e for e in canary_log(state) if e.get("at", "") >= cutoff
            and e.get("video_id") and e.get("outcome") in ("ok", "refused")
            and e.get("path", "captions") == path]
    refusals = [e for e in rows if e["outcome"] == "refused"]
    successes = [e for e in rows if e["outcome"] == "ok"]
    refused = {e["video_id"] for e in refusals}
    served = {e["video_id"] for e in successes}
    probes = [e for e in rows if e.get("role") == "probe"]
    evidence = {"window_hours": window_hours, "path": path, "refused": sorted(refused),
                "served": sorted(served), "paired_probes": probes,
                "contrast_minutes": CANARY_CONTRAST_MINUTES}

    for probe in reversed(probes):
        other = probe.get("after") or "the canary"
        if probe["outcome"] == "refused":
            return "address", (f"paired test at {probe['at']}: {other} and {probe['video_id']} "
                               f"were both refused within a minute, from one address"), evidence
        return "video", (f"paired test at {probe['at']}: {other} was refused and "
                         f"{probe['video_id']} was served a minute later, same address"), evidence

    if len(refused) >= 2:
        return "address", (f"{len(refused)} different videos refused in {window_hours}h "
                           f"({', '.join(sorted(refused))}); no property of one video does that"), evidence
    if not refusals:
        return "none", f"no {path} refusal in the last {window_hours}h", evidence

    last = max(refusals, key=lambda e: e["at"])
    only = last["video_id"]
    at = parse_ts(last["at"])
    near, nearest_gap = [], None
    for ok in successes:
        if ok["video_id"] == only:
            continue
        ts = parse_ts(ok.get("at"))
        if ts is None or at is None:
            continue
        gap_min = abs((ts - at).total_seconds()) / 60
        nearest_gap = gap_min if nearest_gap is None else min(nearest_gap, gap_min)
        if gap_min <= CANARY_CONTRAST_MINUTES:
            near.append(ok["video_id"])
    if near:
        return "video", (f"{only} was refused at {last['at']} while {', '.join(sorted(set(near)))} "
                         f"was served within {CANARY_CONTRAST_MINUTES} minutes of it"), evidence
    if nearest_gap is not None:
        return "unknown", (f"{only} was refused at {last['at']}; the nearest success on another "
                           f"video is {nearest_gap / 60:.1f}h away, which is a different moment, "
                           f"not a different video"), evidence
    return "unknown", (f"only {only} has been asked for; one video cannot tell an address limit "
                       f"from a video limit"), evidence


CANARY_REUSE_SECONDS = 1800   # a canary that passed this recently covers the whole slot

# --- which transcript path a slot uses -------------------------------------------------
# Until 2026-09-06 a refused canary ended the slot, because captions were the only way in
# and there was nothing else to try. Now a refusal is a fact about ONE path: it takes that
# path out for the slot and hands the slot to the next usable one. Only "inconclusive"
# still ends a slot - a guard that cannot measure must not quietly pass.
#
# Nothing here knows which path is which. It canaries the path that is about to lead, and
# routes to whatever is left; PATH_PREFERENCE and the two _PATH_ENABLED switches decide
# what leads. That symmetry is the fix for the 09-06 shape, where "the canary" and "the
# caption path" were the same object and a caption refusal therefore looked like a verdict
# on the whole slot.
_SLOT_PLAN = {"primary": None, "fallback": None, "why": None}


def path_enabled(path):
    return {"captions": CAPTION_PATH_ENABLED, "audio": AUDIO_PATH_ENABLED}.get(path, False)


def path_order():
    """The enabled transcript paths, best first."""
    return [p for p in PATH_PREFERENCE if path_enabled(p)]


# --ignore-cooldown, held here rather than threaded through eight call sites. Until now it
# only lifted the one gate in main(); the per-path checks further down still refused, so
# the flag printed a warning and then changed nothing. One switch, read wherever a
# cooldown is enforced, is the only way it can mean what it says.
_IGNORE_COOLDOWN = {"on": False}


def path_blocked(path):
    """Why this path cannot be used right now, as a sentence, or None when it can be."""
    if not path_enabled(path):
        return "switched off"
    left = cooldown_remaining(path)
    if left > 0 and not _IGNORE_COOLDOWN["on"]:
        return (f"in cooldown until {cooldown_until(path):%Y-%m-%d %H:%M} "
                f"({format_duration(left)} left)")
    if budget_left_today(path) <= 0:
        return f"today's budget is spent ({used_today(path)}/{daily_cap_for(path)})"
    return None


def usable_paths():
    """The paths that could fetch a transcript this minute, best first."""
    return [p for p in path_order() if path_blocked(p) is None]


def audio_path_available():
    return path_blocked("audio") is None


def slot_plan():
    """This slot's plan. Before a gate has set one - a caller that never went through
    slot_may_proceed(), or a suite driving process_candidates() directly - it is the
    configured order, so the default is whatever the switches say and not a stale literal
    naming a path that may be switched off."""
    if _SLOT_PLAN["why"] is None:
        order = path_order()
        return {"primary": order[0] if order else None,
                "fallback": order[1] if len(order) > 1 else None,
                "why": "no slot gate has run; the configured order"}
    return dict(_SLOT_PLAN)


def set_slot_plan(primary, fallback, why):
    """A plan may not name a switched-off path: a fallback that is off is dropped, and a
    primary that is off hands the lead to the fallback."""
    if fallback is not None and not path_enabled(fallback):
        fallback = None
    if primary is not None and not path_enabled(primary):
        primary, fallback = fallback, None
    _SLOT_PLAN.update({"primary": primary, "fallback": fallback, "why": why})
    return slot_plan()


def _record_attribution(path="captions"):
    """Keep the current address-or-video verdict in rate-state so `worthwatch status`, `worthwatch paths`
    and tomorrow can read it without recomputing."""
    verdict, sentence, _ = canary_attribution(path=path)
    state = load_rate_state()
    state[_path_key(path, "attribution")] = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "verdict": verdict, "why": sentence}
    save_rate_state(state)
    return verdict, sentence


def report_no_path(mode_label):
    """Why nothing can be fetched at all, path by path. Reached only when every enabled
    path is blocked; a caption cooldown on its own never gets here any more."""
    print(f"No transcript path is available; skipping {mode_label}, nothing was fetched.")
    for path in PATH_PREFERENCE:
        print(f"  {path}: {path_blocked(path)}")
    if not path_order():
        print("  (both paths are switched off - see CAPTION_PATH_ENABLED / "
              "AUDIO_PATH_ENABLED)")
    print("  `worthwatch status` and `worthwatch paths` show the same thing; --ignore-cooldown overrides "
          "a cooldown.")


def slot_may_proceed(mode_label, skip_canary=False):
    """The gate every fetching mode passes through first, and where the slot's transcript
    path is decided. False means the slot ends. The hourly slot runs --channels and then
    --topics as two processes; a canary that passed within CANARY_REUSE_SECONDS covers
    both, so the second does not spend another request."""
    order = usable_paths()
    if skip_canary:
        plan = set_slot_plan(order[0] if order else None,
                             order[1] if len(order) > 1 else None,
                             "canary skipped (--no-canary)")
        if plan["primary"] is None:
            print("Canary skipped (--no-canary), but no path is available.")
            set_slot_plan(None, None, "no transcript path was available")
            report_no_path(mode_label)
            return False
        print(f"Canary skipped (--no-canary); this slot leads on the {plan['primary']} path.")
        return True

    if not order:
        set_slot_plan(None, None, "no transcript path was available")
        report_no_path(mode_label)
        return False

    lead = order[0]
    # A cooldown already in force on the leading path is a refusal that has been measured;
    # usable_paths() has already dropped it, so the canary goes to whatever is leading now
    # and no request is ever spent re-proving a refusal the tool already recorded.
    blocked = [f"{p} ({path_blocked(p)})" for p in path_order() if p not in order]
    if blocked:
        print(f"Unavailable this slot: {', '.join(blocked)}. No canary spent on it.")

    prev = last_canary(lead)
    at = parse_ts(prev.get("at"))
    if prev.get("outcome") == "ok" and at is not None and \
            0 <= (dt.datetime.now() - at).total_seconds() < CANARY_REUSE_SECONDS:
        set_slot_plan(lead, order[1] if len(order) > 1 else None,
                      f"{lead} canary ok {prev.get('video_id') or ''}, reused")
        print(f"Canary ({lead}): ok {format_duration((dt.datetime.now() - at).total_seconds())} "
              f"ago, reused ({prev.get('detail', '')}).")
        return True

    outcome, detail, vid = run_canary(path=lead)
    note_canary(outcome, detail, video_id=vid, path=lead)
    if outcome == "ok":
        set_slot_plan(lead, order[1] if len(order) > 1 else None, f"{lead} canary ok ({vid})")
        print(f"Canary ({lead}): ok ({detail}).")
        return True

    if outcome == "refused":
        print(f"Canary ({lead}): REFUSED. {detail}")
        p_outcome, p_detail, p_vid = run_canary_probe(vid, path=lead)
        if p_outcome is None:
            print(f"  Paired probe: not run - {p_detail}")
        else:
            note_canary(p_outcome, p_detail, video_id=p_vid, role="probe", path=lead, after=vid)
            print(f"  Paired probe ({p_vid}): {p_outcome.upper()}. {p_detail}")
        verdict, sentence = _record_attribution(lead)
        print(f"  Refusal follows: {verdict.upper()} - {sentence}")
        # The 429 armed this path's own cooldown inside run_yt_dlp, so asking again is
        # what routes the slot: the refused path has dropped out, and whatever is left
        # takes over. This is the whole point of the canary - it chooses a path, it does
        # not end the slot.
        rest = [p for p in usable_paths() if p != lead]
        if rest:
            set_slot_plan(rest[0], None, f"{lead} canary refused ({vid}); follows {verdict}")
            print(f"  The {lead} path is off for the rest of this slot. The {rest[0]} path "
                  f"leads instead, and makes no {PATH_NOUN[lead]} request at all.")
            return True
        set_slot_plan(None, None, f"the {lead} canary was refused and no other path "
                                  f"was available")
        print(f"  Ending {mode_label} here; no other path is available:")
        for p in path_order():
            if p != lead:
                print(f"    {p}: {path_blocked(p)}")
        return False

    set_slot_plan(None, None,
                  f"the {lead} canary was inconclusive, so the slot was not spent")
    print(f"Canary ({lead}): INCONCLUSIVE. {detail}\n  Ending {mode_label} here: the slot "
          f"cannot be measured, so it is not spent. Next slot tries again.")
    return False


def fetch_transcript_by_plan(url, plan=None):
    """One transcript, by whichever path this slot's plan puts first, falling back to the
    other. Returns (meta, error). Each path spends its own budget and honours its own
    cooldown and gap, so an attempt that is refused on one path costs the other nothing -
    not its allowance, not its cooldown, not its turn.

    A 429 on the first path is caught here rather than thrown, because being refused on
    one path is exactly the moment the other is worth trying - throwing would end the run
    at the one point the fallback exists for. RateLimitError is re-raised only when every
    path that actually made a request was refused, which is the case the caller means when
    it stops."""
    plan = plan or slot_plan()
    order = [p for p in (plan.get("primary"), plan.get("fallback")) if p]
    notes, tried, refused, last_refusal = [], 0, 0, None
    for i, path in enumerate(order):
        # Re-checked per video, not once at the top of the run: a 429 on an earlier video
        # arms that path's cooldown mid-run, and it is that path that has to stop, not
        # this one. path_blocked() is per path by construction, which is what keeps a
        # caption cooldown from ever standing in the audio path's way.
        why = path_blocked(path)
        if why:
            notes.append(f"{path}: {why}")
            continue
        tried += 1
        try:
            if path == "captions":
                wait_for_fetch_slot("captions")
                note_fetch("captions")
                meta, err = fetch_transcript(url)
            else:
                meta, err = fetch_transcript_via_audio(url)
        except RateLimitError as e:
            refused, last_refusal = refused + 1, e
            notes.append(f"{path}: refused (HTTP 429)")
            nxt = [p for p in order[i + 1:] if path_blocked(p) is None]
            print(f"  {path} refused; "
                  + (f"trying the {nxt[0]} path" if nxt else "no other path is available"))
            continue
        if meta:
            return meta, None
        notes.append(f"{path}: {err}")
    if tried and refused == tried:
        raise last_refusal
    return None, "; ".join(notes) or "no transcript path was available"


def estimate_tokens(text):
    return max(1, len(text) // CHARS_PER_TOKEN)


def verdict_note(meta):
    """The VERDICT line of the summary prompt: what the transcript triage decided and why,
    so the summary knows whether it is writing up a mechanism, a road not taken, or an
    open question. A hand-run URL has no verdict and says so."""
    verdict = (meta or {}).get("verdict")
    reason = ((meta or {}).get("verdict_reason") or "").strip()
    reason = reason if reason.endswith((".", "!", "?")) else (reason + "." if reason else "")
    if verdict == "OPPORTUNITY":
        return ("OPPORTUNITY - the triage read the transcript and found a documented way people "
                f"are getting paid that is not already in my files. Reason given: {reason} "
                "So section 3 ends with the Opportunity line, and every item in section 6 says "
                "\"Applies to: a road not taken\".")
    if verdict == "YES":
        return ("YES - the triage read the transcript and found a usable mechanism for one of "
                "my projects, or one that changes how I work or this tool. "
                f"Reason given: {reason}")
    if verdict == "MAYBE":
        return ("MAYBE - the triage read the transcript and could not settle it. Reason given: "
                f"{reason} Section 6 decides: quote what it can, and say \"nothing\" if it "
                "cannot.")
    return "not triaged - a URL run by hand. Judge it on what it says."


def build_prompt(context_text, meta, transcript_text, extra="", ideas=False):
    prompt = SUMMARY_INSTRUCTIONS.format(
        reader=READER, applies_choices=applies_choices(),
        context=context_text, title=meta["title"], uploader=meta.get("uploader", "UNKNOWN"),
        url=meta["url"], verdict_note=verdict_note(meta), transcript=transcript_text,
    )
    # The ideas lane: its part comes after the write-up prompt, which stays word for word, and
    # only on the writing brain's write-ups (summarize_claude passes ideas=True).
    if ideas:
        prompt += "\n\n" + ideas_instructions()
    # `extra` is the re-ask: the same prompt with the defect that sank the first answer
    # named at the end, where the model reads it last (see REASK_NOTE).
    return prompt + ("\n\n" + extra if extra else "")


def split_into_chunks(text, max_chars):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current, current_len = [], [], 0
    for s in sentences:
        if current and current_len + len(s) > max_chars:
            chunks.append(" ".join(current))
            current, current_len = [s], len(s)
        else:
            current.append(s)
            current_len += len(s) + 1
    if current:
        chunks.append(" ".join(current))
    return chunks


def ollama_is_running():
    try:
        urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


def ollama_list_models():
    with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m["name"] for m in data.get("models", [])]


def ollama_pull(model_name):
    body = json.dumps({"name": model_name, "stream": True}).encode("utf-8")
    req = urllib.request.Request(f"{OLLAMA_HOST}/api/pull", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=None) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            status = obj.get("status", "")
            total, completed = obj.get("total"), obj.get("completed")
            if total and completed:
                print(f"\r  {status}: {completed / total * 100:5.1f}%" + " " * 10, end="", flush=True)
            else:
                print(f"\r  {status}" + " " * 20, end="", flush=True)
    print()


# --- when the local model does not answer, 2026-09-26 night --------------------------------
# Brief part 2.1. ollama_generate turned only an HTTP error into a RuntimeError, so a refused
# connection (URLError) or a timeout escaped every "except RuntimeError" in the pipeline and
# ended the run, and a hung Ollama cost 120 s a listing and 600 s a transcript, past the run's
# deadline. Now every way of not answering is one OllamaDown, and after the first the run stops
# asking: the rest fail at once instead of each paying the timeout again.
class OllamaDown(RuntimeError):
    """The local model did not answer: refused, unreachable, or too slow. A RuntimeError, so
    every place that treats one call's failure as that video's failure still does."""


_OLLAMA = {"down": None}      # (when, why) once the local model has failed to answer this run


def ollama_down():
    """(when, why) if the local model stopped answering in this run, else None."""
    return _OLLAMA["down"]


def trip_ollama(why):
    """The local model did not answer: say so once, and ask it nothing more in this run."""
    if _OLLAMA["down"] is None:
        _OLLAMA["down"] = (dt.datetime.now().strftime("%H:%M"), why)
        note_problem(f"The local model (Ollama) did not answer from {_OLLAMA['down'][0]} "
                     f"({why}), so nothing more was asked of it in this run. Nothing it had to "
                     f"judge was lost: a listing it could not triage waits in seen.json to be "
                     f"triaged, and a transcript it could not judge waits in the retry queue.")


def ollama_failure_words(error, timeout):
    """What went wrong with a call to Ollama, in words a person reads."""
    reason = getattr(error, "reason", error)
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason).lower():
        return f"no answer within {timeout:.0f} s"
    if isinstance(reason, ConnectionRefusedError) or "refused" in str(reason).lower():
        return f"the connection to {OLLAMA_HOST} was refused, so it is not running"
    return f"{reason.__class__.__name__}: {one_line(str(reason))[:120]}"


def ensure_local_model(model_name):
    """Make sure Ollama has the model, when the quick brain is Ollama; nothing otherwise."""
    if quick_brain() != "ollama":
        return
    if not ollama_is_running():
        why = f"it is not reachable at {OLLAMA_HOST}"
        trip_ollama(why)
        # 2026-09-26 night: raised, not sys.exit(1), so a run that meets it can carry on with
        # what needs no local model and say what it skipped.
        raise OllamaDown(f"the local model (Ollama) did not answer: {why}. Start Ollama (the "
                         f"app, or `ollama serve`) and try again.")
    if model_name not in ollama_list_models():
        print(f"{model_name} is not pulled yet. Pulling now (this can take a while)...")
        ollama_pull(model_name)


def ollama_generate(model, prompt, num_ctx=NUM_CTX, timeout=900, num_predict=None, think=False,
                    temperature=None):
    # qwen3.5 (and similar hybrid-thinking models) burns num_predict on a hidden
    # reasoning block before ever writing to `response`; with a tight cap that means
    # done_reason="length" and an EMPTY response (confirmed directly against the API:
    # think unset -> 5.3s, response ''; think=False -> 0.1s, response 'yes'). Every
    # call in this tool wants the fast, direct answer, not the reasoning trace.
    options = {"num_ctx": num_ctx}
    if num_predict is not None:
        options["num_predict"] = num_predict
    if temperature is not None:
        # Triage passes 0: a verdict that goes into seen.json for good should be the same
        # verdict on a rerun, so a wrong one can be reproduced and fixed rather than
        # re-rolled. Summaries keep the model's default.
        options["temperature"] = temperature
    body = json.dumps({
        "model": model, "prompt": prompt, "stream": False,
        "think": think, "options": options,
    }).encode("utf-8")
    if _OLLAMA["down"]:
        when, why = _OLLAMA["down"]
        raise OllamaDown(f"the local model (Ollama) has not answered since {when} ({why}); not "
                         f"asked again in this run")
    req = urllib.request.Request(f"{OLLAMA_HOST}/api/generate", data=body,
                                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Ollama returned an error: {e.read().decode('utf-8', errors='replace')}") from e
    except (urllib.error.URLError, OSError) as e:
        # Refused, unreachable, reset or timed out (TimeoutError is an OSError): not an answer.
        why = ollama_failure_words(e, timeout)
        trip_ollama(why)
        raise OllamaDown(f"the local model (Ollama) did not answer: {why}") from e
    except ValueError as e:
        raise RuntimeError(f"Ollama answered with something that is not JSON: {e}") from e
    if "response" not in data:
        raise RuntimeError(f"Ollama answered without a response: {one_line(str(data))[:160]}")
    return data["response"]


SUMMARY_NUM_PREDICT = 2000    # ~600 words plus headings, generous but bounded
EXTRACTION_NUM_PREDICT = 800  # a terse bullet list of one transcript chunk


def summarize_local(model, context_text, meta, transcript_text, num_ctx=NUM_CTX, extra=""):
    """The short write-up, on the quick brain. A local model's window is small, so a long
    transcript is read in chunks there; the other brains take it whole."""
    if quick_brain() != "ollama":
        return quick_generate(build_prompt(context_text, meta, transcript_text, extra)), False
    overhead_tokens = estimate_tokens(build_prompt(context_text, meta, "", extra))
    available_for_transcript = num_ctx - overhead_tokens - RESPONSE_RESERVE_TOKENS

    if estimate_tokens(transcript_text) <= available_for_transcript:
        prompt = build_prompt(context_text, meta, transcript_text, extra)
        return quick_generate(prompt, model=model, num_ctx=num_ctx,
                              num_predict=SUMMARY_NUM_PREDICT), False

    chunk_char_budget = int(available_for_transcript * CHARS_PER_TOKEN * 0.8)
    chunks = split_into_chunks(transcript_text, max(chunk_char_budget, 2000))
    notes = []
    for i, chunk in enumerate(chunks, 1):
        print(f"  chunk {i}/{len(chunks)}...")
        extraction_prompt = EXTRACTION_INSTRUCTIONS.format(i=i, n=len(chunks), chunk=chunk)
        note = quick_generate(extraction_prompt, model=model, num_ctx=num_ctx,
                              num_predict=EXTRACTION_NUM_PREDICT)
        notes.append(f"[part {i} of {len(chunks)}]\n{note.strip()}")
    merged_notes = "\n\n".join(notes)
    prompt = build_prompt(context_text, meta, merged_notes, extra)
    response = quick_generate(prompt, model=model, num_ctx=num_ctx, num_predict=SUMMARY_NUM_PREDICT)
    prefix = f"(This video's transcript was long enough that it was summarized in {len(chunks)} chunks and merged.)\n\n"
    return prefix + response, True


# --- how long a summary may take, 2026-09-21 ---------------------------------------------
# FIX 3. Until today every summary got 600 seconds, whatever it was summarising, and on
# 2026-09-21 a 51.7 minute conference talk was killed at 600 seconds after it had already
# cost two YouTube requests, a 51 MB download and 127 seconds of GPU. A fixed timeout is a
# guess about the longest talk applied to every talk.
#
# The line below is least squares over the 67 summaries in the logs since 2026-09-17, all of
# them Sonnet (scripts/fit_timeout.py recomputes it and prints every number):
#
#     summary seconds = 73.5 + 4.58 x minutes of audio,  r = 0.475, residual sd 83.5 s
#
# The fit is weak and that IS the finding: length explains about a quarter of the time and
# how much the model chooses to write explains the rest. So the multiplier is chosen from
# the spread rather than from the line. The worst ratio of actual to predicted ever observed
# is 2.26 (a 14.3 minute video that took 313 s against a prediction of 139 s), and k = 3
# clears that with a third again on top.
SUMMARY_FIT_INTERCEPT = 73.5
SUMMARY_FIT_SLOPE = 4.58
SUMMARY_TIMEOUT_K = 3
# The floor is the old fixed timeout, kept: it governs everything under about 28 minutes of
# audio, which is most videos, so nothing short is affected by any of this.
SUMMARY_TIMEOUT_FLOOR = 600
# The ceiling is 2.8 times the slowest summary ever recorded (423.6 s) and binds only above
# about 82 minutes of audio. MAX_VIDEO_SECONDS is 90 minutes, so it is reachable and rare,
# which is what a ceiling should be.
SUMMARY_TIMEOUT_CEILING = 1200
# And the run budget, which is the only thing that bounds a whole scheduled run. An hourly
# schedule that runs --channels then --topics gets two invocations of 1350 s, plus about 150 s
# each of tail for a fetch already running when the deadline passes: 50 minutes, under the
# hour, so a run that overruns never eats the next one. No per-call ceiling can do that on its
# own: six write-ups at a fixed 600 s are already an hour.
RUN_DEADLINE_SECONDS = 1350
_RUN_CLOCK = {"deadline": None}


def start_run_clock(seconds=RUN_DEADLINE_SECONDS):
    """Start this invocation's wall-clock budget. Scheduled fetching modes only."""
    _RUN_CLOCK["deadline"] = time.monotonic() + seconds


def run_seconds_left():
    """Seconds this invocation may still spend, or None when no clock was started.

    None means a hand-run command, where you are at the terminal waiting and a scheduled run
    you are not in has no claim on your time."""
    if _RUN_CLOCK["deadline"] is None:
        return None
    return max(0.0, _RUN_CLOCK["deadline"] - time.monotonic())


def may_start_a_summary():
    """(may it, why not). A summary is not started with less than the floor left: it would
    be killed part way and the run would have paid for it twice."""
    left = run_seconds_left()
    if left is None or left >= SUMMARY_TIMEOUT_FLOOR:
        return True, ""
    return False, (f"the run's time budget is spent: {left:.0f} s left of "
                   f"{RUN_DEADLINE_SECONDS} s, under the {SUMMARY_TIMEOUT_FLOOR} s floor")


def predicted_summary_seconds(minutes):
    """The fit, or None when the length is unknown."""
    if not minutes or minutes <= 0:
        return None
    return SUMMARY_FIT_INTERCEPT + SUMMARY_FIT_SLOPE * minutes


def summary_timeout_for(minutes, attempt=1, seconds_left=None):
    """(seconds, which bound decided it) for one summary call.

    max(floor, k x predicted x attempt), under the ceiling, and never past what is left of
    the run. The attempt multiplier is what makes a retry worth making: repeating the same
    timeout on the same transcript is repeating the same call and expecting a different
    answer, and it is also how a record reaches "NOT WRITTEN (too long for the ceiling)"."""
    predicted = predicted_summary_seconds(minutes)
    if predicted is None:
        timeout, why = float(SUMMARY_TIMEOUT_FLOOR), "floor, length unknown"
    else:
        wanted = SUMMARY_TIMEOUT_K * predicted * max(1, int(attempt))
        if wanted <= SUMMARY_TIMEOUT_FLOOR:
            timeout, why = float(SUMMARY_TIMEOUT_FLOOR), f"floor, the fit wanted {wanted:.0f} s"
        elif wanted >= SUMMARY_TIMEOUT_CEILING:
            timeout, why = float(SUMMARY_TIMEOUT_CEILING), f"ceiling, the fit wanted {wanted:.0f} s"
        else:
            timeout, why = wanted, (f"fit, {SUMMARY_TIMEOUT_K} x {predicted:.0f} s predicted for "
                                    f"{minutes} min" + (f" on attempt {attempt}" if attempt > 1 else ""))
    left = run_seconds_left() if seconds_left is None else seconds_left
    if left is not None and left < timeout:
        return float(left), f"deadline, {left:.0f} s left of the run ({why} wanted {timeout:.0f} s)"
    return timeout, why


def claude_generate(prompt, timeout=600):
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError("claude CLI not found on PATH.")
    proc = subprocess.Popen([claude_path, "-p", "--model", "sonnet"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8", errors="replace", **group_kwargs())
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError(f"claude CLI timed out after {timeout}s and was killed.")
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {stderr.strip()[:1000]}")
    return stdout.strip()


# --- the brains, behind one interface --------------------------------------------------------
# Two jobs need a model. The quick ones (triage, short screens, a one-sentence squeeze) go to
# quick_generate, the write-ups and cards to write_generate. Which brain does each comes from
# settings.toml [brain]: kind for the write-ups, quick for the quick jobs when it should be a
# different one (a local Ollama model for triage and claude to write is how this tool started).
# See brains.py for what each brain is.
QUICK_BRAIN = ""                # empty: the writing brain does the quick jobs too
BRAIN_MODEL = ""                # settings [brain] model
QUICK_MODEL = ""                # settings [brain] quick_model
API = {"provider": "", "base_url": "", "key_env": ""}
CLAUDE_MODELS = {"quick": "haiku", "write": "sonnet", "large": "opus"}
CODEX_TIMEOUT_GRACE = 5


class AgentMode(RuntimeError):
    """The brain is agent mode: worthwatch calls no model, your own agent does the judging."""


AGENT_MODE_WORDS = ("Your brain is agent mode, so worthwatch calls no model itself: open your AI "
                    "agent with worthwatch's MCP server (`worthwatch mcp`) and ask it to check your "
                    "channels and judge the videos. Or choose a brain that worthwatch can call: "
                    "`worthwatch init --brain claude` (or codex, api, ollama).")


def quick_brain():
    return QUICK_BRAIN or BRAIN


def quick_generate(prompt, model=None, num_ctx=NUM_CTX, timeout=900, num_predict=None,
                   temperature=None):
    """A quick job, on the quick brain. `model` is a local model's name, used only when the
    quick brain is Ollama."""
    if quick_brain() == "ollama":
        return ollama_generate(model or QUICK_MODEL or DEFAULT_LOCAL_MODEL, prompt, num_ctx=num_ctx,
                               timeout=timeout, num_predict=num_predict, temperature=temperature)
    return generate_with(quick_brain(), prompt, "quick", timeout)


def write_generate(prompt, timeout=600, size="write"):
    """A write-up or a card, on the writing brain. size "large" asks for the biggest model the
    brain has (the daily remix), and falls back to the writing one."""
    return generate_with(BRAIN, prompt, size, timeout)


def generate_with(kind, prompt, size, timeout):
    model = (QUICK_MODEL if size == "quick" else BRAIN_MODEL) or ""
    if kind == "agent":
        raise AgentMode(AGENT_MODE_WORDS)
    if kind == "claude":
        if size == "write" and not model:
            return claude_generate(prompt, timeout=timeout)
        return claude_generate_with(prompt, model or CLAUDE_MODELS[size], timeout=timeout)
    if kind == "codex":
        return codex_generate(prompt, model=model or None, timeout=timeout)
    if kind == "api":
        return api_generate(prompt, "quick" if size == "quick" else "write", timeout=timeout)
    if kind == "ollama":
        return ollama_generate(model or DEFAULT_LOCAL_MODEL, prompt, timeout=timeout)
    raise RuntimeError(f"{kind!r} is not a brain worthwatch knows (agent, claude, codex, api, ollama)")


def brain_label(kind, size, model=None):
    """What a write-up's header and the history say wrote it."""
    if kind == "ollama":
        name = model or (QUICK_MODEL if size == "quick" else BRAIN_MODEL) or DEFAULT_LOCAL_MODEL
        return f"local:{name}"
    if kind == "claude":
        return f"claude:{(QUICK_MODEL if size == 'quick' else BRAIN_MODEL) or CLAUDE_MODELS[size]}"
    if kind == "api":
        provider, base, _key, models = api_endpoint(os.environ)
        return f"api:{provider or base}/{models['quick' if size == 'quick' else 'write']}"
    return kind


def write_label():
    return brain_label(BRAIN, "write")


def writes_judgment(backend):
    """True when a write-up came from the writing brain (or your agent), whose sections 6 to 8
    are kept. A local quick brain's are blanked (local_judgment_unavailable), because a small
    local model was measured inventing what applies; that follows the job, not the word
    "claude" in a label."""
    label = str(backend or "")
    return label.startswith("claude") or label == "agent" or label == write_label()


def quick_label(model=None):
    return brain_label(quick_brain(), "quick", model)


def codex_generate(prompt, model=None, timeout=600):
    """OpenAI's Codex CLI, as its documentation describes non-interactive use: the prompt on
    stdin (`-`), progress on stderr and the final message alone on stdout, a read-only sandbox,
    no session files kept (--ephemeral), and --skip-git-repo-check because it runs in an empty
    folder of its own inside the data folder, where there is nothing of yours to read."""
    codex_path = shutil.which("codex")
    if not codex_path:
        raise RuntimeError("the codex CLI is not installed, or not on the PATH")
    workdir = Path(DATA_DIR) / "codex-workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    argv = [codex_path, "exec", "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
            "--color", "never"] + (["-m", model] if model else []) + ["-"]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            cwd=str(workdir), **group_kwargs())
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.wait(timeout=CODEX_TIMEOUT_GRACE)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError(f"codex exec timed out after {timeout}s and was stopped.")
    if proc.returncode != 0:
        raise RuntimeError(f"codex exec failed (exit {proc.returncode}): {one_line(stderr)[-600:]}")
    return (stdout or "").strip()


def api_endpoint(env):
    """(provider, base URL, key, {"quick": model, "write": model}) for the api brain: a base_url
    you set, else the provider you named, else the first service whose key is set. The key is
    returned to the caller only, and never put in a message."""
    from . import brains
    if API.get("base_url"):
        key = (env.get(API.get("key_env") or "") or "").strip()
        models = {"quick": QUICK_MODEL or BRAIN_MODEL, "write": BRAIN_MODEL or QUICK_MODEL}
        return "", API["base_url"], key, models
    names = [API["provider"]] if API.get("provider") else list(brains.API_PROVIDERS)
    for name in names:
        conf = brains.API_PROVIDERS[name]
        key = (env.get(conf["env"]) or "").strip()
        if key or API.get("provider"):
            return name, conf["base"], key, {"quick": QUICK_MODEL or conf["quick"],
                                             "write": BRAIN_MODEL or conf["write"]}
    return "", "", "", {"quick": QUICK_MODEL, "write": BRAIN_MODEL}


def api_generate(prompt, size, timeout=600, env=None, opener=None):
    """One chat completion from an OpenAI-compatible API: POST <base>/chat/completions with the
    key as a bearer token, the prompt as one user message. A refusal (HTTP 429) says to wait;
    no message ever holds the key."""
    from . import brains
    env = os.environ if env is None else env
    opener = opener or urllib.request.urlopen
    provider, base, key, models = api_endpoint(env)
    if not base or not key:
        where = (API.get("key_env") or "the variable named in api_key_env") if API.get("base_url") \
            else ", ".join(brains.API_PROVIDERS[API["provider"]]["env"] if API.get("provider")
                           else brains.API_KEY_VARIABLES)
        raise RuntimeError(f"the api brain has no key: none is set in {where}")
    model = models["quick" if size == "quick" else "write"]
    if not model:
        raise RuntimeError("the api brain has no model: set model under [brain] in settings.toml")
    url = base.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}]})
    req = urllib.request.Request(url, data=body.encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}",
                                          "User-Agent": f"worthwatch/{__version__}"})
    try:
        with opener(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace").replace(key, "<key>")
        if e.code == 429:
            raise RuntimeError(f"the API refused for now (HTTP 429, too many requests); wait and "
                               f"try again later: {one_line(detail)[:200]}") from None
        raise RuntimeError(f"the API answered HTTP {e.code}: {one_line(detail)[:300]}") from None
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"the API did not answer: {one_line(str(e)).replace(key, '<key>')}") from None
    except ValueError as e:
        raise RuntimeError(f"the API answered with something that is not JSON: {e}") from None
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"the API answered without a message: {one_line(str(data))[:200]}") from None


def summarize_claude(context_text, meta, transcript_text, timeout=None, extra=""):
    """The write-up, on the writing brain (the name is the tool's history: it was Claude's).
    timeout=None means "work it out from the length", which is what every caller in the
    tool now does; a number is still honoured so a test can pin it."""
    if timeout is None:
        timeout, _why = summary_timeout_for((meta or {}).get("minutes"))
    reply = write_generate(build_prompt(context_text, meta, transcript_text, extra, ideas=True),
                           timeout=timeout)
    # The ideas lane: the Ideas part is taken off here, before any check reads the write-up, and
    # waits on the meta for save_writeup. A re-ask that brings no part of its own keeps the
    # first one.
    text, part = split_ideas_section(reply)
    if meta is not None and (part is not None or "ideas_part" not in meta):
        meta["ideas_part"] = part
    return text, False


# --- the lock --------------------------------------------------------------------------------
# A lock stale by its age alone hands a live run's lock to the next one, and makes a lock left
# by a power cut refuse every run for an hour. So the lock names the process that holds it and
# when that process started; a lock whose process is alive is never taken, however old, and one
# whose process is gone, or whose id now belongs to a younger process, is cleared at once.
def process_started(pid):
    """When the process with this id started, in epoch seconds, or None when there is no such
    process or it cannot be asked."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended),
                                        ctypes.byref(kernel), ctypes.byref(user)):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 1e7 - 11644473600
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid):
    """True when a process with this id is running."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and \
                code.value == 259                                  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def lock_holder():
    """(pid, started) from the lock file, (None, None) for a lock written before 2026-09-26
    night (a bare timestamp), or None when there is no lock."""
    try:
        text = LOCK_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
        return data.get("pid"), data.get("started")
    except (json.JSONDecodeError, AttributeError):
        return None, None


def acquire_lock():
    # os.O_EXCL makes the create-if-absent check atomic at the OS level; a plain
    # "if exists: ... else: write" has a race window two near-simultaneous launches
    # can both slip through (observed in testing: two processes both passed the check).
    for _ in range(3):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "started": time.time()}).encode("utf-8"))
            os.close(fd)
            return
        except FileExistsError:
            pass
        holder = lock_holder()
        if holder is None:
            continue                     # released between the two looks: try again
        pid, started = holder
        try:
            age = time.time() - LOCK_PATH.stat().st_mtime
        except FileNotFoundError:
            continue
        if pid is not None:
            born = process_started(pid)
            alive = process_alive(pid) and (born is None or started is None
                                            or born <= float(started) + 5)
            if alive:
                when = (dt.datetime.fromtimestamp(float(started)).strftime("%H:%M")
                        if started else "an unknown time")
                print(f"Another worthwatch run is still going (process {pid}, started at {when}); this "
                      f"run did nothing, so it cannot overlap it.")
                sys.exit(1)
            print(f"A lock left by a run that is no longer going (process {pid}, "
                  f"{age / 60:.0f} min old) was cleared.")
        elif age < LOCK_STALE_SECONDS:
            print(f"Another worthwatch fetching run appears to be in progress "
                  f"(lock is {age / 60:.0f} min old). If you're sure nothing is running, "
                  f"delete {LOCK_PATH} and try again.")
            sys.exit(1)
        else:
            print(f"Stale lock ({age / 60:.0f} min old) found, removing it.")
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
    print(f"Could not take the lock at {LOCK_PATH} after three tries; this run did nothing.")
    sys.exit(1)


def release_lock():
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


# --- rate-limit state -------------------------------------------------------
# One small file, rate-state.json, is the single source of truth for: when the
# post-429 cooldown ends, how many caption fetches today has spent, and when the
# last caption fetch happened. It lives on disk rather than in memory because the
# limit is per-IP, not per-process: the hourly task launches --channels and
# --topics back to back, and a hand-run `worthwatch --file` can land between them.


def load_rate_state():
    # 2026-09-26 night: a torn file stops the run. Read as empty it forgot every cooldown, and
    # the next request after a 429 would have gone out as if nothing had been refused.
    return read_state_json(RATE_STATE_PATH)


def save_rate_state(state):
    atomic_write_text(RATE_STATE_PATH, json.dumps(state, indent=2, ensure_ascii=False))


def parse_ts(value):
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def format_duration(seconds):
    seconds = int(max(0, seconds))
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


# The cooldown is per path, not per address. Added 2026-09-06 with the audio path: the
# caption path's keys keep their original unprefixed names (cooldown_until, strikes,
# last_429, detail) so rate-state.json, the eleven suites and `worthwatch status` read the same as
# before, and the audio path gets the same four under an "audio_" prefix. A 429 on
# timedtext has never told us anything about googlevideo, so it stops captions and leaves
# the audio path free to be tried - which is the only reason the audio path can be the
# primary one on a day like today.
def _path_key(path, key):
    return key if path == "captions" else f"{path}_{key}"


def cooldown_until(path="captions"):
    return parse_ts(load_rate_state().get(_path_key(path, "cooldown_until")))


def cooldown_remaining(path="captions"):
    """Seconds left on the post-429 cooldown for one path; 0 when there isn't one."""
    until = cooldown_until(path)
    if until is None:
        return 0
    return max(0, (until - dt.datetime.now()).total_seconds())


def current_strikes(state, path="captions"):
    # A 429 older than STRIKE_DECAY_HOURS stops counting: escalation is for being
    # limited repeatedly right now, not for something that happened last week.
    last = parse_ts(state.get(_path_key(path, "last_429")))
    if last is None or (dt.datetime.now() - last) > dt.timedelta(hours=STRIKE_DECAY_HOURS):
        return 0
    return int(state.get(_path_key(path, "strikes"), 0))


def arm_cooldown(detail, path="captions"):
    """Stop fetching on this path for at least an hour, longer if it keeps happening. Also
    the moment that path's adaptive cap is halved: a refusal is the one fact the budget is
    built on."""
    state = load_rate_state()
    strikes = current_strikes(state, path) + 1
    wait = min(COOLDOWN_BASE_SECONDS * (2 ** (strikes - 1)), COOLDOWN_MAX_SECONDS)
    now = dt.datetime.now()
    until = now + dt.timedelta(seconds=wait)
    running = parse_ts(state.get(_path_key(path, "cooldown_until")))
    if running and running > until:
        until = running  # never shorten a cooldown already in force
    state.update({
        _path_key(path, "last_429"): now.isoformat(timespec="seconds"),
        _path_key(path, "cooldown_until"): until.isoformat(timespec="seconds"),
        _path_key(path, "strikes"): strikes,
        _path_key(path, "detail"): (detail or "").strip()[:300],
    })
    by_day = state.setdefault(_path_key(path, "strikes_by_day"), {})
    by_day[now.date().isoformat()] = int(by_day.get(now.date().isoformat(), 0)) + 1
    save_rate_state(state)
    halve_daily_cap(f"429 at {now.isoformat(timespec='seconds')} (strike {strikes})", path=path)
    return until, wait, strikes


# Brief part 2.12, 2026-09-26 night: a run belongs to the day it started on. Every write of a
# run read the date at the moment it happened, so a run that crossed midnight put its first
# videos on one day and the rest, their history rows and its run section on the next.
# start_this_run pins the day; a command that is not a run (status) reads the calendar.
_RUN_DAY = {"day": None}


def today_key():
    return _RUN_DAY["day"] or dt.date.today().isoformat()


# --- the adaptive daily cap ---------------------------------------------------------
# rate-state.json carries daily_cap (today's caption ceiling), cap_day (the day it was set
# for), cap_reason (why it is what it is) and cap_history (the last twenty changes). The
# rules: on the first budget question of a new day, yesterday is judged - no 429 and at
# least one caption fetched means the cap rises by CAPTION_CAP_STEP; any 429 halves the cap
# at once, with a floor of CAPTION_CAP_MIN. Nothing else moves it.


# Both paths share these rules and these bounds; each keeps its own current value, its own
# day counter and its own history. The caption path's keys stay unprefixed (daily_cap,
# cap_day, cap_reason, cap_history, caption_day, caption_count, last_caption_fetch) so
# rate-state.json and the suites read exactly as they did; the audio path's carry an
# "audio_" prefix.
def _cap_keys(path):
    if path == "captions":
        return ("daily_cap", "cap_day", "cap_reason", "cap_history",
                "caption_day", "caption_count", "last_caption_fetch", "strikes_by_day")
    return (f"{path}_daily_cap", f"{path}_cap_day", f"{path}_cap_reason", f"{path}_cap_history",
            f"{path}_day", f"{path}_count", f"{path}_last_fetch", f"{path}_strikes_by_day")


def _record_cap_change(state, new_cap, reason, path="captions"):
    k_cap, k_day, k_reason, k_hist = _cap_keys(path)[:4]
    old = state.get(k_cap)
    state[k_cap] = int(new_cap)
    state[k_day] = today_key()
    state[k_reason] = reason
    hist = state.setdefault(k_hist, [])
    hist.append({"at": dt.datetime.now().isoformat(timespec="seconds"), "from": old,
                 "to": int(new_cap), "reason": reason})
    del hist[:-20]


def roll_daily_cap(state, path="captions"):
    """Bring this path's daily cap up to date for today, judging the previous cap day.
    Mutates and returns state; the caller saves it."""
    k_cap, k_day, _, _, k_fetch_day, k_count, _, k_strikes = _cap_keys(path)
    cap = state.get(k_cap)
    if cap is None:
        _record_cap_change(state, CAPTION_CAP_START, f"first day: starting at {CAPTION_CAP_START}", path)
        return state
    cap = int(cap)
    cap_day = state.get(k_day)
    if cap_day == today_key():
        return state
    # judge the day the cap was set for, using only what rate-state recorded about it
    strikes_that_day = int((state.get(k_strikes) or {}).get(cap_day, 0))
    fetched_that_day = int(state.get(k_count, 0)) if state.get(k_fetch_day) == cap_day else 0
    if strikes_that_day:
        # already halved when it happened; carry the halved cap forward unchanged
        _record_cap_change(state, cap, f"{cap_day} had {strikes_that_day} refusal(s): cap {cap} carried into "
                                       f"{today_key()} unchanged", path)
    elif fetched_that_day >= 1:
        new = min(CAPTION_CAP_MAX, cap + CAPTION_CAP_STEP)
        noun = "caption(s)" if path == "captions" else f"{path} fetch(es)"
        _record_cap_change(state, new, f"clean day {cap_day} ({fetched_that_day} {noun}, no 429): "
                                       f"{cap} -> {new}", path)
    else:
        _record_cap_change(state, cap, f"{cap_day} fetched nothing, so proved nothing: cap {cap} kept", path)
    return state


def halve_daily_cap(reason, path="captions"):
    state = load_rate_state()
    roll_daily_cap(state, path)
    k_cap = _cap_keys(path)[0]
    old = int(state.get(k_cap, CAPTION_CAP_START))
    new = max(CAPTION_CAP_MIN, old // 2)
    _record_cap_change(state, new, f"{reason}: {old} -> {new}", path)
    save_rate_state(state)
    return old, new


def daily_cap_for(path="captions"):
    """This path's cap today, rolling the day over if this is the first question of it."""
    state = load_rate_state()
    k_cap, k_day = _cap_keys(path)[:2]
    before = (state.get(k_cap), state.get(k_day))
    roll_daily_cap(state, path)
    if (state.get(k_cap), state.get(k_day)) != before:
        save_rate_state(state)
    return int(state[k_cap])


def used_today(path="captions"):
    state = load_rate_state()
    _, _, _, _, k_fetch_day, k_count, _, _ = _cap_keys(path)
    if state.get(k_fetch_day) != today_key():
        return 0
    return int(state.get(k_count, 0))


def budget_left_today(path="captions"):
    return max(0, daily_cap_for(path) - used_today(path))


def note_fetch(path="captions"):
    """Count one fetch on this path and stamp the clock. Called for every attempt,
    successful or not: YouTube counted the request either way."""
    state = load_rate_state()
    _, _, _, _, k_fetch_day, k_count, k_last, _ = _cap_keys(path)
    if state.get(k_fetch_day) != today_key():
        state[k_fetch_day] = today_key()
        state[k_count] = 0
    state[k_count] = int(state.get(k_count, 0)) + 1
    # full precision, not timespec="seconds": rounding this down shortens the
    # next gap by up to a second, which is the wrong direction for a rate limit.
    state[k_last] = dt.datetime.now().isoformat()
    save_rate_state(state)


def wait_for_fetch_slot(path="captions"):
    """Hold until this path's minimum gap has passed since its last fetch by any run, not
    just this one."""
    last = parse_ts(load_rate_state().get(_cap_keys(path)[6]))
    if last is None:
        return
    delay = PATH_DELAY_SECONDS.get(path, CAPTION_DELAY_SECONDS)
    gap = (dt.datetime.now() - last).total_seconds()
    if 0 <= gap < delay:
        time.sleep(delay - gap)


# The caption-named wrappers the rest of the tool and the suites already call.
def daily_caption_cap():
    return daily_cap_for("captions")


def captions_used_today():
    return used_today("captions")


def caption_budget_left_today():
    return budget_left_today("captions")


def note_caption_fetch():
    return note_fetch("captions")


def wait_for_caption_slot():
    return wait_for_fetch_slot("captions")


def wait_for_request_slot():
    """Hold until MIN_REQUEST_GAP_SECONDS have passed since the last YouTube request by
    any run, and return the gap that will actually separate the two (None for the first
    request ever), which is what the request log records."""
    last = parse_ts(load_rate_state().get("last_request"))
    if last is None:
        return None
    gap = (dt.datetime.now() - last).total_seconds()
    if 0 <= gap < MIN_REQUEST_GAP_SECONDS:
        time.sleep(MIN_REQUEST_GAP_SECONDS - gap)
        gap = MIN_REQUEST_GAP_SECONDS
    return gap


def note_request():
    """Stamp the clock when a yt-dlp call finishes, so the next gap is measured from when
    YouTube last heard from this IP rather than from when the call started. A listing that
    takes 40 seconds should not buy the right to fire the next request immediately."""
    state = load_rate_state()
    state["last_request"] = dt.datetime.now().isoformat()
    save_rate_state(state)
    # Keep the lock's mtime fresh while work is genuinely happening. LOCK_STALE_SECONDS
    # is meant to spot a process that died, but at 25s a call the weekly --discover run
    # can spend the better part of an hour legitimately working (6 searches, then two
    # requests per candidate channel, up to ~114 requests); at 4s a call it never came
    # close. A live run that looks dead gets its lock deleted and a second fetcher
    # started on the same IP, which is the exact thing the lock exists to prevent.
    try:
        os.utime(LOCK_PATH, None)
    except OSError:
        pass   # no lock held: `worthwatch status`, or a mode that does not fetch


def new_budget():
    """This run's transcript allowance: the per-run cap, or what is left of the best
    enabled path's daily ceiling, whichever is smaller. It asks about every enabled path
    because a day when one is spent or refused is exactly the day the other earns its
    keep, and a run that stopped at the caption budget would never reach the audio one.

    2026-10-05: less the room held back for the favourite channels (favourite_reserve_left);
    their own step spends from favourite_budget() instead."""
    left = max([budget_left_today(p) for p in path_order()] or [0])
    return {"remaining": max(0, min(MAX_CAPTION_FETCHES_PER_RUN, left - favourite_reserve_left()))}


def report_cooldown(mode, path="captions"):
    """One path's cooldown, said out loud. It is never on its own a reason to skip a run
    any more - see report_no_path(), which is what a mode calls when EVERY path is out."""
    until = cooldown_until(path)
    left = cooldown_remaining(path)
    state = load_rate_state()
    print(f"The {path} path is in a rate-limit cooldown until {until:%Y-%m-%d %H:%M} "
          f"({format_duration(left)} left, strike {state.get(_path_key(path, 'strikes'), 1)}, "
          f"triggered {state.get(_path_key(path, 'last_429'), 'UNKNOWN')}).")
    print(f"Skipping {mode}; nothing was fetched. `worthwatch status` shows the same thing, "
          f"and --ignore-cooldown overrides it.")


def report_daily_cap(mode):
    state = load_rate_state()
    print(f"Every transcript path has spent today's budget. Skipping {mode}; nothing was "
          f"fetched. It resets at midnight.")
    for path in path_order():
        print(f"  {path}: {used_today(path)}/{daily_cap_for(path)} fetched "
              f"(cap reason: {state.get(_cap_keys(path)[2], 'UNKNOWN')})")
    for path in [p for p in PATH_PREFERENCE if not path_enabled(p)]:
        print(f"  {path}: switched off")
    held = favourite_reserve_left()
    if held:
        print(f"  {held} of today's fetches are held back for the priority channel(s): "
              f"{', '.join(c.rsplit('/', 1)[-1] for c in favourite_channels())}.")


def load_seen():
    return read_state_json(SEEN_PATH)


def save_seen(seen):
    atomic_write_text(SEEN_PATH, json.dumps(seen, indent=2, ensure_ascii=False))


def mark_seen(seen, video_id, verdict, title):
    seen[video_id] = {"date": today_key(), "verdict": verdict, "title": title}
    save_seen(seen)


def append_history(verdict, channel, title, url, reason, summary=None, backend=None,
                    try_this_week=None, source=None, confidence=None):
    # "confidence" joined the row on 2026-09-20 and is written on every row from then on,
    # including as null where there is none. Rows written before that date have no such key
    # and load exactly as they did: every reader here goes through .get(), and
    # tests/test_rules.py pins that with a row copied from before the change.
    entry = {
        "date": today_key(), "verdict": verdict, "channel": channel,
        "title": title, "url": url, "reason": reason, "summary": summary,
        "backend": backend, "try_this_week": try_this_week, "source": source,
        "confidence": confidence,
    }
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_history_since(cutoff_date):
    if not HISTORY_PATH.exists():
        return []
    entries = []
    for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            d = dt.date.fromisoformat(e.get("date", ""))
        except ValueError:
            continue
        if d >= cutoff_date:
            entries.append(e)
    return entries


def todays_totals():
    """(videos today, YES today, OPPORTUNITY today) for the digest's header."""
    day = today_key()
    entries = [e for e in load_history_since(dt.date.fromisoformat(day)) if e.get("date") == day]
    yes = sum(1 for e in entries if e["verdict"] == "YES")
    opportunity = sum(1 for e in entries if e["verdict"] == "OPPORTUNITY")
    return len(entries), yes, opportunity


def todays_source_count(source):
    # The run's day (brief part 2.12), and only that day: the topics cap is per day.
    day = today_key()
    entries = [e for e in load_history_since(dt.date.fromisoformat(day)) if e.get("date") == day]
    return sum(1 for e in entries if e.get("source") == source)


# --- the record store, 2026-09-21 -------------------------------------------------------
# FIX 1. One record per video, with an explicit state for its write-up, in a file of its
# own. seen.json keeps its format, its content and its meaning; this store answers a
# different question. seen.json says "has this video been dealt with"; a record says what
# the tool knows about it and, above all, whether the summary that is supposed to justify
# its verdict actually exists.
#
# The defect it is for: a long talk was fetched, transcribed in 127 s,
# read as YES at confidence 95, and then the write-up call timed out after 600 s and was
# killed. The digest printed "YES (the listing said MAYBE) | 95", the header led with it as
# the day's second keeper, and no write-up for it exists. The verdict was decided in one
# place, the summary in another, and nothing joined them up.
SUMMARY_STATES = ("NOT_READ", "WAITING", "READ_NOT_WRITTEN", "WRITTEN")
SUMMARY_MAX_ATTEMPTS = 3

# Why a record is in the state it is in, as a closed set, because this is what decides
# whether a video is a table row or a line under the table and prose cannot be asked to
# decide that. The principle: a video that will never be read gets a row, and a video that
# is going to be tried again gets a line. Each entry is (state, gets a row, which count).
REASON_CODES = {
    "written":                  ("WRITTEN", True, None),   # the verdict decides the count
    "read_then_dropped":        ("READ_NOT_WRITTEN", False, "read_and_skipped"),
    "over_ceiling":             ("NOT_READ", True, "not_read"),
    "no_transcript":            ("NOT_READ", True, "not_read"),
    "no_speech":                ("NOT_READ", True, "not_read"),   # fix 4, 2026-09-22
    "transcribe_failed":        ("NOT_READ", True, "not_read"),
    "removed":                  ("NOT_READ", True, "not_read"),   # brief part 2.8, 2026-09-26
    "garbled":                  ("NOT_READ", True, "not_read"),   # brief part 2.3, 2026-09-26
    "screened_out":             ("NOT_READ", False, "not_read"),
    "too_old":                  ("NOT_READ", False, None),
    "off_topic":                ("NOT_READ", False, None),   # fix 3, 2026-09-25
    "rolled":                   ("WAITING", False, "waiting"),
    # 2026-10-05: its channel already had CHANNEL_DAILY_CAP videos read that day.
    "channel_cap":              ("WAITING", False, "waiting"),
    "refused_429":              ("WAITING", False, "waiting"),
    "captions_not_ready":       ("WAITING", False, "waiting"),
    # 2026-09-26 night, brief part 2.1: the listing triage could not be asked (Ollama did not
    # answer); seen.json queues it to be triaged, so it waits, and nothing was spent on it.
    "not_triaged":              ("WAITING", False, "waiting"),
    # ... and the transcript triage could not be asked: read, saved, not judged, not written.
    "triage_failed":            ("READ_NOT_WRITTEN", True, "read_not_written"),
    # Brief part 2.10: where a record stands while a run works on it, so a run that loses the
    # power in the middle leaves it in the retry queue, not marked done and forgotten. A run
    # that finishes always moves it on, so a digest shows these only after a cut.
    "triage_pending":           ("READ_NOT_WRITTEN", True, "read_not_written"),
    "summary_pending":          ("READ_NOT_WRITTEN", True, "read_not_written"),
    # Brief part 2, items 2, 5 and 6: a fetch failed for a reason that passes (the network, the
    # transcriber, the disk), or the run stopped fetching; queued in seen.json for another try.
    "fetch_failed":             ("WAITING", False, "waiting"),
    "summary_timeout":          ("READ_NOT_WRITTEN", True, "read_not_written"),
    "summary_error":            ("READ_NOT_WRITTEN", True, "read_not_written"),
    "no_run_time":              ("READ_NOT_WRITTEN", True, "read_not_written"),
    "too_long_for_the_ceiling": ("READ_NOT_WRITTEN", True, "read_not_written"),
}
# The five counts of the day's header line, in the order it prints them.
COUNT_TAGS = ("kept", "read_and_skipped", "read_not_written", "not_read", "waiting")
# Everything final_verdict may say about a record. Closed, so a caller cannot invent a tag
# and have it silently mean nothing.
VERDICT_TAGS = frozenset(COUNT_TAGS) | {"row", "header", "age_filtered", "off_topic",
                                         "screened_out", "opportunity",
                                         "guess", "demoted", "contradicts_decision",
                                         "retried"}


def load_records():
    # 2026-09-26 night: a torn file stops the run (read_state_json); it used to be read as an
    # empty store, which the next save_records wrote over the whole of it.
    return read_state_json(RECORDS_PATH)


def save_records(records):
    atomic_write_text(RECORDS_PATH, json.dumps(records, indent=2, ensure_ascii=False))


def update_record(video_id, **fields):
    """Create or update one video's record in place and save it. Returns the record.

    `day` moves with every update, and that is the definition of which digest a record
    belongs to: the digest for a day shows the records that day's runs touched. A video
    that waited yesterday and is read today is one row, in today's file, which is what
    "exactly one row across the day" means when a video crosses midnight."""
    records = load_records()
    record = records.get(video_id)
    if record is None:
        record = {"id": video_id, "attempts": 0, "terminal": False,
                  "created": dt.datetime.now().isoformat(timespec="seconds")}
    record.update(fields)
    record["day"] = fields.get("day") or today_key()
    record["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    records[video_id] = record
    save_records(records)
    return record


def todays_records(day=None):
    """The day's records, oldest first, which is the order the runs met them."""
    day = day or today_key()
    rows = [r for r in load_records().values() if r.get("day") == day]
    return sorted(rows, key=lambda r: (r.get("created") or "", r.get("id") or ""))


def record_from_history(row):
    """The record a history.jsonl row implies, for reading days that were written before
    this store existed. A row with a summary was written; a row without one was not, and
    the reason it carries says which kind of not-written it is."""
    reason = row.get("reason") or ""
    if row.get("summary"):
        state, code = "WRITTEN", "written"
    elif "summarize failed" in reason:
        state, code = "READ_NOT_WRITTEN", ("summary_timeout" if "timed out" in reason
                                            else "summary_error")
    elif "screened out from the listing" in reason:
        state, code = "NOT_READ", "screened_out"
    elif "over the" in reason and "ceiling" in reason:
        state, code = "NOT_READ", "over_ceiling"
    elif "no captions" in reason:
        state, code = "NOT_READ", "no_transcript"
    else:
        state, code = "NOT_READ", "no_transcript"
    listing = re.search(r"\[listing said (\w+): ", reason)
    # What the TRANSCRIPT read, which is not always the verdict the row stored. Two ways it
    # differs, and both matter to the first-guess measurement: a row for a video that was
    # never read carries the listing's guess as its verdict, and nothing read anything, so
    # the read verdict is None; and a row that rule 1 demoted stores MAYBE while the
    # transcript said YES, which the reason records as "demoted from YES".
    demoted = re.search(r"\[demoted from (\w+):", reason)
    if state == "NOT_READ":
        read_verdict, settled = None, None
    elif demoted:
        read_verdict, settled = demoted.group(1), row.get("verdict")
    else:
        read_verdict, settled = row.get("verdict"), row.get("verdict")
    # What section 6 came to is read off the stored summary, not left blank: a day that ran
    # before the record store existed still has to answer "does this verdict have anything
    # under it", and the summary is right there in the row.
    applies, project, takeaway = section6_state(row.get("summary"))
    # 2026-09-25: section 8 is read off the summary when the row's own field is empty, and
    # section 9 with it, because the header's sentence comes from there now.
    summary = row.get("summary")
    try_line = row.get("try_this_week") or (extract_try_this_week(summary) if summary else None)
    return {"id": video_id_from_url(row.get("url") or ""), "url": row.get("url"),
            "title": row.get("title"), "channel": row.get("channel"),
            "source": row.get("source"), "day": row.get("date"),
            "listing_verdict": listing.group(1) if listing else row.get("verdict"),
            "listing_reason": "", "listing_confidence": None,
            "read_verdict": read_verdict, "settled_verdict": settled,
            "read_reason": reason, "read_confidence": row.get("confidence"),
            "summary_state": state, "reason_code": code, "state_reason": "",
            "attempts": 1 if state == "READ_NOT_WRITTEN" else 0, "terminal": False,
            "applies": applies, "project": project, "takeaway": takeaway,
            "try_this_week": try_line, "header": writeup_header(summary),
            "decision_hit": None}


def section6_state(summary_text):
    """(applies, project, takeaway) read off a finished summary.

    applies is "missing" when there is no section 6 at all, "nothing" when it came to the
    word nothing, and "items" otherwise. project is the single project the most applicable
    item names, or None when no item names one, and takeaway is that item's text, which is
    what the day's header quotes. An item that names no project is why "items" and a
    project of None can happen together, and final_verdict treats that as no keep."""
    if not summary_text:
        return "missing", None, None
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, APPLIES_HEADING_RE)
    if bounds is None:
        return "missing", None, None
    if applies_is_nothing(summary_text):
        return "nothing", None, None
    takeaway = extract_top_takeaway(summary_text)
    if takeaway and is_nothing_useful(takeaway):
        return "nothing", None, None
    project = None
    for line in ([takeaway] if takeaway else []) + [l for l in lines[bounds[0] + 1:bounds[1]]
                                                     if l.strip()]:
        named = projects_named(line or "")
        if len(named) == 1:
            project = next(iter(named))
            break
    return "items", project, takeaway


# --- the transcript store, 2026-09-21 -----------------------------------------------------
# FIX 2. Free retries. The transcript is written here BEFORE the summary is called, with a
# small record of its own beside it, so a summary that fails costs nothing but the summary.
# One long talk cost two YouTube requests, a 51 MB download and 127 seconds of GPU, and when
# its summary timed out all of that was thrown away: seen.json says the video is done, so no
# later run would ever look at it again.
#
# raw/ has held the text all along and is read as a fallback, which is not tidiness: a
# transcript saved before this store existed is still there, and sending its video back for
# a 51 MB download would be the opposite of what a free retry is for.
SUMMARY_RETRY_CODES = ("summary_timeout", "summary_error", "no_run_time",
                       "too_long_for_the_ceiling", "triage_failed", "triage_pending",
                       "summary_pending")
RETRY_MAX_PER_RUN = 3
# Measured over the 200 transcripts on disk against the audio_seconds in logs/requests.jsonl:
# mean 189.6 words a minute, median 189.8, sd 41.0. Only ever used to estimate the minutes of
# a transcript saved before this store existed, and the estimate is labelled as one.
WORDS_PER_MINUTE = 190

# --- no speech, 2026-09-22 ----------------------------------------------------------------
# FIX 4. Two music-only videos came out of Whisper as "Oh" and as
# "Subtitles by the Amara.org community", went through the transcript triage and a full
# write-up of nothing, and were counted as read. The threshold is the data's: of the 215
# transcripts on disk with a recorded length, five lie between 0.0 and 4.4 words a minute
# (".", ".", the Amara line, "Oh", "Thank you for watching.") and the next is 101.9, with
# nothing between. 20 sits inside that gap near its middle on a log scale (the geometric mean
# of 4.4 and 101.9 is 21.1): four and a half times the fastest junk and a fifth of the
# slowest real speech. scripts/speech_rates.py recomputes the distribution.
NO_SPEECH_WPM = 20
# Lines Whisper is known to produce on silence or music, matched only when one of them is
# the WHOLE transcript. The first two are on this disk; the rest are the common ones.
WHISPER_PHRASES = ("Subtitles by the Amara.org community", "Thank you for watching.",
                   "Thanks for watching!", "Thank you.", "you")
NO_TEXT_ERROR = "the audio was transcribed but produced no text"
NO_TEXT_RE = re.compile(re.escape(NO_TEXT_ERROR) + r"(?: \(([\d.]+) min of audio\))?")


def spoken_words(text):
    """The words of a transcript: tokens with a letter or a digit in them, so "." is none."""
    return [w for w in (text or "").split() if any(c.isalnum() for c in w)]


def phrase_key(text):
    return re.sub(r"\s+", " ", (text or "").strip().strip("\"'").rstrip(".!?").strip()).lower()


WHISPER_PHRASE_KEYS = frozenset(phrase_key(p) for p in WHISPER_PHRASES)


def speech_check(text, minutes=None):
    """(why, words): why is None when the transcript holds speech, and otherwise "no_text"
    (not one word), "rate" (under NO_SPEECH_WPM words a minute of audio) or "phrase" (the
    whole transcript is one known Whisper line). `minutes` must be a measurement or None: an
    estimate from the word count is 190 words a minute by construction and proves nothing."""
    words = len(spoken_words(text))
    if words == 0:
        return "no_text", 0
    if minutes and minutes > 0 and words / minutes < NO_SPEECH_WPM:
        return "rate", words
    if phrase_key(text) in WHISPER_PHRASE_KEYS:
        return "phrase", words
    return None, words


# --- a garbled transcript, 2026-09-26 night -------------------------------------------------
# Brief part 2.3. Measured over the 438 transcripts on this disk that night: none held an
# unreadable character, and in the most repetitive one the single most repeated 8-word phrase
# covered 3.6% of its words (a 225-word clip in which nothing repeats at all). A transcript
# that is mostly replacement characters, or one phrase looping (Whisper does this on music and
# on silence), would otherwise be judged and written up like a talk.
GARBLED_ERROR = "the transcript came out"   # followed by garbled_text's "garbled: ..."
GARBLED_UNREADABLE_SHARE = 0.02     # of the characters: U+FFFD or a control character
GARBLED_LOOP_SHARE = 0.25           # of the words, taken by the single most repeated 8-word phrase


def garbled_text(text, loops=True):
    """Why a transcript is garbled, in plain words, or None when it reads as speech.

    The loop is looked for where Whisper's words are made (transcribe_audio), because a loop is
    how Whisper fails; everywhere else only unreadable characters are (loops=False), which is
    also what keeps the suites' stand-in transcripts, one sentence repeated, from reading as
    garbled."""
    body = text or ""
    if not body.strip():
        return None                      # empty is the no-speech check's to say
    unreadable = sum(1 for c in body if c == "�" or (ord(c) < 32 and c not in "\n\r\t"))
    if unreadable / len(body) > GARBLED_UNREADABLE_SHARE:
        return (f"garbled: {unreadable / len(body):.0%} of its characters are unreadable, so it "
                f"was not judged or written up")
    words = body.split()
    if loops and len(words) >= 80:
        grams = {}
        for i in range(len(words) - 7):
            key = tuple(words[i:i + 8])
            grams[key] = grams.get(key, 0) + 1
        phrase, count = max(grams.items(), key=lambda kv: kv[1])
        share = count * 8 / len(words)
        if count >= 3 and share >= GARBLED_LOOP_SHARE:
            return (f"garbled: one phrase repeated makes up {min(share, 1):.0%} of it "
                    f"(\"{' '.join(phrase)}\"), so it was not judged or written up")
    return None


def no_speech_text(record):
    """What a no-speech row says: "no speech: 1 word in 0.3 min of audio"."""
    words = int(record.get("words") or 0)
    minutes = record.get("minutes")
    noun = "word" if words == 1 else "words"
    if isinstance(minutes, (int, float)) and minutes > 0:
        text = f"no speech: {words} {noun} in {minutes:.1f} min of audio"
    else:
        text = f"no speech: {words} {noun}; the length of the audio was not recorded"
    if record.get("no_speech") == "phrase":
        text += ", all of it a line Whisper makes up on silence"
    return text


def no_text_minutes(error):
    """(True, minutes or None) when a fetch error is the audio path's "produced no text",
    else (False, None)."""
    m = NO_TEXT_RE.search(error or "")
    if not m:
        return False, None
    return True, (float(m.group(1)) if m.group(1) else None)


def save_transcript(meta, source=None):
    """Write one transcript and its record, and return the record.

    The record is small and says what the retry needs to know without re-reading anything:
    which video, how many minutes of audio it was, how many words came out, which whisper
    model heard it, and when."""
    vid = meta["id"]
    text = meta.get("transcript") or ""
    stats = meta.get("audio_stats") or {}
    seconds = stats.get("audio_seconds")
    record = {
        "id": vid, "title": meta.get("title"), "url": meta.get("url"),
        "channel": meta.get("channel") or meta.get("uploader"), "source": source,
        "minutes": round(seconds / 60.0, 1) if seconds else None,
        "minutes_estimated": False if seconds else True,
        "words": len(text.split()),
        "whisper_model": stats.get("model") or (WHISPER_MODEL if seconds else None),
        "created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if record["minutes"] is None:
        record["minutes"] = round(record["words"] / WORDS_PER_MINUTE, 1) or None
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(TRANSCRIPTS_DIR / f"{vid}.txt", text)
    atomic_write_text(TRANSCRIPTS_DIR / f"{vid}.json",
                      json.dumps(record, indent=2, ensure_ascii=False))
    return record


def request_log_minutes(video_id):
    """Minutes of audio the request log measured for this video (audio_seconds of its
    audio_transcribe entry, the newest if there are several), or None."""
    if not REQUEST_LOG_PATH.exists():
        return None
    found = None
    for line in REQUEST_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        if video_id not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (entry.get("kind") == "audio_transcribe" and entry.get("video_id") == video_id
                and entry.get("audio_seconds")):
            found = round(float(entry["audio_seconds"]) / 60.0, 1)
    return found


def measured_minutes(video_id, record=None, sidecar=None):
    """(minutes, where they came from) for a video's audio, only ever a measurement, or
    (None, None).

    FIX 3. The retry of one long talk estimated 51.07
    minutes from the 9,704 words in raw/, which has no sidecar, while the video's record
    held the measured 51.7 and the request log the 3,099.8 s it came from. The places a
    measurement is kept, in order: the transcript's own record, the video's record, the
    request log."""
    if sidecar and sidecar.get("minutes") and not sidecar.get("minutes_estimated"):
        return float(sidecar["minutes"]), "the transcript's own record"
    if record is None:
        record = load_records().get(video_id) or {}
    if record.get("minutes") and not record.get("minutes_estimated"):
        return float(record["minutes"]), "the video's record"
    logged = request_log_minutes(video_id)
    if logged:
        return logged, "the request log"
    return None, None


def saved_transcript(video_id, record=None):
    """(text, record) for a transcript already on this machine, or (None, None).

    transcripts/ first, then raw/, which is where every transcript this tool has ever
    written still is. The minutes are the measured ones wherever they are kept (FIX 3), and
    only when none is kept anywhere are they estimated from the word count, and the record
    says so: a number that is a guess must never be readable as a measurement."""
    path = TRANSCRIPTS_DIR / f"{video_id}.txt"
    side = TRANSCRIPTS_DIR / f"{video_id}.json"
    fallback = RAW_DIR / f"{video_id}.txt"
    if path.exists():
        # 2026-09-26 night, brief part 2.3: with replacement, so a file that is not text any
        # more is read and called garbled rather than raising out of the retry phase, which runs
        # before every mode and would have ended every run of the day.
        text = path.read_text(encoding="utf-8", errors="replace")
        info = {}
        if side.exists():
            try:
                info = json.loads(side.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                info = {}
    elif fallback.exists():
        text = fallback.read_text(encoding="utf-8", errors="replace")
        info = {"id": video_id, "whisper_model": None, "created": None, "from": "raw"}
    else:
        return None, None
    info["words"] = info.get("words") or len(text.split())
    minutes, source = measured_minutes(video_id, record=record, sidecar=info)
    if minutes:
        info.update(minutes=minutes, minutes_estimated=False, minutes_from=source)
    else:
        info.update(minutes=round(len(text.split()) / WORDS_PER_MINUTE, 1),
                    minutes_estimated=True, minutes_from="an estimate from the word count")
    return text, info


def seen_attempts(video_id):
    """How many summary attempts this video has already spent."""
    return int((load_records().get(video_id) or {}).get("attempts") or 0)


def retry_queue():
    """The records whose summary failed and may be tried again, oldest first.

    Oldest first by when the record was created, so a video that failed this morning does
    not keep jumping the one that failed on Monday. A record that was read and deliberately
    not summarised is not in here: that is not a failure.

    2026-10-05: a favourite channel's video goes first, and is never given up: past
    SUMMARY_MAX_ATTEMPTS it is tried once a day, until it is written."""
    today = today_key()

    def may_try(r):
        if int(r.get("attempts") or 0) < SUMMARY_MAX_ATTEMPTS:
            return True
        last = max((f.get("day") or "" for f in r.get("failures") or [] if isinstance(f, dict)),
                   default="")
        return bool(r.get("favourite")) and last < today

    queue = [r for r in load_records().values()
             if r.get("summary_state") == "READ_NOT_WRITTEN"
             and r.get("reason_code") in SUMMARY_RETRY_CODES
             and not r.get("terminal")
             and may_try(r)
             # 2026-09-26 night: one still to be judged waits while the local model is down.
             and not (ollama_down() and r.get("reason_code") == "triage_failed")]
    return sorted(queue, key=lambda r: (not r.get("favourite"), r.get("created") or "",
                                        r.get("id") or ""))


def summary_failed(video_id, error, attempts):
    """One failed summary attempt, recorded. Returns the record.

    The reason code separates a timeout from every other failure because they are retried
    differently: a timeout is the one failure a longer timeout can fix, and after the last
    attempt it is terminal with the reason still attached."""
    text = str(error)
    timed_out = "timed out" in text
    code = "summary_timeout" if timed_out else "summary_error"
    seconds = re.search(r"after (\d+)s", text)
    if timed_out and seconds:
        why = f"summary timed out after {seconds.group(1)} s"
    elif timed_out:
        why = "summary timed out"
    else:
        why = f"summary failed: {one_line(text)[:120]}"
    timeout_s = int(seconds.group(1)) if (timed_out and seconds) else None
    # 2026-10-05: a favourite channel's write-up is never terminal (retry_queue spaces it out).
    favourite = bool((load_records().get(video_id) or {}).get("favourite"))
    return update_record(video_id, summary_state="READ_NOT_WRITTEN", reason_code=code,
                         state_reason=why, attempts=attempts,
                         terminal=attempts >= SUMMARY_MAX_ATTEMPTS and not favourite,
                         failures=failures_with(video_id, attempts, code, why, timeout_s))


def failures_with(video_id, attempt, code, why, timeout_s=None):
    """The record's failure history with one more attempt on it.

    FIX 2, 2026-09-22. Kept because the reason a summary failed is otherwise lost the
    moment it succeeds: summary_written blanks state_reason, and one long talk's row went
    on showing the failure only because a backfill had copied it into the reason by
    accident. A row once written shows the note final_verdict makes from this."""
    failures = list((load_records().get(video_id) or {}).get("failures") or [])
    failures.append({"attempt": int(attempt), "day": today_key(), "code": code,
                     "timeout_s": timeout_s, "why": why})
    return failures


ATTEMPT_ORDINALS = {1: "first", 2: "second", 3: "third"}


def attempt_history_note(record):
    """What a written record's row says about the attempts before the one that wrote it,
    or "" when it was written on the first.

    "Hung" is a measurement: it is said only when the attempt that succeeded took less time
    than the failed one was given, which is what happened to one long talk (killed at
    600 s, written in 122.6 s the next morning). A success slower than that is
    a timeout that was too short, and the note says so instead."""
    failures = [f for f in (record.get("failures") or []) if isinstance(f, dict)]
    written = int(record.get("written_attempt") or (len(failures) + 1 if failures else 1))
    if written <= 1 and not failures:
        return ""
    head = f"written on retry {written} of {SUMMARY_MAX_ATTEMPTS}"
    if (record.get("written_day") and record.get("read_day")
            and record["written_day"] != record["read_day"]):
        head += f" on {record['written_day']}"
    parts = [head]
    took = record.get("summary_seconds")
    for n, failure in enumerate(failures):
        label = ATTEMPT_ORDINALS.get(failure.get("attempt"), f"number {failure.get('attempt')}")
        who = f"the {label} attempt" if n == 0 else f"the {label}"
        timeout_s = failure.get("timeout_s")
        if failure.get("code") == "summary_timeout" and timeout_s:
            if isinstance(took, (int, float)) and took < timeout_s:
                parts.append(f"{who} hung past {timeout_s:.0f} s")
            elif isinstance(took, (int, float)):
                parts.append(f"{who} was stopped at {timeout_s:.0f} s, and this one took "
                             f"{took:.0f} s")
            else:
                parts.append(f"{who} timed out after {timeout_s:.0f} s")
        elif failure.get("code") == "no_run_time":
            parts.append(f"{who} was not started, for want of time left in the run")
        else:
            why = re.sub(r"^summary failed:\s*", "", failure.get("why") or "")
            parts.append(f"{who} failed: {why or 'no reason recorded'}")
    return "; ".join(parts)


def summary_written(video_id, summary_text, settled_verdict=None, attempt=1, seconds=None,
                    **lane):
    """A write-up exists. What section 6 came to goes on the record at the same moment, so
    nothing downstream has to re-read the summary to know whether the verdict has any
    evidence under it.

    settled_verdict is what rules 1 and 2 left the verdict as, and it is stored beside the
    transcript verdict rather than over it: the OPPORTUNITY gate can demote a verdict for a
    reason final_verdict cannot see from a record, and "read as OPPORTUNITY" is still the
    true history of a row that now says MAYBE.

    FIX 1, 2026-09-22: and when, on which attempt, and how long the call took. A morning
    retry wrote one long talk up and nothing downstream could tell it had been a
    retry, so the write-up went into no digest at all."""
    applies, project, takeaway = section6_state(summary_text)
    try_line = extract_try_this_week(summary_text)
    # 2026-09-26 night, brief part 3: a keep under the label past the day's cap is recorded as
    # such, and final_verdict demotes it; the log says so here.
    cap = label_cap_for(video_id, project, settled_verdict)
    return update_record(video_id, summary_state="WRITTEN", reason_code="written", label_cap=cap,
                         state_reason="", applies=applies, project=project,
                         takeaway=takeaway, try_this_week=try_line, terminal=False,
                         header=writeup_header(summary_text),
                         settled_verdict=settled_verdict,
                         decision_hit=try_line_contradiction(try_line, project),
                         written_day=today_key(), written_attempt=int(attempt),
                         summary_seconds=round(seconds, 1) if seconds is not None else None,
                         **lane)


CEILING_ERROR_RE = re.compile(r"(?:the smallest audio-bearing format is|the audio re-encoded "
                               r"to 16 kHz mono (?:is|would be)) ([\d.]+) MB, over the "
                               r"([\d.]+) MB ceiling")


def ceiling_reason(text):
    """"audio 67.0 MB over the 63 MB ceiling" out of the fetcher's own error, or None.

    One function so the row a run writes and the row a rebuild writes say the same thing
    in the same words. The fetcher's sentence names the constant, AUDIO_MAX_BYTES, which
    belongs in a log and not in a line a person reads."""
    m = CEILING_ERROR_RE.search(text or "")
    return f"audio {m.group(1)} MB over the {m.group(2)} MB ceiling" if m else None


def count_bucket(tags):
    """Which of the day's five counts a record joins, or None when it joins none."""
    found = [c for c in COUNT_TAGS if c in tags]
    return found[0] if found else None


# THE one function. Every table row, every header line and every count in the digest comes
# from here, and nothing else in this tool decides a verdict. It is pure on purpose: no file
# read, no clock, no model, so every state and every transition can be checked by
# construction in tests/test_records.py rather than by running the tool and hoping.
def final_verdict(record):
    """(verdict, state text, tags) for one record.

    It takes the listing verdict, the transcript verdict, the summary state, the section 6
    project and the decisions check off the record, and it is the only place that turns
    them into what a reader sees.

    Two rules live here rather than anywhere else. A record that is not WRITTEN can never
    display YES or OPPORTUNITY, enter the header, or be counted as a keep: without the
    write-up there is no evidence for the verdict, and the row says so plainly while
    keeping the transcript verdict as history. And a WRITTEN record whose section 6 named
    no project is demoted to MAYBE, whether section 6 said the word "nothing" or was never
    written at all. That second half differs from demote_if_nothing_applies, which treats a
    missing section 6 as unmeasured and leaves the verdict alone; the difference is
    deliberate and is this run's brief, and it costs nothing on the golden set, where all
    66 stored summaries have a section 6 that is either items or nothing."""
    state = record.get("summary_state")
    code = record.get("reason_code") or ""
    if state not in SUMMARY_STATES:
        raise ValueError(f"record {record.get('id')!r} has summary_state {state!r}, which is "
                         f"not one of {SUMMARY_STATES}")
    if code not in REASON_CODES:
        raise ValueError(f"record {record.get('id')!r} has reason_code {code!r}, which is not "
                         f"one of {sorted(REASON_CODES)}")
    expected_state, _row, _bucket = REASON_CODES[code]
    if expected_state != state:
        raise ValueError(f"record {record.get('id')!r} says state {state!r} with reason code "
                         f"{code!r}, which belongs to {expected_state!r}")

    tags = {"contradicts_decision"} if record.get("decision_hit") else set()
    reason = one_line(record.get("state_reason") or "")
    read = record.get("read_verdict")

    # FIX 4, 2026-09-22: a transcript with no speech in it was never read, whatever state
    # the record reached before this check existed. The two music videos of that day were
    # WRITTEN, from "Oh" and from a line Whisper makes up on silence.
    if record.get("no_speech") or code == "no_speech":
        return "NOT READ", no_speech_text(record), frozenset(tags | {"row", "not_read", "guess"})

    if state == "WRITTEN":
        # settled_verdict is what the OPPORTUNITY gate and the nothing-applies rule left
        # behind at the moment the summary was finished; read is the transcript triage's
        # own answer and stays on the record as history.
        verdict = record.get("settled_verdict") or read or record.get("listing_verdict") or "MAYBE"
        # FIX 2, 2026-09-22: a record written on a retry says so, and says what happened to
        # the attempts before, in place of a failure that is no longer true. One talk's row
        # still ended "[summarize failed: ... timed out after 600s ...]" the day
        # it was written up in 122.6 s.
        note = attempt_history_note(record)
        if note:
            tags.add("retried")
        # 2026-09-30, the opportunity lane: a record it judged is decided by its card, and an
        # OPPORTUNITY is never demoted for naming no project (lane_verdict). None means the
        # project rules below decide, exactly as before; a record written before the lane
        # never comes here.
        if record.get("opp_lane"):
            decided = lane_verdict(record, verdict, read, note, tags)
            if decided is not None:
                return decided
        # The no-project demotion is decided here from the record, not inherited from
        # whichever rule happened to notice first. settle_verdict already demotes this case
        # during a run and final_verdict has to demote it during a rebuild, and a row that
        # says "demoted" in one and not the other would be two answers to one question.
        if read in ("YES", "OPPORTUNITY") and not record.get("project"):
            why = ("section 6 is missing, which counts as nothing"
                   if record.get("applies") == "missing" else "section 6 named no project")
            text = "; ".join(x for x in (note, f"demoted from {read}: {why}") if x)
            return "MAYBE", text, frozenset(tags | {"row", "read_and_skipped", "demoted"})
        # FIX 4, 2026-09-25: a YES is a live project, an action, and nothing already done,
        # decided or generic; everything else is MAYBE, and the row says which it failed.
        if read == "YES" and verdict in ("YES", "MAYBE"):
            why = yes_problem(record)
            if why:
                text = "; ".join(x for x in (note, f"demoted from YES: {why}") if x)
                return "MAYBE", text, frozenset(tags | {"row", "read_and_skipped", "demoted"})
        if verdict in ("YES", "OPPORTUNITY"):
            return verdict, note, frozenset(tags | {"row", "header", "kept"})
        return verdict, note, frozenset(tags | {"row", "read_and_skipped"})

    if state == "READ_NOT_WRITTEN":
        if code == "read_then_dropped":
            # Read, found empty by the transcript triage, and deliberately not summarised.
            # Not a failure, so it is not in the "read but not written" count and gets no
            # table row: it keeps the Screened out section it has always had.
            return (read or "NO", reason or "read, then screened out; no summary spent on it",
                    frozenset(tags | {"read_and_skipped", "screened_out"}))
        attempts = int(record.get("attempts") or 0)
        parts = [f"read as {read}" if read else "read"]
        if reason:
            parts.append(reason + f", attempt {attempts} of {SUMMARY_MAX_ATTEMPTS}"
                          if attempts else reason)
        if record.get("terminal"):
            parts.append("no further attempt")
        return "NOT WRITTEN", "; ".join(parts), frozenset(tags | {"row", "read_not_written"})

    if state == "WAITING":
        return "WAITING", reason or "queued; nothing spent on it yet", frozenset(
            tags | {"waiting"})

    # NOT_READ, and which kind it is decides whether the reader sees a row or a line.
    if code == "off_topic":
        # FIX 3, 2026-09-25: dropped by the topic filter before any triage, like an
        # age-filtered result: no row, none of the five counts, one line under the table.
        return "NOT READ", reason, frozenset(tags | {"off_topic"})
    if code == "too_old":
        return "NOT READ", reason, frozenset(tags | {"age_filtered"})
    if code == "screened_out":
        return "NO", reason or "screened out from the listing; never fetched", frozenset(
            tags | {"not_read", "screened_out", "guess"})
    return "NOT READ", reason, frozenset(tags | {"row", "not_read", "guess"})


def write_last_run(mode, **info):
    data = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode, **info}
    atomic_write_text(LAST_RUN_PATH, json.dumps(data, indent=2, ensure_ascii=False))


def load_backlog():
    if BACKLOG_PATH.exists():
        return json.loads(BACKLOG_PATH.read_text(encoding="utf-8"))
    return {}


def save_backlog(backlog):
    atomic_write_text(BACKLOG_PATH, json.dumps(backlog, indent=2, ensure_ascii=False))


def backlog_candidates(curl):
    """One channel's 2026 catch-up list. Split out of the ordinary scan on 2026-09-04:
    it used to be that a channel which wasn't backfilled yet had its whole 2026 history
    returned *as* its recent window, which is why one channel's backlog could swallow
    every slot of every day and the ten channels below it in the file were never listed
    at all. The backlog is now its own phase with its own gate; see run_backlog_phase()."""
    floor = MIN_BACKFILL_DATE
    fetch_size = BACKFILL_FETCH_SIZE
    if BACKLOG_MAX_AGE_DAYS is not None:
        cutoff = (dt.date.today() - dt.timedelta(days=BACKLOG_MAX_AGE_DAYS)).strftime("%Y%m%d")
        floor = max(floor, cutoff)
        fetch_size = min(BACKFILL_FETCH_SIZE, BACKLOG_WINDOW_FETCH_SIZE)
    videos = list_channel_videos(curl, max_items=fetch_size)
    in_window = [v for v in videos if (v.get("upload_date") or "99999999") >= floor]
    hit_fetch_limit = len(videos) >= fetch_size and len(in_window) == len(videos)
    return in_window, hit_fetch_limit


# --- channel rotation -------------------------------------------------------
# channels.txt used to be walked from the top on every slot, six slots a day. That reads
# like full coverage and delivered the opposite: the first channel's backlog spent the
# per-run caption budget, run_channels broke, and after three days backlog.json had
# exactly one channel in it. A small rotating window per slot spreads the requests over
# the day AND is the only version that actually reaches the bottom of the file.
# The cursor lives in rotation.json and deliberately does NOT reset at midnight; only the
# record of what today has already covered does. Resetting the cursor daily would start
# every morning at channel one and starve channel eleven exactly as before.


def load_rotation():
    if ROTATION_PATH.exists():
        try:
            return json.loads(ROTATION_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_rotation(state):
    # The topics cursor (run_topics) and the per-channel listing dates (2026-09-20) live in
    # this file too; a channel-side write that does not mention one must not lose it.
    # scanned_today is deliberately NOT carried over: replacing it is the whole point of a
    # write.
    prev = load_rotation()
    for key in ("topics_index", "listed"):
        if key in prev and key not in state:
            state = dict(state, **{key: prev[key]})
    atomic_write_text(ROTATION_PATH, json.dumps(state, indent=2, ensure_ascii=False))


def note_topic_searched(next_index):
    """Advance the topics cursor: the next --topics run starts at queries[next_index]."""
    state = load_rotation()
    state["topics_index"] = int(next_index)
    save_rotation(state)


def rotation_state(channel_urls):
    """(cursor state, channels already covered by the pass now running). Anything in
    scanned_today that is no longer in channels.txt is dropped, so editing the file can't
    leave a pass permanently 'complete' against a channel that isn't there any more.

    A pass deliberately is NOT bounded by midnight. Six slots covering two channels each
    leaves exactly one slot of slack against eleven channels, and a single post-429
    cooldown eats a whole slot - three strikes on 2026-09-04 cost four. Clearing an
    unfinished pass at midnight would mean a day that lost a slot cancelled its own
    backlog phase and then started over from scratch, forever. So an unfinished pass
    carries over and a finished one is cleared when the date changes."""
    state = load_rotation()
    scanned = [u for u in state.get("scanned_today", []) if u in channel_urls]
    if state.get("day") != today_key() and channel_urls and set(channel_urls).issubset(set(scanned)):
        scanned = []
    return ({"day": today_key(), "index": int(state.get("index", 0)),
             "scanned_today": scanned}, scanned)


def start_new_pass(channel_urls):
    """Close a completed pass and begin the next one."""
    state, _ = rotation_state(channel_urls)
    save_rotation({"day": today_key(), "index": int(state.get("index", 0)), "scanned_today": []})


def channels_for_this_slot(channel_urls):
    """The next CHANNELS_PER_SLOT channels, wrapping, skipping anything already covered
    today. The skip matters at the end of a day: without it a wrap-around would spend the
    last slot re-reading channel one while channel eleven was still untouched."""
    state, scanned = rotation_state(channel_urls)
    start = int(state.get("index", 0)) % len(channel_urls)
    picked = []
    for step in range(len(channel_urls)):
        curl = channel_urls[(start + step) % len(channel_urls)]
        if curl in scanned or curl in picked:
            continue
        picked.append(curl)
        if len(picked) >= CHANNELS_PER_SLOT:
            break
    return picked


def mark_channel_scanned(channel_urls, curl):
    """Recorded per channel as it finishes, not per slot up front: if a 429 or the budget
    stops the run halfway through the slot, the next slot resumes on the channel that
    didn't get done rather than skipping it until tomorrow.

    Since 2026-09-20 it also writes the date into rotation.json's `listed` map, which is a
    permanent per-channel record rather than a within-the-day one. `scanned_today` could
    never answer "when was this channel last listed", because it is cleared when a pass
    completes, and that is why `worthwatch status` could call nine channels "never listed" on a day
    the rotation had listed all twelve."""
    state, scanned = rotation_state(channel_urls)
    if curl not in scanned:
        scanned.append(curl)
    if curl in channel_urls:
        index = (channel_urls.index(curl) + 1) % len(channel_urls)
    else:
        index = int(state.get("index", 0))
    listed = dict(load_rotation().get("listed") or {})
    listed[curl] = today_key()
    save_rotation({"day": today_key(), "index": index, "scanned_today": scanned,
                   "listed": listed})


def day_scan_complete(channel_urls):
    _, scanned = rotation_state(channel_urls)
    return bool(channel_urls) and set(channel_urls).issubset(set(scanned))


def channel_listing_dates(channel_urls):
    """{channel url: (date, where the date came from)} for the last time each channel was
    listed, for whatever can be shown. Three sources, newest kept:

      the record      rotation.json's `listed` map, written by mark_channel_scanned since
                      2026-09-20. The only exact source, and it only knows about runs since.
      today's pass    rotation.json's `scanned_today` with its `day`, so the channels this
                      day's rotation has already covered are known even before the map has
                      an entry for them. This is what makes the fix true on the day it lands
                      rather than tomorrow.
      history         the newest history.jsonl row whose `channel` field is the URL itself,
                      which is how a channel-sourced video records its channel. Evidence the
                      rotation reached it, not proof of the last time it did: a listing that
                      found nothing new leaves no row.

    A channel with nothing from any of the three gets None, and the caller says "no listing
    recorded" rather than "never listed", because those are different claims and only one of
    them is provable from this file."""
    state = load_rotation()
    dates = {}

    def note(url, date, source):
        if not date or url not in channel_urls:
            return
        if url not in dates or date > dates[url][0]:
            dates[url] = (date, source)

    for url, date in (state.get("listed") or {}).items():
        note(url, date, "rotation record")
    day = state.get("day")
    for url in state.get("scanned_today") or []:
        note(url, day, "today's pass")
    for entry in load_history_since(dt.date.today() - dt.timedelta(days=60)):
        note(entry.get("channel"), entry.get("date"), "a video it produced")
    return {url: dates.get(url) for url in channel_urls}


def update_backlog_progress(curl, videos_2026, hit_fetch_limit, seen):
    # Only videos this tool would ever actually process count towards done. A channel's
    # 2026 list is full of Shorts and other things duration_allows() rejects, and
    # process_candidates skips those without writing them to seen.json - so counting them
    # here held `remaining` permanently above zero and `backfilled` permanently false.
    # That was survivable while every channel was retried each slot; now that the backlog
    # works one channel at a time it would have pinned the queue to the first channel in
    # channels.txt forever and starved the other ten.
    eligible = [v for v in videos_2026 if duration_allows(v)]
    remaining = [v for v in eligible if v["id"] not in seen]
    backlog = load_backlog()
    prev = backlog.get(curl, {})

    # Same belt for everything else that can never finish: a listing entry whose metadata
    # fetch always fails, or a channel bigger than BACKFILL_FETCH_SIZE. If a pass comes
    # back having reduced `remaining` by nothing, that is a stall; three stalls and the
    # channel is called done anyway, with the reason written down. Same shape as the
    # gave_up rule for captions that never finish generating.
    stalls = int(prev.get("stalls", 0))
    if prev and not prev.get("backfilled") and len(remaining) >= int(prev.get("remaining", -1)):
        stalls += 1
    else:
        stalls = 0
    stuck = stalls >= BACKLOG_MAX_STALLS

    if stuck:
        note = (f"stopped with {len(remaining)} video(s) left: {stalls} passes in a row "
                f"made no progress on them")
    elif hit_fetch_limit:
        note = "channel has more than the fetch window; total may be higher"
    else:
        note = None
    backlog[curl] = {
        "backfilled": (not remaining and not hit_fetch_limit) or stuck,
        "total_2026_known": len(eligible), "remaining": len(remaining),
        "stalls": stalls, "note": note,
    }
    save_backlog(backlog)


def read_channel_urls():
    if not CHANNELS_PATH.exists():
        return []
    return [l.strip() for l in CHANNELS_PATH.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


# --- favourite channels, 2026-10-05 -------------------------------------------------------
# FAVOURITE_CHANNELS says which; these say who is one, and how much of the day is theirs.


def channel_handle(url):
    """"@name", lowercased, from a channel URL or a bare handle; None when it carries none."""
    tail = str(url or "").strip().rstrip("/").rsplit("/", 1)[-1].lower()
    return tail if tail.startswith("@") and len(tail) > 1 else None


def favourite_channels(channel_urls=None):
    """The channels of channels.txt that FAVOURITE_CHANNELS names, in the file's order."""
    named = {channel_handle(c) for c in FAVOURITE_CHANNELS} - {None}
    urls = read_channel_urls() if channel_urls is None else channel_urls
    return [c for c in urls if channel_handle(c) in named]


def channel_key(video):
    """One name for a video's channel however it was found: the "@handle" when anything carries
    one (a channel listing's URL, a search result's uploader), else the channel's name,
    lowercased. A search result with no handle and a listing of the same channel can differ."""
    if video.get("channel_key"):
        return video["channel_key"]
    handle = video_handle(video) or channel_handle(video.get("channel"))
    if handle:
        return handle
    return one_line(str(video.get("channel") or "")).lower() or None


def is_favourite(video):
    """True for a video from a favourite channel, whether a listing, a search or the queue met it."""
    if video.get("favourite"):
        return True
    return channel_key(video) in {channel_handle(c) for c in favourite_channels()}


def channel_reads_today(key):
    """How many of one channel's videos were read today (a transcript landed), however they
    were found; `key` is channel_key()'s. CHANNEL_DAILY_CAP is checked against it."""
    day = today_key()
    return sum(1 for r in load_records().values()
               if r.get("read_day") == day and channel_key(r) == key)


def favourite_reads_today():
    """How many favourite channels' videos were read today (a transcript landed)."""
    day = today_key()
    return sum(1 for r in load_records().values() if r.get("favourite") and r.get("read_day") == day)


def favourite_reserve_left():
    """How much of today's cap is still held back for the favourite channels' videos."""
    favourites = favourite_channels()
    if not favourites:
        return 0
    return max(0, FAVOURITE_DAILY_RESERVE * len(favourites) - favourite_reads_today())


def favourite_budget():
    """What the favourite channels' step of a run may spend: all that is left of the best
    enabled path's daily cap, the held-back room included, and not the run's 3."""
    return {"remaining": max([budget_left_today(p) for p in path_order()] or [0])}


def note_channel_listed(curl):
    """A favourite is listed in every run, outside the rotation; only the day it was last listed
    is recorded, in rotation.json's `listed` map, so `worthwatch status` can still say it."""
    state = load_rotation()
    listed = dict(state.get("listed") or {})
    listed[curl] = today_key()
    state["listed"] = listed
    save_rotation(state)


def channel_videos_for_run(videos, curl, days, favourite=False):
    """One channel's listing as a run reads it: the window applied, and each video stamped with
    its channel's name and key (and, for a favourite, that it is one)."""
    # The channel's name rides along on every listing entry; the separate channel request
    # that used to fetch it was one more request per channel per slot.
    channel_name = next((v.get("channel") for v in videos if v.get("channel")), None) or curl
    if days is not None:
        videos = [v for v in videos if within_days(v.get("upload_date"), days)]
    for v in videos:
        v["channel"] = channel_name
        v["channel_key"] = channel_handle(curl) or channel_key(v)
        if favourite:
            v["favourite"] = True
    return videos


def cmd_pause():
    PAUSE_PATH.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    print("Paused. Scheduled runs will exit silently until you run `worthwatch resume`. Manual runs still work.")


def cmd_resume():
    if PAUSE_PATH.exists():
        PAUSE_PATH.unlink()
        print("Resumed. Scheduled runs will proceed normally.")
    else:
        print("Wasn't paused.")


def cmd_cooldown():
    """Exit code for a scheduler script: 1 only while EVERY enabled path is in cooldown, 0
    otherwise. A script calls this instead of reading rate-state.json itself, so the policy
    stays in exactly one place. A cooldown on one path is a fact about that path only, so it
    never cancels a run another path could still make."""
    live = [p for p in path_order() if cooldown_remaining(p) <= 0]
    cooling = [p for p in path_order() if cooldown_remaining(p) > 0]
    for path in cooling:
        print(f"The {path} path is in cooldown until {cooldown_until(path):%Y-%m-%d %H:%M} "
              f"({format_duration(cooldown_remaining(path))} left).")
    if live:
        print(f"Running this slot on: {', '.join(live)}.")
        return 0
    if not path_order():
        print("No transcript path is switched on; skipping this slot entirely.")
    else:
        print("Every transcript path is in cooldown; skipping this slot entirely.")
    return 1


def cmd_requests(limit=40):
    """The last N YouTube requests, one line each, straight from logs/requests.jsonl:
    time, kind, endpoint, gap since the previous request, duration, status, signals."""
    rows = load_request_log()
    if not rows:
        print(f"No requests logged yet ({REQUEST_LOG_PATH}).")
        return 0
    print(f"{'time':19}  {'kind':12} {'endpoint':34} {'gap':>7} {'dur':>6}  status   video        signals")
    for e in rows[-limit:]:
        gap = "-" if e.get("gap_s") is None else f"{e['gap_s']:.0f}s"
        sig = ",".join(e.get("signals") or [])
        vid = e.get("video_id") or ""
        print(f"{e.get('ts', ''):19}  {e.get('kind', ''):12} {e.get('endpoint', ''):34} "
              f"{gap:>7} {e.get('duration_s', 0):>5.1f}s  {e.get('status', ''):8} {vid:12} {sig}")
    return 0


def cmd_audio(urls):
    """`worthwatch audio <url>...` - fetch a transcript through the audio path only, and say
    what it cost. The caption endpoint is not touched; it still honours the audio path's own
    cooldown, cap and gaps."""
    if not urls:
        print("Usage: worthwatch audio <url> [<url> ...]")
        return 1
    left = cooldown_remaining("audio")
    if left > 0:
        print(f"The audio path is in cooldown for another {format_duration(left)} "
              f"(until {cooldown_until('audio'):%Y-%m-%d %H:%M}). Nothing fetched.")
        return 1
    budget = budget_left_today("audio")
    print(f"Audio path: {used_today('audio')}/{daily_cap_for('audio')} used today, "
          f"{budget} left; {WHISPER_MODEL} on {WHISPER_DEVICE}.")
    done = 0
    for url in urls:
        if budget - done <= 0:
            print(f"Today's audio budget is spent; {len(urls) - done} url(s) not fetched.")
            break
        print(f"\n{url}")
        try:
            meta, err = fetch_transcript_via_audio(url)
        except RateLimitError:
            print("  refused; stopping.")
            return 1
        if err:
            print(f"  FAILED: {err}")
            continue
        done += 1
        print(f"  {meta['title']} - {len(meta['transcript'])} chars -> {meta['raw_path']}")
    return 0


def cmd_paths():
    """`worthwatch paths` - the two transcript paths side by side: which is available right now,
    what each has spent, and what the canary last said about the caption endpoint."""
    labels = {"captions": "captions (/api/timedtext)",
              "audio": "audio (googlevideo + local whisper)"}
    order = usable_paths()
    print(f"Leading this minute: {order[0] if order else 'nothing - no path is usable'}"
          + (f", falling back to {order[1]}" if len(order) > 1 else "") + ".")
    print(f"Configured order: {' then '.join(path_order()) or 'nothing is switched on'} "
          f"(PATH_PREFERENCE {' > '.join(PATH_PREFERENCE)})\n")
    for path in PATH_PREFERENCE:
        state = load_rate_state()
        left = cooldown_remaining(path)
        print(labels[path])
        blocked = path_blocked(path)
        print(f"  {'available' if blocked is None else blocked.upper()}"
              + (f", strike {current_strikes(state, path)}" if left > 0 else ""))
        print(f"  today: {used_today(path)}/{daily_cap_for(path)}  "
              f"(reason: {state.get(_cap_keys(path)[2], 'not set yet')})")
        canary = last_canary(path, state)
        print("  last canary: " + (f"{canary.get('outcome', '?').upper()} at {canary.get('at')} "
                                   f"on {canary.get('video_id')}" if canary else "none yet"))
        verdict, sentence, evidence = canary_attribution(path=path)
        print(f"  refusals follow: {verdict.upper()} - {sentence}")
        print(f"    refused in window: {', '.join(evidence['refused']) or 'none'}; "
              f"served: {', '.join(evidence['served']) or 'none'}")
    return 0


# --- re-summarising what is already on disk -------------------------------------------
# Every transcript this tool has ever read is in raw/<id>.txt, and until now there was no
# way to run the summariser over one again. That is the reason a change to
# SUMMARY_INSTRUCTIONS or to the guards could only be judged the next morning, in the next
# digest, on whatever videos happened to turn up: measuring the old summariser against the
# new one meant spending the day's fetch budget to get transcripts already sitting on the
# disk. `worthwatch resummarize <url-or-id>` makes no YouTube request of any kind, so it runs
# during a cooldown, on a paused tool, and as many times as a prompt change needs.
#
# It prints and does not save. A test run has no business appending to INDEX.md,
# TRY-NEXT.md, OPPORTUNITIES.md or today's digest; --save is there when the point really
# is to replace a bad summary on disk.

def transcript_on_disk(video_id):
    """The transcript this machine already has, from transcripts/ first and then raw/.

    One reader for both stores since 2026-09-21, so `worthwatch resummarize` and the retry phase
    cannot disagree about whether a transcript exists."""
    text, _record = saved_transcript(video_id)
    return text


def history_rows_for(video_id):
    """Every history.jsonl row for this video, oldest first."""
    rows = []
    if not HISTORY_PATH.exists():
        return rows
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if video_id_from_url(row.get("url") or "") == video_id:
                rows.append(row)
    return rows


def meta_from_disk(video_id):
    """The meta dict the summariser wants, rebuilt from what is already on this machine:
    the transcript from raw/, the title and uploader from the info cache, and the verdict
    the transcript triage settled on from history.jsonl. Returns (meta, previous_summary)."""
    transcript = transcript_on_disk(video_id)
    if transcript is None:
        return None, None
    info_path = info_cache_path(video_id)
    info = {}
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            info = {}
    rows = history_rows_for(video_id)
    last = rows[-1] if rows else {}
    summarized = [r for r in rows if r.get("summary")]
    meta = {
        "id": video_id,
        "title": info.get("title") or last.get("title") or video_id,
        "uploader": info.get("uploader") or info.get("channel") or last.get("channel") or "UNKNOWN",
        "channel": last.get("channel") or info.get("uploader") or "UNKNOWN",
        "url": last.get("url") or f"https://www.youtube.com/watch?v={video_id}",
        "transcript": transcript,
        "raw_path": RAW_DIR / f"{video_id}.txt",
        "lang": "en",
        "track_kind": "on disk",
        "verdict": last.get("verdict"),
        "verdict_reason": last.get("reason") or "",
    }
    return meta, (summarized[-1] if summarized else None)


def print_section(summary_text, number, header):
    body = section_text(summary_text, rf"^##\s*{number}\b")
    print(f"\n{header}")
    print("-" * len(header))
    print(body if body.strip() else "(no section)")


def cmd_resummarize(argv):
    """worthwatch resummarize <url-or-id> [...] [--claude] [--model M] [--verdict V] [--section N]
                                  [--before] [--save]

    Re-runs the summariser over transcripts already in raw/. Makes no YouTube request."""
    ids, use_claude, model, verdict_override, sections, show_before, save = [], False, None, None, [], False, False
    it = iter(argv)
    for arg in it:
        if arg == "--claude":
            use_claude = True
        elif arg == "--model":
            model = next(it, None)
        elif arg == "--verdict":
            verdict_override = (next(it, "") or "").upper()
        elif arg == "--section":
            value = next(it, "")
            sections += [int(n) for n in re.findall(r"\d", value)]
        elif arg == "--before":
            show_before = True
        elif arg == "--save":
            save = True
        elif arg.startswith("-"):
            print(f"Unknown option {arg!r}.")
            return 2
        else:
            ids.append(video_id_from_url(arg) or arg)
    if not ids:
        print("Usage: worthwatch resummarize <url-or-id> [...] [--claude] [--model M] [--verdict V] "
              "[--section 6] [--before] [--save]")
        return 2

    context_text = load_context()
    local_model = model or DEFAULT_LOCAL_MODEL
    if not use_claude:
        ensure_local_model(local_model)

    for video_id in ids:
        meta, previous = meta_from_disk(video_id)
        if meta is None:
            print(f"\n{video_id}: no transcript in {RAW_DIR}. Nothing to re-summarise, and "
                  f"this command never fetches one.")
            continue
        if verdict_override:
            meta["verdict"] = verdict_override
        words = len(meta["transcript"].split())
        print(f"\n{'=' * 78}\n{meta['title']}\n{meta['url']}")
        print(f"  {words} words on disk, verdict {meta.get('verdict') or 'none recorded'}")

        if use_claude:
            def again(note, _m=meta):
                return summarize_claude(context_text, _m, _m["transcript"], extra=note)[0]
            label = write_label()
        else:
            def again(note, _m=meta):
                return summarize_local(local_model, context_text, _m, _m["transcript"], extra=note)[0]
            label = quick_label(local_model)

        t0 = time.monotonic()
        summary, chunked = (summarize_claude(context_text, meta, meta["transcript"]) if use_claude
                            else summarize_local(local_model, context_text, meta, meta["transcript"]))
        elapsed = time.monotonic() - t0
        print(f"  -> {label}, {elapsed:.1f}s{' [chunked]' if chunked else ''}")
        summary = finalize_summary(summary, meta["transcript"], local_model,
                                   verdict=meta.get("verdict"), regenerate=again, meta=meta,
                                   backend=label)
        # The same two gates the scheduled path applies, so a re-summarise reports the verdict
        # the tool would actually publish rather than the one the model asked for. This is the
        # command Phase 3's re-scoring runs, and a gate it skipped here would be a gate
        # measured nowhere.
        settled, summary, gate_notes = settle_verdict(meta.get("verdict"), summary)
        for note in gate_notes:
            print(f"  ({note})")
        if settled != meta.get("verdict"):
            print(f"  verdict: {meta.get('verdict')} -> {settled}")

        wanted = sections or None
        if show_before and previous:
            print(f"\n### BEFORE ({previous.get('backend', 'unknown backend')}, "
                  f"{previous.get('date', '')[:10]})")
            for n in (wanted or [6]):
                print_section(previous["summary"], n, f"section {n}")
        elif show_before:
            print("\n### BEFORE: no summary for this video in history.jsonl.")

        if wanted:
            print(f"\n### AFTER ({label})")
            for n in wanted:
                print_section(summary, n, f"section {n}")
        else:
            print(f"\n{'-' * 78}\n{summary}\n{'-' * 78}")

        print(f"  top takeaway -> {extract_top_takeaway(summary)}")
        print(f"  try this week -> {extract_try_this_week(summary)}")
        if save:
            path = save_writeup(meta, summary, label, elapsed, chunked)
            print(f"  saved: {path}")
    return 0


# The notes last-run.json can carry that are facts about TODAY rather than about the run.
# "daily cap already reached" printed on a fresh morning is a false sentence: the run that
# wrote it was right, and `worthwatch status` repeated it for as long as nothing overwrote it. So the
# day-scoped ones are re-measured at the moment status prints them.
TOPICS_CAP_NOTE = "daily cap already reached"
BUDGET_SPENT_NOTE = "every transcript path had spent today's budget"


def note_still_true(note):
    """(True, line) if the note holds now, (False, the correction) if it does not, and
    (None, note) for a note this cannot check, which is printed as written rather than
    guessed at."""
    if note == TOPICS_CAP_NOTE:
        used = todays_source_count("topics")
        if used >= TOPICS_DAILY_CAP:
            return True, (f"the topics daily cap is reached: {used}/{TOPICS_DAILY_CAP} "
                          f"topics-sourced video(s) today")
        return False, (f"not any more: today is at {used}/{TOPICS_DAILY_CAP} topics-sourced "
                       f"video(s), so {TOPICS_DAILY_CAP - used} are left")
    if note == BUDGET_SPENT_NOTE:
        left = new_budget()["remaining"]
        if left <= 0:
            return True, f"{note}, and none has come back"
        return False, f"not any more: {left} transcript fetch(es) available now"
    return None, note


def print_last_run():
    """The "Last run:" block of `worthwatch status`. Its own function so that a missing or unreadable
    last-run.json cannot cut the rest of the report off (see the call site)."""
    if not LAST_RUN_PATH.exists():
        print("Last run: never")
        return
    try:
        d = json.loads(LAST_RUN_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("Last run: (last-run.json unreadable)")
        return
    print(f"Last run: {d.get('timestamp')} ({d.get('mode')})")
    mode = d.get("mode")
    if mode == "discover":
        print(f"  {d.get('candidates_found', 0)} candidate channel(s) found")
    elif mode == "weekly":
        print(f"  {d.get('videos', 0)} video(s) rolled up - YES {d.get('yes', 0)}, "
              f"OPPORTUNITY {d.get('opportunity', 0)}, MAYBE {d.get('maybe', 0)}, "
              f"NO {d.get('no', 0)} ({d.get('word_count', 0)} words)")
    elif d.get("note"):
        still, line = note_still_true(d["note"])
        if still is False:
            # Both halves, because the run's own record is not wrong, it is just old.
            print(f"  it stopped saying: {d['note']}")
            print(f"  as of now, {line}")
        else:
            print(f"  {line}")
    else:
        gave_up_note = f", gave up {d['gave_up']}" if d.get("gave_up") else ""
        print(f"  {d.get('new_videos', 0)} new video(s) - YES {d.get('yes', 0)}, "
              f"OPPORTUNITY {d.get('opportunity', 0)}, MAYBE {d.get('maybe', 0)}, "
              f"NO {d.get('no', 0)}{gave_up_note}")
    if d.get("skipped_too_old"):
        print(f"  {d['skipped_too_old']} search result(s) skipped as older than "
              f"{TOPIC_MAX_AGE_DAYS // 365} years")


def cmd_status():
    if PAUSE_PATH.exists():
        since = PAUSE_PATH.read_text(encoding="utf-8").strip()
        print(f"Paused: YES, since {since}")
    else:
        print("Paused: no")

    rate = load_rate_state()
    order = usable_paths()
    print(f"Transcript path: {order[0].upper() if order else 'NONE AVAILABLE'}"
          + (f", falling back to {order[1]}" if len(order) > 1 else "")
          + f" (configured: {' then '.join(path_order()) or 'nothing switched on'})")
    for path in PATH_PREFERENCE:
        blocked = path_blocked(path)
        if blocked:
            print(f"  {path}: {blocked}")
    cooling = [p for p in path_order() if cooldown_remaining(p) > 0]
    if cooling:
        for path in cooling:
            print(f"Rate-limit cooldown: ACTIVE on the {path.upper()} path, ends "
                  f"{cooldown_until(path):%Y-%m-%d %H:%M} "
                  f"({format_duration(cooldown_remaining(path))} from now); 429 at "
                  f"{rate.get(_path_key(path, 'last_429'), 'UNKNOWN')}, strike "
                  f"{rate.get(_path_key(path, 'strikes'), 1)} (strikes double the wait, "
                  f"up to {COOLDOWN_MAX_SECONDS // 3600}h)")
        rest = [p for p in path_order() if p not in cooling]
        print(f"  It stops that path only. This slot would run on: "
              f"{', '.join(rest) if rest else 'nothing - every path is out'}.")
    elif rate.get("last_429"):
        print(f"Rate-limit cooldown: clear (last 429 was {rate['last_429']})")
    else:
        print("Rate-limit cooldown: clear (no 429 recorded)")

    cap = daily_caption_cap()
    off = "" if CAPTION_PATH_ENABLED else " [OFF by default - --captions turns it on]"
    print(f"Caption path{off}: {captions_used_today()}/{cap} fetched today "
          f"({caption_budget_left_today()} left; max {MAX_CAPTION_FETCHES_PER_RUN} per run, "
          f"{CAPTION_DELAY_SECONDS:.0f}s apart)")
    print(f"  Adaptive cap: {cap} (min {CAPTION_CAP_MIN}, max {CAPTION_CAP_MAX}, +{CAPTION_CAP_STEP} per "
          f"clean day, halved on any 429). Reason: {rate.get('cap_reason', 'UNKNOWN')}")
    for path in PATH_PREFERENCE:
        canary = last_canary(path, rate)
        if canary:
            print(f"  Last {path} canary: {canary.get('outcome', '?').upper()} at "
                  f"{canary.get('at', '?')} on {canary.get('video_id', '?')} - "
                  f"{one_line(canary.get('detail'))[:150]}")
        else:
            print(f"  Last {path} canary: none yet")
        verdict, sentence, ev = canary_attribution(path=path)
        print(f"    refusals follow: {verdict.upper()} - {sentence}")
        if ev["refused"]:
            print(f"      refused in the last {ev['window_hours']}h: {', '.join(ev['refused'])}; "
                  f"served: {len(ev['served'])}")
    pool = canary_pool()
    if pool:
        print(f"  Canary rotation: {len(pool)} recent videos, next up "
              f"{pick_canary_video(rate, pool)}; the canary probes whichever path leads the slot")
    else:
        print("  Canary rotation: none yet; the canary needs a video this tool has already read")

    audio_left = cooldown_remaining("audio")
    if AUDIO_PATH_ENABLED:
        if audio_left > 0:
            state_word = (f"COOLDOWN until {cooldown_until('audio'):%Y-%m-%d %H:%M} "
                          f"({format_duration(audio_left)} left)")
        elif budget_left_today("audio") <= 0:
            state_word = "today's budget is spent"
        else:
            state_word = "available"
        lead = " [PRIMARY]" if path_order()[:1] == ["audio"] else ""
        print(f"Audio path{lead}: {state_word}; {used_today('audio')}/{daily_cap_for('audio')} "
              f"used today ({budget_left_today('audio')} left, {AUDIO_DELAY_SECONDS:.0f}s apart)")
        print(f"  googlevideo, then {WHISPER_MODEL} on "
              f"{WHISPER_DEVICE}/{WHISPER_COMPUTE_TYPE}; audio deleted after every transcript. "
              f"Reason: {rate.get('audio_cap_reason', 'not set yet')}")
    else:
        print("Audio path: OFF (AUDIO_PATH_ENABLED)")
    ejs_ok, ejs_detail = ejs_status()
    print(f"JS challenge solver: {'ON' if ejs_ok else 'OFF'} - {ejs_detail}")
    print(f"Player client: yt-dlp's own (skip {EXTRACTOR_SKIP} for captions); caption settle "
          f"{CAPTION_SETTLE_SECONDS:.0f}s after extraction")
    last_req = parse_ts(rate.get("last_request"))
    print(f"Request floor: {MIN_REQUEST_GAP_SECONDS:.0f}s between any two YouTube requests"
          + (f" (last request {last_req:%H:%M:%S})" if last_req else " (none recorded yet)"))
    todays = load_request_log(since=today_key())
    if todays:
        by = {}
        for e in todays:
            key = f"{e.get('kind')}:{e.get('status')}"
            by[key] = by.get(key, 0) + 1
        print(f"Requests today: {len(todays)} - " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    else:
        print("Requests today: none logged (logs/requests.jsonl)")

    channel_urls = read_channel_urls()
    # 2026-10-05: the favourite channels are listed in every run and are not in the rotation.
    favourites = favourite_channels(channel_urls)
    rotation = [c for c in channel_urls if c not in favourites]
    if favourites:
        print(f"Priority channels, first in every run: "
              f"{', '.join(c.rsplit('/', 1)[-1] for c in favourites)} "
              f"({favourite_reserve_left()} of today's fetches still held back for them)")
    if rotation:
        _, covered = rotation_state(rotation)
        slots_per_pass = (len(rotation) + CHANNELS_PER_SLOT - 1) // CHANNELS_PER_SLOT
        nxt = channels_for_this_slot(rotation)
        print(f"Channel rotation: {len(covered)}/{len(rotation)} covered today, "
              f"{CHANNELS_PER_SLOT} per slot, {slots_per_pass} slots to a full pass")
        if day_scan_complete(rotation):
            print("  Today's pass is complete; the 2026 backlog is eligible to run.")
        else:
            print(f"  Next up: {', '.join(c.rsplit('/', 1)[-1] for c in nxt) or '(none)'}")

    # FIX 7: one line, the day beside the week, because the week's number is the one that
    # decides whether the listing verdict is worth a column in the table at all.
    today_records = todays_day_records()
    week = days_records(WEEK_LOOKBACK_DAYS)
    today_counts = first_guess_counts(today_records)
    week_counts = first_guess_counts(week)
    print(f"First guess vs after reading: agreed {today_counts['agreed']} of "
          f"{today_counts['read']} today, {week_counts['agreed']} of {week_counts['read']} "
          f"this week (the listing verdict against what the transcript read)")

    # The last-run block used to `return` when last-run.json was missing or unreadable, which
    # took the whole backlog section with it: on a machine that has never run, and on one with
    # a truncated write, `worthwatch status` printed "Last run: never" and then stopped, and nothing
    # below was reachable. Found while testing the backlog fix, which could not be tested at
    # all until this moved into its own function.
    print_last_run()

    backlog = load_backlog()
    window = ("full 2026 catch-up" if BACKLOG_MAX_AGE_DAYS is None
              else f"capped at the last {BACKLOG_MAX_AGE_DAYS} days")
    # A channel missing from backlog.json has not had its 2026 BACKLOG run, which is not the
    # same as never having been listed: the backlog is its own phase with its own gate and
    # reaches one channel at a time, while the rotation lists every channel most days. This
    # line said "never listed" about nine channels on a day the line above it said 12/12
    # covered, and both came out of the same file.
    no_backlog = [c for c in channel_urls if c not in backlog]
    if no_backlog:
        listed = channel_listing_dates(channel_urls)
        print(f"Backlog ({window}): {len(no_backlog)} channel(s) with no backlog pass yet. "
              f"Their last listing, which is a different thing:")
        for curl in no_backlog:
            name = curl.rsplit("/", 1)[-1]
            when = listed.get(curl)
            if when:
                print(f"  {name}: listed {when[0]} ({when[1]})")
            else:
                print(f"  {name}: no listing recorded (the per-channel record starts "
                      f"2026-09-20; before that only the current day was kept)")
    if backlog:
        if not no_backlog:
            print(f"Backlog ({window}):")
        for curl, state in backlog.items():
            name = curl.rsplit("/", 1)[-1]
            if state.get("backfilled"):
                print(f"  {name}: done ({state.get('total_2026_known', 0)} videos for 2026)")
            else:
                total = state.get("total_2026_known", 0)
                remaining = state.get("remaining", 0)
                done = total - remaining
                pct = (done / total * 100) if total else 0
                note = f" ({state['note']})" if state.get("note") else ""
                stale = (BACKLOG_MAX_AGE_DAYS is not None and "stalls" not in state)
                if stale:
                    note += " - counted before the cap; refreshes on its next backlog run"
                print(f"  {name}: {done}/{total} processed ({pct:.0f}%){note}")


def get_channel_info(channel_url):
    """Only --discover still uses this (it needs the follower count and the About text).
    The scheduled path reads the channel's name off its listing entries instead."""
    cmd = [YT_DLP, "--playlist-items", "0", "--dump-single-json", channel_url]
    result = run_yt_dlp(cmd, timeout=60)
    try:
        d = json.loads(result.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return {
        "name": d.get("channel") or d.get("uploader") or channel_url,
        "followers": d.get("channel_follower_count"),
        "description": d.get("description") or "",
        "url": channel_url,
    }


# --- a listing that cannot be read, 2026-09-26 night -----------------------------------------
# Brief part 2.7. A line that was not JSON was already skipped, but a duration that was not a
# number ("12:34") raised in duration_allows and in the triage prompt and ended the run, text
# that was not text raised wherever it was lowercased, and a channel listing kept entries that
# were not videos. Every entry now goes through one cleaner, and the run says how many lines of
# its listings could not be read at all.
def parse_duration(value):
    """Seconds as a number, from a number or from "754", "12:34" or "1:02:03"; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return float(text)
        m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", text)
        if m:
            return int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


def listing_text(value):
    """A listing field as text, whatever yt-dlp put there."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(listing_text(v) for v in value)
    return str(value)


def read_listing_lines(stdout, what):
    """Every JSON object yt-dlp printed, one a line. A line that starts like JSON and is not
    an object is counted as unreadable, said in the log and, for the run, in its section."""
    entries, unreadable = [], 0
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        if isinstance(d, dict):
            entries.append(d)
        else:
            unreadable += 1
    if unreadable:
        _THIS_RUN["unreadable"] = _THIS_RUN.get("unreadable", 0) + unreadable
        print(f"    ({what}: {unreadable} line(s) could not be read and were skipped)")
    return entries


def clean_listing_entry(d):
    """One entry of a listing or a search as the pipeline reads it: an id that is a string,
    text that is text, a duration that is a number or None, an upload date of eight digits or
    None. None when there is no id at all."""
    vid = d.get("id")
    if not isinstance(vid, str) or not vid.strip():
        return None
    vid = vid.strip()
    upload = d.get("upload_date")
    upload = upload if isinstance(upload, str) and re.fullmatch(r"\d{8}", upload) else None
    url = d.get("url")
    return dict(d, id=vid, title=listing_text(d.get("title")),
                url=url if isinstance(url, str) and url else f"https://www.youtube.com/watch?v={vid}",
                duration=parse_duration(d.get("duration")), upload_date=upload,
                description=listing_text(d.get("description")),
                channel=listing_text(d.get("channel") or d.get("uploader")) or None,
                uploader=listing_text(d.get("uploader")) or None)


def list_channel_videos(channel_url, max_items=CHANNEL_LOOKBACK_VIDEOS):
    """The flat listing of a channel's Videos tab: one request, no player calls. Each
    entry carries the title, duration, approximate upload date, the channel's own name
    and id, and whatever description snippet the tab exposes (usually none)."""
    cmd = [YT_DLP, "--flat-playlist",
           "--extractor-args", "youtubetab:approximate_date",
           "--playlist-end", str(max_items), "--dump-json", f"{channel_url}/videos"]
    result = run_yt_dlp(cmd, timeout=90)
    videos, not_videos = [], []
    for raw_entry in read_listing_lines(result.stdout, f"listing of {channel_url}"):
        d = clean_listing_entry(raw_entry)
        if d is None:
            continue
        # 2026-09-26 night, brief part 2.7: a channel listing keeps only videos, as a search
        # has since 25 September; anything else would be fetched as if it were one.
        if not is_video_entry(d):
            not_videos.append(d["title"] or d["id"])
            continue
        vid = d["id"]
        videos.append({
            "id": vid, "title": d["title"], "url": d["url"],
            "duration": d["duration"], "upload_date": d["upload_date"],
            "description": d["description"], "channel": d["channel"],
            "channel_id": listing_text(d.get("channel_id")) or None,
        })
    if not_videos:
        print(f"    (listing of {channel_url}: {len(not_videos)} entr(ies) not a video, dropped: "
              f"{'; '.join(t[:40] for t in not_videos)})")
    listing_must_not_be_empty_on_error(result, videos, channel_url)
    return videos


class ListingFailed(RuntimeError):
    """A channel listing or a search that came back with an error and nothing in it."""


def listing_must_not_be_empty_on_error(result, videos, what):
    """2026-09-26 night, brief part 2.5: a listing that failed used to come back as an empty
    one, and an empty channel is marked as looked at, so a network that dropped for one
    request hid that channel's new videos for the rest of the day. yt-dlp's error and nothing
    listed is a failed listing, and it says so."""
    if videos or result.returncode == 0:
        return
    errors = [l.strip() for l in (result.stderr or "").splitlines() if l.startswith("ERROR")]
    raise ListingFailed(f"the listing of {what} failed (exit {result.returncode}): "
                        f"{one_line(errors[-1] if errors else result.stderr or '')[-240:]}")


# FIX 5, 2026-09-25. A search can return a channel or a playlist as one of its results. A
# one-word search once returned a channel, whose id is the 24-character
# channel id and whose url is /channel/UC...; it was queued as a video, the media client's
# extraction of the whole channel timed out after 180 s, and the topics run died with it.
VIDEO_ONLY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIDEO_URL_RE = re.compile(r"(?:watch\?v=|/shorts/|/live/|youtu\.be/)")


def is_video_entry(entry):
    """True when a listing or search entry is one video: an 11-character id, not a channel or
    a playlist by yt-dlp's own label, and a watch, shorts or live address when it has one."""
    if not VIDEO_ONLY_ID_RE.match(str(entry.get("id") or "")):
        return False
    if entry.get("ie_key") and entry["ie_key"] != "Youtube":
        return False
    url = entry.get("url") or ""
    return not url or bool(VIDEO_URL_RE.search(url))


# A search asks YouTube for this year's videos only, with the search page's own filter (upload
# date this year, type video): a plain relevance search returns many results two or more years
# old, which the age rule would then drop before any triage. The two-year rule stays behind the
# filter. Set it to None for a plain ytsearch.
TOPIC_SEARCH_FILTER = "EgQIBRAB"


def search_target(query, count):
    """What one search asks yt-dlp for: the search page with the filter, or ytsearch without."""
    if TOPIC_SEARCH_FILTER:
        return (f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
                f"&sp={TOPIC_SEARCH_FILTER}")
    return f"ytsearch{count}:{query}"


def search_videos(query, count=15):
    # approximate_date is the whole reason rule 4 can exist. Without it a search result has NO
    # date at all: yt-dlp's _extract_video() sets "timestamp" only when that extractor argument
    # is present (yt_dlp/extractor/youtube/_tab.py, the `if
    # self._configuration_arg('approximate_date', ie_key=YoutubeTabIE)` line), and upload_date
    # is derived from the timestamp. It costs no extra request: same listing, one more argument.
    target = search_target(query, count)
    cmd = [YT_DLP, "--flat-playlist", "--extractor-args", "youtubetab:approximate_date"]
    if not target.startswith("ytsearch"):
        cmd += ["--playlist-end", str(count)]    # the search page has no count of its own
    cmd += ["--dump-json", target]
    result = run_yt_dlp(cmd, timeout=90)
    videos, not_videos = [], []
    for raw_entry in read_listing_lines(result.stdout, f"search {query!r}"):
        # 2026-09-26 night, brief part 2.7: through the one cleaner, like a channel listing.
        d = clean_listing_entry(raw_entry)
        if d is None:
            continue
        vid = d["id"]
        if not is_video_entry(d):
            not_videos.append(d["title"] or vid)
            continue
        videos.append({
            "id": vid, "title": d["title"], "url": d["url"],
            "duration": d["duration"], "upload_date": d["upload_date"],
            "description": d["description"],
            "channel": d["channel"] or "UNKNOWN",
            "channel_url": listing_text(d.get("channel_url") or d.get("uploader_url")) or None,
            "channel_id": listing_text(d.get("channel_id")) or None,
            # The handle ("@example-channel"), which is what channels.txt holds. channel_url is the
            # /channel/UC... form and cannot be compared with that file.
            "uploader_id": listing_text(d.get("uploader_id")) or None,
            "uploader_url": listing_text(d.get("uploader_url")) or None,
        })
    if not_videos:
        print(f"    (search {query!r}: {len(not_videos)} result(s) not a video, a channel or a "
              f"playlist, dropped: {'; '.join(t[:40] for t in not_videos)})")
    if not not_videos:
        listing_must_not_be_empty_on_error(result, videos, f"the search {query!r}")
    return videos


FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def fetch_channel_feed(channel_id):
    """{video_id: description} for a channel's last fifteen uploads, from its public RSS
    feed. One request to a feed endpoint, nothing to do with the player or the caption
    server. Empty dict on any failure; a missing description is never fatal."""
    if not channel_id:
        return {}
    gap = wait_for_request_slot()
    started = dt.datetime.now()
    status, found = "ok", {}
    try:
        # An honest name, never a browser's: worthwatch does not pretend to be anything else.
        req = urllib.request.Request(FEED_URL.format(channel_id=channel_id),
                                     headers={"User-Agent": f"worthwatch/{__version__}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_text = resp.read().decode("utf-8", errors="replace")
        entries = re.findall(r"<entry>(.*?)</entry>", xml_text, re.DOTALL)
        for entry in entries:
            vid = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", entry)
            desc = re.search(r"<media:description>(.*?)</media:description>", entry, re.DOTALL)
            if vid:
                text = desc.group(1) if desc else ""
                text = (text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
                        .replace("&#39;", "'").replace("&amp;", "&"))
                found[vid.group(1)] = text.strip()
    except urllib.error.HTTPError as e:
        status = f"http{e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        status = f"error:{e.__class__.__name__}"
    note_request()
    log_request("feed", "youtube.com/feeds/videos.xml", status, started, dt.datetime.now(), gap,
                channel_id=channel_id, entries=len(found))
    return found


def enrich_with_feed(videos, need):
    """Fill in descriptions for the videos in `need` (the ones about to be triaged) from
    the channel feed, when the listing did not carry them."""
    if not FEED_ENRICHMENT:
        return
    missing = [v for v in need if not v.get("description")]
    if not missing:
        return
    channel_id = next((v.get("channel_id") for v in videos if v.get("channel_id")), None)
    if not channel_id:
        return
    feed = fetch_channel_feed(channel_id)
    for v in missing:
        if v["id"] in feed:
            v["description"] = feed[v["id"]]


# RULE 4, the age filter, 2026-09-20. A topic search ranks on words and has no idea what year
# it is: on 2026-09-20 "marketing channel that stopped working and what replaced it" returned
# "The 3 Most Important Marketing Channels of 2023", which cost a listing, a 17.6 MB audio
# download, a GPU transcription and a Sonnet summary to reach a MAYBE about 2023. Two years is
# the bound. A named channel is exempt because channels.txt is a hand-confirmed list and its
# 2026 backlog is deliberately walked; this rule is about what a SEARCH drags in.
TOPIC_MAX_AGE_DAYS = 730


def named_channel_handles():
    """The handles in channels.txt as "@name", lowercased."""
    handles = set()
    for url in read_channel_urls():
        tail = url.rstrip("/").rsplit("/", 1)[-1].lower()
        if tail.startswith("@"):
            handles.add(tail)
    return handles


def video_handle(video):
    """A search result's channel handle, from whichever field carries it."""
    for key in ("uploader_id", "uploader_url", "channel_url"):
        value = str(video.get(key) or "")
        tail = value.rstrip("/").rsplit("/", 1)[-1].lower()
        if tail.startswith("@"):
            return tail
    return None


# FIX 6, 2026-09-22. A listing's date is not a date. YouTube shows "8 years ago", and yt-dlp
# (youtube/_base.py extract_relative_time, utils datetime_from_str) turns it into one by
# taking N units off the moment of the listing in UTC, months and years by calendar, and
# rounding to the nearest day, so every run after midday UTC lands on tomorrow's month and
# day: all seventeen "uploaded" dates in one morning's too-old list fell on the next day. And
# "2 years ago", which YouTube says of anything 24 to 35 months old, came out 729 days back,
# under the 730 the rule skipped at, so no "2 years ago" result was ever skipped. The two
# functions below are that arithmetic and its inverse; the rule reads the age the source
# gave and counts "N years ago" as at least N years old.
AGE_UNITS = ("year", "month", "week", "day")


def _add_months(moment, months):
    """yt-dlp's datetime_add_months: the same day of the month, clamped to the month."""
    month = moment.month + months - 1
    year = moment.year + month // 12
    month = month % 12 + 1
    following = dt.date(year + (month == 12), month % 12 + 1, 1)
    last_day = (following - dt.date(year, month, 1)).days
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def _utc(moment):
    """An aware moment in UTC; a naive one, like a record's "created", is local time."""
    return (moment if moment.tzinfo else moment.astimezone()).astimezone(dt.timezone.utc)


def yt_dlp_listing_date(listed_at, n, unit):
    """The upload_date yt-dlp gives "n <unit> ago" in a listing made at `listed_at`."""
    moment = _utc(listed_at)
    if unit == "year":
        moment = _add_months(moment, -12 * n)
    elif unit == "month":
        moment = _add_months(moment, -n)
    else:
        moment = moment - dt.timedelta(days=n * (7 if unit == "week" else 1))
    # datetime_round(..., "day"): the UTC day nearest the moment, which is the date of the
    # moment twelve hours on. Done by date arithmetic rather than a timestamp, which Windows
    # cannot turn back into a date before 1970.
    return (moment + dt.timedelta(hours=12)).strftime("%Y%m%d")


def listing_age(upload_date, listed_at):
    """(n, unit) when `upload_date` is what yt-dlp makes of "n unit ago" at `listed_at`,
    which is the age the listing itself showed, or None when no such age produces it."""
    if not upload_date or listed_at is None:
        return None
    date = str(upload_date)
    for unit, most in (("year", 60), ("month", 36), ("week", 5), ("day", 7)):
        for n in range(0 if unit == "day" else 1, most):
            if yt_dlp_listing_date(listed_at, n, unit) == date:
                return n, unit
    # "N hours ago" is rounded to the hour, not the day, so it lands on the listing's own
    # UTC date or the one before it.
    try:
        days_back = (_utc(listed_at).date() - dt.datetime.strptime(date, "%Y%m%d").date()).days
    except ValueError:
        return None
    return (0, "day") if days_back in (0, 1) else None


def min_age_months(upload_date, listed_at, exact=False):
    """The fewest whole months old a result can be, or None when its date is unreadable.
    "N years ago" is at least 12 x N months and "N months ago" at least N; an exact date is
    its calendar age at the listing, so exactly two years back is two years old."""
    if not exact:
        age = listing_age(upload_date, listed_at)
        if age:
            n, unit = age
            return 12 * n if unit == "year" else n if unit == "month" else 0
    try:
        date = dt.datetime.strptime(str(upload_date), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None
    on = listed_at.date()
    return (on.year - date.year) * 12 + (on.month - date.month) - (on.day < date.day)


def age_words(age):
    n, unit = age
    if unit == "day" and n == 0:
        return "less than a day ago"
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def cached_upload_date(video_id):
    """An exact upload date from an extraction this tool already fetched, or None."""
    for name in (f"{video_id}.json", f"{video_id}.media.json"):
        path = INFO_CACHE_DIR / name
        if path.exists():
            try:
                found = json.loads(path.read_text(encoding="utf-8")).get("upload_date")
            except (json.JSONDecodeError, OSError):
                continue
            if found:
                return str(found)
    return None


def age_label(record):
    """How old a result is, the way the source said it: "about 8 years ago". An exact date
    is shown as a date only when fetched metadata already holds it; a date a listing
    implied is never printed as though somebody had checked it."""
    exact = cached_upload_date(record.get("id") or "")
    if exact:
        return f"uploaded {format_upload_date(exact)}"
    words = record.get("upload_age")
    if not words and record.get("created"):
        try:
            age = listing_age(record.get("upload_date"),
                              dt.datetime.fromisoformat(record["created"]))
        except ValueError:
            age = None
        words = age_words(age) if age else None
    if words:
        return f"about {words}"
    return f"uploaded {format_upload_date(record.get('upload_date'))}"


def older_than(upload_date_str, days):
    """True only when the date is READABLE and older than `days`. A missing or unparseable
    date is not old, it is unknown, and the caller says so out loud rather than dropping a
    video on a guess or keeping one in silence."""
    if not upload_date_str:
        return False
    try:
        d = dt.datetime.strptime(str(upload_date_str), "%Y%m%d").date()
    except ValueError:
        return False
    return (dt.date.today() - d).days > days


def drop_stale_search_results(videos, days=TOPIC_MAX_AGE_DAYS, label="search", seen=None,
                              now=None):
    """(kept, skipped, undated) for one search's results, before anything is triaged or
    fetched. Every skip is logged with its date, and the count of results whose age could not
    be read is logged too, because that number is the size of the hole in this rule.

    FIX 6, 2026-09-22: a result is skipped when it is at least `days` (two years) old by the
    age the listing gave, so "2 years ago" is skipped; `now` is the moment of the listing,
    injectable so the arithmetic can be checked against a fixed time. And a skipped result
    is recorded only when this tool has not met it before: from this fix a video read on
    2026-09-22 as "2 years ago" would otherwise have its WRITTEN record overwritten the next
    time a search returned it."""
    handles = named_channel_handles()
    now = now or dt.datetime.now().astimezone()
    bound = 12 * (days // 365)
    kept, skipped, undated = [], [], 0
    for v in videos:
        handle = video_handle(v)
        if handle and handle in handles:
            kept.append(v)
            continue
        months = min_age_months(v.get("upload_date"), now)
        if months is not None and months >= bound:
            skipped.append(v)
            continue
        if not v.get("upload_date"):
            undated += 1
        kept.append(v)
    records = load_records()
    for v in skipped:
        age = listing_age(v.get("upload_date"), now)
        words = age_words(age) if age else None
        shown = f"about {words}" if words else f"uploaded {format_upload_date(v.get('upload_date'))}"
        met = v.get("id") in (seen or {}) or (
            v.get("id") in records and records[v["id"]].get("reason_code") != "too_old")
        # FIX 4: recorded, not only logged. Until today an age-filtered result existed in one
        # log line and nowhere else, so the digest could not name it and the header folded it
        # into "skipped" beside videos that had been read and judged.
        if v.get("id") and not met:
            update_record(v["id"], url=v.get("url") or f"https://www.youtube.com/watch?v={v['id']}",
                          title=v.get("title"), channel=v.get("channel"),
                          upload_date=v.get("upload_date"), upload_age=words,
                          summary_state="NOT_READ", reason_code="too_old",
                          state_reason=f"{shown}, so at least {days // 365} years old")
        print(f"    (age filter: skipped {v.get('title', '')[:60]!r}, uploaded "
              f"{v.get('upload_date')}, older than {days // 365} years by the listing's "
              f"'{words or 'date'}'; nothing fetched)")
    if skipped or undated:
        print(f"    (age filter on {label}: {len(skipped)} of {len(videos)} skipped as older "
              f"than {days // 365} years"
              + (f", {undated} carried no date and were kept" if undated else "") + ")")
    return kept, skipped, undated


# --- the topic filter, 2026-09-25 ---------------------------------------------------------
# FIX 3. A cheap judgment of a search result on its title, its channel and the search's own
# snippet, after the age filter and before any model call. A one-word search once returned
# songs that share its name, a playlist, a poem, a movie clip and a brand's
# accessories, the listing triage called every one of them MAYBE, and only the day's cap stood
# between them and a download. Each entry is (what it is, the pattern, the field it reads).
TOPIC_JUNK = (
    ("a song's lyrics", re.compile(r"\blyrics?\b|\blyric\s+video\b", re.I), "title"),
    ("an official music video or audio",
     re.compile(r"\bofficial\s+(?:music\s+)?(?:video|audio)\b|\bvisuali[sz]er\b", re.I), "title"),
    ("a music playlist",
     re.compile(r"\bplaylist\s+for\b|\b(?:music|songs?|indie|folk|pop|lo-?fi|chill|jazz|study|"
                r"workout)\b[\s/-]+(?:[\w'-]+[\s/-]+){0,2}playlist\b", re.I), "title"),
    ("poetry", re.compile(r"\bpoetry\b", re.I), "title"),
    ("a music channel", re.compile(r"\s-\sTopic$|vevo$", re.I), "channel"),
)
NEEDS_RE = re.compile(r"\s*\|\s*needs:\s*", re.I)


def parse_topic_line(line):
    """(query, needed words) from one line of topics.txt. "query | needs: a, b" says that a
    result must carry at least one of the words in its title, channel or snippet."""
    parts = NEEDS_RE.split((line or "").strip(), maxsplit=1)
    needs = tuple(w.strip() for w in parts[1].split(",") if w.strip()) if len(parts) > 1 else ()
    return parts[0].strip(), needs


def needed_word_re(word):
    """A needed word, whole, in any case.

    Until 2026-09-26 a word with a capital in it was matched exactly as written, to keep out a
    brand that spells the same word in other capitals, and that dropped a real video whose title
    capitalised the word its own way. A title's capitals are the uploader's, not a spelling of
    the thing, so every filter matches in any case now. The cost is measured and named in
    tests/test_topics.py."""
    return re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.I)


def topic_result_problem(video, needs=()):
    """Why this search result is off topic, as a plain phrase, or None to keep it."""
    title = one_line(video.get("title") or "")
    channel = one_line(video.get("channel") or "")
    for what, pattern, field in TOPIC_JUNK:
        found = pattern.search(title if field == "title" else channel)
        if found:
            return f"{what}, by its {field} ({found.group(0).strip()!r})"
    if needs:
        text = " ".join((title, channel, one_line(video.get("description") or "")))
        if not any(needed_word_re(w).search(text) for w in needs):
            shown = ", ".join(needs[:3]) + (", ..." if len(needs) > 3 else "")
            return f"none of the words its query needs ({shown})"
    return None


def drop_off_topic(videos, needs=(), label="search", seen=None):
    """(kept, dropped) for one search's results. Every drop is logged with its reason, and a
    result this tool has not met before is recorded, so the digest can name it on a line of
    its own, like the age filter's."""
    kept, dropped = [], []
    records = load_records()
    for v in videos:
        why = topic_result_problem(v, needs)
        if not why:
            kept.append(v)
            continue
        dropped.append(v)
        met = v.get("id") in (seen or {}) or (
            v.get("id") in records and records[v["id"]].get("reason_code") != "off_topic")
        if v.get("id") and not met:
            update_record(v["id"], url=v.get("url") or f"https://www.youtube.com/watch?v={v['id']}",
                          title=v.get("title"), channel=v.get("channel"),
                          upload_date=v.get("upload_date"), source="topics",
                          summary_state="NOT_READ", reason_code="off_topic", state_reason=why)
        print(f"    (topic filter: dropped {(v.get('title') or '')[:60]!r}: {why}; nothing fetched)")
    if dropped:
        print(f"    (topic filter on {label}: {len(dropped)} of {len(videos)} dropped before any "
              f"triage)")
    return kept, dropped


def within_days(upload_date_str, days):
    if days is None or not upload_date_str:
        return True
    try:
        d = dt.datetime.strptime(upload_date_str, "%Y%m%d").date()
    except ValueError:
        return True
    return (dt.date.today() - d).days <= days


def title_obviously_on_topic(title):
    t = title.lower()
    return any(k in t for k in TOPIC_KEYWORDS)


def duration_allows(video):
    d = video.get("duration")
    if d is None:
        return True
    if MIN_VIDEO_SECONDS <= d <= MAX_VIDEO_SECONDS:
        return True
    return title_obviously_on_topic(video.get("title", ""))


def fetch_video_basic_info(video_id):
    """A full extraction for one video, reduced to the triage fields. No longer part of
    the scheduled path (triage reads the listing, and the one extraction a YES/MAYBE
    video gets is the caption fetch's own); kept for hand use and the suites."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    d, err = extract_video_info(url)
    if err:
        return None
    return {
        "id": d.get("id") or video_id, "title": d.get("title") or "",
        "uploader": d.get("uploader") or d.get("channel") or "UNKNOWN",
        "description": (d.get("description") or "")[:1200],
        "duration": d.get("duration"), "upload_date": d.get("upload_date"), "url": url,
    }


TRIAGE_INSTRUCTIONS = """You are triaging one YouTube video for {reader}, the person the CONTEXT below describes, deciding whether it is worth their time, using the CONTEXT below. They are the one building and buying their own time back.

=== CONTEXT ===
{context}
=== END CONTEXT ===

CHANNEL: {channel}
TITLE: {title}
DURATION: {duration}
UPLOADED: {upload_date}
{evidence_block}

{sales_note}

{already_block}

Four verdicts. Give exactly one.

YES: the video teaches a MECHANISM for a live project, all named in CONTEXT's "Projects and status today": {project_list}. Or a mechanism that changes how they work, or this tool (the digest you are triaging for). A mechanism is something that could be followed directly: a specific order of operations, a specific channel to go through, a way to structure a test or a judging setup, a prompt pattern, a price or a number to aim at. It does not have to name their stack or any of their projects: a build, test, launch, selling or distribution move transfers even when the tool it was shown on does not.

  CONTEXT defines a YES as a mechanism they can use within two weeks on a live project, and
  that definition is theirs. YOUR JOB HERE IS TO FIND THE MECHANISM, NOT TO JUDGE WHETHER THEY
  CAN PULL IT OFF. Never
  answer MAYBE or NO because of something about their situation: their age, their money, their
  time, their access, their customers, their permission, what industry they are in or what
  they would have to set up first. Those are their calls and not yours, and a mechanism they
  cannot use this fortnight is still worth reading about. If you can name one thing a person
  could copy from this video, that is YES, and your reason names the thing rather than the
  obstacle. The two-week bound in CONTEXT is applied later, by the summary, which has to name
  the mechanism AND the one project it changes or the whole finding is deleted.

OPPORTUNITY: something a person could sell, judged on its own terms: a BUYER THAT IS A COMPANY OR A DEVELOPER WHO ALREADY PAYS (never consumers), A PRICE, DEAL SIZE OR REVENUE FIGURE, marked as shown or only said, and a concrete first step toward a first customer, that is not already in their files (CONTEXT's "What's been tried", the ideas they killed, and the ALREADY IN MY FILES list above). A new market, a new shape of business, a new kind of customer. It counts even when it is far from anything they do today; the point of this verdict is the road they have not taken. Your reason must name who pays AND a number, or it is not OPPORTUNITY.

  NEVER OPPORTUNITY, however large the number in it: a story about a big company, a merger,
  an acquisition, a funding round, a hiring war, a stock move, or an industry trend. A
  paradigm shift, a "new era", a market forecast and a company's strategy are not ways for
  one person to get paid. If the money in the video is a company's money rather than
  somebody's fee, price, salary or payout for work they did, the verdict is MAYBE or NO.
  Being interesting is not the test; a buyer who already pays is.

MAYBE: possibly YES or OPPORTUNITY, but what you have been given is too thin to tell. A missing description is a reason for MAYBE, never for NO.

NO: a sales pitch with no mechanism in it, a reaction or news video with nothing to follow, hype about a model or a product with no move in it, or a domain with nothing usable. When in doubt between NO and MAYBE, choose MAYBE.

KEEP RULE: a video that contains or implies a business, product or project idea, gives a revenue, price or payout figure, or tells a founder's story is never NO, even when it fits no live project: it is MAYBE at least. This rule only ever lifts a NO to MAYBE; it is never a reason for YES or OPPORTUNITY.

Read YES generously. A tutorial that shows how to install, structure, test, share or distribute a tool the CONTEXT says they use is a mechanism that changes how they work: that is YES, never "generic". A comparison or an experiment is YES when it shows a judging or testing setup that could be copied: blind judging with the labels stripped, a written definition of done, a "done" call that has to carry a reason and an output file, a cost-normalised comparison of two runs. "How we got our first 1,000 GitHub stars", "the launch post that hit #1 on Hacker News", "how I sold to my first ten customers", "how I got paid through Stripe instead of invoices" are all YES or OPPORTUNITY if the video says what to do. Being about a different tool, stack, market or product than theirs disqualifies nothing.

NO is NEVER the verdict for a video about the subjects in the CONTEXT, selling, or getting paid that merely sounds generic: that is YES or OPPORTUNITY if it carries a repeatable move and MAYBE if it might. A channel selling a course is not a reason for NO either: judge the mechanism, not the seller. Hype is a video with no move in it at all.

{stage_note}

Your reason must justify the verdict you gave: no YES with a reason that says the video is vague, no NO with a reason that concedes it teaches a mechanism, no OPPORTUNITY without naming what people are paid for.

Reply with exactly three lines and nothing else. First name the one concrete thing a person could copy from this video; then the verdict, which must agree with it: something to copy is YES or OPPORTUNITY, "unknown" is MAYBE, "none" is NO; then how sure you are.
COPY: <the one thing a person could copy, under 15 words; "unknown" if what you were given cannot tell you; "none" only if it shows there is nothing>
VERDICT: YES|OPPORTUNITY|MAYBE|NO - <reason, under 20 words>
CONFIDENCE: <a whole number from 0 to 100: how sure you are of that verdict, given only the evidence above. 90 or more means the evidence settles it. 50 means you could as easily have said something else. Under 30 means you are guessing. A missing description or an empty transcript is a reason for a LOW number, never a high one.>"""

TRIAGE_STAGE_LISTING = (
    "You are judging from the listing alone; the transcript has not been fetched, so COPY is "
    "what the title and description promise, and \"unknown\" is the honest answer whenever "
    "the description is missing or says nothing - that is MAYBE, never NO. YES and "
    "OPPORTUNITY mean it is worth fetching and reading now; MAYBE is fetched when there is "
    "budget left; NO is never fetched, so give NO only when the listing itself settles it. A "
    "title that is the whole story settles it: hype (\"X is AGI\"), a launch reaction, a news "
    "round-up (\"AI news in 5 mins\"), a verdict with no method (\"X kills Y\") - those are NO "
    "even with no description. A title that names a topic, a tool, a comparison, a build or a "
    "how-to does not settle anything, and is MAYBE until read.")
TRIAGE_STAGE_TRANSCRIPT = (
    "You have the transcript now, so a thin description is no longer a reason for MAYBE: give "
    "MAYBE only if the transcript itself leaves it unclear. The transcript is the evidence, "
    "not the title. Count what it shows: an install path, a command, a workflow, a judging "
    "setup, a prompt pattern, a price or a payout. If you can name one thing a person could "
    "copy from it, the verdict is YES or OPPORTUNITY, and your reason names that thing. NO "
    "is only for a transcript with nothing to copy at all.")

SALES_NOTE_KNOWN = ("This channel sells something (a course, coaching, a community, or similar). "
                     "A video being persuasive, well-produced, or from someone credible is NOT by itself a reason to say YES, "
                     "and the channel selling something is NOT a reason to say NO: judge the mechanism.")
SALES_NOTE_UNKNOWN = ("This video may or may not be selling something (a course, coaching, a community, an affiliate link). "
                       "If it is, being persuasive or well-produced is NOT by itself a reason to say YES, "
                       "and selling something is NOT a reason to say NO: judge the mechanism.")


def already_in_my_files():
    """The ways of getting paid OPPORTUNITIES.md already records, for the triage prompt: a
    video that only repeats one of them is not a new road."""
    rows = opportunities_recorded()
    if not rows:
        return ("ALREADY IN MY FILES: nothing recorded yet beyond what CONTEXT says has been "
                "tried.")
    recent = rows[-ALREADY_IN_FILES_LIMIT:]
    lines = [f"- {r['works']} (for {r['for']}, {r['date']})" for r in recent]
    return ("ALREADY IN MY FILES (ways of getting paid this tool has already recorded; a video "
            "that only repeats one of these is not an OPPORTUNITY, though it may still be YES "
            "or MAYBE):\n" + "\n".join(lines))


def triage_excerpt(transcript, limit=None):
    """The transcript as the triage sees it: whole when it fits, else the head and the
    tail, because a video's pitch is at the front and its payoff at the back."""
    limit = TRIAGE_TRANSCRIPT_CHARS if limit is None else limit
    body = (transcript or "").strip()
    if len(body) <= limit:
        return body
    head = int(limit * 2 / 3)
    tail = limit - head
    return body[:head] + "\n[... middle of the transcript cut for length ...]\n" + body[-tail:]


TRIAGE_MIN_TIMEOUT = 30    # seconds a triage may always have, even at the run's deadline


def triage_prompt(context_text, channel_name, details, sells_something=None):
    """The triage prompt for one video, from its listing or, when `details` carries one, from
    its transcript. The quick brain answers it in triage_video; an agent is handed it by the
    MCP server's judge_video, so both judge by the same rules."""
    duration = details.get("duration")
    duration_str = f"{int(duration) // 60}m{int(duration) % 60:02d}s" if duration else "UNKNOWN"
    sales_note = SALES_NOTE_KNOWN if sells_something else SALES_NOTE_UNKNOWN
    transcript = details.get("transcript")
    if transcript:
        evidence_block = ("=== TRANSCRIPT (machine transcription: names may be misspelt) ===\n"
                          f"{triage_excerpt(transcript)}\n=== END TRANSCRIPT ===")
        # The listing's verdict is deliberately NOT in this prompt: given it, the model
        # anchored on it (the same transcript read YES under a MAYBE prior and NO under a NO
        # prior, 2026-09-07). The code keeps it for the digest; the model gets the evidence.
        stage_note = TRIAGE_STAGE_TRANSCRIPT
    else:
        evidence_block = (f"DESCRIPTION: {(details.get('description') or 'UNKNOWN')[:800]}\n\n"
                          "(The description is whatever the channel listing or feed exposes: "
                          "often the first sentence or two, sometimes nothing. Judge from what "
                          "is there.)")
        stage_note = TRIAGE_STAGE_LISTING
    return TRIAGE_INSTRUCTIONS.format(
        reader=READER, project_list=project_list_words(),
        context=context_for_triage(context_text), channel=channel_name, title=details["title"],
        duration=duration_str, upload_date=details.get("upload_date") or "UNKNOWN",
        evidence_block=evidence_block, sales_note=sales_note,
        already_block=already_in_my_files(), stage_note=stage_note,
    )


def triage_video(context_text, channel_name, details, model, sells_something=None):
    """One verdict from the quick brain: (verdict, reason, confidence).

    The third value arrived 2026-09-20 and the shape of the return changed with it rather
    than the number being smuggled inside the reason string. A caller that unpacks two values
    now fails loudly at the moment of the change, which is the point: a confidence nothing
    reads is worse than none, and a test stub still returning a pair would hide that the
    production path had stopped agreeing with it. confidence is None when the model gave no
    parseable number, and None is printed and stored as unknown, never as a middling 50.

    Called at two moments. From the listing - title, description, duration, date - which
    costs no request and decides what is fetched at all. And, since 2026-09-07, again from
    the transcript once it is on disk: `details` then carries "transcript", plus the
    listing's verdict and reason as "listing_verdict" and "listing_reason". That second
    call is the one that can move a verdict, because many listings carry no description at
    all, and a title like "I had five models build me the same app" says nothing about the
    blind judging setup inside it. One prompt serves both moments, so the rubric cannot
    drift between them."""
    timeout = 600 if details.get("transcript") else 120
    # 2026-09-26 night, brief part 2.1: a hung model waits no longer than the run has left,
    # with a floor so a call near the deadline can still answer. A 600 s transcript triage used
    # to run on past the deadline that every summary respects.
    left = run_seconds_left()
    if left is not None:
        timeout = max(TRIAGE_MIN_TIMEOUT, min(timeout, left))
    prompt = triage_prompt(context_text, channel_name, details, sells_something)
    # num_predict caps worst-case generation length for what should be a two-line
    # answer: a model that fails to stop naturally should hit this, not run away.
    response = quick_generate(prompt, model=model, num_ctx=NUM_CTX, timeout=timeout,
                              num_predict=200, temperature=0)
    confidence = parse_confidence(response)
    m = re.search(r"VERDICT:\s*(YES|OPPORTUNITY|MAYBE|NO)\b\s*-\s*(.+)", response,
                  re.IGNORECASE | re.DOTALL)
    if not m:
        # An unparsed answer is a MAYBE at zero confidence, not at whatever number happened
        # to be in the text: nothing about that response settled anything.
        return "MAYBE", f"(unparsed triage response: {response.strip()[:120]})", 0
    verdict, reason = m.group(1).upper(), m.group(2).strip().splitlines()[0]
    # The COPY line is the model's own answer to "what would a person copy"; asking for it
    # first is what made the transcript stage find the mechanism instead of dismissing the
    # video as generic (measured 2026-09-07, see patch notes in EXPERIMENT-2026-09-07.md).
    # It rides along in the reason so the digest says what to copy, not only that there is
    # something.
    c = re.search(r"COPY:\s*(.+)", response, re.IGNORECASE)
    copy = c.group(1).strip().splitlines()[0] if c else ""
    copy = re.split(r"\s*VERDICT:", copy, maxsplit=1)[0].strip().strip('"')
    if copy and not is_nothing_useful(copy) and copy.lower() != "unknown" \
            and verdict in ("YES", "OPPORTUNITY"):
        reason = f"{reason.rstrip('.')} [copy: {copy.rstrip('.')}]"
    # 2026-10-08 night, the keep rule (ideas_keep_reason): a NO whose words carry a money figure
    # or a founder's story is a MAYBE, so it is fetched and gets a write-up with its ideas.
    if verdict == "NO":
        keep = ideas_keep_reason(details)
        if keep:
            verdict, reason = "MAYBE", f"{reason.rstrip('.')} [kept by the ideas rule: {keep}]"
    return verdict, reason, confidence


CONFIDENCE_RE = re.compile(r"CONFIDENCE:\s*\**\s*(\d{1,3})", re.IGNORECASE)


def parse_confidence(response):
    """The 0 to 100 the triage was asked for, or None if it gave none.

    None rather than a default, because a default is a number the model never said and
    everything downstream would read it as one it did. A number outside 0 to 100 is clamped
    rather than dropped: 110 is a model being emphatic, not a model refusing to answer."""
    m = CONFIDENCE_RE.search(response or "")
    if not m:
        return None
    return max(0, min(100, int(m.group(1))))


def confidence_cell(value):
    """How a confidence reads in a table or a log line."""
    return "unknown" if value is None else f"{value}"


# title_names_mechanism() lived here until 2026-09-10. It asked the local model whether a
# title named a mechanism and routed a MAYBE on the answer: Sonnet if it did, the local
# model if it did not. It is gone because the branch it fed is gone (see choose_backend),
# and a routing question nothing routes on is a trap waiting for someone to trust it.


def choose_backend(verdict, title, meta, local_only, model):
    """(backend, why) for a fetched video: "claude" or "local".

    Every verdict that gets summarised gets the writing brain ("claude" here). A small local
    model was measured writing the judgment sections badly: across its write-ups, not one
    "What applies to me" item survived the question a stranger would ask (would this quote
    mean anything to someone told only the project and the quote?), and run over the same
    transcripts beside a large model, it invented an item eleven times out of fifteen where
    the large model rightly said "nothing" every time. Prompting harder did not help.

    So the local model keeps the jobs it is good at - both triage passes, the one-line "most
    useful" on a video read as NO, compressing section 8 to one sentence - and stops writing
    the judgment.

    --local-only still keeps everything local except a technical video (TECHNICAL_KEYWORDS),
    which a small local model was measured to mangle; what a local summary may publish is
    settled in local_judgment_unavailable(), not here.

    A priority channel's video (FAVOURITE_CHANNELS) always gets the writing brain, whatever
    the verdict and under --local-only too."""
    if (meta or {}).get("favourite"):
        return "claude", "a priority channel always gets the full write-up"
    if local_only:
        if is_technical_video(meta):
            return "claude", "--local-only, but a technical video always goes to the writing brain"
        return "local", "--local-only"
    if verdict in ("YES", "OPPORTUNITY", "MAYBE"):
        return "claude", (f"{verdict} gets the writing brain; the quick brain does not write "
                          f"judgment")
    return "local", verdict


MOST_USEFUL_INSTRUCTIONS = """In one sentence under 25 words, what is the single most useful concrete thing this video said for {reader}, given the CONTEXT? If nothing, reply with the single word "nothing".

=== CONTEXT ===
{context}
=== END CONTEXT ===

VIDEO: {title}

=== TRANSCRIPT ===
{transcript}
=== END TRANSCRIPT ===

Reply with just that one sentence, nothing else."""


def local_most_useful(context_text, meta, model):
    """The second line of a NO's two-line entry, for a video that was fetched and then read
    as NO: one sentence from the local model, or None if it cannot be asked."""
    try:
        response = quick_generate(MOST_USEFUL_INSTRUCTIONS.format(
            reader=READER, context=context_text, title=meta["title"],
            transcript=triage_excerpt(meta.get("transcript"))),
            model=model, num_ctx=NUM_CTX, timeout=300, num_predict=80)
    except Exception as e:  # noqa: BLE001 - a missing second line is not a failed video
        print(f"    (could not ask the local model for the most useful line: {e.__class__.__name__})")
        return None
    line = normalize_punct(response.strip().splitlines()[0].strip()) if response.strip() else ""
    return None if not line or is_nothing_useful(line) else line


def triage_details_from_listing(v):
    vid = v["id"]
    return {
        "channel": v.get("channel") or "UNKNOWN", "title": v.get("title") or "",
        "url": v.get("url") or f"https://www.youtube.com/watch?v={vid}",
        "description": v.get("description") or "", "duration": v.get("duration"),
        "upload_date": v.get("upload_date"),
    }


def pending_due(seen):
    """Candidates rebuilt from seen.json's pending entries that may be tried today."""
    today = today_key()
    due = []
    for vid, e in seen.items():
        if e.get("verdict") != "pending":
            continue
        if (e.get("retry_after") or "") > today:
            continue
        candidate = {"id": vid, "title": e.get("title") or "",
                     "channel": e.get("channel") or "UNKNOWN",
                     "url": e.get("url") or f"https://www.youtube.com/watch?v={vid}",
                     "duration": None, "upload_date": None}
        # 2026-10-05: who the video belongs to rides along, for the favourites and the cap.
        for key in ("favourite", "channel_key"):
            if e.get(key):
                candidate[key] = e[key]
        due.append(candidate)
    return due


def mark_pending(seen, vid, details, verdict, reason, retry_count, note, retry_after=None,
                  confidence=None):
    # pending_confidence joined this entry on 2026-09-20. An entry written before then has no
    # such key and .get() reads it as None, which is what "no number" means anyway; nothing
    # about the meaning of "pending" changed, and seen.json is read exactly as before.
    entry = {"date": today_key(), "verdict": "pending", "title": details["title"],
             "channel": details["channel"], "url": details["url"], "pending_verdict": verdict,
             "pending_reason": reason, "retry_count": retry_count, "note": note,
             "pending_confidence": confidence}
    if retry_after:
        entry["retry_after"] = retry_after
    # 2026-10-05: kept on the queue entry, so the run that tries it again knows the video is a
    # favourite channel's and which channel's day it counts against.
    for key in ("favourite", "channel_key"):
        if details.get(key):
            entry[key] = details[key]
    seen[vid] = entry
    save_seen(seen)


def summarise_one(context_text, meta, model, local_only, verdict, attempt=1):
    """One summary, with everything that has to happen around it, in one place.

    Choose the backend, call it, finalize, then rules 1 and 2. Both the fetch path and the
    retry path come through here, which is what keeps the call-site count in
    tests/test_citations2.py meaningful: a second copy of this chain is exactly how a guard
    comes to run on one path and not on the other.

    Returns (summary, backend_label, backend_why, elapsed, chunked, settled, gate_notes)."""
    backend, backend_why = choose_backend(verdict, meta.get("title"), meta, local_only, model)
    t0 = time.monotonic()
    if backend == "claude":
        # FIX 3. The fit is Sonnet's, measured on Sonnet summaries, so it is applied to
        # Sonnet only; the local path keeps the 900 s it has always had, because nothing
        # here has measured it.
        timeout, why = summary_timeout_for(meta.get("minutes"), attempt=attempt)
        print(f"    summary timeout: {timeout:.0f}s ({why})")
        summary, chunked = summarize_claude(context_text, meta, meta["transcript"],
                                            timeout=timeout)
        backend_label = write_label()
    else:
        ensure_local_model(model)
        summary, chunked = summarize_local(model, context_text, meta, meta["transcript"])
        backend_label = quick_label(model)
    elapsed = time.monotonic() - t0
    print(f"    -> summarized ({backend_label}, {elapsed:.1f}s; {backend_why})")

    def ask_again(note, _b=backend, _m=meta):
        """One more attempt at this summary, on the same backend, with the defect named.
        finalize_summary decides whether it is needed and calls it at most once."""
        if _b == "claude":
            return summarize_claude(context_text, _m, _m["transcript"], extra=note)[0]
        return summarize_local(model, context_text, _m, _m["transcript"], extra=note)[0]

    summary = finalize_summary(summary, meta["transcript"], model, verdict=verdict,
                               regenerate=ask_again, meta=meta, backend=backend_label)
    # Rules 1 and 2, after the summary is finished and before anything records the verdict.
    # Here rather than earlier because both read the finished summary: whether section 6
    # survived anything, and whether the Opportunity line survived at all.
    settled, summary, gate_notes = settle_verdict(verdict, summary)
    return summary, backend_label, backend_why, elapsed, chunked, settled, gate_notes


def writeup_already_in_history(record):
    """The row history.jsonl already holds with this video's write-up since the day it was
    read, or None. A run that lost the power after appending it and before recording it must
    not pay for it a second time (brief part 2.10)."""
    url, since = record.get("url"), record.get("read_day") or record.get("day")
    if not url or not since:
        return None
    try:
        start = dt.date.fromisoformat(since)
    except ValueError:
        return None
    found = None
    for row in load_history_since(start):
        if row.get("url") == url and row.get("summary"):
            found = row
    return found


def judge_saved_transcript(context_text, model, record, text):
    """(verdict, reason, confidence) for a transcript that was read while the local model did
    not answer, judged now from the copy on disk; or None when it still does not answer, which
    leaves the record waiting as it was (2026-09-26 night, brief part 2.1)."""
    vid = record["id"]
    details = {"title": record.get("title") or vid, "channel": record.get("channel") or "UNKNOWN",
               "url": record.get("url") or f"https://www.youtube.com/watch?v={vid}",
               "duration": None, "upload_date": record.get("upload_date"), "transcript": text}
    try:
        verdict, reason, confidence = triage_video(context_text, details["channel"], details,
                                                   model)
    except Exception as e:  # noqa: BLE001 - one video's failure, whatever raised it
        why = one_line(str(e) if isinstance(e, RuntimeError)
                       else f"{e.__class__.__name__}: {e}")[:200]
        print(f"    (still not judged: {why}; it waits in the queue, and no attempt is used up)")
        update_record(vid, state_reason=f"not judged yet: {why}")
        return None
    print(f"    judged from the saved transcript: {verdict} ({confidence_cell(confidence)}): "
          f"{reason}")
    update_record(vid, read_verdict=verdict, read_reason=reason, read_confidence=confidence)
    return verdict, reason, confidence


def run_retry_phase(context_text, model, local_only, limit=RETRY_MAX_PER_RUN):
    """The summaries that failed, tried again from the transcript already on disk.

    FIX 2. Summary only, oldest first, at most three a run, and not one YouTube request:
    nothing here fetches, nothing here counts against the audio cap or the day's budget,
    and tests/test_retry.py proves it with a spy on run_yt_dlp rather than by saying so.

    A video whose saved transcript has gone goes back to the fetch queue once, as a WAITING
    record marked refetch, and never again: a retry that cannot find its transcript twice is
    a loop, not a retry."""
    queue = retry_queue()[:limit]
    if not queue:
        return [], []
    print(f"\nRetrying {len(queue)} summary(ies) from transcripts already on disk "
          f"(no YouTube request, no budget spent):")
    rows, summaries = [], []
    first_read_days = set()
    changed = 0
    for record in queue:
        vid = record["id"]
        attempt = int(record.get("attempts") or 0) + 1
        title = record.get("title") or vid
        # 2026-09-26 night, brief part 2.10: a run that lost the power after the write-up
        # reached history.jsonl and before its record said so left it here; it is recorded as
        # it is, with no model call and no second row in history.jsonl.
        already = writeup_already_in_history(record)
        if already:
            written = summary_written(vid, already["summary"], already.get("verdict"),
                                      attempt=attempt, seconds=None)
            print(f"  [{title}] already written up on {already.get('date')} by a run that "
                  f"stopped before recording it; recorded now, not paid for again")
            if written.get("read_day"):
                first_read_days.add(written["read_day"])
            changed += 1
            continue
        text, tmeta = saved_transcript(vid, record)
        # 2026-09-26 night, brief part 2.3: a saved transcript that is garbled (a file that is
        # not text any more, or one phrase looping) is as good as gone.
        garbled = garbled_text(text, loops=False) if text is not None else None
        if text is None or garbled:
            what = "gone" if text is None else "garbled"
            if record.get("refetch"):
                update_record(vid, terminal=True,
                              state_reason=f"the saved transcript is {what} and it has already "
                                           f"been queued for one more fetch")
                print(f"  [{title}] transcript on disk {what}, and already queued once; "
                      f"leaving it")
                continue
            update_record(vid, summary_state="WAITING", reason_code="rolled", refetch=True,
                          state_reason=f"the saved transcript is {what}; queued for one more "
                                       f"fetch")
            # 2026-09-26 night: and queued for real. seen.json still said the video was done,
            # so no run ever fetched it again; pending, the next channels run fetches it once.
            seen = load_seen()
            mark_pending(seen, vid, {"title": title, "channel": record.get("channel") or "UNKNOWN",
                                     "url": record.get("url")
                                     or f"https://www.youtube.com/watch?v={vid}"},
                         record.get("read_verdict") or record.get("listing_verdict") or "MAYBE",
                         record.get("read_reason") or "", 0,
                         f"the saved transcript is {what}; one more fetch")
            print(f"  [{title}] transcript on disk {what}; back in the fetch queue once")
            continue
        # FIX 4, 2026-09-22: the same question the fetch path now asks before a triage: a
        # transcript with no speech in it is not summarised on a retry either.
        measured = None if tmeta.get("minutes_estimated") else tmeta.get("minutes")
        kind, spoken = speech_check(text, measured)
        if kind:
            quiet = {"no_speech": kind, "words": spoken, "minutes": measured}
            why = no_speech_text(quiet)
            silenced = update_record(vid, summary_state="NOT_READ", reason_code="no_speech",
                                     state_reason=why, terminal=True, **quiet)
            if silenced.get("read_day"):
                first_read_days.add(silenced["read_day"])
            changed += 1
            print(f"  [{title}] {why}: not read, and not retried")
            continue
        verdict = record.get("read_verdict") or record.get("listing_verdict") or "MAYBE"
        meta = {"id": vid, "title": title, "uploader": record.get("channel") or "UNKNOWN",
                "channel": record.get("channel") or "UNKNOWN",
                "url": record.get("url") or f"https://www.youtube.com/watch?v={vid}",
                "transcript": text, "raw_path": RAW_DIR / f"{vid}.txt", "lang": "en",
                "track_kind": "on disk", "verdict": verdict,
                "verdict_reason": record.get("read_reason") or "",
                "minutes": (tmeta or {}).get("minutes"),
                "favourite": bool(record.get("favourite"))}    # 2026-10-05: always Sonnet
        # FIX 3: the line says how long the audio was and where that number came from,
        # because the timeout is sized from it and 2026-09-22's was an estimate nobody saw.
        print(f"  [retry {attempt}/{SUMMARY_MAX_ATTEMPTS}] {title} "
              f"({(tmeta or {}).get('words', 0)} words on disk, "
              f"{(tmeta or {}).get('minutes')} min of audio from "
              f"{(tmeta or {}).get('minutes_from')}, "
              + (f"cached verdict {verdict})" if record.get("read_verdict") else "not judged yet)"))
        may, why_not = may_start_a_summary()
        if not may:
            print(f"    (not this run: {why_not})")
            break
        if not record.get("read_verdict"):
            # 2026-09-26 night, brief part 2.1: read while the local model did not answer, so
            # never judged. Judged now from the transcript on disk, before any write-up.
            judged = judge_saved_transcript(context_text, model, record, text)
            if judged is None:
                continue          # still no answer: it waits, and no attempt is used up
            verdict, reason, _confidence = judged
            meta["verdict"], meta["verdict_reason"] = verdict, reason
            record = load_records().get(vid) or record
            if verdict == "NO" and not meta["favourite"]:   # 2026-10-05: a favourite is written
                dropped = update_record(vid, summary_state="READ_NOT_WRITTEN",
                                        reason_code="read_then_dropped",
                                        state_reason="read, then screened out; no summary spent on it")
                append_history("NO", meta["channel"], title, meta["url"],
                               f"{reason} [read, then screened out; not summarised]",
                               source=record.get("source"), confidence=_confidence)
                if dropped.get("read_day"):
                    first_read_days.add(dropped["read_day"])
                changed += 1
                continue
        # 2026-09-30, the opportunity lane: a write-up done on a retry is judged by it too. The
        # screen the fetch path asked is reused; a record read before the lane is screened now,
        # from the transcript on disk.
        screen = None
        if lane_on():
            screen = record.get("opp_screen")
            if not screen:
                screen = screen_opportunity(meta, model)
                meta["triage_verdict"] = record.get("triage_verdict") or verdict
                verdict, _routed_reason = route_for_opportunity(verdict, meta["verdict_reason"],
                                                                screen)
                meta["verdict"] = verdict
            else:
                meta["triage_verdict"] = record.get("triage_verdict") or verdict
        try:
            (summary, backend_label, _why, elapsed, chunked, settled,
             gate_notes) = summarise_one(context_text, meta, model, local_only, verdict,
                                          attempt=attempt)
        except Exception as e:  # noqa: BLE001 - brief part 2.4: any failure is this write-up's
            e = e if isinstance(e, RuntimeError) else RuntimeError(f"{e.__class__.__name__}: {e}")
            print(f"    ERROR summarizing: {e}")
            failed = summary_failed(vid, e, attempts=attempt)
            state = final_verdict(failed)
            print(f"    -> {state[0]}: {state[1]}")
            continue
        lane_fields = None
        if screen is not None:
            summary, settled, gate_notes, lane_fields = lane_written(
                vid, context_text, meta, verdict, screen, summary, settled, gate_notes)
            lane_fields["read_verdict"] = verdict
        # 2026-09-26 night, brief part 3: the label's daily cap, as on the fetch path.
        cap = label_cap_for(vid, section6_state(summary)[1], settled)
        if cap:
            gate_notes = list(gate_notes) + [f"demoted from YES: {cap}"]
        shown = "MAYBE" if cap else settled
        if lane_fields is not None:
            shown, gate_notes = lane_display(vid, summary, settled, cap, lane_fields, shown,
                                             gate_notes)
        for note in gate_notes:
            print(f"    ({note})")
        reason = record.get("read_reason") or record.get("listing_reason") or ""
        history_reason = f"{reason} [written on retry {attempt} of {SUMMARY_MAX_ATTEMPTS}]"
        if gate_notes:
            history_reason = f"{history_reason} [{'; '.join(gate_notes)}]"
        meta["verdict"] = shown
        append_history(shown, meta["channel"], title, meta["url"], history_reason,
                       summary=summary, backend=backend_label,
                       try_this_week=extract_try_this_week(summary),
                       source=record.get("source"), confidence=record.get("read_confidence"))
        save_writeup(meta, summary, backend_label, elapsed, chunked)
        if lane_fields is not None:
            if card_surfaces(lane_fields["opp_card"]) and not lane_fields["opp_cap"]:
                append_opportunity_card(meta, lane_fields["opp_card"])
        elif settled == "OPPORTUNITY":
            append_opportunity(meta, summary)
        written = summary_written(vid, summary, settled, attempt=attempt, seconds=elapsed,
                                  **(lane_fields or {}))
        _THIS_RUN["retry_written"].append(vid)
        if written.get("read_day"):
            first_read_days.add(written["read_day"])
        rows.append({"verdict": shown, "channel": meta["channel"], "title": title,
                     "reason": history_reason, "url": meta["url"], "id": vid,
                     "listing_verdict": record.get("listing_verdict"),
                     "confidence": record.get("read_confidence")})
        summaries.append({"verdict": shown, "meta": meta, "summary": summary,
                          "backend": backend_label, "backend_why": "retry from the saved transcript",
                          "listing_verdict": record.get("listing_verdict"),
                          "confidence": record.get("read_confidence"),
                          "elapsed": elapsed, "chunked": chunked})
    # FIX 1, 2026-09-22. The write-ups go into today's digest here, in its "Written on
    # retry" section, rather than being handed back to a caller: main() never used what
    # this returned, and a retry's write-up once reached no digest at all. Then
    # the day each video was first read, so its row there stops saying NOT WRITTEN. A past
    # day whose digest was never written is left alone rather than invented.
    if summaries or changed:
        refresh_digest(today_key(), create=True)
        for day in sorted(first_read_days - {today_key()}):
            refresh_digest(day)
    return rows, summaries


# --- a fetch that fails ------------------------------------------------------------------------
# A fetch that fails for a reason that passes (a transcriber out of GPU memory, a network that
# dropped, a disk with no room) is not the video's fault. It is tried again by the next run, at
# most FETCH_MAX_TRIES times in all; a video that YouTube no longer serves is the video's own
# failure and is not.
FETCH_MAX_TRIES = 3
# A download is at most 150 MiB (AUDIO_DOWNLOAD_MAX_BYTES), plus its 16 kHz re-encode and a
# transcript. 2 GiB leaves room for all of it and for the rest of the machine.
DISK_MIN_FREE_BYTES = 2 * 1024 ** 3
# Two transcriber failures in a row in one run means the GPU is not coming back in this run,
# and every further download would be two requests and tens of megabytes for nothing.
TRANSCRIBER_FAILURES_BEFORE_STOP = 2
# Which failure a fetch's error is, first match wins. "Try again later" and the bot check come
# before the removed video, because YouTube words a throttled address as "Video unavailable.
# This content isn't available, try again later", and that video is not gone.
FAILURE_PATTERNS = (
    ("disk", re.compile(r"no space left on device|not enough space on the disk|\[errno 28\]"
                        r"|errno 28\b|winerror 112\b", re.I)),
    ("refused", re.compile(r"try again later|confirm you.?re not a bot|too many requests", re.I)),
    ("removed", re.compile(r"video unavailable|this video is (?:no longer )?(?:unavailable|private"
                           r"|not available)|private video|has been removed|no longer available"
                           r"|account associated with this video has been terminated"
                           r"|removed for violating", re.I)),
    ("account", re.compile(r"members[- ]only|join this channel|confirm your age|age[- ]restricted"
                           r"|inappropriate for some users", re.I)),
    ("transcriber", re.compile(r"local transcription failed|\bcuda\b|out of memory|cublas|cudnn",
                               re.I)),
    # Every format a fetch tried brought nothing back (DOWNLOAD_FAILED_ERROR), or an older
    # sentence for the same thing.
    ("download", re.compile(r"no audio came back|listed \([^)]*\) but nothing was downloaded",
                            re.I)),
    ("network", re.compile(r"getaddrinfo failed|failed to resolve|name resolution"
                           r"|name or service not known|no address associated"
                           r"|network is unreachable|no route to host"
                           r"|connection (?:reset|refused|aborted|timed out)|timed out"
                           r"|remote ?disconnected|remote end closed|incompleteread"
                           r"|unable to download (?:webpage|api page|video data|m3u8)"
                           r"|urlopen error|transporterror|ssl: |errno (?:11001|10054|10060"
                           r"|10061|10065)|winerror (?:10054|10060|10061|10065)"
                           r"|http error 5\d\d|temporary failure", re.I)),
)
TRANSIENT_KINDS = ("disk", "refused", "transcriber", "network", "download")
# After one of these, nothing more is fetched in the run: the disk will not empty itself, and a
# second request into a refusal makes the refusal worse.
STOP_AT_ONCE_KINDS = ("disk", "refused")
# The run section's sentence for each; {words} is fetch_failure_words, capitalised.
FAILURE_NOTES = {
    "network": "{words} while fetching; every video it failed is queued in seen.json and the "
               "next run tries it again, at most 3 tries in all.",
    "transcriber": "{words} while reading audio; every video it failed is queued in seen.json "
                   "and the next run tries it again, at most 3 tries in all.",
    "disk": "{words} while a video was being saved, so nothing more was downloaded; every "
            "video not read is queued in seen.json for the next run.",
    "refused": "{words}, so nothing more was fetched; every video not read is queued in "
               "seen.json for the next run.",
    "download": "YouTube sent no audio for a video in any format the tool tried; every video this "
                "happened to is queued in seen.json and the next run tries it again, at most 3 "
                "tries in all.",
}


def fetch_failure_kind(text):
    """Which failure a fetch's error is (FAILURE_PATTERNS), or None for one that says nothing
    anybody recognises, which is treated as the video's own."""
    for kind, pattern in FAILURE_PATTERNS:
        if pattern.search(text or ""):
            return kind
    return None


def fetch_failure_words(kind, text):
    """The failure in the words a row uses."""
    if kind == "transcriber":
        return ("the transcriber ran out of GPU memory" if re.search(r"out of memory", text or "",
                                                                     re.I)
                else "the transcriber failed")
    if kind == "disk" and not FAILURE_PATTERNS[0][1].search(text or ""):
        # A write that failed for some other reason than room, said as what it was.
        return f"the transcript could not be saved ({one_line(text or '')[:80]})"
    return {"network": "the network failed", "disk": "the disk had no room left",
            "refused": "YouTube asked this address to try again later",
            "download": "YouTube sent no audio for it"}.get(kind, "it failed")


def disk_free_bytes(path=None):
    """Free bytes on the drive the audio is downloaded to."""
    p = Path(path or AUDIO_DIR)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def queue_left_for_next_run(items, seen, why):
    """The candidates a run stopped short of, kept: pending in seen.json with the verdict they
    already have, so the next channels run fetches them whether or not a listing returns them,
    and waiting on the books with the reason."""
    for v, verdict, reason, retry_count, details, confidence in items:
        mark_pending(seen, v["id"], details, verdict, reason, retry_count,
                     f"not fetched: {why}", confidence=confidence)
        update_record(v["id"], summary_state="WAITING", reason_code="fetch_failed",
                      state_reason=f"not fetched in this run: {why}; queued for the next run")


# --- this run, 2026-09-26 night --------------------------------------------------------------
# What one invocation has met, so its run section can count what it wrote and say where each
# other video went, from the records (run_section_account). One process is one run: main()
# starts it, and a suite that drives several runs in one process starts each.
_THIS_RUN = {"met": [], "retry_written": [], "notes": []}


def start_this_run():
    _THIS_RUN["met"] = []
    _THIS_RUN["retry_written"] = []
    _THIS_RUN["notes"] = []
    _THIS_RUN["unreadable"] = 0
    _THIS_RUN["seen_obj"], _THIS_RUN["handled"] = None, set()
    _OLLAMA["down"] = None
    _RUN_DAY["day"] = dt.date.today().isoformat()      # brief part 2.12


def note_met(video_id):
    if video_id and video_id not in _THIS_RUN["met"]:
        _THIS_RUN["met"].append(video_id)


def note_problem(text):
    """One plain sentence for the run section about something that went wrong in this run,
    said once however often it happens (brief part 2: the digest says what happened)."""
    if text not in _THIS_RUN["notes"]:
        _THIS_RUN["notes"].append(text)
        print(f"  NOTE: {text}")


def queue_for_triage(seen, vid, details, why):
    """A listing the triage could not judge, kept: pending in seen.json and marked to be
    triaged, so the next channels run hands it back (pending_due) whether or not a listing
    or a search ever returns it again. 2026-09-26 night; before, it was simply skipped."""
    seen[vid] = {"date": today_key(), "verdict": "pending", "title": details["title"],
                 "channel": details["channel"], "url": details["url"],
                 "pending_verdict": None, "pending_reason": None, "retry_count": 0,
                 "note": f"not triaged: {why}", "needs_triage": True}
    for key in ("favourite", "channel_key"):          # 2026-10-05, as mark_pending keeps them
        if details.get(key):
            seen[vid][key] = details[key]
    save_seen(seen)


def process_candidates(candidates, context_text, model, local_only, seen, sells_something=None,
                        budget=None, source=None):
    # Two passes. First, every unseen candidate is triaged from what the listing (and, for
    # channels, the feed) said about it: title, description, duration, date. That costs no
    # request at all. NO is settled right there: recorded in seen.json and history, never
    # fetched, never summarised. Second, YES and MAYBE are fetched, YES first, within the
    # run's caption budget; the rest wait for the next run with nothing spent on them.
    #
    # seen[vid]["verdict"] can be a real verdict (YES/MAYBE/NO, done, skip forever),
    # "pending" (fetch not completed yet: cached verdict carried in pending_verdict and
    # pending_reason so a retry needn't re-triage; retry_after, when set, is the first day
    # it may be tried again), or "gave_up" (3 failed attempts, permanently skipped).
    if budget is None:
        budget = new_budget()

    pass_rows = []
    no_entries = []
    gave_up_entries = []
    summaries = []
    rate_limited = False
    today = today_key()

    # 2026-09-26 night, brief part 2.9: a video is handled once a run. Listed twice in one
    # search it was triaged twice, fetched twice and written twice; queued from seen.json and
    # listed again in the same run, it could be fetched twice. A run is one seen.json loaded
    # once and handed to every call, so the register starts again with a different one (a
    # reference, not an id: Python hands a freed object's id to the next one).
    if _THIS_RUN.get("seen_obj") is not seen:
        _THIS_RUN["seen_obj"], _THIS_RUN["handled"] = seen, set()
    handled = _THIS_RUN["handled"]
    queue = []   # (video, verdict, reason, retry_count, details)
    for v in candidates:
        vid = v["id"]
        if vid in handled:
            print(f"  (met already in this run, not handled again: {v.get('title') or vid})")
            continue
        handled.add(vid)
        existing = seen.get(vid)
        if existing and existing.get("verdict") not in ("pending",):
            continue  # fully done, or already gave up
        if existing and not existing.get("needs_triage"):
            # pending retry: reuse the cached verdict, skip re-triage
            if (existing.get("retry_after") or "") > today:
                continue   # refused earlier; not before tomorrow, never hammered today
            details = {
                "channel": existing.get("channel", v.get("channel") or "UNKNOWN"),
                "title": existing.get("title", v.get("title", "")),
                "url": existing.get("url", v.get("url") or f"https://www.youtube.com/watch?v={vid}"),
                "favourite": bool(existing.get("favourite")) or is_favourite(v),
                "channel_key": existing.get("channel_key") or channel_key(v),
            }
            queue.append((v, existing.get("pending_verdict") or "MAYBE",
                          existing.get("pending_reason") or "(queued by an earlier run)",
                          int(existing.get("retry_count", 0)), details,
                          existing.get("pending_confidence")))
            note_met(vid)
            continue
        # 2026-09-26 night: a pending entry marked needs_triage was queued because the triage
        # could not be asked when it was listed; it is triaged now, like a new listing.
        if not duration_allows(v):
            continue
        details = triage_details_from_listing(v)
        # 2026-10-05: whose video it is, for the favourites (FAVOURITE_CHANNELS) and the cap.
        details.update(favourite=is_favourite(v), channel_key=channel_key(v))
        try:
            verdict, reason, confidence = triage_video(context_text, details["channel"], details,
                                                       model, sells_something)
        except RuntimeError as e:
            # 2026-09-26 night, brief part 2.1: kept, not skipped. It waits on the books with
            # the reason, and seen.json queues it to be triaged by a later run.
            why = one_line(str(e))[:200]
            print(f"  ERROR triaging {details['title']!r}: {why}")
            note_met(vid)
            update_record(vid, summary_state="WAITING", reason_code="not_triaged",
                          state_reason=f"not triaged: {why}", url=details["url"],
                          title=details["title"], channel=details["channel"], source=source,
                          upload_date=v.get("upload_date"))
            queue_for_triage(seen, vid, details, why)
            continue
        if existing:
            # Triaged at last: seen.json keeps the verdict, so the queue does not ask again.
            mark_pending(seen, vid, details, verdict, reason, 0, "triaged; waiting to be fetched",
                         confidence=confidence)
        print(f"  [{verdict} {confidence_cell(confidence)}] {details['title']}  -  {reason}")
        # The record is created the moment the tool forms an opinion, which is here, and is
        # updated in place as the video moves. A candidate that is never reached is then
        # already on the books as WAITING, which is what puts it under the table instead of
        # leaving it in a log line nobody reads.
        listing_fields = {
            "url": details["url"], "title": details["title"], "channel": details["channel"],
            "source": source, "listing_verdict": verdict, "listing_reason": reason,
            "listing_confidence": confidence, "upload_date": v.get("upload_date"),
            "channel_key": details["channel_key"],
        }
        if details["favourite"]:
            listing_fields["favourite"] = True
        note_met(vid)
        if verdict == "NO" and details["favourite"]:
            # 2026-10-05: a favourite channel's video is read whatever its listing says.
            print("    (a priority channel's video: read whatever its listing says)")
        elif verdict == "NO":
            update_record(vid, summary_state="NOT_READ", reason_code="screened_out",
                          state_reason="screened out from the listing; never fetched",
                          **listing_fields)
            mark_seen(seen, vid, "NO", details["title"])
            no_entries.append({"verdict": "NO", "channel": details["channel"], "title": details["title"],
                               "reason": reason, "url": details["url"], "most_useful": None,
                               "fetched": False, "confidence": confidence, "id": vid})
            append_history("NO", details["channel"], details["title"], details["url"],
                           f"{reason} [screened out from the listing; not fetched]", source=source,
                           confidence=confidence)
            continue
        update_record(vid, summary_state="WAITING", reason_code="rolled",
                      state_reason="rolled to the next run; nothing spent on it",
                      **listing_fields)
        queue.append((v, verdict, reason, 0, details, confidence))

    # Stable: a favourite channel's videos first (2026-10-05), then YES, OPPORTUNITY, MAYBE.
    queue.sort(key=lambda item: (not item[4].get("favourite"), FETCH_PRIORITY.get(item[1], 2)))
    stop_fetching = None      # why this run fetches nothing more (2026-09-26 night, part 2)
    in_a_row = {}             # passing failures of one kind since the last transcript landed

    for idx, (v, verdict, reason, retry_count, details, confidence) in enumerate(queue):
        vid = v["id"]
        favourite = bool(details.get("favourite"))
        # 2026-10-05: at most CHANNEL_DAILY_CAP of one channel's videos read a day, a favourite
        # exempt. Over it, a video waits with nothing spent on it, and one already queued in
        # seen.json stays queued, with no try used.
        key = details.get("channel_key") or channel_key(v)
        if not favourite and key and channel_reads_today(key) >= CHANNEL_DAILY_CAP:
            if retry_count or (seen.get(vid) or {}).get("verdict") == "pending":
                print(f"    ({details['title']}: its channel has had {CHANNEL_DAILY_CAP} videos "
                      f"read today; it stays queued for a later day)")
            else:
                update_record(vid, summary_state="WAITING", reason_code="channel_cap",
                              state_reason=f"its channel had {CHANNEL_DAILY_CAP} videos read today "
                                           f"already; nothing spent on it")
                print(f"    ({details['title']}: waits, its channel has had {CHANNEL_DAILY_CAP} "
                      f"videos read today)")
            continue
        if budget["remaining"] <= 0:
            left = len(queue) - idx
            print(f"    (this run's transcript cap reached; {left} YES/MAYBE candidate(s) "
                  f"wait for the next run, nothing spent on them)")
            break
        if retry_count:
            print(f"  [retry {retry_count + 1}/{PENDING_MAX_RETRIES}] {details['title']} (cached: {verdict})")
        else:
            print(f"  fetching [{verdict}] {details['title']} "
                  f"(via {slot_plan()['primary'] or 'no path'})")

        def unsummarized(note, code="no_transcript", state_reason=None):
            mark_seen(seen, vid, verdict, details["title"])
            update_record(vid, summary_state="NOT_READ", reason_code=code,
                          state_reason=state_reason or note.strip("[]"))
            entry = {"verdict": verdict, "channel": details["channel"], "title": details["title"],
                     "reason": reason, "url": details["url"], "most_useful": None,
                     "confidence": confidence, "id": vid}
            pass_rows.append(entry)
            append_history(verdict, details["channel"], details["title"], details["url"],
                           f"{reason} {note}", source=source, confidence=confidence)

        def passing_failure(kind, problem):
            """Brief part 2, items 2, 5 and 6: a failure that is not the video's own. Queued in
            seen.json for another try, at most FETCH_MAX_TRIES in all, and waiting on the books
            with the reason; the run is stopped from fetching when the next one would fail the
            same way. Returns the reason fetching stopped, or None.

            2026-10-05: a favourite channel's video is never given up; past FETCH_MAX_TRIES it
            is tried once a day, until it is read."""
            words = fetch_failure_words(kind, problem)
            tries = retry_count + 1
            print(f"    ({words}: {one_line(problem)[:160]})")
            if tries >= FETCH_MAX_TRIES and not favourite:
                why = f"could not fetch after {tries} tries: {words}"
                unsummarized(f"[{why}: {one_line(problem)[:120]}]", code="transcribe_failed",
                             state_reason=why)
            else:
                later = None
                if tries >= FETCH_MAX_TRIES:
                    later = (dt.date.fromisoformat(today_key()) + dt.timedelta(days=1)).isoformat()
                mark_pending(seen, vid, details, verdict, reason, tries,
                             f"{words}: {one_line(problem)[:160]}", retry_after=later,
                             confidence=confidence)
                if favourite:
                    state = (f"{words}; try {tries}; a priority channel's video is tried until "
                             f"it is read, " + ("once a day from now" if later
                                                else "and the next run tries again"))
                else:
                    state = (f"{words}; try {tries} of {FETCH_MAX_TRIES}, and the next run tries "
                             f"again")
                update_record(vid, summary_state="WAITING", reason_code="fetch_failed",
                              state_reason=state, fetch_error=one_line(problem)[:300])
            if kind in FAILURE_NOTES:
                note_problem(FAILURE_NOTES[kind].format(words=words[:1].upper() + words[1:]))
            if favourite:
                note_problem("A priority channel's video that could not be fetched is tried again "
                             "until it is read: by the next run for its first 3 tries, then once "
                             "a day.")
            in_a_row[kind] = in_a_row.get(kind, 0) + 1
            if kind in STOP_AT_ONCE_KINDS:
                return words
            if kind == "transcriber" and in_a_row[kind] >= TRANSCRIBER_FAILURES_BEFORE_STOP:
                return f"{words}, {in_a_row[kind]} times in a row"
            return None

        # 2026-09-26 night, brief part 2.6: no download onto a disk without room for it. The
        # rest wait in seen.json for the next run, with nothing spent on them.
        if stop_fetching is None:
            free = disk_free_bytes()
            if free < DISK_MIN_FREE_BYTES:
                stop_fetching = (f"free disk space was {free / 1e9:.1f} GB, under the "
                                 f"{DISK_MIN_FREE_BYTES / 1e9:.1f} GB a download needs")
                note_problem(f"The disk had too little room ({stop_fetching}), so nothing was "
                             f"downloaded; every video not fetched is queued in seen.json for "
                             f"the next run.")
        if stop_fetching:
            print(f"    (fetching stopped: {stop_fetching}; {len(queue) - idx} candidate(s) "
                  f"queued for the next run, nothing spent on them)")
            queue_left_for_next_run(queue[idx:], seen, stop_fetching)
            break

        budget["remaining"] -= 1
        failure = None
        try:
            meta, err = fetch_transcript_by_plan(details["url"])
        except (RuntimeError, OSError) as e:
            # FIX 5, 2026-09-25: a fetch that fails for one video (a timeout, a failed
            # extraction) is that video's failure. Until today it ended the whole invocation:
            # twice this week, on a channel the search had returned as a result. A 429 is
            # RateLimitError, which is not a RuntimeError, and still ends the slot below.
            # 2026-09-26 night: an OSError (a disk with no room) is caught with it, and what the
            # failure was decides below whether the video is tried again.
            meta, err = None, None
            failure = one_line(str(e) if isinstance(e, RuntimeError)
                               else f"{e.__class__.__name__}: {e}")
        except RateLimitError as e:
            print(f"YouTube refused the request (HTTP 429). Stopping here: the tool waits "
                  f"rather than try another way in.\n  {str(e)[-200:]}")
            rate_limited = True
            tomorrow = (dt.date.fromisoformat(today_key()) + dt.timedelta(days=1)).isoformat()
            mark_pending(seen, vid, details, verdict, reason, retry_count,
                         "every available transcript path refused it (HTTP 429)",
                         retry_after=tomorrow, confidence=confidence)
            update_record(vid, summary_state="WAITING", reason_code="refused_429",
                          state_reason=f"every transcript path refused it (HTTP 429); "
                                       f"not retried before {tomorrow}")
            print(f"    (marked pending; retried on or after {tomorrow}, not today)")
            break

        if err and err.startswith(f"{GARBLED_ERROR} garbled"):
            # 2026-09-26 night, brief part 2.3: Whisper looped or wrote what nobody can read.
            # Before the failure classifier, which could match a word inside the loop.
            why = err[len(GARBLED_ERROR):].strip()
            print(f"    ({why})")
            unsummarized(f"[{why}]", code="garbled", state_reason=why)
            continue
        problem = failure or err
        kind = fetch_failure_kind(problem) if problem else None
        if kind in TRANSIENT_KINDS:
            stop_fetching = passing_failure(kind, problem)
            if stop_fetching:
                note_problem(f"Fetching stopped for the rest of the run: {stop_fetching}.")
            continue
        if kind in ("removed", "account"):
            # 2026-09-26 night, brief part 2.8: gone between the listing and the fetch, or
            # behind an account. The video's own failure, said in plain words and done: it
            # will not come back, so nothing is queued.
            why = ("the video was removed or made private after it was listed"
                   if kind == "removed" else
                   "the video needs a signed-in account (members-only or age-restricted)")
            print(f"    ({why})")
            unsummarized(f"[{why}: {one_line(problem)[:120]}]", code="removed", state_reason=why)
            continue
        if (favourite and problem and not ceiling_reason(problem)
                and not no_text_minutes(problem)[0] and "could not be downloaded" not in problem):
            # 2026-10-05: a favourite channel's video is tried again until it is read, whatever
            # went wrong, unless it is gone, longer than the ceiling or has no speech in it.
            stop_fetching = passing_failure(kind, problem)
            if stop_fetching:
                note_problem(f"Fetching stopped for the rest of the run: {stop_fetching}.")
            continue
        if failure:
            print(f"    (could not fetch it: {failure[:200]})")
            unsummarized(f"[could not fetch: {failure[:200]}]", code="transcribe_failed",
                         state_reason=f"could not fetch: {failure[:200]}")
            continue

        if err and "could not be downloaded" in err:
            # listed but empty: YouTube hasn't finished generating them, not a real failure
            retry_count += 1
            if retry_count >= PENDING_MAX_RETRIES:
                seen[vid] = {"date": today, "verdict": "gave_up", "title": details["title"]}
                save_seen(seen)
                gave_up_entries.append({"title": details["title"], "channel": details["channel"],
                                        "url": details["url"], "attempts": retry_count})
                append_history(verdict, details["channel"], details["title"], details["url"],
                               f"{reason} [gave up after {retry_count} attempts: captions never finished]",
                               source=source)
                update_record(vid, summary_state="NOT_READ", reason_code="no_transcript",
                              state_reason=f"captions never finished after {retry_count} attempts")
                print(f"    (gave up after {retry_count} attempts: captions never finished)")
            else:
                mark_pending(seen, vid, details, verdict, reason, retry_count,
                             "captions not ready yet", confidence=confidence)
                update_record(vid, summary_state="WAITING", reason_code="captions_not_ready",
                              state_reason=f"captions not ready yet, retry {retry_count} of "
                                           f"{PENDING_MAX_RETRIES} next run")
                print(f"    (captions not ready yet, retry {retry_count}/{PENDING_MAX_RETRIES} next run)")
            continue
        if err:
            print(f"    (no usable captions: {err})")
            # FIX 4, 2026-09-22: audio that transcribed to nothing is no speech, one state
            # with a transcript that holds none, and its row says how long the audio was.
            silent, silent_minutes = no_text_minutes(err)
            if silent:
                quiet = {"no_speech": "no_text", "words": 0, "minutes": silent_minutes}
                why = no_speech_text(quiet)
                unsummarized(f"[{why}]", code="no_speech", state_reason=why)
                update_record(vid, **quiet)
                continue
            # Over the byte ceiling is not "no captions", it is a video this tool will never
            # read, and its row has to say which of the two it was.
            over = ceiling_reason(err)
            unsummarized(f"[no captions: {err}]",
                         code="over_ceiling" if over else "no_transcript",
                         state_reason=over or one_line(err))
            continue

        meta["channel"] = details["channel"]
        meta["favourite"] = favourite           # 2026-10-05: choose_backend reads it
        in_a_row.clear()
        # FIX 2: on disk before a single token is spent on the summary, so a summary that
        # fails costs the summary and nothing else. 2026-09-26 night: and before the video is
        # marked done, so a disk that fills in between can never leave it done with no
        # transcript kept; a disk that fills here is that video's passing failure.
        try:
            saved = save_transcript(meta, source=source)
        except OSError as e:
            problem = f"{e.__class__.__name__}: {e}"
            stop_fetching = passing_failure(fetch_failure_kind(problem) or "disk", problem)
            if stop_fetching:
                note_problem(f"Fetching stopped for the rest of the run: {stop_fetching}.")
            continue
        # On the meta as well as on the record: summarise_one reads meta["minutes"] to size
        # the timeout, and without this line fix 3 would quietly give every video the floor
        # on the one path that matters.
        meta["minutes"] = saved["minutes"]
        # FIX 3: and whether they were measured, so a later retry knows which to trust.
        # 2026-09-26 night, brief part 2.10: and the record says it was read before seen.json
        # says the video is done. A run that loses the power after this leaves it in the retry
        # queue (triage_pending), which judges it and writes it up; before, it stayed WAITING,
        # marked done, and no run ever came back to it.
        update_record(vid, minutes=saved["minutes"], words=saved["words"],
                      minutes_estimated=bool(saved.get("minutes_estimated")),
                      summary_state="READ_NOT_WRITTEN", reason_code="triage_pending",
                      state_reason="read; the run stopped before it was judged, and the next "
                                   "run finishes it", read_day=today_key())
        mark_seen(seen, vid, verdict, details["title"])  # transcript landed - this one's really done

        # FIX 4, 2026-09-22: before the transcript triage, is there any speech in it at
        # all. A music video transcribed as "Oh" is not a video that was read, and it gets
        # no triage call and no write-up.
        measured = None if saved.get("minutes_estimated") else saved["minutes"]
        kind, spoken = speech_check(meta["transcript"], measured)
        if kind:
            quiet = {"no_speech": kind, "words": spoken, "minutes": measured}
            why = no_speech_text(quiet)
            print(f"    ({why}: not read, so no triage and no summary)")
            unsummarized(f"[{why}]", code="no_speech", state_reason=why)
            update_record(vid, **quiet)
            continue
        # 2026-09-26 night, brief part 2.3: nor a transcript nobody can read, from any path.
        garbled = garbled_text(meta["transcript"], loops=False)
        if garbled:
            print(f"    ({garbled})")
            unsummarized(f"[{garbled}]", code="garbled", state_reason=garbled)
            continue

        # The second triage, from the transcript. The listing's verdict decided what to
        # fetch; this one decides what the video actually is, and it is the verdict that
        # goes in the digest, routes the summary and feeds the weekly. It costs no request.
        listing_verdict, listing_reason = verdict, reason
        read = dict(details, transcript=meta["transcript"], listing_verdict=verdict,
                    listing_reason=reason)
        try:
            verdict, reason, confidence = triage_video(context_text, details["channel"], read,
                                                       model, sells_something)
        except Exception as e:  # noqa: BLE001 - one video's failure, whatever raised it
            # 2026-09-26 night, brief part 2.1. It used to keep the listing's guess and write the
            # video up under it, so the digest showed a MAYBE nobody had judged. Now the
            # transcript is saved and the record says it was read and not judged; the retry
            # phase judges it and writes it up, with no new request. An outage of the local
            # model is not the video's fault, so it does not use up one of its attempts.
            why = one_line(str(e) if isinstance(e, RuntimeError)
                           else f"{e.__class__.__name__}: {e}")[:200]
            print(f"    (the transcript could not be judged: {why}; it is saved, and a later run "
                  f"judges it and writes it up)")
            update_record(vid, summary_state="READ_NOT_WRITTEN", reason_code="triage_failed",
                          state_reason=f"not judged yet: {why}", read_day=today_key(),
                          read_verdict=None)
            pass_rows.append({"verdict": listing_verdict, "channel": details["channel"],
                              "title": details["title"], "reason": listing_reason,
                              "url": details["url"], "most_useful": None,
                              "listing_verdict": listing_verdict, "confidence": None, "id": vid})
            append_history(listing_verdict, details["channel"], details["title"], details["url"],
                           f"{listing_reason} [read, not judged yet: {why}]", source=source)
            continue
        # 2026-09-30, the opportunity lane: one more cheap question about the transcript alone,
        # which can make a MAYBE or a NO an OPPORTUNITY and never demotes anything.
        screen = None
        if lane_on():
            screen = screen_opportunity(dict(details, transcript=meta["transcript"]), model)
            meta["triage_verdict"] = verdict
            routed, reason = route_for_opportunity(verdict, reason, screen)
            if screen.get("error"):
                print(f"    (the opportunity screen could not be asked: {screen['error']})")
            elif routed != verdict:
                print(f"    the opportunity screen passed: read as OPPORTUNITY {reason[-90:]}")
            verdict = routed
        moved = verdict != listing_verdict
        if moved:
            print(f"    read as {verdict} ({confidence_cell(confidence)}) "
                  f"(listing said {listing_verdict}): {reason}")
            mark_seen(seen, vid, verdict, details["title"])
        meta["verdict"], meta["verdict_reason"] = verdict, reason
        history_reason = (f"{reason} [listing said {listing_verdict}: {listing_reason}]"
                          if moved else reason)
        # Read. Whatever happens to the summary from here, the record now knows the
        # transcript verdict, and that is what the row keeps as history when no write-up
        # ever arrives. FIX 1: and the day, because that day's digest keeps the video's row
        # after a retry on a later day has moved the record on.
        update_record(vid, read_verdict=verdict, read_reason=reason, read_confidence=confidence,
                      read_day=today_key(), reason_code="summary_pending",
                      state_reason=f"read as {verdict}; the run stopped before the write-up was "
                                   f"finished, and the next run finishes it",
                      **({"opp_lane": OPPORTUNITY_LANE, "opp_screen": screen,
                          "triage_verdict": meta["triage_verdict"]} if screen is not None else {}))

        # 2026-10-05: a favourite channel's video is written up in full even when it is read as
        # NO; the verdict stays NO.
        if verdict == "NO" and not favourite:
            # Read and found empty: the two-line entry, and no summary spent on it.
            most_useful = local_most_useful(context_text, meta, model)
            no_entries.append({"verdict": "NO", "channel": details["channel"], "title": details["title"],
                               "reason": reason, "url": details["url"], "most_useful": most_useful,
                               "fetched": True, "listing_verdict": listing_verdict,
                               "confidence": confidence})
            append_history("NO", details["channel"], details["title"], details["url"],
                           f"{history_reason} [read, then screened out; not summarised]",
                           source=source, confidence=confidence)
            update_record(vid, summary_state="READ_NOT_WRITTEN", reason_code="read_then_dropped",
                          state_reason="read, then screened out; no summary spent on it")
            if rate_limited:
                break
            continue

        may, why_not = may_start_a_summary()
        if not may:
            # FIX 3: the slot is nearly over. The transcript is already saved, so this
            # costs nothing but the wait: the record joins the retry queue and the next
            # run writes it up without a single request.
            print(f"    (no summary this run: {why_not})")
            attempt = int(seen_attempts(vid)) + 1
            record = update_record(vid, summary_state="READ_NOT_WRITTEN",
                                   reason_code="no_run_time", state_reason=why_not,
                                   attempts=attempt,
                                   failures=failures_with(vid, attempt, "no_run_time", why_not))
            pass_rows.append({"verdict": verdict, "channel": details["channel"],
                              "title": details["title"], "reason": reason,
                              "url": details["url"], "most_useful": None,
                              "listing_verdict": listing_verdict, "confidence": confidence,
                              "id": vid})
            append_history(verdict, details["channel"], details["title"], details["url"],
                           f"{history_reason} [{why_not}]", source=source,
                           confidence=confidence)
            print(f"    -> {final_verdict(record)[0]}: {final_verdict(record)[1]}")
            continue
        try:
            (summary, backend_label, backend_why, elapsed, chunked, settled,
             gate_notes) = summarise_one(context_text, meta, model, local_only, verdict)
        except Exception as e:  # noqa: BLE001 - FIX 2: the record joins the retry queue,
            # transcript saved. 2026-09-26 night, brief part 2.4: any exception, not only a
            # RuntimeError: a claude CLI that vanished (FileNotFoundError) used to end the run.
            e = e if isinstance(e, RuntimeError) else RuntimeError(f"{e.__class__.__name__}: {e}")
            print(f"    ERROR summarizing: {e}")
            record = summary_failed(vid, e, attempts=1)
            pass_rows.append({"verdict": verdict, "channel": details["channel"], "title": details["title"],
                              "reason": reason, "url": details["url"], "most_useful": None,
                              "listing_verdict": listing_verdict, "confidence": confidence,
                              "id": vid})
            append_history(verdict, details["channel"], details["title"], details["url"],
                           f"{history_reason} [summarize failed: {e}]", source=source,
                           confidence=confidence)
            print(f"    -> {final_verdict(record)[0]}: {final_verdict(record)[1]}")
            if rate_limited:
                break
            continue
        # 2026-09-30, the opportunity lane: its card, written after the write-up and appended to
        # it as section 10, and the verdict the day's table will show.
        lane_fields = None
        if screen is not None:
            summary, settled, gate_notes, lane_fields = lane_written(
                vid, context_text, meta, verdict, screen, summary, settled, gate_notes)
        # A keep under the work label past the day's cap is MAYBE, logged here and in
        # history.jsonl; summary_written records it, and
        # final_verdict demotes it, from the settled verdict passed on unchanged.
        cap = label_cap_for(vid, section6_state(summary)[1], settled)
        if cap:
            gate_notes = list(gate_notes) + [f"demoted from YES: {cap}"]
        shown = "MAYBE" if cap else settled
        if lane_fields is not None:
            shown, gate_notes = lane_display(vid, summary, settled, cap, lane_fields, shown,
                                             gate_notes)
        if shown != verdict:
            for note in gate_notes:
                print(f"    ({note})")
            reason = f"{reason} [{'; '.join(gate_notes)}]"
            history_reason = f"{history_reason} [{'; '.join(gate_notes)}]"
            verdict = meta["verdict"] = shown
            mark_seen(seen, vid, verdict, details["title"])
        append_history(verdict, details["channel"], details["title"], details["url"], history_reason,
                       summary=summary, backend=backend_label,
                       try_this_week=extract_try_this_week(summary), source=source,
                       confidence=confidence)

        save_writeup(meta, summary, backend_label, elapsed, chunked)
        if lane_fields is not None:
            if card_surfaces(lane_fields["opp_card"]) and not lane_fields["opp_cap"]:
                append_opportunity_card(meta, lane_fields["opp_card"])
        elif verdict == "OPPORTUNITY":
            append_opportunity(meta, summary)
        summary_written(vid, summary, settled, attempt=1, seconds=elapsed, **(lane_fields or {}))
        pass_rows.append({"verdict": verdict, "channel": details["channel"],
                          "title": details["title"], "reason": reason, "url": details["url"],
                          "listing_verdict": listing_verdict, "confidence": confidence,
                          "id": vid})
        summaries.append({"verdict": verdict, "meta": meta, "summary": summary,
                          "backend": backend_label, "backend_why": backend_why,
                          "listing_verdict": listing_verdict, "confidence": confidence,
                          "elapsed": elapsed, "chunked": chunked})
        if rate_limited:
            break   # the fallback delivered this one; the cooldown still ends the slot

    return pass_rows, no_entries, gave_up_entries, summaries, rate_limited


def md_cell(text):
    # A literal "|" in cell content is parsed as a new column boundary; a raw
    # newline breaks the row. Escape both before embedding dynamic text (titles,
    # model-written reasons) in a markdown table (observed breaking
    # CHANNEL-CANDIDATES.md: the SELLS/FIT text itself contains " | ").
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


DIGEST_HEADER_RE = re.compile(r"<!-- HEADER-START -->.*?<!-- HEADER-END -->\n*", re.DOTALL)
# 2026-09-30: the day's opportunity cards, regenerated with the header, right after it.
DIGEST_OPPS_RE = re.compile(r"<!-- OPPS-START -->.*?<!-- OPPS-END -->\n*", re.DOTALL)

# RULE 6, the thirty-second header, 2026-09-20. At most four lines: up to three KEEP-grade
# items with one clause each on why, and then one line that counts what was skipped and
# carries the totals the old two-line header used to carry on its own.
#
# It is written LAST in the sense that matters, and that is a property of where the numbers
# come from rather than of when the function is called: every line is computed from today's
# rows in history.jsonl, so whichever run of the day writes the digest last leaves a header
# describing the whole day, not its own slot. A run that adds nothing returns before this and
# leaves the header alone, which is correct: nothing about the day changed.
DIGEST_HEADER_ITEMS = 3
HEADER_COPY_RE = re.compile(r"\[copy:\s*(.+?)\]")


SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def first_sentence(text):
    """The first sentence of a piece of prose, with a full stop inside quotation marks not
    counted as the end of anything. Section 6's entries quote the video, and a quote ending
    in a full stop is where the naive split cuts a sentence in half."""
    body = one_line(text or "").strip()
    if not body:
        return ""
    depth, out = 0, []
    for i, ch in enumerate(body):
        closing = ch == '"' and depth == 1
        if ch == '"':
            depth = 0 if depth else 1
        out.append(ch)
        ends_here = i + 1 >= len(body) or body[i + 1] in " \t"
        # 2026-09-22: a sentence can end inside the quote it closes, as in `... order -
        # "Now the first customer will get the PIN generated." This would let ...`, where
        # the old rule ran on into the next sentence.
        if depth == 0 and ends_here and (ch in ".!?" or (closing and body[i - 1] in ".!?")):
            break
    return "".join(out).strip()


def truncate_words(text, limit):
    """text cut to at most limit characters, at a word boundary. A single word longer than
    the budget is cut anyway, because something has to give, and that is the only case in
    which this function can split a word."""
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    cut = body[:limit]
    if " " in cut:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:").rstrip()


# The header line is verdict, project, one whole sentence, then the video as a short title
# link. The title is what gives, to 50 characters.
#
# The sentence is the write-up's own, and never a cut of section 6. The first sentence of
# section 6's "Most applicable" entry, which the writer opens with the video's words in quotes,
# cut at a clause boundary, came out broken nearly every time: opening with the quote, ending
# in a dangling "- for <project>", or a fragment such as "For <project>, before the launch.".
# The source is section 9, the header the write-up writes for itself, and for a write-up with
# no section 9, section 8, which is already one sentence of what to do this week. Neither is
# cut.
HEADER_TITLE_CHARS = 50
HEADER_HEADING_RE = re.compile(r"^##\s*9\b")
NOT_TRIED_RE = re.compile(r"^\s*not tried:", re.IGNORECASE)
# A quote left hanging off the front of a sentence ends up as "... - for <project>."
DANGLING_FOR_RE = re.compile(r"\s[-" + chr(0x2013) + chr(0x2014) + r"]\s+for\s+\S", re.IGNORECASE)
OPENING_QUOTES = "\"'" + chr(0x201C) + chr(0x2018)
DASHES = "-\u2013\u2014"
SMALL_WORDS = frozenset("a an and as at by for from in into of on or the to with".split())


def capitalised(text):
    return text[:1].upper() + text[1:] if text[:1].isalpha() else text


def whole_sentence(text):
    """The text as one whole, plain sentence of what to do, or None when it is not one.

    One line; not "nothing" and not rule 3's "Not tried:" in place of an action it struck;
    not opening with a quote mark, because a line that opens with the video's words is
    evidence, not an action; not ending in a dangling "- for <project>". The first sentence
    only, when there are two, and never cut inside it: a sentence cut to fit is the fragment
    this replaces. A capital first letter and a closing full stop are the only repairs."""
    body = re.sub(r"^\s*(?:[-*]\s+)+", "", one_line(text or "")).strip()
    if not body or is_nothing_useful(body) or NOT_TRIED_RE.match(body):
        return None
    if body[:1] in OPENING_QUOTES or DANGLING_FOR_RE.search(body):
        return None
    sentence = capitalised(first_sentence(body).strip())
    if not sentence:
        return None
    if not sentence.endswith((".", "!", "?", '."', '!"', '?"')):
        sentence += "."
    return sentence


def writeup_header(summary_text):
    """The header a write-up wrote for itself, section 9, or None.

    Used only when it is exactly one whole sentence and the write-up's section 8 holds an
    action. The second condition is what keeps it true to the rest of the write-up: every
    guard that finds nothing to do (section 6 came to nothing, the model thought aloud, a
    local summary, rule 3 striking an action) writes section 8, and none of them has to
    know that section 9 exists."""
    if not summary_text:
        return None
    if not whole_sentence(extract_try_this_week(summary_text)):
        return None
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, HEADER_HEADING_RE)
    if bounds is None:
        return None
    body = [l.strip() for l in lines[bounds[0] + 1:bounds[1]] if l.strip()]
    return one_sentence_only(body[0]) if body else None


def one_sentence_only(text):
    """whole_sentence, but None when the text holds more than one sentence. The write-up's
    own header is asked for exactly one; a second sentence means it did not do what it was
    asked, and section 8 is used instead."""
    sentence = whole_sentence(text)
    if not sentence:
        return None
    if sentence.rstrip(".!?\"") != capitalised(one_line(text).strip()).rstrip(".!?\""):
        return None
    return sentence


def header_sentence(record):
    """The sentence of a keeper's header line: the write-up's own header (section 9), else its
    section 8, else None, in which case the keeper has no header line of its own and is
    counted in "more kept below". Never section 6 (fix 1, 2026-09-25), never the triage's
    reason and never the listing's [copy:] answer (fix 5 of 2026-09-21: on that day it put a
    seller's "$2,000 per task" in the header of a day whose section 6 said something else)."""
    return one_sentence_only(record.get("header")) or whole_sentence(record.get("try_this_week"))


def short_title(title, limit=HEADER_TITLE_CHARS):
    """A title short enough to be a link at the end of a header line, with its square
    brackets escaped. Whole when it fits; otherwise the part before its first separator
    (" | " or a spaced dash, then ": ", then " - " or " ("), when that part is a title at
    all; otherwise whole words up to the limit, never ending on a small word."""
    title = one_line(title or "").strip()
    if len(title) > limit:
        for separators in ((" | ", " \u2014 ", " \u2013 "), (": ",), (" - ", " (")):
            heads = [title.split(sep, 1)[0] for sep in separators if sep in title]
            heads = [h for h in heads if 10 <= len(h) <= limit]
            if heads:
                title = min(heads, key=len)
                break
        else:
            words, kept = title.split(), []
            for word in words:
                if len(" ".join(kept + [word])) > limit:
                    break
                kept.append(word)
            while len(kept) > 1 and kept[-1].lower().strip(",;:") in SMALL_WORDS:
                kept.pop()
            title = " ".join(kept).rstrip(" ,;:" + DASHES) or title[:limit]
    return title.replace("[", "\\[").replace("]", "\\]")


def todays_day_records(day=None):
    """The day's records, from the record store when the day has one and from what
    history.jsonl implies when it does not.

    One code path either way, on purpose. The rule that decides a keep must not depend on
    which store the day happens to be in, and a day that ran before the record store
    existed still has to answer "was this one written" correctly: a history row with no
    summary is a video with no write-up, whichever store you read it from."""
    day = day or today_key()
    records = todays_records(day)
    # FIX 1, 2026-09-22: and the videos first read on this day whose record has since moved
    # to a later day, which a retry does when it writes one up. The day they were read keeps
    # their row, and the row shows the record as it is now: a talk read on one day must not
    # go on saying NOT WRITTEN there once a later day has written it.
    moved_on = [r for r in load_records().values()
                if r.get("read_day") == day and (r.get("day") or "") > day]
    if records or moved_on:
        return sorted(records + moved_on, key=lambda r: (r.get("created") or "", r.get("id") or ""))
    rows = [record_from_history(e) for e in load_history_since(dt.date.fromisoformat(day))
            if e.get("date") == day]
    return [r for r in rows if r.get("id")]


def header_sort_key(record, order):
    """Project order first, then the order the day's runs met the video.

    The project order is PROJECTS, the order of your settings: your projects, then the work
    label, then this tool. A project the list does not know sorts last rather than first,
    because an unknown label is not a promotion."""
    project = record.get("project")
    place = PROJECTS.index(project) if project in PROJECTS else len(PROJECTS)
    return (place, order.get(record.get("id"), 0))


def header_line(record, sentence=None):
    """One keeper's line as the header prints it: verdict, project, its sentence, a short title
    link. With no sentence of its own the line says so rather than borrowing a fragment."""
    sentence = sentence if sentence is not None else header_sentence(record)
    return (f"- **{final_verdict(record)[0]}** {record.get('project')}: "
            f"{sentence or '(no sentence of its own)'} "
            f"[{short_title(record.get('title') or record.get('id'))}]({record.get('url') or ''})")


def todays_header_lines(day=None):
    """The day's header, as a list of at most four lines. `day` is today unless a past
    day's digest is being regenerated (FIX 1, 2026-09-22).

    FIX 1: which videos may appear here is decided by final_verdict and nothing else, so a
    video whose summary never arrived cannot lead the day. Once, a talk with no write-up
    behind it was the second line of this header.

    FIX 5: the sentence beside each title is the write-up's own, the order is the project
    order of your settings, and the last line is the day's five counts. Regenerated
    from the records on every write, so whichever run of the day writes the digest last
    leaves a header describing the whole day."""
    entries = todays_day_records(day)
    order = {e.get("id"): n for n, e in enumerate(entries)}
    keepers = sorted((e for e in entries if "header" in final_verdict(e)[2]),
                     key=lambda e: header_sort_key(e, order))
    # 2026-09-22: verdict, project, one whole sentence, then the video as a short title link.
    # 2026-09-25: the sentence is the write-up's own, and a keeper without one is not shown
    # with a fragment in its place; it stays in the table and in "more kept below".
    shown = [(e, header_sentence(e)) for e in keepers]
    shown = [(e, s) for e, s in shown if s][:DIGEST_HEADER_ITEMS]
    lines = [header_line(e, s) for e, s in shown]
    more = len(keepers) - len(lines)
    counts = day_count_line(entries, more_kept=more)
    if not keepers:
        return [f"Nothing to keep today. {counts}"]
    return lines + [counts]


DIGEST_TABLE_RE = re.compile(r"<!-- TABLE-START -->.*?<!-- TABLE-END -->\n*", re.DOTALL)

# FIX 4, 2026-09-21. The day's table, rebuilt from the records on every write, which is what
# makes "each video has exactly one row" true across six runs instead of one. Before today
# each run appended its own table, so a video read in one run and written in another had two
# rows, and a video that was never read at all had a row that looked like a judgment:
# one video was never fetched (67.0 MB of audio against a 63 MB ceiling) and its row read
# "MAYBE | 10 | Description provides no mechanism", which is the listing's guess printed as a
# verdict.
DAY_COUNT_WORDS = (("kept", "kept"), ("read_and_skipped", "read and skipped"),
                   ("read_not_written", "read but not written"), ("not_read", "not read"),
                   ("waiting", "waiting"))


def day_counts(records):
    """The five counts of the day, one bucket per record, from final_verdict and nothing
    else. Age-filtered results are in none of them: they were dropped before any triage, so
    they are not videos of the day, and they are named on their own line instead."""
    counts = {name: 0 for name in COUNT_TAGS}
    for record in records:
        bucket = count_bucket(final_verdict(record)[2])
        if bucket:
            counts[bucket] += 1
    return counts


def day_count_line(records, more_kept=0):
    """The day in five numbers, and how many keepers the header could not fit.

    The age-filtered results are deliberately in none of the five and are named under the
    table instead: they were dropped before any triage, so they are not videos of the day."""
    counts = day_counts(records)
    body = ", ".join(f"{counts[key]} {words}" for key, words in DAY_COUNT_WORDS)
    if more_kept > 0:
        body += f"; {more_kept} more kept below"
    return body + "."


# FIX 2, 2026-09-22: the failure tag a history row's reason carries ("[summarize failed:
# claude CLI timed out after 600s and was killed.]"), up to the next tag or the end.
FAILURE_TAG_RE = re.compile(r"\s*\[summarize failed: .*?\](?=\s*\[|\s*$)")


def day_table_lines(records, screen=False):
    """The day's table, and the lines under it for the videos that have no row yet.

    Every verdict here comes from final_verdict. A row is a record that has been attempted;
    a rolled or refused candidate has no row until the run that reads it, and an
    age-filtered result never has one. With `screen`, a row the opportunity screen judged
    carries its answer (screen_note); only the full digest asks for that."""
    rows, rolled, aged, off = [], [], [], []
    for record in records:
        verdict, state_text, tags = final_verdict(record)
        if "age_filtered" in tags:
            aged.append(record)
            continue
        if "off_topic" in tags:
            off.append(record)
            continue
        if "waiting" in tags:
            rolled.append(record)
            continue
        if "row" not in tags:
            continue                      # screened out from the listing: its own section
        if "kept" in tags or "read_and_skipped" in tags:
            # A video that was read shows its verdict. A demotion is a note on the reason, not
            # a second thing in the verdict cell, which keeps scripts/digest_rows.py able to
            # read this column. DECISION A, 2026-09-25: no "(the listing said MAYBE)" beside it.
            # The first guess agreed with the read verdict 12 times in 69 that week and said
            # MAYBE for 67 of them, so it said nothing about the video.
            cell = verdict
        else:
            cell = f"{verdict} ({state_text})" if state_text else verdict
        if "guess" in tags:
            # DECISION A: a row nobody read says why in its verdict cell and nothing more;
            # what the listing guessed about a video nobody read is not a fact about it.
            reason = ""
        else:
            reason = record.get("read_reason") or record.get("listing_reason") or ""
            if "kept" in tags or "read_and_skipped" in tags:
                # FIX 2, 2026-09-22: a written row drops a failure tag that came in with its
                # reason, because it is no longer true; final_verdict's note says what
                # happened instead.
                reason = FAILURE_TAG_RE.sub("", reason)
            if state_text and ("demoted" in tags or "retried" in tags):
                reason = f"{reason} [{state_text}]"
            # The full digest's rows carry what the opportunity screen answered, so "None
            # surfaced" can be checked row by row. Last in the reason, after every other note;
            # a record that was never screened gets nothing.
            note = screen_note(record) if screen else ""
            if note:
                reason = f"{reason} [{note}]".strip()
        # DECISION B, 2026-09-25: no confidence column. It was 95 on 64 of the week's 69 read
        # rows (95.0 on average on the kept, 91.7 on the rest), so it separated nothing, and the
        # guessed number on a row nobody read decided no retry. The triage still asks for it
        # and the stores still keep it, so it can be calibrated against the golden set later.
        rows.append(f"| {cell} | {md_cell(record.get('channel') or '')} | "
                    f"[{md_cell(record.get('title') or record['id'])}]"
                    f"({record.get('url') or ''}) | {md_cell(reason)} |")

    lines = [f"## The day so far", ""]
    if rows:
        lines.append("| Verdict | Channel | Title | Reason |")
        lines.append("|---|---|---|---|")
        lines.extend(rows)
        lines.append("")
    # 2026-09-26 night, brief part 2.1: a listing the local model could not triage is waiting
    # for a different reason, and comes back a different way, so it gets its own line.
    untriaged = [r for r in rolled if r.get("reason_code") == "not_triaged"]
    queued = [r for r in rolled if r.get("reason_code") == "fetch_failed"]
    capped = [r for r in rolled if r.get("reason_code") == "channel_cap"]   # 2026-10-05
    rolled = [r for r in rolled
              if r.get("reason_code") not in ("not_triaged", "fetch_failed", "channel_cap")]
    if queued:
        # Brief part 2, items 2, 5 and 6: each with what went wrong and which try it was.
        titles = "; ".join(f"{truncate_words(one_line(r.get('title') or r['id']), 60)} "
                           f"({r.get('state_reason') or 'queued'})" for r in queued)
        # 2026-10-05: a favourite channel's video is the exception, and the line says so.
        except_favourites = (", except a priority channel's, which is tried until it is read"
                             if any(r.get("favourite") for r in queued) else "")
        lines.append(f"Queued for another try by the next run, after a fetch failed or was "
                     f"stopped: {len(queued)}, at most {FETCH_MAX_TRIES} tries "
                     f"each{except_favourites}. {titles}")
        lines.append("")
    if untriaged:
        titles = "; ".join(truncate_words(one_line(r.get("title") or r["id"]), 60)
                           for r in untriaged)
        lines.append(f"Not triaged yet, because the local model (Ollama) did not answer: "
                     f"{len(untriaged)}. Each is queued in seen.json and the next channels run "
                     f"triages it; nothing was spent on it. {titles}")
        lines.append("")
    if rolled:
        titles = "; ".join(truncate_words(one_line(r.get("title") or r["id"]), 60)
                           for r in rolled)
        # FIX 5, 2026-09-25: said as it is. "Rolled to the next run" was not true: 15 real
        # videos were still waiting at the end of the week, because nothing brings one back but
        # a later listing or search that returns it again while the day's cap has room.
        lines.append(f"Waiting, not fetched, nothing spent on them yet: {len(rolled)}. A later "
                     f"run reads one only if its listing or search returns it again while the "
                     f"day's cap has room. {titles}")
        lines.append("")
    if capped:
        titles = "; ".join(truncate_words(one_line(r.get("title") or r["id"]), 60)
                           for r in capped)
        lines.append(f"Waiting because its channel already had {CHANNEL_DAILY_CAP} videos read "
                     f"that day (a priority channel has no such limit), nothing spent on them: "
                     f"{len(capped)}. A later run reads one only if its listing or search returns "
                     f"it again on a day its channel has room. {titles}")
        lines.append("")
    if aged:
        # FIX 6, 2026-09-22: the age as the listing gave it, never a date it implied.
        titles = "; ".join(f"{truncate_words(one_line(r.get('title') or r['id']), 60)} "
                           f"({age_label(r)})"
                           for r in aged)
        lines.append(f"Dropped on age before any triage, and counted in none of the five "
                     f"above: {len(aged)}. {titles}")
        lines.append("")
    if off:
        # FIX 3, 2026-09-25: the topic filter's drops, each with its reason.
        titles = "; ".join(f"{truncate_words(one_line(r.get('title') or r['id']), 60)} "
                           f"({r.get('state_reason') or 'off topic'})" for r in off)
        lines.append(f"Dropped as off-topic before any triage, and counted in none of the "
                     f"five above: {len(off)}. {titles}")
        lines.append("")
    return lines


def format_upload_date(value):
    """20230922 as 2023-09-22, and anything else as what it is."""
    text = str(value or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text or "date unknown"


DIGEST_FOOTER_RE = re.compile(r"<!-- FOOTER-START -->.*?<!-- FOOTER-END -->\n*", re.DOTALL)

# FIX 7, 2026-09-21. How often the listing verdict survives being read. Of the fourteen
# videos read today the transcript moved thirteen of them, all from MAYBE to YES, which
# means the number in the Confidence column beside a listing verdict is describing a guess
# that is about to be overturned. Nobody could see that without counting by hand.
#
# Only videos that were READ are in the fraction. Three of today's seventeen rows were
# never fetched at all, and their verdict is the listing's guess sitting unchallenged;
# counting those as agreement would make the tool look right about videos nobody read.
FIRST_GUESS_SQUARES = ("YES", "MAYBE")


def first_guess_counts(records):
    """How the listing's guess compared with the transcript, over one set of records.

    Returns read, agreed, never_read, outside, and the four-way table keyed by
    (listing verdict, read verdict). "outside" is the read videos whose pair is not one of
    the four squares, an OPPORTUNITY or a NO at either end: counted and reported rather
    than quietly dropped, so the four squares and the outsiders always add up to read."""
    table = {(a, b): 0 for a in FIRST_GUESS_SQUARES for b in FIRST_GUESS_SQUARES}
    read = agreed = never_read = outside = 0
    for record in records:
        listing = record.get("listing_verdict")
        after = record.get("read_verdict")
        if not after or record.get("no_speech"):   # fix 4: no speech was never read
            never_read += 1
            continue
        read += 1
        if listing == after:
            agreed += 1
        if (listing, after) in table:
            table[(listing, after)] += 1
        else:
            outside += 1
    return {"read": read, "agreed": agreed, "never_read": never_read,
            "outside": outside, "table": table}


def first_guess_line(records):
    counts = first_guess_counts(records)
    return f"First guess vs after reading: agreed {counts['agreed']} of {counts['read']}"


def days_records(days):
    """Records for the last N days, the store where a day has one and what history.jsonl
    implies where it does not, so the week's number exists from the first day rather than
    seven days after the store was added."""
    out, counted = [], set()
    for n in range(days):
        day = (dt.date.today() - dt.timedelta(days=n)).isoformat()
        # FIX 1, 2026-09-22: a video written on retry now has a row on the day it was read
        # and on the day it was written, and over a week it is still one video.
        for record in todays_day_records(day):
            if record.get("id") in counted:
                continue
            counted.add(record.get("id"))
            out.append(record)
    return out


def first_guess_block(records):
    """The footer of the digest: the day's fraction and the four-way table under it."""
    counts = first_guess_counts(records)
    lines = ["## First guess vs after reading", "",
             f"{first_guess_line(records)}, over the {counts['read']} video(s) this day "
             f"read. The listing verdict is a guess from a title and a description; this "
             f"is how often it survived the transcript.", ""]
    lines.append("| first guess | read YES | read MAYBE |")
    lines.append("|---|---|---|")
    for guess in FIRST_GUESS_SQUARES:
        cells = " | ".join(str(counts["table"][(guess, after)])
                           for after in FIRST_GUESS_SQUARES)
        lines.append(f"| **{guess}** | {cells} |")
    tail = []
    if counts["outside"]:
        tail.append(f"{counts['outside']} read video(s) fell outside those four squares "
                    f"(an OPPORTUNITY or a NO at one end)")
    if counts["never_read"]:
        tail.append(f"{counts['never_read']} video(s) were never read and are in neither "
                    f"half of the fraction")
    if tail:
        lines += ["", ". ".join(tail) + "."]
    return lines


DIGEST_RETRY_RE = re.compile(r"<!-- RETRY-START -->.*?<!-- RETRY-END -->\n*", re.DOTALL)


# FIX 1, 2026-09-22. The write-ups a retry produced, in the digest of the day the retry ran.
# A morning retry wrote up a long talk, main() threw away what the retry
# returned, and the write-up reached no digest: it existed only in its own file, in
# history.jsonl and in one line of INDEX.md. The section is rebuilt from the records and
# from history.jsonl on every write, like the header and the table, so a later run of the
# same day neither loses it nor stacks a second copy.
def written_on_retry(day):
    """The records a retry wrote up on `day`, in the order the day met them."""
    found = [r for r in load_records().values()
             if r.get("summary_state") == "WRITTEN" and r.get("written_day") == day
             and int(r.get("written_attempt") or 1) > 1]
    return sorted(found, key=lambda r: (r.get("updated") or "", r.get("id") or ""))


def history_writeup(url, day):
    """(summary, backend) from the newest history.jsonl row for this video and day that
    carries a summary, or (None, None). history.jsonl is where every write-up is kept."""
    found = (None, None)
    for row in load_history_since(dt.date.fromisoformat(day)):
        if row.get("date") == day and row.get("url") == url and row.get("summary"):
            found = (row["summary"], row.get("backend"))
    return found


def retry_block_lines(day):
    """The "Written on retry" section of one day's digest, or nothing when there is none."""
    records = written_on_retry(day)
    if not records:
        return []
    lines = ["## Written on retry", "",
             "Write-ups that failed on an earlier run and were written on this day from the "
             "transcript already on disk, with no new YouTube request. Each video's row in the "
             "table above says what it came to.", ""]
    for record in records:
        verdict, state_text, _tags = final_verdict(record)
        summary, backend = history_writeup(record.get("url"), day)
        seconds = record.get("summary_seconds")
        cost = f", {seconds:.1f}s" if isinstance(seconds, (int, float)) else ""
        lines.append(f"#### [{verdict}] {record.get('title') or record['id']} "
                     f"({record.get('channel') or 'channel unknown'}, "
                     f"{backend or 'backend not recorded'}{cost})")
        lines.append("")
        notes = []
        if record.get("read_day") and record["read_day"] != day:
            notes.append(f"first read on {record['read_day']}")
        # FIX 2: final_verdict's note already says which retry wrote it and why the
        # attempts before it failed, so it is used as it is rather than said twice.
        notes.append(state_text or f"written on retry {record.get('written_attempt')} of "
                                   f"{SUMMARY_MAX_ATTEMPTS}")
        lines.append(f"*{'; '.join(notes)}.*")
        lines.append("")
        lines.append(summary or "(the write-up is not in history.jsonl for this day)")
        lines.append("")
    return lines


# The day's scheduled starts, for someone who runs the tool on a schedule and has each start
# write its log to logs/hourly-<day>_<HHMM>.log in the data folder: a start that left no log
# did not run (the computer was off or asleep), and the digest says so. Empty, the default,
# means no schedule is known and the digest says nothing about one.
SCHEDULED_HOURS = ()
SLOT_SKIPS = (("skipping this slot", "the local model (Ollama) was not reachable"),
              ("slot skipped", "every transcript path was in a cooldown"))


def scheduled_runs_line(day, now=None):
    """One line about the day's scheduled starts, or "" before the first of them."""
    now = now or dt.datetime.now()
    try:
        date = dt.date.fromisoformat(day)
    except ValueError:
        return ""
    due = [h for h in SCHEDULED_HOURS
           if date < now.date() or (date == now.date() and h <= now.hour)]
    if not due:
        return ""
    logs = {}
    for path in sorted(LOGS_DIR.glob(f"hourly-{day}_*.log")) if LOGS_DIR.exists() else []:
        m = re.search(r"_(\d{2})(\d{2})\.log$", path.name)
        if m:
            logs.setdefault(int(m.group(1)), path)
    ran, missing, skipped = [], [], []
    for hour in due:
        path = logs.get(hour)
        if path is None:
            missing.append(f"{hour:02d}:00")
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        why = next((words for marker, words in SLOT_SKIPS if marker in text), None)
        (skipped.append(f"{hour:02d}:00 was skipped ({why})") if why
         else ran.append(f"{hour:02d}:00"))
    if not missing and not skipped:
        return f"Scheduled runs of this day: all {len(due)} started."
    parts = [f"Scheduled runs of this day: {len(ran)} of {len(due)} ran."]
    parts += [s + "." for s in skipped]
    if missing:
        parts.append(f"{', '.join(missing)} did not start (no log: the PC was off or asleep, or "
                     f"the task did not fire).")
    return " ".join(parts)


def context_age_line(day):
    """The first line of every digest: how old the context file is on the digest's day, said
    loudly past CONTEXT_STALE_DAYS, because every verdict below is judged against it."""
    try:
        updated = dt.date.fromtimestamp(CONTEXT_PATH.stat().st_mtime)
    except OSError:
        return "**The context file is missing, so nothing below was judged against it.**"
    days = (dt.date.fromisoformat(day) - updated).days
    if days < 0:
        return f"Context file last updated {updated:%d %B}, after this day."
    words = context_age_words(days)
    if days > CONTEXT_STALE_DAYS:
        return (f"**Context file last updated {words}: more than a week, so the verdicts below "
                f"may be judged against projects that have moved on. Update it.**")
    return f"Context file last updated {words}."


def digest_blocks(day, full=True):
    """(header, table, retry, footer): every regenerated block of one day's digest, from
    that day's records. Everything between them is the run sections, which are appended
    once and kept as they were written. `full` is the full digest, whose rows carry the
    opportunity screen's answer; the short digest passes False and leaves them out."""
    records = todays_day_records(day)
    # 2026-10-08 night, the ideas lane: a day with ideas opens with them, then the project tips;
    # a day with none is written exactly as before.
    ideas_today = ideas_today_lines(day)
    tips = (["## Project tips", ""] if ideas_today else []) + todays_header_lines(day)
    header = ("<!-- HEADER-START -->\n" + f"# Digest {day}\n\n" + context_age_line(day) + "\n\n"
              + "".join(line + "\n" for line in ideas_today)
              + "\n".join(tips) + "\n<!-- HEADER-END -->\n\n")
    # 2026-09-30: the opportunities in their own section, after the project items. It travels
    # with the header, so every caller of this function places it the same way.
    opps = opportunity_block_lines(day, records, full=full)
    if opps:
        header += "<!-- OPPS-START -->\n" + "\n".join(opps).rstrip("\n") + "\n<!-- OPPS-END -->\n\n"
    # 2026-10-08 night: the remix and every idea of the day, in the full digest only.
    idea_lines = every_idea_lines(day) if full else []
    if idea_lines:
        header += ("<!-- IDEAS-START -->\n" + "\n".join(idea_lines).rstrip("\n")
                   + "\n<!-- IDEAS-END -->\n\n")
    table_lines = day_table_lines(records, screen=full)
    slots = scheduled_runs_line(day)
    if slots:
        table_lines += [slots, ""]
    table = ("<!-- TABLE-START -->\n" + "\n".join(table_lines)
             + "\n<!-- TABLE-END -->\n\n")
    retry_lines = retry_block_lines(day)
    retry = ("<!-- RETRY-START -->\n" + "\n".join(retry_lines).rstrip("\n")
             + "\n<!-- RETRY-END -->\n\n") if retry_lines else ""
    # DECISION A, 2026-09-25: no first-guess footer. write_day_digest still strips an old one,
    # so a day regenerated today loses it; first_guess_counts stays for `worthwatch status`.
    footer = ""
    return header, table, retry, footer


def write_day_digest(day, run_section="", create=True):
    """Rewrite one day's digest: its regenerated blocks from the records, the run sections
    already in the file kept as they are, and `run_section` appended after them. A day with
    no file is only created when `create` says so. Returns the path, or None."""
    path = OUTPUT_DIR / f"DIGEST-{day}.md"
    if not path.exists() and not create:
        return None
    header, table, retry, footer = digest_blocks(day)
    body = ""
    if path.exists():
        body = path.read_text(encoding="utf-8")
        for block_re in (DIGEST_HEADER_RE, DIGEST_OPPS_RE, DIGEST_IDEAS_RE, DIGEST_TABLE_RE,
                         DIGEST_RETRY_RE, DIGEST_FOOTER_RE):
            body = block_re.sub("", body, count=1)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # 2026-09-26 night: whole or not at all. Half a digest keeps half a table for good, because
    # the next write replaces only the blocks it can find.
    atomic_write_text(path, header + table + retry + body + run_section + footer)
    write_short_digest(day)
    write_weekly_opportunities(day)
    return path


def short_digest_text(day):
    """Brief part 7, 2026-09-26 night: the header, the day's table, and each kept video's header
    line and "Try this week", nothing else, so one short file can be pasted. The page's hidden
    markers are left out; the full digest is untouched."""
    header, table, _retry, _footer = digest_blocks(day, full=False)
    records = todays_day_records(day)
    order = {r.get("id"): n for n, r in enumerate(records)}
    # 2026-09-30: an opportunity is kept in its own section, which the header block carries.
    keepers = sorted((r for r in records if "kept" in final_verdict(r)[2]
                      and "opportunity" not in final_verdict(r)[2]),
                     key=lambda r: header_sort_key(r, order))
    lines = [l for l in (header + table).splitlines() if not l.startswith("<!--")]
    lines += ["## Kept: each video's header line and its Try this week", ""]
    if not keepers:
        lines.append("Nothing was kept on this day.")
    for record in keepers:
        lines.append(header_line(record))
        lines.append(f"  Try this week: {one_line(record.get('try_this_week') or 'nothing')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_short_digest(day):
    """DIGEST-<day>-short.md beside the day's digest, written whole or not at all."""
    path = OUTPUT_DIR / f"DIGEST-{day}-short.md"
    atomic_write_text(path, short_digest_text(day))
    return path


# --- the opportunities section and the weekly file, 2026-09-30 --------------------------------
def screen_note(record):
    """What the opportunity screen answered for one record, as a short note for its row in the
    full digest, or "" for a record that was never screened. Jev answers with a score, which
    passes at JEV_SCREEN_THRESHOLD; when the local model answered instead, the note says so and
    what it said. Pure: it reads the record."""
    screen = record.get("opp_screen")
    if not screen:
        return ""
    if screen.get("model") == "jev" and isinstance(screen.get("p"), (int, float)):
        return f"Jev {screen['p']:.2f}"
    if screen.get("error"):
        return "the opportunity screen could not be asked"
    says = screen.get("says") or "nothing"
    if screen.get("jev_error"):
        return f"Jev did not answer; the local screen said {says}"
    return f"the local screen said {says}"


def opportunity_block_lines(day, records=None, full=True):
    """The day's Opportunities section, as lines, or [] for a day the lane never judged (every
    day before 30 September, so regenerating an old digest adds nothing to it). At most
    OPPORTUNITY_DAILY_CAP cards, in the order the day met them; a killed idea with new evidence is
    one line under them, marked as killed. `full` is the full digest, whose rows carry the
    screen's answer; the short digest's do not, and its sentence says where they are."""
    records = todays_day_records(day) if records is None else records
    lane = [r for r in records if r.get("opp_lane")]
    if not lane:
        return []
    shown = [r for r in lane if opportunity_state(r)[0]][:OPPORTUNITY_DAILY_CAP]
    killed = [r for r in lane if (r.get("opp_card") or {}).get("kind") == "killed"
              and (r.get("opp_card") or {}).get("evidence")]
    lines = ["## Opportunities today", ""]
    if not shown:
        passed = sum(1 for r in lane if screen_passes(r.get("opp_screen")))
        cards = sum(1 for r in lane if (r.get("opp_card") or {}).get("kind") == "card")
        # 2026-10-02: this used to end "each one's row in the table says why it is not an
        # opportunity", and on 1 October no row said anything about the screen. It now says
        # where the answers are, which differs by file: on each row of the full digest, and
        # not in the short one at all.
        if not full:
            where = "the screen's answer for each video is on its row in the full digest."
        elif any((r.get("opp_screen") or {}).get("model") == "jev" for r in lane):
            where = (f"each row of the table carries the screen's answer, and Jev's score "
                     f"passes at {JEV_SCREEN_THRESHOLD:g}.")
        else:
            where = "each row of the table carries the screen's answer."
        lines.append(f"None surfaced. Of the {len(lane)} video(s) read, {passed} passed the "
                     f"screen and {cards} got a card; {where}")
        lines.append("")
    for n, record in enumerate(shown, 1):
        card = record["opp_card"]
        what = _field_value(card.get("what"))
        lines.append(f"### {n}. {truncate_words(what, 30)}")
        lines.append(f"From [{short_title(record.get('title') or record.get('id'))}]"
                     f"({record.get('url') or ''}) ({record.get('channel') or 'unknown channel'})")
        lines.append("")
        lines += card_lines(card)
        lines.append("")
    for record in killed:
        card = record["opp_card"]
        lines.append(f"- Killed idea ({card.get('killed')}), new evidence: {card.get('evidence')} "
                     f"[{short_title(record.get('title') or record.get('id'))}]"
                     f"({record.get('url') or ''})")
    return lines


def week_monday(day):
    """The Monday of the week a day falls in, as a date."""
    d = dt.date.fromisoformat(day)
    return d - dt.timedelta(days=d.weekday())


def weekly_opportunities_path(day):
    """OPPORTUNITIES-WEEK-<Monday>.md in the output folder, resolved at call time so a suite
    that redirects OUTPUT_DIR redirects this with it."""
    return OUTPUT_DIR / f"OPPORTUNITIES-WEEK-{week_monday(day).isoformat()}.md"


def weekly_opportunities_text(day):
    """Every card surfaced in the week of `day`, by the day it was written, newest day first."""
    monday = week_monday(day)
    days = [(monday + dt.timedelta(days=n)).isoformat() for n in range(7)]
    by_day = {d: [] for d in days}
    for record in sorted(load_records().values(), key=lambda r: (r.get("created") or "",
                                                                  r.get("id") or "")):
        if record.get("opp_lane") and record.get("written_day") in by_day \
                and opportunity_state(record)[0]:
            by_day[record["written_day"]].append(record)
    lines = [f"# Opportunities, week of {monday:%d %B %Y}", "",
             f"Every opportunity card this tool surfaced from Monday {monday:%d %B} to Sunday "
             f"{monday + dt.timedelta(days=6):%d %B}, at most {OPPORTUNITY_DAILY_CAP} a day, by "
             f"the day it was written. Rewritten by worthwatch with every digest; each day's digest "
             f"carries the same cards.", ""]
    found = 0
    for d in reversed(days):
        if not by_day[d]:
            continue
        lines += [f"## {d}", ""]
        for record in by_day[d][:OPPORTUNITY_DAILY_CAP]:
            found += 1
            card = record["opp_card"]
            lines.append(f"### {truncate_words(_field_value(card.get('what')), 30)}")
            lines.append(f"From [{short_title(record.get('title') or record.get('id'))}]"
                         f"({record.get('url') or ''}) ({record.get('channel') or 'unknown channel'})")
            lines.append("")
            lines += card_lines(card)
            lines.append("")
    if not found:
        lines.append("Nothing surfaced yet this week.")
    return "\n".join(lines).rstrip() + "\n"


def write_weekly_opportunities(day):
    """The week's file, rewritten whole, but only for a day the lane judged or a week whose file
    already exists: regenerating a digest from before the lane writes no weekly file."""
    path = weekly_opportunities_path(day)
    lane_day = any(r.get("opp_lane") for r in todays_day_records(day))
    if not lane_day and not path.exists():
        return None
    atomic_write_text(path, weekly_opportunities_text(day))
    return path


def lane_display(vid, summary, settled, label_cap, fields, shown, notes):
    """(shown, notes): the verdict the day's table will show for a write-up the lane judged, and
    the note that says why it differs from the project rules' answer, for the run's log and
    history.jsonl."""
    lane_v, lane_text, _tags = lane_shown(vid, summary, settled, label_cap, fields)
    notes = list(notes)
    if lane_v == "OPPORTUNITY" and shown != "OPPORTUNITY":
        notes.append("its opportunity card passes all three tests")
    elif lane_v != shown and lane_text:
        notes.append(lane_text.split("; ")[-1])
    return lane_v, notes


def refresh_digest(day, create=False):
    """One day's digest regenerated from the records, adding no run section."""
    return write_day_digest(day, "", create=create)


def run_section_account(summaries, gave_up=()):
    """The top of a run section: how many write-ups this run wrote, with their verdicts, and
    then one line for each other place a video the run met went.

    2026-09-26 night, brief part 1.3. Every state here is the record's, through final_verdict,
    never the triage's verdict: on 26 September the 11:26 run's section opened "New videos: 3 -
    YES 3" and showed no write-up, because three videos read as YES were not written for want
    of time and the line counted what the triage said, not what the run wrote."""
    records = load_records()
    written_ids = [s["meta"].get("id") for s in summaries]
    counts = {v: 0 for v in VERDICTS}
    for s in summaries:
        record = records.get(s["meta"].get("id"))
        verdict = (final_verdict(record)[0] if record and record.get("reason_code") in REASON_CODES
                   else s["verdict"])
        counts[verdict] = counts.get(verdict, 0) + 1
    lines = [f"Written in this run: {len(summaries)}"
             + (" - " + ", ".join(f"{v} {counts.get(v, 0)}" for v in VERDICTS) if summaries else "")
             + "."]
    retry_later, retry_done, not_read, screened, dropped, waiting = [], [], [], [], [], []
    for vid in _THIS_RUN["met"]:
        record = records.get(vid)
        if vid in written_ids or not record or record.get("reason_code") not in REASON_CODES:
            continue
        verdict, text, tags = final_verdict(record)
        title = truncate_words(one_line(record.get("title") or vid), 60)
        entry = f"{title} ({text})" if text else title
        if "read_not_written" in tags:
            (retry_done if record.get("terminal") else retry_later).append(entry)
        elif "screened_out" in tags and "read_and_skipped" in tags:
            dropped.append(title)
        elif "screened_out" in tags:
            screened.append(title)
        elif "not_read" in tags:
            not_read.append(entry)
        elif "waiting" in tags:
            waiting.append(entry)
    if retry_later:
        lines.append(f"Read, not written in this run: {len(retry_later)}. Each waits in the retry "
                     f"queue with its transcript saved, and a later run writes it up with no new "
                     f"YouTube request, under \"Written on retry\" in that day's digest: "
                     + "; ".join(retry_later) + ".")
    if retry_done:
        lines.append(f"Read, not written, and no further attempt: {len(retry_done)}: "
                     + "; ".join(retry_done) + ".")
    if not_read:
        lines.append(f"Not read: {len(not_read)}: " + "; ".join(not_read) + ".")
    if dropped:
        lines.append(f"Read and dropped as NO, with no write-up: {len(dropped)} (named below).")
    if screened:
        lines.append(f"Screened out from the listing, never fetched: {len(screened)} (named below).")
    if waiting:
        lines.append(f"Left waiting, nothing spent on them in this run: {len(waiting)}: "
                     + "; ".join(waiting) + ".")
    if gave_up:
        lines.append(f"Gave up on captions: {len(gave_up)} (named below).")
    if _THIS_RUN["retry_written"]:
        lines.append(f"Also written in this run from transcripts already on disk, before its "
                     f"listings: {len(_THIS_RUN['retry_written'])} (under \"Written on retry\" "
                     f"above).")
    if _THIS_RUN.get("unreadable"):
        # 2026-09-26 night, brief part 2.7.
        lines.append(f"Lines of this run's listings that could not be read, skipped: "
                     f"{_THIS_RUN['unreadable']}.")
    if _THIS_RUN["notes"]:
        # 2026-09-26 night, brief part 2: what went wrong, said in the digest, not only the log.
        lines.append("What went wrong in this run: " + " ".join(_THIS_RUN["notes"]))
    return lines


def write_digest(pass_rows, no_entries, summaries, gave_up=None, label=None):
    gave_up = gave_up or []
    all_rows = pass_rows + no_entries
    day_records = todays_day_records()
    if not all_rows and not gave_up and not day_records and not _THIS_RUN["notes"]:
        print("\nNo new videos found.")
        return None
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = today_key()      # the run's day, brief part 2.12

    lines = [f"## Run at {dt.datetime.now().strftime('%H:%M')}" + (f" ({label})" if label else ""), ""]
    # 2026-09-26 night: what this run wrote, and where everything else it met went, from the
    # records. It replaces "New videos: N - YES a, ...", which counted the triage's verdicts.
    lines.extend(run_section_account(summaries, gave_up))
    lines.append("")

    # FIX 4: no per-run table any more. The day has ONE table, regenerated from the records
    # above the run sections, so a video read in one run and written in another is one row
    # rather than two, and so a row can change when the retry writes it up.

    if summaries:
        lines.append("\n### Summaries\n")
        for s in summaries:
            m = s["meta"]
            chunk_note = " [chunked]" if s["chunked"] else ""
            lines.append(f"#### [{s['verdict']}] {m['title']} ({m.get('channel', '')}, "
                          f"{s['backend']}, {s['elapsed']:.1f}s{chunk_note})\n")
            # Say why a summary went local; a Sonnet summary needs no note. DECISION A,
            # 2026-09-25: and no "the listing said" note, for the reason the table has none.
            notes = []
            if s.get("backend_why") and not writes_judgment(s["backend"]):
                notes.append(s["backend_why"])
            if notes:
                lines.append(f"*{'; '.join(notes)}.*\n")
            lines.append(s["summary"])
            lines.append("")

    if no_entries:
        # NO from the listing is never fetched: one line, the reason. NO after reading -
        # a fetched video the transcript triage found empty - gets the two-line entry: the
        # reason, and the local model's one sentence on the most useful thing it said.
        read = [r for r in no_entries if r.get("fetched")]
        if not read:
            note = "not fetched"
        else:
            note = (f"{len(no_entries) - len(read)} not fetched, {len(read)} read and then "
                    f"dropped")
        lines.append(f"\n### Screened out (NO) - {len(no_entries)}, {note}\n")
        for r in no_entries:
            # A NO from the listing is what decides that a video is never read, and it stays.
            # DECISION A, 2026-09-25: one that was read and then dropped says so, and no longer
            # what the listing said about it.
            tail = " (read, then dropped)" if r.get("fetched") else ""
            lines.append(f"- [{md_cell(r['title'])}]({r['url']}) ({md_cell(r['channel'])}) "
                          f"- {md_cell(r['reason'])}{tail}")
            if r.get("most_useful"):
                lines.append(f"  Most useful: {md_cell(r['most_useful'])}")

    if gave_up:
        lines.append(f"\n### Gave up on captions - {len(gave_up)}\n")
        lines.append("YouTube never finished generating captions after 3 attempts across separate runs:")
        for r in gave_up:
            lines.append(f"- [{md_cell(r['title'])}]({r['url']}) ({md_cell(r['channel'])})")

    # A run that added nothing still refreshes the header and the table, because something
    # else may have moved: a retry writing up yesterday's failure, or an age filter that
    # dropped results before any of them reached a triage. It appends no run section, so the
    # file gains nothing to read and stops being out of date.
    run_section = (("\n".join(lines) + "\n\n---\n\n")
                   if (all_rows or gave_up or _THIS_RUN["met"] or _THIS_RUN["notes"]) else "")

    # The header at the top of the file (rule 6, at most four lines), the day's table under
    # it, the "Written on retry" section when a retry wrote anything today, and the footer
    # at the end (fix 7), all rebuilt from the records on every write by write_day_digest,
    # which is also what regenerates a past day's digest (FIX 1, 2026-09-22). Opening the
    # digest tells you in thirty seconds what today is worth even after six runs have
    # appended to it, and a stale measurement never sits at the bottom of the file.
    path = write_day_digest(date_str, run_section)
    print(f"\nDigest: {path}" if run_section else f"\nNo new videos found; digest: {path}")
    return path


def verdict_counts(rows):
    counts = {v: 0 for v in VERDICTS}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    return counts


def skip_listing_without_the_model(mode, label):
    """True, having said so in the log and in the digest, when the local model is not
    answering: nothing listed could be triaged, so nothing is listed and no canary request is
    spent. 2026-09-26 night, brief part 2.1."""
    down = ollama_down()
    if not down:
        return False
    print(f"Skipping {mode}: the local model (Ollama) is not answering ({down[1]}), so nothing "
          f"listed could be triaged; nothing was listed or fetched.")
    write_digest([], [], [], label=label)
    write_last_run(mode.strip("-").split()[0], new_videos=0, yes=0, maybe=0, no=0, gave_up=0,
                   note=f"the local model (Ollama) did not answer: {down[1]}")
    return True


def run_favourites(favourites, days, context_text, model, local_only, seen):
    """The priority channels (FAVOURITE_CHANNELS), first in every --channels run. What earlier
    runs left queued for them, then each one's listing, fetched and
    read before any other channel's, from the day's whole room (favourite_budget), not the run's
    3. Returns (pass rows, NO entries, gave up, summaries, whether a 429 stopped it)."""
    budget = favourite_budget()
    keys = {channel_handle(c) for c in favourites}
    out = ([], [], [], [])
    rate_limited = False

    def add(result):
        for into, part in zip(out, result[:4]):
            into.extend(part)
        return result[4]

    due = [c for c in pending_due(seen) if channel_key(c) in keys]
    if due:
        print(f"\nQueued from earlier runs, priority channels: {len(due)} due today.")
        rate_limited = add(process_candidates(due, context_text, model, local_only, seen,
                                              budget=budget, source="pending"))
    for curl in favourites:
        if rate_limited:
            break
        print(f"\nFavourite channel, first in every run: {curl}")
        try:
            videos = list_channel_videos(curl)
        except RateLimitError as e:
            print(f"YouTube is rate-limiting requests (HTTP 429). Stopping here.\n  {e}")
            rate_limited = True
            break
        except ListingFailed as e:
            print(f"  ({e}; the next run lists it again)")
            note_problem(f"The listing of {curl.rsplit('/', 1)[-1]} failed "
                         f"({fetch_failure_words(fetch_failure_kind(str(e)), str(e))}); the next "
                         f"run lists it again.")
            continue
        videos = channel_videos_for_run(videos, curl, days, favourite=True)
        unseen = [v for v in videos if v["id"] not in seen and duration_allows(v)]
        print(f"  {len(videos)} video(s) in {f'last {days}d' if days else 'recent window'}, "
              f"{len(unseen)} not yet seen...")
        if unseen:
            enrich_with_feed(videos, unseen)
        rate_limited = add(process_candidates(videos, context_text, model, local_only, seen,
                                              sells_something=True, budget=budget,
                                              source="channels"))
        note_channel_listed(curl)
    return (*out, rate_limited)


def run_channels(context_text, days, model, local_only, skip_canary=False):
    if not CHANNELS_PATH.exists():
        print(f"ERROR: {CHANNELS_PATH} does not exist. Add channel URLs to it first.")
        sys.exit(1)
    channel_urls = read_channel_urls()
    if not channel_urls:
        print("No channels in channels.txt.")
        return

    # 2026-10-05: the favourite channels (FAVOURITE_CHANNELS) come first in every run, from the
    # day's whole room, and are not in the rotation below.
    favourites = favourite_channels(channel_urls)
    rotation = [c for c in channel_urls if c not in favourites]

    # Bail before the channel listings, not after: with no caption budget left there
    # is nothing this run could do with them, and each listing is its own request.
    budget = new_budget()
    if budget["remaining"] <= 0 and not (favourites and favourite_budget()["remaining"] > 0):
        report_daily_cap("--channels")
        write_last_run("channels", new_videos=0, yes=0, maybe=0, no=0, gave_up=0,
                       note="every transcript path had spent today's budget")
        return

    if skip_listing_without_the_model("--channels",
                                      "channels" + (f", last {days}d" if days else "")):
        return

    if not slot_may_proceed("--channels", skip_canary):
        write_last_run("channels", new_videos=0, yes=0, maybe=0, no=0, gave_up=0,
                       note=f"slot gate: {slot_plan()['why']}")
        return

    seen = load_seen()
    all_pass, all_no, all_gave_up, all_summaries = [], [], [], []
    rate_limited = False
    if favourites:
        f_pass, f_no, f_gave_up, f_summaries, rate_limited = run_favourites(
            favourites, days, context_text, model, local_only, seen)
        all_pass.extend(f_pass)
        all_no.extend(f_no)
        all_gave_up.extend(f_gave_up)
        all_summaries.extend(f_summaries)
        budget = new_budget()       # what they read is the day's, and the held-back room moved

    # What this run is allowed to spend, recorded before anything spends it, so the
    # backlog gate below can tell "this run found nothing new to fetch" apart from
    # "this run started with nothing left in the day's budget".
    budget_at_start = budget["remaining"]
    # The previous pass finished and has already had its backlog shot; start the next.
    if day_scan_complete(rotation):
        start_new_pass(rotation)
    slot = channels_for_this_slot(rotation) if rotation else []
    slots_per_pass = (len(rotation) + CHANNELS_PER_SLOT - 1) // CHANNELS_PER_SLOT
    _, already = rotation_state(rotation)
    print(f"Channel rotation: "
          f"{', '.join(c.rsplit('/', 1)[-1] for c in slot) or 'nothing left today'} "
          f"({len(already)}/{len(rotation)} covered today; a full pass is "
          f"{slots_per_pass} slots)"
          + (f", and {', '.join(c.rsplit('/', 1)[-1] for c in favourites)} in every run."
             if favourites else "."))
    if rate_limited:
        slot = []
    elif budget["remaining"] <= 0:
        print("No room left in this run for the queue or the rotation: the rest of the day's cap "
              "is held back for the priority channels.")
        slot = []

    # Videos an earlier run left pending (a refused caption request, or captions that were
    # not ready yet) are retried from seen.json itself, so a retry never depends on the
    # video still being in some listing. Same budget, YES first, never before retry_after.
    # 2026-10-05: a favourite channel's were tried above, in run_favourites.
    keys = {channel_handle(c) for c in favourites}
    due = ([] if rate_limited or budget["remaining"] <= 0
           else [c for c in pending_due(seen) if channel_key(c) not in keys])
    if due:
        print(f"\nPending from earlier runs: {len(due)} due today.")
        p_pass, p_no, p_gave_up, p_summaries, rate_limited = process_candidates(
            due, context_text, model, local_only, seen, budget=budget, source="pending")
        all_pass.extend(p_pass)
        all_no.extend(p_no)
        all_gave_up.extend(p_gave_up)
        all_summaries.extend(p_summaries)
        if rate_limited:
            slot = []

    for curl in slot:
        print(f"\nChannel: {curl}")
        try:
            videos = list_channel_videos(curl)
        except RateLimitError as e:
            print(f"YouTube is rate-limiting requests (HTTP 429). Stopping here.\n  {e}")
            rate_limited = True
            break
        except ListingFailed as e:
            # 2026-09-26 night, brief part 2.5: that channel's failure, not the run's, and not
            # marked as looked at, so the next slot lists it again.
            print(f"  ({e}; the channel is not marked as looked at, so the next slot lists it "
                  f"again)")
            note_problem(f"The listing of {curl.rsplit('/', 1)[-1]} failed "
                         f"({fetch_failure_words(fetch_failure_kind(str(e)), str(e))}); the "
                         f"channel was not marked as looked at, so the next slot lists it again.")
            continue
        videos = channel_videos_for_run(videos, curl, days)
        window_note = f"last {days}d" if days else "recent window"
        unseen = [v for v in videos if v["id"] not in seen and duration_allows(v)]
        print(f"  {len(videos)} video(s) in {window_note}, {len(unseen)} not yet seen...")
        if unseen:
            enrich_with_feed(videos, unseen)

        pass_rows, no_entries, gave_up, summaries, rate_limited = process_candidates(
            videos, context_text, model, local_only, seen, sells_something=True,
            budget=budget, source="channels")
        all_pass.extend(pass_rows)
        all_no.extend(no_entries)
        all_gave_up.extend(gave_up)
        all_summaries.extend(summaries)

        if rate_limited:
            break
        if budget["remaining"] <= 0:
            print("This run's transcript cap is reached; the rest of the rotation "
                  "rolls to the next slot.")
            break

        # Marked only once the channel is genuinely finished - past the 429 break and
        # past the budget break. A channel abandoned mid-list must stay uncovered so the
        # next slot resumes on it: its leftovers were never written to seen.json, and
        # `--days 2` will have aged them out of the listing by the time the rotation
        # comes round again, so marking it here would lose them silently.
        # The imprecision is deliberate and cheap: a channel that spent its last fetch on
        # its last video is also left unmarked and costs one extra listing next slot,
        # where it finds nothing new and is then marked.
        mark_channel_scanned(rotation, curl)

    # The 2026 catch-up is the last thing in the tool allowed to spend anything, and only
    # when the day has nothing fresher to offer: every channel covered today, and this
    # run's budget still whole because none of them had a new video worth fetching.
    if not rate_limited and budget["remaining"] > 0:
        if budget["remaining"] < budget_at_start:
            print("\n2026 backlog: skipped, this run spent its budget on new uploads.")
        elif rotation and not day_scan_complete(rotation):
            _, done = rotation_state(rotation)
            print(f"\n2026 backlog: skipped, {len(rotation) - len(done)} channel(s) "
                  f"still uncovered today.")
        else:
            b_pass, b_no, b_gave_up, b_summaries = run_backlog_phase(
                channel_urls, context_text, model, local_only, seen, budget)
            all_pass.extend(b_pass)
            all_no.extend(b_no)
            all_gave_up.extend(b_gave_up)
            all_summaries.extend(b_summaries)

    write_digest(all_pass, all_no, all_summaries, gave_up=all_gave_up,
                 label="channels" + (f", last {days}d" if days else ""))
    c = verdict_counts(all_pass + all_no)
    write_last_run("channels", new_videos=len(all_pass) + len(all_no), yes=c["YES"],
                    opportunity=c["OPPORTUNITY"], maybe=c["MAYBE"], no=c["NO"],
                    gave_up=len(all_gave_up))


def run_backlog_phase(channel_urls, context_text, model, local_only, seen, budget):
    """One channel's 2026 catch-up, and only one: whatever budget is left goes to the
    first channel in channels.txt that isn't backfilled yet, so the backlog finishes a
    channel at a time instead of leaving eleven of them all half-done."""
    backlog = load_backlog()
    target = next((c for c in channel_urls if not backlog.get(c, {}).get("backfilled")), None)
    if target is None:
        print("\n2026 backlog: nothing left, every channel is backfilled.")
        return [], [], [], []

    print(f"\n2026 backlog: the day's pass is complete and found nothing new to fetch, "
          f"so catching up {target.rsplit('/', 1)[-1]}.")
    try:
        videos, hit_fetch_limit = backlog_candidates(target)
    except RateLimitError as e:
        print(f"YouTube is rate-limiting requests (HTTP 429). Stopping here.\n  {e}")
        return [], [], [], []

    channel_name = next((v.get("channel") for v in videos if v.get("channel")), None) or target
    for v in videos:
        v["channel"] = channel_name
    unseen_videos = [v for v in videos if v["id"] not in seen and duration_allows(v)]
    window = "2026" if BACKLOG_MAX_AGE_DAYS is None else f"last {BACKLOG_MAX_AGE_DAYS}d"
    print(f"  {len(videos)} {window} video(s), {len(unseen_videos)} not yet seen, "
          f"{budget['remaining']} caption fetch(es) available.")
    if unseen_videos:
        enrich_with_feed(videos, unseen_videos)   # covers only the last 15 uploads; the rest triage on title

    pass_rows, no_entries, gave_up, summaries, _ = process_candidates(
        videos, context_text, model, local_only, seen, sells_something=True,
        budget=budget, source="backlog")
    update_backlog_progress(target, videos, hit_fetch_limit, seen)
    return pass_rows, no_entries, gave_up, summaries


def run_search(query, context_text, model, local_only, count=15, skip_canary=False):
    budget = new_budget()
    if budget["remaining"] <= 0:
        report_daily_cap(f'--search "{query}"')
        write_last_run("search", new_videos=0, yes=0, maybe=0, no=0, gave_up=0,
                       note="every transcript path had spent today's budget")
        return
    if skip_listing_without_the_model(f'--search "{query}"', f'search "{query}"'):
        return
    if not slot_may_proceed(f'--search "{query}"', skip_canary):
        write_last_run("search", new_videos=0, yes=0, maybe=0, no=0, gave_up=0,
                       note=f"slot gate: {slot_plan()['why']}")
        return
    seen = load_seen()
    print(f"\nSearching: {query!r}")
    try:
        videos = search_videos(query, count=count)
    except RateLimitError as e:
        print(f"YouTube is rate-limiting requests (HTTP 429). Stopping here.\n  {e}")
        return
    videos, _stale, _undated = drop_stale_search_results(videos, label=f"search {query!r}",
                                                         seen=seen)
    print(f"  {len(videos)} result(s), checking against seen.json...")
    pass_rows, no_entries, gave_up, summaries, _ = process_candidates(
        videos, context_text, model, local_only, seen, budget=budget, source="search")
    write_digest(pass_rows, no_entries, summaries, gave_up=gave_up, label=f'search "{query}"')
    c = verdict_counts(pass_rows + no_entries)
    write_last_run("search", new_videos=len(pass_rows) + len(no_entries), yes=c["YES"],
                    opportunity=c["OPPORTUNITY"], maybe=c["MAYBE"], no=c["NO"],
                    gave_up=len(gave_up))


def run_topics(context_text, model, local_only, count=10, skip_canary=False):
    if not TOPICS_PATH.exists():
        print(f"ERROR: {TOPICS_PATH} does not exist. Add search queries to it first.")
        sys.exit(1)
    entries = [parse_topic_line(l) for l in TOPICS_PATH.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.strip().startswith("#")]
    queries = [q for q, _needs in entries]
    needs_of = dict(entries)
    if not queries:
        print("No queries in topics.txt.")
        return

    if new_budget()["remaining"] <= 0:
        report_daily_cap("--topics")
        write_last_run("topics", new_videos=0, yes=0, maybe=0, no=0, gave_up=0,
                       note="every transcript path had spent today's budget")
        return

    already_today = todays_source_count("topics")
    remaining_cap = TOPICS_DAILY_CAP - already_today
    if remaining_cap <= 0:
        print(f"Daily topics cap ({TOPICS_DAILY_CAP}) already reached today ({already_today} processed). Skipping.")
        write_last_run("topics", new_videos=0, yes=0, maybe=0, no=0, gave_up=0, note="daily cap already reached")
        return
    print(f"Topics daily cap: {already_today}/{TOPICS_DAILY_CAP} used today, {remaining_cap} left.")

    if skip_listing_without_the_model("--topics", "topics"):
        return

    if not slot_may_proceed("--topics", skip_canary):
        write_last_run("topics", new_videos=0, yes=0, maybe=0, no=0, gave_up=0,
                       note=f"slot gate: {slot_plan()['why']}")
        return

    seen = load_seen()
    budget = {"remaining": min(new_budget()["remaining"], remaining_cap)}
    all_pass, all_no, all_gave_up, all_summaries = [], [], [], []
    skipped_old = 0
    # Which query goes first. With twelve questions and three fetches a run, the first
    # query in the file took every fetch of every run and the other queries were never
    # searched (measured 2026-09-07: one query, both runs, all day). Each run now starts
    # one past the last query the previous run searched; the cursor lives in rotation.json
    # beside the channel cursor. A 429 leaves it where it was, because a refusal is a fact
    # about the address, not about the query.
    start = int(load_rotation().get("topics_index", 0)) % len(queries)
    order = queries[start:] + queries[:start]
    if start:
        print(f"Topics rotation: starting at query {start + 1} of {len(queries)}.")
    for n, q in enumerate(order):
        if budget["remaining"] <= 0:
            print("Daily topics cap reached; remaining queries roll to the next run.")
            break
        print(f"\nTopic: {q!r}")
        try:
            videos = search_videos(q, count=count)
        except RateLimitError as e:
            print(f"YouTube is rate-limiting requests (HTTP 429). Stopping here.\n  {e}")
            break
        except ListingFailed as e:
            # 2026-09-26 night, brief part 2.5: that search's failure, not the run's; the
            # cursor does not move past it, so it is searched again.
            print(f"  ({e}; searched again by a later run)")
            note_problem(f"The search {q!r} failed "
                         f"({fetch_failure_words(fetch_failure_kind(str(e)), str(e))}); a later "
                         f"run searches it again.")
            continue
        note_topic_searched((start + n + 1) % len(queries))
        # Rule 4 runs here, between the listing and the triage, which is before any
        # transcript is fetched and before a single model call is spent on the video.
        videos, stale, _undated = drop_stale_search_results(videos, label=f"topic {q!r}",
                                                            seen=seen)
        skipped_old += len(stale)
        # FIX 3, 2026-09-25: then the topic filter, still before any triage or model call.
        videos, _off = drop_off_topic(videos, needs_of.get(q, ()), label=f"topic {q!r}",
                                      seen=seen)
        print(f"  {len(videos)} result(s), checking against seen.json...")
        pass_rows, no_entries, gave_up, summaries, rate_limited = process_candidates(
            videos, context_text, model, local_only, seen, budget=budget, source="topics")
        all_pass.extend(pass_rows)
        all_no.extend(no_entries)
        all_gave_up.extend(gave_up)
        all_summaries.extend(summaries)
        if rate_limited:
            break
        polite_delay(2.5)

    if skipped_old:
        print(f"Age filter: {skipped_old} result(s) skipped today as older than "
              f"{TOPIC_MAX_AGE_DAYS // 365} years, before any transcript was fetched.")
    write_digest(all_pass, all_no, all_summaries, gave_up=all_gave_up, label="topics")
    c = verdict_counts(all_pass + all_no)
    write_last_run("topics", new_videos=len(all_pass) + len(all_no), yes=c["YES"],
                    opportunity=c["OPPORTUNITY"], maybe=c["MAYBE"], no=c["NO"],
                    gave_up=len(all_gave_up), skipped_too_old=skipped_old)


WORKING_NOW_INSTRUCTIONS = """You are grouping {n} opportunity lines into one table for {reader}. Each line is one video's record of a way people are getting paid: what works, for whom, and whether the video showed evidence for it or only claimed it. Group lines that describe the SAME way of getting paid - the same mechanism for the same kind of customer - into one row; a line that matches nothing else is its own row. Do not add anything the lines do not say, and never invent a line.

=== OPPORTUNITY LINES ({n}) ===
{lines}
=== END OPPORTUNITY LINES ===

Reply with exactly this markdown table and nothing else - no preamble, no closing note - one row per distinct way of getting paid, the rows with the most lines first:

| # | What is working | For whom | Which |
|---|---|---|---|

Column rules: "What is working" is one concrete line in your words, not a topic label. "For whom" is who pays or who does it. "Which" is every O-number in the row, e.g. "O2, O5". Every O-number from O1 to O{n} appears in exactly one row."""

WORKING_NOW_TABLE_HEADER = "| # | What is working | For whom | Which |\n|---|---|---|---|"
O_NUMBER_RE = re.compile(r"\bO(\d+)\b")


def working_now_table(cutoff):
    """The weekly's table of what is working for people right now, built from
    OPPORTUNITIES.md. Sonnet groups the lines into rows; the counts - how many videos, how
    many this week, how many showed evidence - are computed here from the O-numbers each
    row cites, never taken from the model. Returns (lines, repeated), where repeated is
    [(what, videos, shown)] for every row that has appeared three or more times."""
    rows = opportunities_recorded()
    if not rows:
        return ["No OPPORTUNITY video recorded yet, so there is nothing to tabulate."], []
    cutoff_str = cutoff.isoformat()
    if len(rows) == 1:
        body = f"| 1 | {md_cell(rows[0]['works'])} | {md_cell(rows[0]['for'])} | O1 |"
        return enforce_working_now_table(WORKING_NOW_TABLE_HEADER + "\n" + body, rows, cutoff_str)
    numbered = [f"O{i} ({r['date']}): works: {r['works']} | for: {r['for']} | "
                f"evidence: {r['evidence']} | video: {r['title']}" for i, r in enumerate(rows, 1)]
    prompt = WORKING_NOW_INSTRUCTIONS.format(reader=READER, n=len(rows),
                                             lines="\n".join(numbered))
    print(f"  Asking the writing brain ({write_label()}) to group {len(rows)} opportunity line(s)...")
    try:
        body = normalize_punct(write_generate(prompt, timeout=900).strip())
    except RuntimeError as e:
        return [f"> **TABLE NOT BUILT.** the writing brain could not be asked ({e}); the "
                f"{len(rows)} opportunity line(s) are in OPPORTUNITIES.md, ungrouped."], []
    return enforce_working_now_table(body, rows, cutoff_str)


def enforce_working_now_table(body, rows, cutoff_str):
    """Re-render the grouped table with the counts computed from the cited O-numbers:
    Videos, This week, and Evidence as "X of Y" - bold, and FASHION when X is 0, exactly
    as the roll-up's consensus table does. A line the model left out becomes its own row,
    so nothing recorded can vanish; a row that cites no line is dropped, and said so."""
    header_cells, data = None, []
    for line in body.splitlines():
        cells = table_cells(line)
        if cells is None or is_separator_row(cells):
            continue
        if header_cells is None:
            header_cells = [c.lower() for c in cells]
            continue
        data.append(cells)
    header_cells = header_cells or []
    which_idx = next((j for j, c in enumerate(header_cells) if "which" in c), None)
    what_idx = next((j for j, c in enumerate(header_cells) if "working" in c), 1)
    for_idx = next((j for j, c in enumerate(header_cells) if "whom" in c), 2)
    n = len(rows)
    grouped, assigned, dropped = [], set(), 0
    for cells in data:
        if which_idx is None or which_idx >= len(cells):
            dropped += 1
            continue
        ids = sorted({int(x) for x in O_NUMBER_RE.findall(cells[which_idx]) if 1 <= int(x) <= n})
        ids = [i for i in ids if i not in assigned]
        if not ids:
            dropped += 1
            continue
        assigned.update(ids)
        grouped.append({"what": cells[what_idx] if what_idx < len(cells) else "UNKNOWN",
                        "for": cells[for_idx] if for_idx < len(cells) else "UNKNOWN",
                        "ids": ids})
    left_out = [i for i in range(1, n + 1) if i not in assigned]
    for i in left_out:
        grouped.append({"what": rows[i - 1]["works"], "for": rows[i - 1]["for"], "ids": [i]})

    def counts(ids):
        week = sum(1 for i in ids if rows[i - 1]["date"] >= cutoff_str)
        shown = sum(1 for i in ids if rows[i - 1]["evidence"].lower().startswith("shown"))
        return len(ids), week, shown

    grouped.sort(key=lambda g: (-len(g["ids"]), -counts(g["ids"])[1]))
    out = ["| # | What is working | For whom | Videos | This week | Evidence | Which |",
           "|---|---|---|---|---|---|---|"]
    zero, repeated = 0, []
    for k, g in enumerate(grouped, 1):
        videos, week, shown = counts(g["ids"])
        evidence = f"**{shown} of {videos}**" if shown else f"**0 of {videos} - FASHION**"
        zero += 0 if shown else 1
        if videos >= 3:
            repeated.append((g["what"], videos, shown))
        out.append(f"| {k} | {md_cell(g['what'])} | {md_cell(g['for'])} | {videos} | {week} | "
                   f"{evidence} | " + ", ".join(f"O{i}" for i in g["ids"]) + " |")
    banner = [f"> **Read the Evidence column first.** {zero} of {len(grouped)} rows here were "
              f"evidenced by nobody; a way of getting paid that is only ever said is a fashion, "
              f"not a finding. Grouped by claude, counted here from the {n} line(s) in "
              f"OPPORTUNITIES.md; O-numbers are line order, oldest first."]
    if left_out:
        banner.append(f"> {len(left_out)} line(s) the grouping left out were added as their own "
                      f"rows: " + ", ".join(f"O{i}" for i in left_out) + ".")
    if dropped:
        banner.append(f"> {dropped} row(s) the model wrote cited no line and were dropped.")
    return banner + [""] + out, repeated


def run_weekly():
    cutoff = dt.date.today() - dt.timedelta(days=WEEK_LOOKBACK_DAYS)
    entries = load_history_since(cutoff)

    yes = [e for e in entries if e["verdict"] == "YES"]
    opportunity = [e for e in entries if e["verdict"] == "OPPORTUNITY"]
    maybe = [e for e in entries if e["verdict"] == "MAYBE"]
    no_count = sum(1 for e in entries if e["verdict"] == "NO")

    date_str = dt.date.today().isoformat()
    lines = [f"# Week of {date_str}", "",
             f"{len(entries)} new videos this week: {len(yes)} YES, {len(opportunity)} OPPORTUNITY, "
             f"{len(maybe)} MAYBE, {no_count} NO.", ""]
    # 2026-10-08 night, the ideas lane: the week's top ideas and remixes before everything else.
    lines += week_board_lines(date_str)

    lines.append("## YES, in full\n" if yes else "## YES: none this week.\n")
    for e in yes:
        lines.append(f"### {e['title']} ({e['channel']})\n")
        lines.append(e.get("summary") or f"(summary unavailable: {e.get('reason', '')})")
        lines.append("")

    # An OPPORTUNITY video is one line here - the Opportunity line its summary wrote - and
    # its full summary stays in its own file. The table under them is what they add up to.
    lines.append("## OPPORTUNITY, one line each\n" if opportunity
                 else "## OPPORTUNITY: none this week.\n")
    for e in opportunity:
        parsed = parse_opportunity_line(e.get("summary"))
        detail = (f"works: {parsed['works']} | for: {parsed['for']} | evidence: {parsed['evidence']}"
                  if parsed else e.get("reason", ""))
        lines.append(f"- {e['title']} ({e['channel']}): {detail}")
    if opportunity:
        lines.append("")

    lines.append("## What is working for people right now\n")
    table, repeated = working_now_table(cutoff)
    lines.extend(table)
    lines.append("")
    if repeated:
        lines.append("Appeared three or more times: " + "; ".join(
            f"{what} ({videos} videos, {shown} of {videos} evidenced)"
            for what, videos, shown in repeated) + ".")
    else:
        lines.append("Nothing has appeared three or more times yet.")
    lines.append("")

    lines.append("## MAYBE, one line each\n" if maybe else "## MAYBE: none this week.\n")
    for e in maybe:
        top = extract_top_takeaway(e["summary"]) if e.get("summary") else None
        lines.append(f"- {e['title']} ({e['channel']}): {top or e.get('reason', '')}")
    if maybe:
        lines.append("")

    lines.append(f"## NO: {no_count} videos screened out, none summarized.\n")

    cutoff_str = cutoff.isoformat()
    try_lines = []
    if TRYNEXT_PATH.exists():
        for line in TRYNEXT_PATH.read_text(encoding="utf-8").splitlines():
            m = re.match(r"-\s*(\d{4}-\d{2}-\d{2})", line)
            if m and m.group(1) >= cutoff_str:
                try_lines.append(line)
    lines.append("## Try-next gained this week\n" if try_lines else "## Try-next gained this week: nothing new.")
    lines.extend(try_lines)

    content = "\n".join(lines).rstrip() + "\n"
    word_count = len(content.split())
    if word_count > 800:
        print(f"NOTE: weekly file is {word_count} words, over the 800-word target. Nothing was cut.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"WEEK-{date_str}.md"
    atomic_write_text(path, content)
    print(f"\nWeekly: {path} ({word_count} words)")
    write_last_run("weekly", videos=len(entries), yes=len(yes), opportunity=len(opportunity),
                    maybe=len(maybe), no=no_count, word_count=word_count)
    return path


# --- roll-up ----------------------------------------------------------------

ROLLUP_MAX_WORDS = 2500
ROLLUP_MAX_INPUT_CHARS = 600_000
SUMMARY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")

ROLLUP_INSTRUCTIONS = """You are writing ONE roll-up across {n} video summaries for {reader}, the person the CONTEXT below describes, who is the one building and the one reading this. They cannot read {n} separate summaries and will not paste them anywhere. This single file replaces them, so it has to carry everything those summaries agree on, disagree on, and left out.

Everything you write must come from the summaries below or from CONTEXT. Never add outside knowledge, never invent a number, a person, or a video.

Each summary was written from one video's transcript in eight numbered sections. Section 3 of each, "Evidenced versus asserted", is your ground truth for whether something was SHOWN (on screen, sourced, a real artifact) or only SAID. That distinction is the point of this file: use section 3, not the video's own confidence.

=== CONTEXT ===
{context}
=== END CONTEXT ===

=== VIDEO SUMMARIES ({n}) ===
{summaries}
=== END VIDEO SUMMARIES ===

Write the roll-up in under {max_words} words, plain prose and markdown tables, no preamble and no closing note, in exactly this order with exactly these headings:

## 1. Consensus
Every claim or method that appears in MORE THAN ONE video. One row per claim. A claim only one video makes does not belong in this table at all.

| # | Claim or method | Videos | Evidence | Which videos |
|---|---|---|---|---|

Column rules, followed exactly:
- "Claim or method": one line, concrete, in your words, not a topic label. "Post short-form video daily on three platforms" is a claim; "content marketing" is not.
- "Videos": how many videos make the claim. A bare number.
- "Evidence": how many of THOSE videos showed evidence for it rather than only asserting it. Write it exactly as "X of Y", where Y is the number in the Videos column. Count a video toward X only if its section 3 lists the supporting number, screenshot, dashboard or artifact as shown or sourced. If none did, write "0 of Y".
- "Which videos": V-numbers only, e.g. "V3, V7, V12".
Sort rows by the Videos column descending; break ties by the evidenced count descending.

Then one line under the table: "Highest-consensus claim nobody evidenced: ..." naming the worst offender and its counts, or "None: every repeated claim was evidenced at least once."

## 2. Contradictions
### Videos against each other
Bullets. Each names the V-numbers on each side and what exactly they disagree about. Prefer disagreements about a mechanism or a number over disagreements of tone.
### Videos against CONTEXT.md
Bullets. Each quotes the line, decision or belief from CONTEXT being contradicted, then names the V-numbers and what they say instead. Pay attention to CONTEXT's "Decisions already made" and "Beliefs a video should test me on".
If either subsection has nothing, say so in one line rather than padding it.

## 3. The ten worth reading in full
Exactly ten, numbered, or all of them if the batch has fewer than ten. Each entry is the video title with its V-number, then exactly two lines: line one, what this video has that no other video in the batch has; line two, why that matters for what CONTEXT says they are doing right now. Prefer videos carrying evidence over videos carrying only claims, and say when you are picking one despite thin evidence.

## 4. What applies to me
A ranked list, most useful first, judged against CONTEXT: their projects ({project_list}), how they work, and this tool. Each item: one line saying the concrete thing to do, then "Best argued by: V<n>, <title>", then one clause saying whether that video showed it or only said it. Anything a decision in CONTEXT's "Decisions already made" forbids goes in the list too, marked "RULED OUT by <the decision>", so it is not silently dropped.

## 5. What nobody said
Bullets. Things you would expect somebody in a batch of {n} videos like this to have raised, and no video did. Each bullet: the missing thing, then one clause on why its absence matters given CONTEXT. Ground every bullet in what this specific batch is about; do not list generic omissions that would fit any batch of videos.

## 6. The founders themselves
One line per distinct person in the batch, not one per video: if someone appears in several videos, merge them into a single line. Format each line exactly:
- **Name** (V-numbers) - age or UNKNOWN - what they built - SHOWN: <what was actually evidenced> - CLAIMED ONLY: <what was asserted with no proof>
Ages and revenue figures come from the summaries only; write UNKNOWN rather than guess.

Rules: write UNKNOWN rather than guess; keep every claim a claim and never restate one as settled fact; never invent a video, a person or a number; quote at most one sentence from any one summary."""

ROLLUP_TRIM_INSTRUCTIONS = """This roll-up is {actual} words and must come in under {target}. Cut it down.

Keep: every row of the section 1 table with its counts untouched, every heading, and the six-section structure. Cut prose, not evidence: tighten section 3's two-line entries and section 4's items, drop the least useful bullets from sections 2 and 5, never drop a table row and never drop a person from section 6.

Reply with the trimmed roll-up and nothing else.

=== ROLL-UP ===
{text}
=== END ROLL-UP ==="""


def load_summary_files():
    """Every per-video summary file save_writeup has written. INDEX,
    DIGEST, WEEK, ROLLUP, TRY-NEXT and CHANNEL-CANDIDATES are not video
    summaries and are excluded by the date-prefixed filename shape."""
    if not OUTPUT_DIR.exists():
        return []
    items = []
    for path in sorted(OUTPUT_DIR.glob("*.md")):
        if not SUMMARY_FILE_RE.match(path.name):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        source = re.search(r"^Source:\s*(\S+)", text, re.MULTILINE)
        if not source:
            continue
        title = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        uploader = re.search(r"^Uploader:\s*(.+)$", text, re.MULTILINE)
        date = re.search(r"^Date:\s*(\d{4}-\d{2}-\d{2})", text, re.MULTILINE)
        body = text.split("\n---\n", 1)[-1].strip()
        items.append({
            "path": path, "url": source.group(1), "id": video_id_from_url(source.group(1)),
            "title": title.group(1).strip() if title else path.stem,
            "uploader": uploader.group(1).strip() if uploader else "UNKNOWN",
            "date": date.group(1) if date else path.name[:10],
            "body": body, "mtime": path.stat().st_mtime,
        })
    return items


def select_rollup_summaries(url_file=None, channel=None, days=None):
    """Pick the batch. --file picks exactly the URLs of a --file run (and reports
    which of them have no summary yet); --from-channel picks one channel's;
    --days narrows by summary date. Returns (items, requested, missing)."""
    items = load_summary_files()

    # Same video summarized twice (a --file run redoing what --channels already
    # did) would double-count every claim it makes. Keep the newest file only.
    by_id, loose = {}, []
    for it in items:
        if it["id"]:
            kept = by_id.get(it["id"])
            if kept is None or it["mtime"] > kept["mtime"]:
                by_id[it["id"]] = it
        else:
            loose.append(it)
    items = sorted(list(by_id.values()) + loose, key=lambda x: (x["date"], x["title"]))

    requested, missing = None, []
    if url_file:
        if not Path(url_file).exists():
            print(f"ERROR: {url_file} does not exist.")
            sys.exit(1)
        wanted = []
        for line in Path(url_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                wanted.append(video_id_from_url(line) or line)
        requested = len(wanted)
        picked = []
        for w in wanted:
            if w in by_id:
                picked.append(by_id[w])
            else:
                missing.append(w)
        items = picked

    if channel:
        needle = channel.lower()
        items = [it for it in items if needle in it["uploader"].lower()]
    if days is not None:
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        items = [it for it in items if it["date"] >= cutoff]

    return items, requested, missing


def table_cells(line):
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")) or len(s) < 2:
        return None
    return [c.strip() for c in s[1:-1].split("|")]


def is_separator_row(cells):
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells)


EVIDENCE_CELL_RE = re.compile(r"(\d+)\s*(?:of|/)\s*(\d+)")


def enforce_evidence_column(text):
    """The evidence count is the column that matters, so it is not left to the
    model's discipline. Every data row of section 1's table is re-rendered here:
    a zero-evidence row is labelled FASHION in bold, and the totals are counted
    and stated in a banner above the table. If not one row can be parsed, the
    banner says so loudly instead of passing quietly."""
    lines = text.splitlines()
    unverified = [
        "> **EVIDENCE COLUMN NOT VERIFIED.** The consensus table did not come back in the expected",
        "> shape, so nothing below counted its evidence for you. Read every count in it as the",
        "> model's word, unchecked.", "",
    ]
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s*1\b", l.strip())), None)
    if start is None:
        # No section 1 to check at all: say so at the top rather than silently
        # returning a file that looks verified.
        return "\n".join(unverified + lines), (0, 0)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")),
               len(lines))

    header_seen, evidence_idx = False, None
    rows = zero = 0
    for i in range(start, end):
        cells = table_cells(lines[i])
        if cells is None or is_separator_row(cells):
            continue
        if not header_seen:
            header_seen = True
            evidence_idx = next((j for j, c in enumerate(cells) if "evidence" in c.lower()), None)
            continue
        if evidence_idx is None or evidence_idx >= len(cells):
            continue
        m = EVIDENCE_CELL_RE.search(cells[evidence_idx])
        if not m:
            continue
        rows += 1
        shown, total = int(m.group(1)), int(m.group(2))
        if shown == 0:
            zero += 1
            cells[evidence_idx] = f"**0 of {total} - FASHION**"
        else:
            cells[evidence_idx] = f"**{shown} of {total}**"
        lines[i] = "| " + " | ".join(cells) + " |"

    if rows == 0:
        banner = unverified
    else:
        banner = [
            f"> **Read the Evidence column first.** {zero} of {rows} repeated claims here were evidenced",
            "> by nobody. A claim made in twelve videos and shown in none is a fashion, not a finding.", "",
        ]
    lines[start + 1:start + 1] = [""] + banner
    return "\n".join(lines), (rows, zero)


def run_rollup(name, context_text, url_file=None, channel=None, days=None):
    items, requested, missing = select_rollup_summaries(url_file, channel, days)
    if not items:
        print(f"No summaries in {OUTPUT_DIR} matched that selection. Nothing written.")
        return None
    if len(items) < 2:
        print(f"Only {len(items)} summary matched; a roll-up needs at least two to find "
              f"anything they agree on. Nothing written.")
        return None

    scope = []
    if url_file:
        scope.append(f"the {requested} URL(s) in {Path(url_file).name}")
    if channel:
        scope.append(f"channel matching {channel!r}")
    if days is not None:
        scope.append(f"summaries from the last {days} day(s)")
    scope_text = ", ".join(scope) or "every summary in the output folder"

    print(f"Roll-up {name!r}: {len(items)} summary file(s) from {scope_text}.")
    if requested is not None:
        print(f"  {len(items)} of {requested} in the batch have a summary; {len(missing)} do not yet.")

    blocks = []
    for i, it in enumerate(items, 1):
        blocks.append(f"[V{i}] {it['title']}\nCHANNEL: {it['uploader']}\n"
                      f"DATE: {it['date']}\nURL: {it['url']}\n\n{it['body']}")
    joined = ("\n\n" + "=" * 60 + "\n\n").join(blocks)
    if len(joined) > ROLLUP_MAX_INPUT_CHARS:
        per = ROLLUP_MAX_INPUT_CHARS // len(items)
        joined = ("\n\n" + "=" * 60 + "\n\n").join(b[:per] + "\n[...trimmed...]" for b in blocks)
        print(f"  (batch was larger than {ROLLUP_MAX_INPUT_CHARS} chars; each summary trimmed to {per})")

    appendix = ["", "---", "", "## Videos in this roll-up", "",
                "| V | Title | Channel | Link |", "|---|---|---|---|"]
    for i, it in enumerate(items, 1):
        appendix.append(f"| V{i} | {md_cell(it['title'])} | {md_cell(it['uploader'])} | "
                        f"[watch]({it['url']}) |")
    if missing:
        appendix.append("")
        appendix.append(f"{len(missing)} video(s) in the batch have no summary yet and are not "
                        f"represented above: {', '.join(missing)}")
    appendix_text = "\n".join(appendix)

    reserve = len(appendix_text.split()) + 120
    prompt = ROLLUP_INSTRUCTIONS.format(
        reader=READER, project_list=project_list_words(),
        n=len(items), context=context_text, summaries=joined,
        max_words=max(600, ROLLUP_MAX_WORDS - reserve),
    )
    print(f"  Asking the writing brain ({write_label()}) over {len(prompt):,} characters. This "
          f"takes a few minutes...")
    t0 = time.monotonic()
    body = normalize_punct(write_generate(prompt, timeout=1800).strip())
    elapsed = time.monotonic() - t0

    target = ROLLUP_MAX_WORDS - reserve
    actual = len(body.split())
    if actual > target:
        print(f"  ({actual} words, over the {target}-word budget; one trim pass...)")
        try:
            trimmed = normalize_punct(write_generate(
                ROLLUP_TRIM_INSTRUCTIONS.format(actual=actual, target=target, text=body),
                timeout=1200).strip())
            if trimmed:
                body = trimmed
        except RuntimeError as e:
            print(f"  (trim pass failed, keeping the long version: {e})")

    body, (rows, zero) = enforce_evidence_column(body)

    date_str = dt.date.today().isoformat()
    header = [f"# Roll-up: {name}", "",
              f"{len(items)} video summaries, rolled up {date_str} through claude:sonnet "
              f"({elapsed:.0f}s). Source: {scope_text}."]
    if requested is not None:
        header.append(f"The batch asked for {requested} videos; {len(items)} of them have a summary "
                      f"so far, {len(missing)} do not.")
    header += ["", "---", ""]

    content = "\n".join(header) + body.strip() + "\n" + appendix_text + "\n"
    word_count = len(content.split())

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"ROLLUP-{slugify(name, 40)}-{date_str}.md"
    atomic_write_text(path, content)

    print(f"\nRoll-up: {path} ({word_count} words)")
    if rows:
        print(f"  Consensus table: {rows} repeated claim(s), {zero} of them evidenced by nobody.")
    else:
        print("  WARNING: the consensus table could not be parsed, so its evidence counts are "
              "unverified. The file says so at the top of section 1.")
    if word_count > ROLLUP_MAX_WORDS:
        print(f"  NOTE: {word_count} words, over the {ROLLUP_MAX_WORDS}-word target. Nothing was cut.")
    write_last_run("rollup", note=f"{name}: {len(items)} summaries, {word_count} words, "
                                   f"{zero}/{rows} claims evidenced by nobody")
    return path


def estimate_cadence(recent_videos):
    dates = []
    for v in recent_videos:
        ud = v.get("upload_date")
        if ud:
            try:
                dates.append(dt.datetime.strptime(ud, "%Y%m%d").date())
            except ValueError:
                pass
    if len(dates) < 2:
        return "UNKNOWN"
    dates.sort(reverse=True)
    span_days = (dates[0] - dates[-1]).days
    if span_days <= 0:
        return "multiple/day"
    per_week = (len(dates) - 1) / span_days * 7
    return f"~{per_week:.1f}/week"


FIT_INSTRUCTIONS = """Judge this YouTube channel's fit against the CONTEXT below.

=== CONTEXT ===
{context}
=== END CONTEXT ===

CHANNEL: {name}
ABOUT: {about}
RECENT TITLES:
{titles}

In one line, under 25 words: what does this channel sell (course/coaching/community/product/nothing), and how well its content fits what CONTEXT says {reader} cares about.

A channel of founder interviews or case studies that give real numbers (revenue, prices, customers, costs) is high fit, in any field.

Judge fit on whether the MECHANISM or SKILL this channel teaches is usable by {reader}, not on whether the channel itself, its presenter, or its own product obeys the rules in CONTEXT. A channel about SEO, distribution, or engineering technique can be high fit even though its presenter works differently - that is normal for a general-education channel and is not a mark against it. CONTEXT's rules are about what {reader} may do, not about what a channel they merely watch must do; only mark fit down for a rule conflict if actually following the channel's core teaching would require them to break one.

Format exactly: SELLS: <...> | FIT: <high|medium|low> - <why, under 20 words>"""


def judge_channel_fit(context_text, info, recent, model):
    titles_text = "\n".join(f"- {v['title']}" for v in recent[:3]) or "(none found)"
    prompt = FIT_INSTRUCTIONS.format(reader=READER, context=context_text, name=info["name"],
                                      about=(info["description"] or "")[:400], titles=titles_text)
    try:
        response = quick_generate(prompt, model=model, num_ctx=NUM_CTX, timeout=90,
                                  num_predict=150)
    except RuntimeError as e:
        return f"SELLS: UNKNOWN | FIT: low - could not judge ({e})"
    return response.strip().splitlines()[0]


def run_discover(context_text, model, skip_canary=False):
    if not slot_may_proceed("--discover", skip_canary):
        write_last_run("discover", candidates_found=0,
                       note=f"slot gate: {slot_plan()['why']}")
        return
    candidates = list(KNOWN_DISCOVER_CANDIDATES)
    # 2026-10-08 night: a channel already in channels.txt is not checked again; each check is two
    # requests, and the six known ones are all in it by now.
    seen_urls = set(read_channel_urls())

    for topic in DISCOVER_TOPICS:
        print(f"Searching topic: {topic!r}")
        try:
            found = search_videos(topic, count=8)
        except RateLimitError as e:
            print(f"YouTube is rate-limiting requests (HTTP 429). Stopping discovery search.\n  {e}")
            break
        for v in found:
            curl = v.get("channel_url")
            if curl and curl not in candidates:
                candidates.append(curl)
        polite_delay(2)

    results = []
    for curl in candidates:
        if curl in seen_urls:
            continue
        seen_urls.add(curl)
        print(f"Checking channel: {curl}")
        try:
            info = get_channel_info(curl)
            polite_delay()
            recent = list_channel_videos(curl, max_items=5)
        except RateLimitError as e:
            print(f"YouTube is rate-limiting requests (HTTP 429). Stopping here.\n  {e}")
            break
        if not info:
            print("  could not resolve, skipping")
            continue
        cadence = estimate_cadence(recent)
        fit_response = judge_channel_fit(context_text, info, recent, model)
        print(f"  {info['name']}: {fit_response}")
        results.append({
            "url": curl, "name": info["name"], "followers": info["followers"],
            "cadence": cadence, "titles": [v["title"] for v in recent[:3]],
            "fit_response": fit_response,
        })
        polite_delay(1.5)

    def fit_rank(r):
        m = re.search(r"FIT:\s*(high|medium|low)", r["fit_response"], re.IGNORECASE)
        order = {"high": 0, "medium": 1, "low": 2}
        return order.get(m.group(1).lower() if m else "low", 2)
    results.sort(key=fit_rank)
    results = results[:10]

    write_channel_candidates(results)
    write_last_run("discover", candidates_found=len(results))


def write_channel_candidates(results):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = dt.date.today().isoformat()
    lines = ["# Channel candidates", "",
             f"Generated {date_str} by `worthwatch --discover`. Never auto-added to channels.txt; "
             f"add a line there yourself if you want one.", ""]
    lines.append("| Rank | Channel | Subscribers | Cadence | Sells / fit | Recent titles |")
    lines.append("|---|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        followers = f"{r['followers']:,}" if r["followers"] else "UNKNOWN"
        titles = md_cell("; ".join(r["titles"]) or "(none found)")
        lines.append(f"| {i} | [{md_cell(r['name'])}]({r['url']}) | {followers} | {r['cadence']} | "
                      f"{md_cell(r['fit_response'])} | {titles} |")
    atomic_write_text(CANDIDATES_PATH, "\n".join(lines) + "\n")
    print(f"\nCandidates: {CANDIDATES_PATH}")
    return CANDIDATES_PATH


# --- what a summary is allowed to say ---------------------------------------
# Section 6 is the section this whole tool exists for, and it is the easiest one to fake.
# Given a rich context file and a video about nothing in particular, a model will cheerfully
# write a plan for one of your projects and attribute it to a video that never said a word
# about it: the actions come out of the context file, not out of any video. So the section
# has to quote the video, and the quotes are checked here rather than trusted. Prompting
# alone is not a guard.
#
# The quote check is not enough on its own either. A quote can be perfectly real and still
# be bent onto a project it has nothing to do with - "the younger you are, the easier it is"
# filed under how you work, or a line about mentors filed under a person's name, which is not
# a project. So four more things are enforced here, not just asked for:
#
#   the closed list  an item names exactly one project from PROJECTS. Zero is not an item
#                    (a quote with no project is a quote, not a finding); two or more is
#                    not an item either, because a line that applies to four projects has
#                    not been mapped to any of them.
#   nothing          if no item survives, the whole section is the word "nothing". Not
#                    "Most applicable: nothing" over a paragraph explaining the absence:
#                    an explanation of why there is nothing reads like a finding, and such
#                    a paragraph tends to quote the context file back as though the video
#                    had said it.
#   the video only   quotes are checked across the whole summary, not just section 6's.
#                    Quote marks mean the video said those words. A quote that is not in
#                    the transcript loses its marks and the removal is counted. Sections 3,
#                    7 and 8 are exempt and QUOTE_EXEMPT_SECTIONS says why each one is.
#   no thinking      output carrying "Actually", "re-reading", "let me", "I should" or a
#                    paragraph working out whether an item qualifies is a defect. It is
#                    re-asked once, and if the second answer has the same shape the
#                    judgment sections are "nothing".

APPLIES_HEADING_RE = re.compile(r"^##\s*6\b")
BELIEFS_HEADING_RE = re.compile(r"^##\s*7\b")
TRY_HEADING_RE = re.compile(r"^##\s*8\b")
MOST_APPLICABLE_RE = re.compile(r"^(\s*(?:[-*]\s*)?(?:\*\*)?Most applicable:(?:\*\*)?\s*)(.*)$",
                                re.IGNORECASE)
IGNORE_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?Ignore:", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s*#")
MOST_APPLICABLE_PREFIX = "Most applicable: "
# The bullet, number or ordinal a ranked item carries. Stripped when the item is read, so
# rebuilding the section cannot produce "- - The skill-audit prompt..." and promoting the
# first survivor cannot produce "Most applicable: - Second: the Manage panel...".
ITEM_MARKER_RE = re.compile(r"^\s*(?:[-*]\s*|\d+[.)]\s*)*(?:(?:also|second|third|next|finally)\s*:\s*)?",
                            re.IGNORECASE)

# The closed list, and the only thing an item may say it applies to. It is closed on
# purpose: "Applies to: <a person's name>" is what a model writes when it has a real quote,
# no real mapping, and a context file full of proper nouns to reach for.
#
# It is your projects (the [you] projects setting), the label for how you work, this tool,
# and a road not taken; apply_profile() sets all three of these from settings.toml. An item
# filed under a project that is not on the list is an item filed under nothing.
PROJECTS = ("how I work", "this tool", "a road not taken")
# Longest alias first so a long label is not read as a shorter one inside it; both
# canonicalise to the same name anyway, and the set dedupes. Every label is its own alias,
# plus the [you] aliases setting.
PROJECT_ALIASES = (("a road not taken", "a road not taken"), ("road not taken", "a road not taken"),
                   ("how i work", "how I work"), ("this tool", "this tool"))
# Names that live in your context file and are therefore the ones a model reaches for when
# it wants a proper noun (the [you] people setting, lower case). Reported separately only so
# the banner can say what went wrong.
PEOPLE_IN_CONTEXT = ()
APPLIES_LABEL_RE = re.compile(r"applies to:\s*(.*)$", re.IGNORECASE)

# Section 7, when there is nothing. One line, these words, and no paragraph explaining
# that a cooking video does not contradict a belief about pricing software.
NO_CONTRADICTION_LINE = "nothing in this video touches the beliefs list"
# A section 7 that says any of these is announcing that there is no contradiction, however
# many sentences it takes to do it.
DENIAL_RE = re.compile(
    r"(does not contradict|doesn't contradict|do not contradict|don't contradict"
    r"|not contradict any|nothing (?:here |in this video )?(?:directly )?contradicts"
    r"|no (?:direct )?contradictions?\b|nothing directly contradicts|nothing contradicts"
    r"|does not (?:test|address|touch|challenge)|doesn't (?:test|address|touch|challenge)"
    r"|is irrelevant|are irrelevant|none of (?:your|my|the) beliefs"
    r"|it does not (?:contradict|conflict)|no beliefs? (?:are|is) contradicted"
    r"|nothing (?:in (?:it|this video) )?pushes against|never touches|never addresses)",
    re.IGNORECASE)
# The other half of a denial: the section simply opens by saying there is none.
OPENS_WITH_NOTHING_RE = re.compile(
    r"\s*(nothing\b|no (?:direct )?contradictions?\b|none of\b|not applicable\b|no beliefs?\b)",
    re.IGNORECASE)
# A body that is the word "nothing" and no more: what is left after this substitution is
# empty only for "Nothing", "None.", "N/A", "nothing at all" and the like.
BARE_NOTHING_RE = re.compile(r"^\s*(?:nothing|none|n/?a|not applicable)\b(?:\s+at all)?[\s.:,;!-]*$",
                             re.IGNORECASE)
# ...unless it also names one. A section that opens "nothing contradicts" and then names a
# specific thing the video said that pulls the other way has done its job; it is the
# section that only explains the absence that rule 1.1 is about. These are the phrases the
# write-ups that already work use to name it ("an analytics tool being recommended as a
# standard sits against your analytics-is-a-deliberate-decision rule").
#
# Two phrases are deliberately NOT here. "challenges the belief", because a local model used
# it to challenge a belief that is not on the list. And "contradicts the belief", because it
# is the tail of "Nothing here contradicts the beliefs list" - the assumption that these
# phrases cannot survive a "not" in front of them is true of the rest and false of that one,
# which would quietly rescue sections that say nothing.
AFFIRMATIVE_RE = re.compile(
    r"(in tension with|sits against|pushes against the belief|cuts against|runs counter"
    r"|pulls against"
    r"|closest tension|closest friction|one real friction|friction point"
    r"|tension worth|worth flagging|one tension|one real conflict"
    r"|this is the contradiction)", re.IGNORECASE)

# The Opportunity line belongs to an OPPORTUNITY video and nowhere else. A MAYBE that writes
# "works: N/A | for: N/A" has a line with an empty field, which is worse than no line,
# because append_opportunity() says UNKNOWN honestly when the line is absent.
OPPORTUNITY_LINE_RE_ANY = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?Opportunity line:", re.IGNORECASE)
EMPTY_FIELD_RE = re.compile(r"^\s*(?:\*\*)?(n/?a|unknown|none|tbd|not applicable|-)\s*(?:\*\*)?\s*$",
                            re.IGNORECASE)

# Thinking aloud. Sentence-initial markers are matched case-sensitively on the capital,
# because "actually" is an ordinary adverb in the middle of a sentence and "Actually," at
# the head of one is the model talking to itself.
THINKING_ALOUD_OPENERS = re.compile(
    r"(?:^|(?<=[.!?])\s|(?<=[.!?]\s)\s*)(Actually|Wait|Hmm|Hold on|On reflection|"
    r"On second thought|Re-reading|Rereading|Looking back at|Let me|First, let)\b")
THINKING_ALOUD_ANY = re.compile(
    r"\b(re-?reading the (?:rules|instructions|context)|let me (?:re-?read|check|think|see)"
    r"|i should (?:not )?(?:write|include|say|check)|i need to (?:check|find|re-?read)"
    r"|there are no items in this section|every potential takeaway"
    r"|cannot be anchored|can't be anchored|per the rules above|the rules say"
    r"|the instructions? (?:say|above|require)|does not belong here because"
    r"|which does ?n[o']?t specifically apply)", re.IGNORECASE)

REASK_NOTE = """IMPORTANT, the previous attempt at this summary was rejected. It thought aloud in the output: it wrote its own reasoning about whether an item qualified instead of writing only the finished answer. Write the summary again from the same transcript.

Sections 6, 7 and 8 each contain EITHER finished items OR the single word "nothing" - never a paragraph about what you considered and rejected, never "Actually", never "re-reading", never "let me", never "I should". If nothing qualifies, the section is the word "nothing" and stops there."""


def section_bounds(lines, heading_re):
    """(start, end) line indices for one numbered heading, end exclusive, or None."""
    start = next((i for i, l in enumerate(lines) if heading_re.match(l.strip())), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("## ")),
               len(lines))
    return start, end


def canon_tokens(text):
    """Lowercase alphanumeric words, nothing else. Auto-generated captions carry no
    reliable punctuation or capitalisation and clean_vtt leaves HTML entities behind, so a
    quote can only honestly be compared on its words."""
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text.lower())
    return re.findall(r"[a-z0-9]+", text)


def transcript_index(transcript_text):
    """(haystack, bigrams) - what quote_supported_by needs, built once per summary."""
    words = canon_tokens(transcript_text)
    return " " + " ".join(words) + " ", {(words[i], words[i + 1]) for i in range(len(words) - 1)}


def document_index(transcript_text, meta=None):
    """The index the whole-summary quote check uses: the transcript plus the video's own
    title and uploader. Section 1 has to be able to name the channel - "Dr. G's Lifestyle
    Medicine Network" is a true quotation of something the video never says out loud - and
    deleting section 1 to protect section 6 would be a worse guard than none. Section 6
    still checks against the transcript alone."""
    extra = " ".join(str((meta or {}).get(k) or "") for k in ("title", "uploader", "channel"))
    return transcript_index(transcript_text + " " + extra)


def quote_supported_by(quote, transcript_words, transcript_bigrams):
    """Is this quote really in the transcript? Exact match on the canonical word string
    first. Then one tolerant pass, because a model quoting a caption tends to repair its
    grammar in passing: a quote whose consecutive word-pairs are nearly all present in the
    transcript is still the video's own words. Below that it is an invention."""
    words = canon_tokens(quote)
    if len(words) < 3:
        return False       # two words prove nothing; they would match almost any video
    if " " + " ".join(words) + " " in transcript_words:
        return True
    pairs = [(words[i], words[i + 1]) for i in range(len(words) - 1)]
    return bool(pairs) and sum(1 for pr in pairs if pr in transcript_bigrams) / len(pairs) >= 0.8


def quoted_spans(text):
    """Every double-quoted span on this line, read as pairs.

    This was a regex until 2026-09-10, and the regex was wrong in a way that only showed
    up once the quote check was run over whole summaries: `"([^"\\n]{8,200})"` on the line
    `"Brick" (the phone-locking tool he uses daily). "Zebra calendar" (his term...)` skips
    "Brick" for being under eight characters and then matches from Brick's CLOSING quote
    to Zebra's OPENING one, so the tool's own prose - never spoken by anyone - was handed
    to the transcript check as a quotation and of course failed. Quotes come in pairs;
    splitting on the quote character is the only reading that cannot straddle two of them.
    An unterminated final quote is dropped, because there is no span to check."""
    parts = text.split('"')
    if len(parts) < 3:
        return []
    spans = parts[1::2]
    if len(parts) % 2 == 0:        # odd number of quote marks: the last one opens nothing
        spans = spans[:-1]
    return [s for s in spans if len(s) <= 400]


def checkable_quotes(text):
    """The quoted spans long enough to prove anything. A two-word quote is not evidence
    and is not a forgery either, so it is neither kept nor struck on."""
    return [q for q in quoted_spans(text) if len(canon_tokens(q)) >= 3]


def line_quotes_the_video(line, haystack, bigrams):
    """True, False, or None when the line quotes nothing checkable."""
    quotes = checkable_quotes(line)
    if not quotes:
        return None
    return any(quote_supported_by(q, haystack, bigrams) for q in quotes)


def unquoted(text):
    """The line with its quoted spans removed. Everything that looks for the model's own
    voice has to look here: a video that says "let me show you" is not thinking aloud."""
    return re.sub(r'"[^"\n]*"', " ", text)


def projects_named(text):
    """Which projects from the closed list this item says it applies to, as a set, read
    ONLY from the text after "Applies to:".

    Scanning the whole item is wrong: an item that names one project and mentions another in
    passing would be read as two labels and struck for applying to two things. An item with
    no label has not named a project at all, which is what test 2 says, so it comes back
    empty."""
    m = APPLIES_LABEL_RE.search(text)
    if not m:
        return set()
    scope = m.group(1).lower()
    return {canonical for alias, canonical in PROJECT_ALIASES if alias in scope}


def people_named(text):
    m = APPLIES_LABEL_RE.search(text)
    scope = (m.group(1) if m else text).lower()
    return {p for p in PEOPLE_IN_CONTEXT if re.search(rf"\b{re.escape(p)}\b", scope)}


def thinking_aloud_lines(summary_text):
    """Line numbers (0-based) where the model talked to itself instead of answering.
    Quoted spans are removed first, and headings are exempt."""
    out = []
    for i, raw in enumerate(summary_text.splitlines()):
        if HEADING_RE.match(raw) or not raw.strip():
            continue
        probe = unquoted(raw)
        if THINKING_ALOUD_OPENERS.search(probe) or THINKING_ALOUD_ANY.search(probe):
            out.append(i)
    return out


# Which sections a quotation mark is a claim about the video in. Three are exempt, each
# for a reason that was measured over the 70 Sonnet summaries already on disk rather than
# assumed:
#   3  is where on-screen numbers are reported, and text on screen is not in the
#      transcript by definition. Every one of the five section 3 lines this rule would
#      have touched was a real figure read off a real dashboard.
#   7  is checked against CONTEXT's beliefs, so quoting one of my beliefs is the correct
#      way to write it, not a forgery.
#   8  is my action, not the video's words; Sonnet uses quote marks there for placeholders
#      ("X parent links sent by people I actually know") and for my own open decisions.
# 6 is exempt here only because it has a harder rule of its own a few functions down.
QUOTE_EXEMPT_SECTIONS = (r"^##\s*3\b", r"^##\s*6\b", r"^##\s*7\b", r"^##\s*8\b")


def enforce_quotes_are_the_video(summary_text, haystack, bigrams):
    """Rule 1.4. A quoted span is a claim that the video said those words, and CONTEXT.md
    is the most convincing thing a model has to hand when the video gave it nothing. So
    every quote outside the exempt sections is checked, and one that is not in the
    transcript loses its quote marks. The words stay - they may well be a fair paraphrase,
    and deleting a whole section 1 to punish one loose phrase would be a worse guard than
    none - but the claim that the video said them does not, and the banner counts it, so
    the removal is visible rather than a quiet laundering. Returns (text, n)."""
    lines = summary_text.splitlines()
    exempt = set()
    for pattern in QUOTE_EXEMPT_SECTIONS:
        bounds = section_bounds(lines, re.compile(pattern))
        if bounds:
            exempt.update(range(bounds[0], bounds[1]))
    unquoted_count = 0
    for i, raw in enumerate(lines):
        if i in exempt or HEADING_RE.match(raw) or not raw.strip():
            continue
        bad = [q for q in checkable_quotes(raw) if not quote_supported_by(q, haystack, bigrams)]
        for span in bad:
            lines[i] = lines[i].replace(f'"{span}"', span.strip())
            unquoted_count += 1
    if not unquoted_count:
        return summary_text, 0
    banner = (f"> **{unquoted_count} phrase(s) below lost their quote marks: the transcript "
              f"was searched and the video does not say them in those words.**\n")
    return banner + "\n".join(lines), unquoted_count


def tidy_blanks(lines):
    """A deleted line leaves its blank behind, and two blanks in a row read as a gap where
    something used to be - exactly the wrong impression to give."""
    out = []
    for line in lines:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    return out


def enforce_applies_citations(summary_text, transcript_text, haystack=None, bigrams=None):
    """Rebuild section 6 out of the items that survive all three tests: the quote is in the
    transcript, the item names exactly one project from the closed list, and - the part
    only the prompt can carry - the quote means something to a stranger told the project
    and nothing else. What is left is the surviving items, then at most one Ignore line.
    Any other prose in the section is gone: a paragraph that is neither an item nor an
    instruction to ignore something is the model reasoning at me.

    If nothing survives, the section becomes the word "nothing". Returns
    (text, (kept, struck, checked)); checked says whether there was a section 6 to check at
    all, which is not the same as one that came back empty, and section 8 needs the
    difference."""
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, APPLIES_HEADING_RE)
    if bounds is None:
        # No section 6 to check. Say so in the file rather than returning something that
        # looks checked: a guard that cannot measure has to fail loudly.
        return ("> **SECTION 6 NOT VERIFIED.** The summary came back without a "
                "\"What applies to me\" heading, so nothing in it was checked against the "
                "transcript. Read any advice below as the model's word.\n\n" + summary_text,
                (0, 0, False))
    start, end = bounds
    if haystack is None:
        haystack, bigrams = transcript_index(transcript_text)

    no_quote = off_list = many_projects = persons = unquoted_in_items = 0
    items, ignore_line = [], None
    for i in range(start + 1, end):
        raw = lines[i]
        line = raw.strip()
        if not line or HEADING_RE.match(raw):
            continue
        if IGNORE_LINE_RE.match(raw):
            # One Ignore line, the first; anything after it is prose, and prose after an
            # Ignore line is where the 2026-09-10 "Actually, re-reading the rules" paragraph
            # hid - the old guard stopped checking the moment it saw the word Ignore.
            # It is quote-checked like an item: the worst single failure in those digests
            # was a belief out of CONTEXT.md quoted back inside this line as though the
            # video had said it.
            if ignore_line is None and line_quotes_the_video(raw, haystack, bigrams) is not False:
                ignore_line = raw.rstrip()
            continue
        m = MOST_APPLICABLE_RE.match(raw)
        content = ITEM_MARKER_RE.sub("", m.group(2) if m else line)
        if is_nothing_useful(content):
            continue                      # "nothing" carries no item and needs no counting

        quotes = quoted_spans(content)
        supported = [q for q in quotes if quote_supported_by(q, haystack, bigrams)]
        if not supported:
            no_quote += 1
            continue
        # One real quote is what makes it an item; it does not license the others. An item
        # can carry a real quote from the video AND a sentence lifted out of CONTEXT.md
        # under a "Quote:" label, which is the 2026-09-10 failure with a fig leaf on. The
        # forged ones lose their marks the same way they do everywhere else in the summary,
        # rather than the whole item being struck: measured over the 40 Sonnet summaries
        # written under this prompt, striking the item would have cost nine real findings
        # to catch four false attributions, and all four read correctly without the marks.
        for span in quotes:
            if span not in supported:
                content = content.replace(f'"{span}"', span.strip())
                unquoted_in_items += 1
        named = projects_named(content)
        if not named:
            off_list += 1
            if people_named(content):
                persons += 1
            continue
        if len(named) > 1:
            many_projects += 1
            continue
        items.append((bool(m), content))

    # The model's own pick leads if it survived; otherwise the first survivor is promoted
    # into that place, because the line the digest and INDEX.md read has to be an item that
    # passed, not a pointer at one that did not.
    items = [c for is_most, c in items if is_most] + [c for is_most, c in items if not is_most]
    struck = no_quote + off_list + many_projects
    kept = len(items)

    header = []
    if struck:
        why = []
        if no_quote:
            why.append(f"{no_quote} quoted nothing the video said")
        if off_list:
            named_note = f", {persons} of them a person's name" if persons else ""
            why.append(f"{off_list} named no project from the list{named_note}")
        if many_projects:
            why.append(f"{many_projects} named more than one project, which is no mapping at all")
        header = ["", f"> **{struck} item(s) removed here: " + "; ".join(why) + ".**", ""]
    if unquoted_in_items and kept:   # nothing "below" to point at when the section is empty
        header += ["", f"> **{unquoted_in_items} phrase(s) in the item(s) below lost their "
                       f"quote marks: the video does not say them in those words.**", ""]

    if kept:
        # Whichever item survived first becomes the Most applicable line, even if the
        # model's own choice was the one struck: the line the digest and INDEX.md read has
        # to be an item that passed, not a leftover pointing at one that did not.
        body = [MOST_APPLICABLE_PREFIX + items[0]]
        body += [f"- {it}" for it in items[1:]]
        if ignore_line:
            body += ["", ignore_line]
    else:
        # Nothing is an answer, and it is the whole answer: no explanation of the absence,
        # no Ignore line reasoning about what was nearly relevant.
        body = ["nothing"]

    lines[start + 1:end] = header + [""] + body + [""]
    return "\n".join(tidy_blanks(lines)), (kept, struck, True)


def enforce_contradictions(summary_text):
    """Section 7 says what the video pushed against, or it says one line. A paragraph
    explaining that a cooking video does not contradict a belief about pricing software is
    not a finding; it is the model filling a heading. Returns
    (text, collapsed)."""
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, BELIEFS_HEADING_RE)
    if bounds is None:
        return summary_text, False
    start, end = bounds
    body = "\n".join(lines[start + 1:end]).strip()
    if not body:
        collapse = True
    elif not BARE_NOTHING_RE.sub("", body).strip():
        # The body is the word "nothing" and no more. This was a length bound first and
        # that was wrong: is_nothing_useful() only looks at how a string STARTS, almost
        # every good section 7 here opens with the word "Nothing" and then names a real
        # tension, and any bound loose enough to catch "None." also caught "Nothing here
        # contradicts outright, but it pulls against distribution-is-the-hard-part."
        collapse = True
    elif APPLIES_LABEL_RE.search(body):
        # An "Applies to:" label means this is a section 6 or section 8 item that landed
        # under the wrong heading - measured 2026-09-10, when the local model put a
        # strength-training action here and left section 8 off the page entirely. It is
        # not a contradiction whatever else it is, and section 6 has already had its say.
        collapse = True
    else:
        denial = bool(DENIAL_RE.search(body)) or bool(OPENS_WITH_NOTHING_RE.match(body))
        collapse = denial and not AFFIRMATIVE_RE.search(body)
    if not collapse:
        return summary_text, False
    already = body.strip().lower().rstrip(".") == NO_CONTRADICTION_LINE
    lines[start + 1:end] = ["", NO_CONTRADICTION_LINE, ""]
    return "\n".join(tidy_blanks(lines)), not already


def enforce_opportunity_line(summary_text, verdict):
    """The Opportunity line is written for an OPPORTUNITY video and for nothing else, and
    never with an empty field in it. Dropping it is not a loss: append_opportunity() falls
    back to section 2 and says UNKNOWN, which is true, where "works: N/A" is just noise in
    OPPORTUNITIES.md and in the weekly table built from it. Returns (text, dropped_why)."""
    lines = summary_text.splitlines()
    for i, raw in enumerate(lines):
        if not OPPORTUNITY_LINE_RE_ANY.match(raw):
            continue
        parsed = parse_opportunity_line(raw)
        if verdict and verdict != "OPPORTUNITY":
            why = f"the verdict is {verdict}, not OPPORTUNITY"
        elif parsed is None:
            why = "it did not have the three fields"
        elif any(EMPTY_FIELD_RE.match(parsed[k]) for k in ("works", "for", "evidence")):
            why = "a field came back empty (N/A or UNKNOWN)"
        else:
            return summary_text, None
        del lines[i]
        return "\n".join(tidy_blanks(lines)), why
    return summary_text, None


# --- the three verdict gates ---------------------------------------------------------
# Measured against one reader's own labels: far more videos were called YES or OPPORTUNITY
# than were worth keeping, and recall was already complete, so every gate here is a precision
# gate that must not cost a keep. `worthwatch score` measures them against your labels.


def applies_is_nothing(summary_text):
    """True when section 6 came to the word "nothing", after every guard has run.

    Read off the finished text rather than passed down from clean_summary's report, and
    deliberately so: the text is what the reader sees, and process_video, cmd_resummarize
    and process_candidates all reach this point by different routes. A missing section 6 is
    NOT "nothing" - it raises its own SECTION 6 NOT VERIFIED banner and means unmeasured,
    which is a different fact and must not demote anything."""
    lines = (summary_text or "").splitlines()
    bounds = section_bounds(lines, APPLIES_HEADING_RE)
    if bounds is None:
        return False
    body = [l.strip() for l in lines[bounds[0] + 1:bounds[1]]]
    body = [l for l in body if l and not l.startswith(">")]     # the guard's own banners
    if not body:
        return True
    return all(is_nothing_useful(MOST_APPLICABLE_RE.sub(r"\2", l) or l) for l in body)


# RULE 1. Whatever the listing said and whatever the transcript read said, a video whose
# section 6 came to "nothing" is a MAYBE. The digest's job is to tell you what to use this
# week; a YES that applies to none of your projects is a YES about nothing.
def demote_if_nothing_applies(verdict, summary_text):
    """(verdict, why or None). Only ever demotes, and only to MAYBE."""
    if verdict not in ("YES", "OPPORTUNITY"):
        return verdict, None
    if not applies_is_nothing(summary_text):
        return verdict, None
    return "MAYBE", (f"demoted from {verdict}: section 6 came to nothing, so the video "
                     f"applies to no project")


# RULE 2. The OPPORTUNITY gate. An OPPORTUNITY is "a documented way a person in my position
# gets paid, with numbers shown, that is not already killed", and "a story about a big
# company, a merger, a funding round or a hiring war is never OPPORTUNITY". The prompt says it
# and this checks it, because the prompt alone does not hold.
#
# The two halves are checked on the Opportunity line, which is where the summary states what
# works and who pays, and not on the whole summary: a summary of an acquisition video
# mentions the acquisition everywhere, and the question is what the video says somebody is
# PAID for.
OPPORTUNITY_NEEDS_A_NUMBER = re.compile(r"\d")
# Derived from real Opportunity lines rather than imagined, and it needs re-measuring as more
# arrive. Each entry is (what it is, the pattern).
NEVER_AN_OPPORTUNITY = (
    ("an acquisition or a merger",
     re.compile(r"\b(acquisitions?|acquir\w+|mergers?|merge[sd]?\s+with|takeovers?|bought\s+by"
                r"|buyer\s+of|sells?\s+itself|sold\s+to\s+the\s+incumbent"
                # One company buying another, said without the word: the real Texas
                # Instruments line reads "buys a design-only rival", and the first version of
                # this pattern missed it. The object has to be a company, because "buy a
                # domain and resell it" is a real way to get paid and must not match.
                r"|buys?\s+(?:a|an|the|its)\s+(?:\w+[\s-]+){0,3}"
                r"(?:rival|competitor|compan\w+|startups?|firms?|makers?|business|team|lab))\b",
                re.I)),
    ("a funding round or a valuation",
     re.compile(r"\b(series\s+[a-h]\b|seed\s+round|funding\s+round|venture\s+round"
                r"|rais\w+\s+(?:\$|\d|about)|valuations?|ipo)\b", re.I)),
    ("a hiring war",
     re.compile(r"\b(talent\s+war|hiring\s+war|poach\w*|hired?\s+one\s+by\s+one"
                r"|compensation\s+packages?|salary\s+band)\b", re.I)),
)


def opportunity_gate(summary_text):
    """(passes, why not). Pure text, so it can be run over a summary already on disk."""
    line = parse_opportunity_line(summary_text)
    if line is None:
        return False, ("no Opportunity line survived, so nothing names a way of getting paid")
    claim = f"{line['works']} | {line['for']}"
    for what, pattern in NEVER_AN_OPPORTUNITY:
        m = pattern.search(claim)
        if m:
            return False, f"what works is {what} ({m.group(0)!r}), never an OPPORTUNITY"
    if not OPPORTUNITY_NEEDS_A_NUMBER.search(" ".join(line.values())):
        return False, "the Opportunity line carries no number at all"
    return True, None


def gate_opportunity_verdict(verdict, summary_text):
    """(verdict, text, why). An OPPORTUNITY that fails the gate becomes a MAYBE, and its
    Opportunity line goes with it: append_opportunity() would otherwise record a road under a
    verdict that no longer exists, and the weekly table is built from that file."""
    if verdict != "OPPORTUNITY":
        return verdict, summary_text, None
    passes, why = opportunity_gate(summary_text)
    if passes:
        return verdict, summary_text, None
    text, _ = enforce_opportunity_line(summary_text, "MAYBE")
    return "MAYBE", text, f"demoted from OPPORTUNITY: {why}"


# --- the opportunity lane ---------------------------------------------------------------------
# OPPORTUNITY is its own lane, judged on its own terms and never demoted for not naming a live
# project: a video about money being made is otherwise demoted because section 6 came to
# nothing, or a triage gives the reader's own situation as the reason ("requires an account"),
# which is the feasibility veto that costs recall.
#
# So the lane sits beside the project verdict, not inside it. Every transcript that is read gets
# one more cheap question (the screen, on the triage brain, with no context file, so it judges
# the video alone); a video that passes gets a card (one call to the writing brain, as every
# write-up); the card, checked in code, decides. A video keeps the opportunity only if it shows
# a buyer that is a company, an organisation or a developer who already pays (never consumers),
# a price, deal size or revenue figure marked as shown or only said, and a concrete first step
# toward a first customer. Age, cost and hired help are notes on the card, never reasons to drop
# it; with the [you] sign_word setting, anything that needs someone else's signature is marked
# with that word. At most OPPORTUNITY_DAILY_CAP a day are surfaced, first come first kept, in
# their own section after the project items.
#
# The lane is switched on by main() for the scheduled modes and by a suite that tests it, never
# at import: every older suite drives process_candidates and the retry phase with its own stubs
# and knows nothing about a screen, and a screen it did not stub would be a real model call.
_LANE = {"on": False}
OPPORTUNITY_LANE = 1                 # the lane's version, kept on every record it judged
OPPORTUNITY_DAILY_CAP = 3
OPPORTUNITY_SCREEN_NUM_PREDICT = 160
CARD_TIMEOUT_SECONDS = 300
CARD_MIN_SECONDS = 90                # a card is not started with less of the run left than this
# Which model screens: the triage brain ("local"), or Jev ("jev"), a paid call that is off
# unless the [jev] enabled setting turns it on. Measured with the cards as the judge, Jev sent
# fewer videos to a card than a local model for the same cards passing, for a fraction of a
# cent a video; its probability moved little between runs, and the line is 0.4. When Jev cannot
# answer (no key, the day's cap spent, an error), the triage brain screens instead.
OPPORTUNITY_SCREEN_BACKEND = "local"
JEV_SCREEN_THRESHOLD = 0.4
JEV_SCREEN_QUESTIONS = {
    "opportunity": {
        "type": "noul",
        "instructions": "Does the video show a business opportunity: a buyer that is a company "
                        "or a developer who already pays (never consumers), a price, deal size "
                        "or revenue figure, and a concrete first step toward a first customer?",
        "criteria": {"true": "All three are in the video.",
                     "false": "At least one of the three is missing, or the buyer is consumers, or "
                              "it is only a story about an acquisition, a funding round or a "
                              "hiring war."}}}

OPPORTUNITY_SCREEN_INSTRUCTIONS = """You are reading one YouTube video's transcript to see whether it shows a business opportunity, judged on its own terms. You know nothing about who will read your answer, and you must not guess.

CHANNEL: {channel}
TITLE: {title}

=== TRANSCRIPT (machine transcription: names may be misspelt) ===
{transcript}
=== END TRANSCRIPT ===

An opportunity here is something a person could sell, and the video must show all three of these:
1. A BUYER that is a company, an organisation or a developer, who already pays for this. Consumers never count: not shoppers, fans, parents, students, viewers, followers or "people".
2. A FIGURE for it: a price, a deal size or a revenue number that the video gives. It was "shown" if it was on screen or sourced, "said" if it was only said.
3. A FIRST STEP toward a first paying customer that a person could take, from what the video describes.

Who would do it is not your concern: never answer NO because of someone's age, money or time, or because they would need to hire help or to have an adult sign. A story about a big company being bought, a funding round, a hiring war, a stock price or a market forecast is not an opportunity by itself.

Reply with exactly four lines and nothing else. Answer the first three from the video before you decide the fourth:
BUYER: <who pays, under 12 words, or "none">
FIGURE: <the figure and what it is for, under 15 words, then (shown) or (said); or "none">
FIRST STEP: <under 20 words, or "none">
OPPORTUNITY: YES|NO"""

OPPORTUNITY_CARD_INSTRUCTIONS = """You are writing one opportunity card for {reader}, the person the CONTEXT below describes, about one YouTube video. They are the one building and the one reading this. The CONTEXT says who they are and what they already run. An opportunity is judged on its own terms, never on whether it fits a project they already run.

=== CONTEXT ===
{context}
=== END CONTEXT ===

KILLED OR DECLINED IDEAS, never an opportunity whatever the video says:
{killed}

VIDEO: {title}
CHANNEL: {uploader}
URL: {url}

=== TRANSCRIPT ===
{transcript}
=== END TRANSCRIPT ===

The video is an opportunity only if it shows all three:
1. a buyer that is a company, an organisation or a developer who already pays for this (never consumers). The buyer already pays when the video shows or says that buyers like it spend money on this today: a price paid, a deal, revenue, a budget. It does not have to name one customer.
2. a price, deal size or revenue figure, marked (shown) when it was on screen or sourced and (said) when it was only said. A figure that was only said counts: mark it (said). Being only said or unsourced is never a reason for NO CARD; put that doubt under Red flags.
3. a concrete first step toward a first paying customer: who to approach first and with what offer, from what the video describes. Write the step yourself when the video gives enough to name it.
Their age, the cost and the need for hired help are notes on the card, never reasons for "no" or for NO CARD.{sign_note}

If the video is about an idea on the killed list, reply with exactly one line and nothing else:
KILLED: <the name from the list>: <the new evidence the video gives, in one sentence, or "nothing new">
If the video fails any of the three tests, reply with exactly one line and nothing else:
NO CARD: <which test fails, in under 15 words>
Otherwise reply with exactly these eight lines and nothing else, each on one line:
What it is: <one sentence>
Who pays: <the buyer, and that they already pay>
How much: <the figure and what it is for> (shown) or (said)
Why now: <one sentence, from the video>
Route to a first customer: <the first concrete step, and the second if it is needed>
Fit with you: <one or two sentences; age, cost and hired help as notes{sign_fit}>
Red flags: <one or two sentences>
Verdict: worth a research run | keep an eye on it | no

Never invent a number the video did not give. No quote marks, no dashes used as punctuation, and no thinking aloud: write the finished card only."""

CARD_FIELDS = (("what", "what it is"), ("who_pays", "who pays"), ("how_much", "how much"),
               ("why_now", "why now"), ("route", "route to a first customer"),
               ("fit", "fit with you"), ("red_flags", "red flags"),
               ("verdict", "verdict"))
CARD_LABELS = {key: label for key, label in CARD_FIELDS}
CARD_REQUIRED = ("what", "who_pays", "how_much", "route", "verdict")
CARD_VERDICTS = ("worth a research run", "keep an eye on it", "no")
# The buyer has to be somebody who pays as a business. A school, a lab, a firm, a team or a
# developer is; a consumer never is, whatever else the line says.
BUYER_RE = re.compile(
    r"\b(?:compan(?:y|ies)|business(?:es)?|firms?|labs?|startups?|enterprises?|teams?"
    r"|developers?|devs|engineers?|agenc(?:y|ies)|schools?|districts?|universit(?:y|ies)"
    r"|colleges?|hospitals?|clinics?|brands?|sellers?|merchants?|retailers?|stores?|shops?"
    r"|restaurants?|cafes?|saas|vendors?|manufacturers?|publishers?|studios?|organi[sz]ations?"
    r"|nonprofits?|governments?|municipalit(?:y|ies)|insurers?|banks?|law\s+firms?"
    r"|property\s+managers?|operators?|(?:business|shop|store|restaurant|practice)\s+owners?"
    r"|founders?|b2b|employers?|institutions?|contractors?|plumbers?|dentists?|realtors?"
    r"|recruiters?|investors?|funds?)\b", re.I)
CONSUMER_RE = re.compile(
    r"\b(?:consumers?|individuals?|people|parents?|students?|kids|children|teens?|teenagers?"
    r"|shoppers?|fans?|viewers?|followers?|subscribers?|homeowners?|home\s+owners?|patients?"
    r"|families|gamers?|hobbyists?|beginners?|retail\s+customers?|end[\s-]users?|users"
    r"|the\s+public|anyone|everyone)\b", re.I)
# With the [you] sign_word setting (say "SIGN", for someone whose contracts another person
# signs), a card's route or fit that needs a signature carries that word; the card is told so,
# and this adds it when the card forgot. Empty, the default, turns it off.
SIGN_WORD = ""
ADULT_RE = re.compile(
    r"\b(?:contracts?|sign(?:s|ed|ing|ature)?|stripe|bank\s+accounts?|payment\s+accounts?"
    r"|invoic\w*|llcs?|incorporat\w*|licen[cs]\w*|insurance|w-?9|taxes|legal|lawyers?|ndas?"
    r"|msas?|purchase\s+orders?|procurement)\b", re.I)
# Ideas you killed or declined, never surfaced as an opportunity: the [[killed]] rules of your
# rules.toml, as (name, pattern), and ideas killed on the idea board. Each name should be a phrase
# of your context file, so the line the digest prints points at a sentence you can find; the
# patterns read what the card says the idea IS and who pays for it. apply_profile() sets it.
KILLED_IDEAS = ()


def lane_on():
    return bool(_LANE.get("on"))


def _field_value(text):
    text = normalize_punct(one_line(text or "")).strip().strip("*").strip()
    return "" if is_nothing_useful(text) or text.lower() in ("unknown", "n/a", "-") else text


def parse_screen(response):
    """The screen's four lines as a dict: buyer, figure, mark ("shown", "said" or None),
    first_step, says (YES, NO or None when it gave no verdict)."""
    found = {}
    for line in (response or "").splitlines():
        m = re.match(r"^\s*\**\s*(BUYER|FIGURE|FIRST\s+STEP|OPPORTUNITY)\s*\**\s*:\s*(.*)$", line,
                     re.I)
        key = re.sub(r"\s+", "_", m.group(1).upper()) if m else None
        if key and key not in found:
            found[key] = m.group(2).strip()
    figure = _field_value(found.get("FIGURE"))
    mark = re.search(r"\b(shown|said)\b", figure, re.I)
    says = re.match(r"\W*(YES|NO)\b", found.get("OPPORTUNITY") or "", re.I)
    return {"buyer": _field_value(found.get("BUYER")), "figure": figure,
            "mark": mark.group(1).lower() if mark else None,
            "first_step": _field_value(found.get("FIRST_STEP")),
            "says": says.group(1).upper() if says else None}


def buyer_problem(buyer):
    """Why a buyer is not one, or None. A company, an organisation or a developer is; a
    consumer never is, and a line that names neither names nobody. The first of them the line
    names is the buyer: "parents of high-school students" are parents, not a school, and
    "schools, for their students" are schools."""
    if not buyer:
        return "it names no buyer"
    business, consumer = BUYER_RE.search(buyer), CONSUMER_RE.search(buyer)
    if business and (not consumer or business.start() < consumer.start()):
        return None
    if consumer:
        return "the buyer is consumers, never an opportunity"
    return "it names no company, organisation or developer as the buyer"


def screen_passes(screen):
    """True when the screen found all three: a business buyer, a figure, a first step. Jev's
    screen answers with one probability, which passes at JEV_SCREEN_THRESHOLD."""
    if not screen or screen.get("error"):
        return False
    if screen.get("model") == "jev":
        p = screen.get("p")
        return isinstance(p, (int, float)) and p >= JEV_SCREEN_THRESHOLD
    if screen.get("says") != "YES":
        return False
    return (buyer_problem(screen.get("buyer")) is None and bool(re.search(r"\d", screen.get("figure") or ""))
            and len((screen.get("first_step") or "").split()) >= 3)


def screen_opportunity(meta, model):
    """The screen on one read transcript, by OPPORTUNITY_SCREEN_BACKEND: Jev, with the local
    model answering whenever Jev cannot. Never raises."""
    if OPPORTUNITY_SCREEN_BACKEND == "jev":
        try:
            return screen_opportunity_jev(meta)
        except Exception as e:  # noqa: BLE001 - no key, the day's cap spent, or no answer
            why = one_line(f"{e.__class__.__name__}: {e}")[:160]
            print(f"    (Jev could not screen this one: {why}; the local model screens it)")
            screen = screen_opportunity_local(meta, model)
            screen["jev_error"] = why
            return screen
    return screen_opportunity_local(meta, model)


def screen_opportunity_jev(meta):
    """Jev's screen: public video text only (title, channel, description, a transcript excerpt),
    never CONTEXT.md, through the tool's daily ledger. Raises when Jev cannot answer."""
    from . import jev
    state = jev.public_state(meta.get("title"), meta.get("channel") or meta.get("uploader"),
                             meta.get("description") or "", meta.get("transcript"))
    out = jev.ask(state, JEV_SCREEN_QUESTIONS, jev_ledger())
    p = float(out["answers"]["opportunity"]["noul"])
    return {"model": "jev", "p": round(p, 3), "says": "YES" if p >= JEV_SCREEN_THRESHOLD else "NO",
            "cost": out.get("cost"), "seconds": out.get("seconds")}


def screen_opportunity_local(meta, model):
    """The screen on the local model, temperature 0, nothing about the reader in the prompt. Never
    raises: a screen that could not be asked is {"error": why}, and the video goes on exactly as
    the triage left it."""
    try:
        prompt = OPPORTUNITY_SCREEN_INSTRUCTIONS.format(
            channel=meta.get("channel") or meta.get("uploader") or "UNKNOWN",
            title=meta.get("title") or "", transcript=triage_excerpt(meta.get("transcript")))
        left = run_seconds_left()
        timeout = 300 if left is None else max(TRIAGE_MIN_TIMEOUT, min(300, left))
        response = quick_generate(prompt, model=model, num_ctx=NUM_CTX, timeout=timeout,
                                  num_predict=OPPORTUNITY_SCREEN_NUM_PREDICT, temperature=0)
    except Exception as e:  # noqa: BLE001 - the screen is extra; its failure is nobody's verdict
        return {"error": one_line(f"{e.__class__.__name__}: {e}")[:200], "model": model}
    screen = parse_screen(response)
    screen["model"] = model
    return screen


def route_for_opportunity(verdict, reason, screen):
    """(verdict, reason): the transcript triage's verdict, routed by the screen. A screen that
    passes makes a MAYBE or a NO an OPPORTUNITY, and leaves a YES or an OPPORTUNITY as it is; a
    screen that does not pass changes nothing. Nothing here demotes."""
    if not screen_passes(screen) or verdict in ("YES", "OPPORTUNITY"):
        return verdict, reason
    if screen.get("model") == "jev":
        found = (f"[the opportunity screen (Jev) put it at {screen.get('p'):.2f}; the triage said "
                 f"{verdict}]")
    else:
        found = (f"[the opportunity screen found a buyer ({screen.get('buyer')}), a figure "
                 f"({screen.get('figure')}) and a first step; the triage said {verdict}]")
    return "OPPORTUNITY", f"{reason} {found}".strip()


def killed_names_for_prompt():
    return "\n".join(f"- {name}" for name, _pattern in KILLED_IDEAS)


def parse_card(text):
    """A card reply as a dict. kind is "card", "no_card", "killed" or "unreadable"."""
    text = normalize_punct((text or "").strip())
    first = text.splitlines()[0].strip() if text else ""
    m = re.match(r"^\W*no\s+card\s*:\s*(.*)$", first, re.I)
    if m:
        return {"kind": "no_card", "why": m.group(1).strip() or "no reason given"}
    m = re.match(r"^\W*killed\s*:\s*([^:]+?)\s*:\s*(.*)$", first, re.I)
    if m:
        evidence = m.group(2).strip()
        return {"kind": "killed", "killed": m.group(1).strip().lower(),
                "evidence": "" if evidence.lower().startswith("nothing new") else evidence}
    card = {"kind": "card"}
    for line in text.splitlines():
        m = re.match(r"^\s*[-*]?\s*\**\s*([A-Za-z][A-Za-z ]+?)\s*\**\s*:\s*\**\s*(.*)$", line)
        if not m:
            continue
        label = m.group(1).strip().lower()
        for key, wanted in CARD_FIELDS:
            if label == wanted and key not in card:
                card[key] = m.group(2).strip().strip("*").strip()
    if not any(key in card for key, _ in CARD_FIELDS):
        return {"kind": "unreadable", "why": one_line(text)[:160] or "an empty reply"}
    verdict = (card.get("verdict") or "").lower().strip(" .")
    card["verdict"] = next((v for v in CARD_VERDICTS if verdict.startswith(v)), verdict)
    mark = re.search(r"\b(shown|said)\b", card.get("how_much") or "", re.I)
    card["mark"] = mark.group(1).lower() if mark else None
    return card


def killed_idea_in(card):
    """The killed name a card is about, from what it says the idea is and who pays, or None."""
    text = " ".join(one_line(card.get(k) or "") for k in ("what", "who_pays"))
    for name, pattern in KILLED_IDEAS:
        if pattern.search(text):
            return name
    return None


def card_problem(card):
    """Why a card is not an opportunity, as a phrase for the row, or None. Pure."""
    if not card:
        return "no opportunity card was written"
    kind = card.get("kind")
    if kind == "error":
        return f"its card could not be written ({card.get('why')})"
    if kind == "no_card":
        return f"the card found no opportunity: {card.get('why')}"
    if kind == "unreadable":
        return "its card could not be read"
    if kind == "killed":
        return f"a killed idea ({card.get('killed')})"
    # The three tests need what it is, who pays, how much and the route; why now, the fit and the
    # red flags may honestly be unknown or none, and are never a reason to drop a card.
    for key in CARD_REQUIRED:
        if not _field_value(card.get(key)):
            return f"its card has no '{CARD_LABELS[key]}' line"
    if card.get("verdict") not in CARD_VERDICTS:
        return f"its card's verdict is not one of the three ({card.get('verdict')})"
    why = buyer_problem(_field_value(card.get("who_pays")))
    if why:
        return why
    if not re.search(r"\d", card.get("how_much") or ""):
        return "it names no price, deal size or revenue figure"
    if not card.get("mark"):
        return "it does not say whether the figure was shown or only said"
    if len(_field_value(card.get("route")).split()) < 6:
        return "it names no concrete first step toward a first customer"
    # The existing exclusions, read on what the idea is (its first clause, because a sentence
    # about labs that have also been acquiring startups is not an acquisition) and who pays.
    first_clause = re.split(r"[,;:(]|\s+which\s+|\s+who\s+", _field_value(card.get("what")))[0]
    claim = f"{first_clause} | {card.get('who_pays')}"
    for what, pattern in NEVER_AN_OPPORTUNITY:
        m = pattern.search(claim)
        if m:
            return f"what it is is {what} ({m.group(0)!r}), never an opportunity"
    name = killed_idea_in(card)
    if name:
        return f"a killed idea ({name})"
    return None


def card_surfaces(card):
    """A card that passes every check and whose own verdict is not "no"."""
    return bool(card) and card_problem(card) is None and card.get("verdict") != "no"


def finish_card(card):
    """The card as it is stored: its killed name when it is one, SIGN_WORD added when its route
    or fit needs a signature and it did not say so (only when SIGN_WORD is set), and the result
    of the checks."""
    card = dict(card)
    if card.get("kind") == "card":
        name = killed_idea_in(card)
        if name:
            card["killed"] = name
        if SIGN_WORD:
            needs = " ".join(one_line(card.get(k) or "") for k in ("route", "fit"))
            card["sign"] = SIGN_WORD in needs or bool(ADULT_RE.search(needs))
    card["problem"] = card_problem(card)
    return card


def card_sign_notes():
    """(sign_note, sign_fit) for the card prompt: the signature marker's two sentences when
    SIGN_WORD is set, else nothing."""
    if not SIGN_WORD:
        return "", ""
    return (f" Mark anything that needs someone else's signature (a contract, a payment account, "
            f"a licence, insurance) with the word {SIGN_WORD}.",
            f"; {SIGN_WORD} before anything that needs a signature")


def write_opportunity_card(context_text, meta):
    """One call to the writing brain for one card, bounded by the run's time. Never raises: a
    card that could not be written is {"kind": "error", "why": ...}."""
    left = run_seconds_left()
    if left is not None and left < CARD_MIN_SECONDS:
        return finish_card({"kind": "error", "why": f"only {left:.0f} s of the run were left"})
    timeout = CARD_TIMEOUT_SECONDS if left is None else max(60, min(CARD_TIMEOUT_SECONDS, left - 15))
    sign_note, sign_fit = card_sign_notes()
    prompt = OPPORTUNITY_CARD_INSTRUCTIONS.format(
        reader=READER, sign_note=sign_note, sign_fit=sign_fit,
        context=context_text, killed=killed_names_for_prompt(), title=meta.get("title") or "",
        uploader=meta.get("uploader") or meta.get("channel") or "UNKNOWN",
        url=meta.get("url") or "", transcript=triage_excerpt(meta.get("transcript")))
    t0 = time.monotonic()
    try:
        reply = write_generate(prompt, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - the card's failure is the card's, not the video's
        return finish_card({"kind": "error", "why": one_line(f"{e.__class__.__name__}: {e}")[:200]})
    card = parse_card(reply)
    card["seconds"] = round(time.monotonic() - t0, 1)
    return finish_card(card)


def card_lines(card):
    """The card as markdown lines, for the write-up's section 10, the digest and the weekly."""
    kind = card.get("kind")
    if kind == "killed":
        evidence = card.get("evidence") or "nothing new"
        return [f"Killed idea ({card.get('killed')}): {evidence}"]
    if kind != "card":
        return [f"No card: {card_problem(card)}"]
    lines = []
    for key, label in CARD_FIELDS:
        value = _field_value(card.get(key))
        if key == "fit" and card.get("sign") and SIGN_WORD and SIGN_WORD not in value:
            value = f"{value} {SIGN_WORD}" if value else SIGN_WORD
        lines.append(f"- **{label[:1].upper() + label[1:]}:** {value or 'UNKNOWN'}")
    return lines


def card_section(card):
    """Section 10 of a write-up that has a card, with the result of the checks under it."""
    problem = card.get("problem")
    gate = ("passes the three tests" if not problem else f"not an opportunity: {problem}")
    if card.get("kind") == "card" and not problem and card.get("verdict") == "no":
        gate = "passes the three tests; its own verdict is no, so it is not surfaced"
    return "\n".join(["## 10. Opportunity card", ""] + card_lines(card) + ["", f"Checked: {gate}."])


def opportunity_cap_for(video_id, card):
    """Why a surfacing card is not surfaced, when the run's day already has OPPORTUNITY_DAILY_CAP
    of them written; else None. First come, first kept."""
    if not card_surfaces(card):
        return None
    day = today_key()
    before = sum(1 for r in load_records().values()
                 if r.get("id") != video_id and r.get("opp_lane")
                 and r.get("written_day") == day and r.get("summary_state") == "WRITTEN"
                 and card_surfaces(r.get("opp_card")) and not r.get("opp_cap"))
    if before < OPPORTUNITY_DAILY_CAP:
        return None
    return (f"the day's opportunity number {before + 1}, and at most {OPPORTUNITY_DAILY_CAP} a "
            f"day are surfaced")


def opportunity_state(record):
    """(surfaced, why not) for a record the lane judged. Pure: it reads the record."""
    card = record.get("opp_card")
    if not card:
        return False, "no opportunity card was written"
    problem = card.get("problem") if "problem" in card else card_problem(card)
    if problem:
        return False, problem
    if card.get("verdict") == "no":
        flags = _field_value(card.get("red_flags"))
        return False, "its card's own verdict is no" + (f" ({flags[:120]})" if flags else "")
    if record.get("opp_cap"):
        return False, record["opp_cap"]
    return True, None


def lane_settle(verdict, settled, notes):
    """(settled, notes) for a write-up the lane judged. An OPPORTUNITY is the card's to decide, so
    the old gates' word on it (the Opportunity line, section 6) is set aside; a YES is left to
    the project rules exactly as before."""
    if verdict != "OPPORTUNITY":
        return settled, list(notes)
    return "OPPORTUNITY", [n for n in notes if not n.startswith("demoted from OPPORTUNITY")]


def lane_verdict(record, verdict, read, note, tags):
    """final_verdict's answer for a record the lane judged, or None to let the project rules
    answer. A YES the project rules keep stays YES (its card is listed too); otherwise a card
    that surfaces is an OPPORTUNITY, kept, in its own section and not the header; and an
    OPPORTUNITY whose card does not surface is a MAYBE that says why. Never demoted for naming
    no project."""
    surfaced, why_not = opportunity_state(record)
    yes_kept = (read == "YES" and verdict in ("YES", "MAYBE") and bool(record.get("project"))
                and yes_problem(record) is None)
    if yes_kept:
        return None
    if surfaced:
        return "OPPORTUNITY", note, frozenset(tags | {"row", "kept", "opportunity"})
    if read == "OPPORTUNITY" or verdict == "OPPORTUNITY":
        text = "; ".join(x for x in (note, f"demoted from OPPORTUNITY: {why_not}") if x)
        return "MAYBE", text, frozenset(tags | {"row", "read_and_skipped", "demoted"})
    return None


def lane_card_needed(verdict, screen):
    """A card is written for an OPPORTUNITY, and for a YES whose screen passed."""
    return verdict == "OPPORTUNITY" or (verdict == "YES" and screen_passes(screen))


def lane_written(vid, context_text, meta, verdict, screen, summary, settled, gate_notes):
    """Everything the lane adds once a write-up exists: (summary, settled, notes, fields). The
    card is written when one is needed and appended to the write-up as section 10; fields are
    what summary_written stores beside the write-up."""
    settled, gate_notes = lane_settle(verdict, settled, gate_notes)
    card = None
    if lane_card_needed(verdict, screen):
        card = write_opportunity_card(context_text, meta)
        summary = summary.rstrip() + "\n\n" + card_section(card) + "\n"
        state = "surfaces" if card_surfaces(card) else (card.get("problem") or "its own verdict is no")
        print(f"    opportunity card ({card.get('seconds', 0)}s): {state}")
    fields = {"opp_lane": OPPORTUNITY_LANE, "opp_screen": screen, "opp_card": card,
              "opp_cap": opportunity_cap_for(vid, card) if card else None,
              "triage_verdict": meta.get("triage_verdict")}
    if fields["opp_cap"]:
        print(f"    ({fields['opp_cap']})")
    return summary, settled, gate_notes, fields


def lane_shown(vid, summary, settled, label_cap, fields):
    """The verdict the digest will show for a write-up about to be recorded, read the way
    final_verdict reads the record summary_written is about to save, so the history row and
    the run's log say the same thing as the day's table."""
    applies, project, takeaway = section6_state(summary)
    record = dict(load_records().get(vid) or {"id": vid})
    record.update(summary_state="WRITTEN", reason_code="written", settled_verdict=settled,
                  applies=applies, project=project, takeaway=takeaway,
                  try_this_week=extract_try_this_week(summary), header=writeup_header(summary),
                  label_cap=label_cap, decision_hit=None, **fields)
    record.setdefault("read_verdict", settled)
    return final_verdict(record)


def append_opportunity_card(meta, card):
    """OPPORTUNITIES.md's one line for a surfaced card: what works is what it is, for whom is who
    pays, and the evidence is the figure, marked shown or said."""
    works = _opportunity_field(card.get("what"))
    who = _opportunity_field(card.get("who_pays"))
    evidence = _opportunity_field(f"{card.get('mark') or 'said'}: {card.get('how_much')}")
    title = _opportunity_field(meta["title"]).replace("[", "(").replace("]", ")")
    line = (f"- {today_key()} | works: {works} | for: {who} | evidence: {evidence} | "
            f"[{title}]({meta['url']})")
    path = opportunities_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        atomic_write_text(path, OPPORTUNITIES_HEADER)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"    -> OPPORTUNITIES.md: {works[:90]}")
    return line


# --- the ideas lane ---------------------------------------------------------------------------
# Section 6 keeps only what maps onto one of your projects, and the opportunity lane only a
# company buyer with a figure, so on their own they give tips for what you already run. The
# lane below is the other horizon: every write-up by the writing brain, in the same call, also
# lists every product, business or project idea the video contains or implies, in any field,
# as short cards. Nothing here is a gate. The checks only make the cards true: a figure the
# transcript does not hold is "unknown", a killed idea is one line unless the video brings new
# evidence against the reason it was killed, and an idea met before gains the new video as
# evidence instead of a second card. The cards live in IDEAS_PATH; IDEAS.md is the board.
#
# Order matters, and is why the split is in summarize_claude: the Ideas part is taken off the
# write-up before any existing check reads it, so sections 1 to 9, every verdict rule and the
# score against your labels see exactly the text they would without this lane.

IDEAS_LANE = 1
IDEAS_HEADING_RE = re.compile(r"^#{2,3}[ \t]*(?:\d+[.)][ \t]*)?Ideas[ \t]*:?[ \t]*$", re.I | re.M)
IDEA_CARD_RE = re.compile(r"^\s*#{2,4}\s*Idea\s*(\d+)\s*[:.)-]\s*(.+?)\s*$", re.I)
IDEA_PICK_RE = re.compile(r"^\W*pick\W*:?\s*(?:idea\s*)?#?(\d+)\b[\s,.:;-]*(?:because\s+)?(.*)$",
                          re.I)
IDEA_ID_RE = re.compile(r"\bI-?(\d{1,5})\b", re.I)
IDEA_FIELDS = (("who_pays", "who pays"), ("how_much", "how much"), ("why_now", "why now"),
               ("first_step", "first step"), ("transfers", "also works for"),
               ("catch", "the catch"), ("quote", "where in the video"), ("project", "project"),
               ("same_as", "same as"), ("killed", "killed"))
IDEA_LABELS = {label: key for key, label in IDEA_FIELDS}
IDEA_LABELS.update({"catch": "catch", "where": "quote", "transfers": "transfers"})
IDEAS_PER_VIDEO_MAX = 10          # a podcast can imply twenty; past ten the card is noise
IDEA_TRANSFERS_MAX = 3
IDEAS_ON_BOARD_IN_PROMPT = 60     # the newest ideas the write-up is shown, for "Same as"
TASTE_IN_PROMPT = 20              # your last 20 likes and nahs
IDEAS_TODAY_MAX = 5
IDEA_SAME_JACCARD = 0.6           # two one-line ideas this close in their words are one idea
REMIX_COMBOS_MAX = 3
REMIX_TOP_MAX = 5
# The day's remix waits until this hour, so it sees the day's runs; `worthwatch remix today`
# runs it at once. The last scheduled hour when there is a schedule.
REMIX_LAST_HOUR = 18
REMIX_CATCH_UP_DAYS = 2
REMIX_TIMEOUT_SECONDS = 600
REMIX_MODELS = ("opus", "sonnet")         # the strongest model first, the next if it fails
WEEK_BOARD_TOP = 10
# Things you already have that an idea could be carried onto: the [you] assets setting.
IDEA_ASSETS = ()
IDEA_STOP_WORDS = frozenset(
    "the and for with that this from into your their them they you are can could would will "
    "who what which when where how why its it's like just also than then more most less some "
    "any all each one two use using used make makes making build builds building tool tools "
    "app apps service services business businesses product products platform small big new "
    "based sell sells selling help helps people company companies".split())
# When and why each killed idea was killed, for the one line a match gets ("seen before: killed
# on <date> because <reason>"): the date and because of each [[killed]] rule, name -> (date,
# because). A rule with no date says "date not recorded", never a guess. apply_profile() sets
# it.
KILLED_IDEA_NOTES = {}


def ideas_board_path():
    """IDEAS.md in the output folder, resolved when called, so a suite that moves OUTPUT_DIR
    moves this with it."""
    return OUTPUT_DIR / "IDEAS.md"


def ideas_taste_path():
    """IDEAS-TASTE.md in the output folder, where you write like or nah next to an id."""
    return OUTPUT_DIR / "IDEAS-TASTE.md"


def empty_ideas_store():
    return {"lane": IDEAS_LANE, "next": 1, "ideas": {}, "seen_before": [], "videos": {},
            "remixes": {}}


def load_ideas():
    store = read_state_json(IDEAS_PATH)
    if not isinstance(store, dict) or "ideas" not in store:
        return empty_ideas_store()
    for key, value in empty_ideas_store().items():
        store.setdefault(key, value)
    return store


def save_ideas(store):
    atomic_write_text(IDEAS_PATH, json.dumps(store, indent=1, ensure_ascii=False) + "\n")


def idea_id(n):
    return f"I-{int(n):04d}"


def normal_idea_id(text):
    """'42', 'i42', 'I-42' and 'I-0042' are all I-0042; None when the text names no id."""
    m = IDEA_ID_RE.search(str(text or "")) or re.fullmatch(r"\s*(\d{1,5})\s*", str(text or ""))
    return idea_id(m.group(1)) if m else None


# --- reading the model's part -------------------------------------------------------------------
def split_ideas_section(text):
    """(write-up, ideas part): the write-up as the model would have written it without the Ideas
    part, and the part's body, or None when the reply has no Ideas heading. The last heading
    wins, so a word "Ideas" earlier in the write-up cannot take its sections with it."""
    text = text or ""
    matches = list(IDEAS_HEADING_RE.finditer(text))
    if matches:
        m = matches[-1]
        return text[:m.start()].rstrip(), text[m.end():].strip()
    # A reply that wrote its cards with no heading of their own: the first "Idea 1" card after
    # section 9 starts the part, so the cards neither vanish nor run on inside the header.
    nine = list(re.finditer(r"^##\s*9\b.*$", text, re.M))
    if nine:
        card = re.search(r"^\s*#{2,4}\s*Idea\s*1\s*[:.)-]", text[nine[-1].end():], re.M | re.I)
        if card:
            at = nine[-1].end() + card.start()
            return text[:at].rstrip(), text[at:].strip()
    return text, None


def _idea_value(text):
    text = normalize_punct(one_line(text or "")).strip().strip("*").strip()
    return "" if EMPTY_FIELD_RE.match(text or "-") and text.lower() != "unknown" else text


def parse_ideas(part):
    """(cards, pick) from the Ideas part. cards are dicts in the order written; pick is
    (card number, why) or None. "none" is no cards. Pure."""
    cards, pick, card = [], None, None
    for raw in (part or "").splitlines():
        line = normalize_punct(raw).rstrip()
        m = IDEA_CARD_RE.match(line)
        if m:
            card = {"n": int(m.group(1)), "line": _idea_value(m.group(2))}
            cards.append(card)
            continue
        p = IDEA_PICK_RE.match(line)
        if p and not line.lstrip().startswith(("-", "*")):
            pick = (int(p.group(1)), _idea_value(p.group(2)))
            continue
        if card is None:
            continue
        f = re.match(r"^\s*[-*]?\s*\**\s*([A-Za-z][A-Za-z ]+?)\s*\**\s*:\s*\**\s*(.*)$", line)
        if not f:
            continue
        key = IDEA_LABELS.get(f.group(1).strip().lower())
        if key and key not in card:
            card[key] = f.group(2).strip().strip("*").strip()
    return [c for c in cards if c.get("line")][:IDEAS_PER_VIDEO_MAX], pick


# --- the checks: true cards, never gated ---------------------------------------------------------
NUMBER_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|b|thousand|million|billion|lakh|crore)?\b",
                       re.I)
NUMBER_UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9,
                "lakh": 1e5, "crore": 1e7}


def figure_values(text):
    """Every number in the text, each as written and with its k, million or crore applied."""
    values = set()
    for num, unit in NUMBER_RE.findall(text or ""):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        values.add(round(v, 6))
        mult = NUMBER_UNITS.get((unit or "").lower())
        if mult:
            values.add(round(v * mult, 6))
    return values


def figure_numbers(text):
    """The numbers a "how much" field states, one value each (with its unit applied), leaving out
    the shown/said mark."""
    text = re.sub(r"\((?:shown|said|unknown)[^)]*\)", " ", text or "", flags=re.I)
    out = []
    for num, unit in NUMBER_RE.findall(text):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        mult = NUMBER_UNITS.get((unit or "").lower())
        out.append((round(v, 6), round(v * mult, 6) if mult else None))
    return out


def figure_in_transcript(how_much, transcript_text, values=None):
    """True when every number the field states is a number the transcript says (as written or
    with its unit applied). A field with no number has nothing to invent."""
    values = figure_values(transcript_text) if values is None else values
    for bare, scaled in figure_numbers(how_much):
        if bare not in values and (scaled is None or scaled not in values):
            return False
    return True


def figure_mark(how_much):
    m = re.search(r"\((shown|said)\b", how_much or "", re.I) or \
        re.search(r"\b(shown|said)\s*$", how_much or "", re.I)
    return m.group(1).lower() if m else None


def quote_position(quote, transcript_text):
    """(word index, words in the transcript) where the quote starts, or None. Exact first, then
    the tolerant match quote_supported_by allows: most of its word pairs in a row."""
    words = canon_tokens(transcript_text or "")
    q = canon_tokens(quote or "")
    if len(q) < 3 or not words:
        return None
    n = len(q)
    first = q[0]
    for i, w in enumerate(words):
        if w == first and words[i:i + n] == q:
            return i, len(words)
    pairs = [(q[k], q[k + 1]) for k in range(n - 1)]
    best, best_i = 0.0, None
    for i in range(0, max(1, len(words) - n + 1)):
        window = set(zip(words[i:i + n + 2], words[i + 1:i + n + 3]))
        score = sum(1 for p in pairs if p in window) / len(pairs)
        if score > best:
            best, best_i = score, i
    return (best_i, len(words)) if best >= 0.8 else None


def quote_seconds(quote, transcript_text, minutes):
    """Where in the video the quote falls, in whole seconds, worked out from its place among the
    transcript's words and the video's length; None when the quote is not found or the length is
    not known. Transcripts carry no timings, so this is an estimate and is always shown as one."""
    if not minutes:
        return None
    found = quote_position(quote, transcript_text)
    if not found:
        return None
    index, total = found
    return int(round(index / max(total, 1) * float(minutes) * 60))


def clock_words(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def timed_link(url, seconds):
    if seconds is None or not url:
        return url or ""
    return f"{url}{'&' if '?' in url else '?'}t={int(seconds)}s"


def split_transfers(text):
    parts = re.split(r"\s*;\s*|\s+\(\d\)\s*|(?:^|\s)\d[.)]\s+", text or "")
    out = [_idea_value(p).rstrip(".") for p in parts]
    return [p for p in out if p and not is_nothing_useful(p)][:IDEA_TRANSFERS_MAX]


def project_of(text):
    """The one project a card names from section 6's closed list, or None ("none" included)."""
    value = _idea_value(text).lower().strip(" .")
    if not value or value in ("none", "no", "no project", "n/a"):
        return None
    for project in PROJECTS:
        if project.lower() == value or project.lower() == re.sub(r"^applies to:\s*", "", value):
            return project if project != "a road not taken" else None
    named = projects_named(text or "")
    named = [p for p in named if p != "a road not taken"]
    return named[0] if len(named) == 1 else None


def killed_name_for(text):
    """The KILLED_IDEAS name a model's "Killed:" line points at, or None."""
    value = _idea_value(text).lower().strip(" .")
    if not value or value in ("none", "no"):
        return None
    for name, pattern in KILLED_IDEAS:
        if value == name or value in name or name in value or pattern.search(value):
            return name
    return None


def killed_note(name):
    date, because = KILLED_IDEA_NOTES.get(name, (None, None))
    return {"name": name, "date": date or "date not recorded",
            "because": because or "the reason is not recorded"}


def check_idea_card(card, transcript_text, minutes=None, values=None):
    """One card made true, never dropped for what it is about. Returns a new dict:

      line, who_pays, how_much, mark, why_now, first_step, transfers, catch, quote, seconds,
      project, same_as, killed (None or name, date, because, new_evidence), seen_before (True
      when it is a killed idea with no new evidence), notes (what the checks changed)

    No word about age, money, licences, hired help, consumers or a project ever removes a card:
    tests/test_ideas.py section E holds that, and its mutation goes red if one does."""
    notes = []
    out = {"line": _idea_value(card.get("line")), "who_pays": _idea_value(card.get("who_pays"))
           or "unknown", "why_now": _idea_value(card.get("why_now")) or "unknown",
           "first_step": _idea_value(card.get("first_step")) or "unknown",
           "catch": _idea_value(card.get("catch")) or "unknown"}
    how = _idea_value(card.get("how_much"))
    mark = figure_mark(how)
    if not how or how.lower().startswith("unknown") or is_nothing_useful(how):
        how, mark = "unknown", None
    elif figure_numbers(how) and not figure_in_transcript(how, transcript_text, values):
        notes.append(f"the figure ({how[:80]}) is not in the transcript, so it reads unknown")
        how, mark = "unknown (the figure is not in the transcript)", None
    elif figure_numbers(how) and not mark:
        mark = "said"
        notes.append("the figure carried no mark; it is in the transcript, so it reads said")
    out["how_much"], out["mark"] = how, mark
    out["transfers"] = split_transfers(card.get("transfers"))
    quote = _idea_value(card.get("quote")).strip('"').strip("'")
    out["quote"] = quote
    out["seconds"] = quote_seconds(quote, transcript_text, minutes) if quote else None
    if quote and out["seconds"] is None:
        notes.append("the quote was not found in the transcript, so the card has no time")
    out["project"] = project_of(card.get("project"))
    same = normal_idea_id(card.get("same_as")) if card.get("same_as") else None
    out["same_as"] = same
    killed_text = card.get("killed") or ""
    name_part, _sep, evidence_part = killed_text.partition("|")
    evidence = re.sub(r"^\W*new\s+evidence\s*:\s*", "", evidence_part.strip(), flags=re.I)
    evidence = _idea_value(evidence)
    if evidence.lower().strip(" .") in ("none", "nothing", "nothing new", "no"):
        evidence = ""
    name = killed_name_for(name_part) or killed_idea_in({"what": out["line"],
                                                          "who_pays": out["who_pays"]})
    out["killed"] = dict(killed_note(name), new_evidence=evidence) if name else None
    out["seen_before"] = bool(name) and not evidence
    out["notes"] = notes
    return out


def _idea_stem(word):
    for suffix in ("ings", "ing", "ers", "er", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def idea_words(line):
    return {_idea_stem(w) for w in canon_tokens(line) if len(w) > 2 and w not in IDEA_STOP_WORDS}


def same_idea(a, b):
    """Two one-line ideas are one idea when their words mostly agree. Deliberately strict: the
    model's own "Same as" is what joins two wordings of one idea; this only catches the same line
    written twice."""
    wa, wb = idea_words(a), idea_words(b)
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= IDEA_SAME_JACCARD


# --- the store ---------------------------------------------------------------------------------
def forget_video_ideas(store, vid):
    """Take one video's evidence out of the store, so writing it up again replaces it."""
    for iid in list(store["ideas"]):
        idea = store["ideas"][iid]
        idea["evidence"] = [e for e in idea.get("evidence", []) if e.get("video") != vid]
        if not idea["evidence"]:
            del store["ideas"][iid]
    store["seen_before"] = [s for s in store["seen_before"] if s.get("video") != vid]
    store["videos"].pop(vid, None)


def record_video_ideas(meta, cards, pick=None, day=None, store=None):
    """Add one video's checked cards to the store and save it. Returns the outcome of each card,
    in order: (card, id or None, "new" | "merged" | "seen_before")."""
    own_store = store is None
    store = load_ideas() if own_store else store
    day = day or today_key()
    vid = meta.get("id") or video_id_from_url(meta.get("url") or "") or meta.get("url")
    forget_video_ideas(store, vid)
    outcomes = []
    for n, card in enumerate(cards, 1):
        evidence = {"video": vid, "url": meta.get("url"), "title": meta.get("title"),
                    "channel": meta.get("channel") or meta.get("uploader"), "day": day,
                    "line": card.get("line"),
                    "quote": card.get("quote"), "seconds": card.get("seconds"),
                    "how_much": card.get("how_much"), "mark": card.get("mark"),
                    "why_now": card.get("why_now"), "card": card.get("n", n)}
        if card.get("seen_before"):
            store["seen_before"].append(dict(evidence, line=card["line"], **card["killed"]))
            outcomes.append((card, None, "seen_before"))
            continue
        target = card.get("same_as") if card.get("same_as") in store["ideas"] else None
        # 2026-10-08 night, the replay: the write-ups said "Same as" for ideas that only shared a
        # topic. Two lines with no word in common are two ideas.
        if target and not idea_words(store["ideas"][target]["line"]) & idea_words(card["line"]):
            card.setdefault("notes", []).append(
                f"the write-up called it the same as {target}, but the two lines share no word, so "
                f"it is its own card")
            target = None
        if target is None:
            target = next((iid for iid, idea in store["ideas"].items()
                           if same_idea(idea.get("line"), card["line"])), None)
        if target:
            idea = store["ideas"][target]
            if not any(e.get("video") == vid for e in idea["evidence"]):
                idea["evidence"].append(evidence)
            idea["last_day"] = max(idea.get("last_day") or day, day)
            if (idea.get("how_much") or "unknown").startswith("unknown") and card.get("mark"):
                idea["how_much"], idea["mark"] = card["how_much"], card["mark"]
            for t in card.get("transfers", []):
                if len(idea.setdefault("transfers", [])) < IDEA_TRANSFERS_MAX + 2 \
                        and t not in idea["transfers"]:
                    idea["transfers"].append(t)
            outcomes.append((card, target, "merged"))
            continue
        iid = idea_id(store["next"])
        store["next"] += 1
        store["ideas"][iid] = {
            "id": iid, "line": card["line"], "who_pays": card["who_pays"],
            "how_much": card["how_much"], "mark": card["mark"], "why_now": card["why_now"],
            "first_step": card["first_step"], "transfers": list(card["transfers"]),
            "catch": card["catch"], "project": card["project"], "killed": card["killed"],
            "first_day": day, "last_day": day, "evidence": [evidence]}
        outcomes.append((card, iid, "new"))
    picked = None
    if pick and 1 <= pick[0] <= len(cards):
        card, iid, how = outcomes[pick[0] - 1]
        if iid:
            picked = {"id": iid, "why": pick[1] or "the write-up's own pick"}
    store["videos"][vid] = {"day": day, "title": meta.get("title"), "url": meta.get("url"),
                            "ideas": [iid for _c, iid, _h in outcomes if iid],
                            "seen_before": sum(1 for _c, _i, h in outcomes if h == "seen_before"),
                            "pick": picked}
    if own_store:
        save_ideas(store)
        write_ideas_board(store)
    return outcomes


def ideas_for_writeup(meta, transcript_text=None, day=None):
    """The Ideas part of one Sonnet write-up, checked, recorded and rendered: the text to append
    to the saved write-up, or "" when the write-up had no Ideas part (every older stub, every
    local write-up). Called once per saved write-up, from save_writeup."""
    part = meta.get("ideas_part")
    if part is None:
        return ""
    transcript_text = meta.get("transcript") if transcript_text is None else transcript_text
    cards, pick = parse_ideas(part)
    values = figure_values(transcript_text)
    checked = [dict(check_idea_card(c, transcript_text, meta.get("minutes"), values), n=c["n"])
               for c in cards]
    outcomes = record_video_ideas(meta, checked, pick=pick, day=day)
    meta["idea_outcomes"] = [(iid, how) for _c, iid, how in outcomes]
    print(f"    ideas: {sum(1 for o in outcomes if o[2] == 'new')} new, "
          f"{sum(1 for o in outcomes if o[2] == 'merged')} added to a card already on the board, "
          f"{sum(1 for o in outcomes if o[2] == 'seen_before')} seen before")
    return ideas_section_text(meta, outcomes, pick)


def ideas_section_text(meta, outcomes, pick=None):
    """The checked cards as the write-up's last section, "## Ideas"."""
    lines = ["## Ideas", ""]
    if not outcomes:
        return "\n".join(lines + ["none"]) + "\n"
    store = load_ideas()
    for card, iid, how in outcomes:
        if how == "seen_before":
            k = card["killed"]
            lines.append(f"- Seen before: {card['line']}. Killed on {k['date']} because "
                         f"{k['because']}.")
            lines.append("")
            continue
        head = f"### {iid}: {card['line']}"
        if how == "merged":
            backing = len((store["ideas"].get(iid) or {}).get("evidence", []))
            head += f" (on the board already; {backing} videos back it now)"
        lines.append(head)
        lines += idea_card_lines(card, meta.get("url"))
        lines.append("")
    if pick and 1 <= pick[0] <= len(outcomes) and outcomes[pick[0] - 1][1]:
        lines.append(f"Pick: {outcomes[pick[0] - 1][1]}, because {pick[1] or 'it is the strongest here'}.")
    notes = [n for card, _i, _h in outcomes for n in card.get("notes", [])]
    if notes:
        lines.append("")
        lines.append("Checked: " + "; ".join(notes) + ".")
    return "\n".join(lines).rstrip() + "\n"


def idea_card_lines(card, url=None):
    """A card's fields as markdown lines."""
    lines = [f"- Who pays: {card['who_pays']}", f"- How much: {how_much_words(card)}",
             f"- Why now: {card['why_now']}", f"- First step: {card['first_step']}",
             f"- Also works for: {'; '.join(card.get('transfers') or []) or 'unknown'}",
             f"- The catch: {card['catch']}"]
    if card.get("killed") and card["killed"].get("new_evidence"):
        k = card["killed"]
        lines.append(f"- Killed idea, new evidence: {k['name']} was killed on {k['date']} because "
                     f"{k['because']}; this video: {k['new_evidence']}")
    if card.get("project"):
        lines.append(f"- Project: {card['project']}")
    if url:
        seconds = card.get("seconds")
        when = f"about {clock_words(seconds)}" if seconds is not None else "no time (the quote was not found)"
        lines.append(f"- Where: {when}, {timed_link(url, seconds)}")
    return lines


# --- the board and the taste file ---------------------------------------------------------------
IDEAS_BOARD_HEAD = (
    "# Ideas\n\n"
    "Every idea card this tool has written, newest first. Rewritten by worthwatch whenever a write-up "
    "adds or backs a card. Write like or nah next to an id in IDEAS-TASTE.md (or run "
    "`worthwatch like <id>` / `worthwatch nah <id>`), and the next write-ups are shown your taste.\n")


def idea_backing(idea):
    return len({e.get("video") for e in idea.get("evidence", [])})


def ideas_newest_first(store):
    return sorted(store["ideas"].values(),
                  key=lambda i: (i.get("last_day") or "", int(i["id"][2:])), reverse=True)


def write_ideas_board(store=None):
    store = load_ideas() if store is None else store
    lines = [IDEAS_BOARD_HEAD]
    ideas = ideas_newest_first(store)
    lines.append(f"{len(ideas)} idea(s) on the board; {len(store['seen_before'])} killed idea(s) "
                 f"seen again.\n")
    for idea in ideas:
        n = idea_backing(idea)
        lines.append(f"## {idea['id']}: {idea['line']}")
        lines.append(f"Backed by {n} video{'s' if n != 1 else ''}, first {idea.get('first_day')}, "
                     f"last {idea.get('last_day')}.")
        lines.append("")
        lines += idea_card_lines(idea)
        for e in idea.get("evidence", []):
            when = f", about {clock_words(e['seconds'])}" if e.get("seconds") is not None else ""
            own = (f", seen as: {e['line']}" if e.get("line") and e["line"] != idea["line"] else "")
            lines.append(f"- From {e.get('day')}: [{short_title(e.get('title') or e.get('video'))}]"
                         f"({timed_link(e.get('url'), e.get('seconds'))}){when}{own}")
        lines.append("")
    if store["seen_before"]:
        lines.append("## Seen before: killed ideas the videos raised again")
        lines.append("")
        for s in sorted(store["seen_before"], key=lambda s: s.get("day") or "", reverse=True):
            lines.append(f"- {s.get('day')}: {s.get('line')}. Killed on {s.get('date')} because "
                         f"{s.get('because')}. [{short_title(s.get('title') or s.get('video'))}]"
                         f"({s.get('url') or ''})")
        lines.append("")
    path = ideas_board_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")
    ensure_taste_file()
    return path


TASTE_HEAD = (
    "# Ideas: my taste\n\n"
    "One line per idea: its id from IDEAS.md, then like or nah, then any words. For example:\n"
    "> I-0042 like: boring and someone already pays\n"
    "> I-0043 nah: one more course\n\n"
    "`worthwatch like <id> [words]` and `worthwatch nah <id> [words]` add a line here too. The newest line for an "
    "id is the one that counts, and the last 20 go into every write-up's idea prompt.\n\n")
TASTE_LINE_RE = re.compile(r"\b(I-?\d{1,5})\b\W{0,3}(like|nah)\b[\s:,.-]*(.*)$", re.I)


def ensure_taste_file():
    path = ideas_taste_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, TASTE_HEAD)
    return path


def read_taste():
    """[(id, "like" | "nah", words)] in the file's order, the newest line per id kept."""
    path = ideas_taste_path()
    if not path.exists():
        return []
    latest = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lstrip().startswith(("#", ">")) or not line.strip():
            continue   # the heading and the examples in the file's own head
        m = TASTE_LINE_RE.search(line)
        if m:
            iid = normal_idea_id(m.group(1))
            latest.pop(iid, None)
            latest[iid] = (iid, m.group(2).lower(), m.group(3).strip())
    return list(latest.values())


def cmd_taste(word, argv):
    """`worthwatch like <id> [words]` / `worthwatch nah <id> [words]`: one line added to IDEAS-TASTE.md."""
    if not argv:
        print(f"usage: yt {word} <idea id> [words]")
        return 2
    iid = normal_idea_id(argv[0])
    store = load_ideas()
    if not iid or iid not in store["ideas"]:
        print(f"no idea {argv[0]!r} on the board (IDEAS.md lists every id); nothing written")
        return 1
    words = " ".join(argv[1:]).strip()
    path = ensure_taste_file()
    body = path.read_text(encoding="utf-8")
    line = f"{iid} {word}" + (f": {words}" if words else "") + \
        f" (yt {word}, {dt.datetime.now():%Y-%m-%d %H:%M})"
    atomic_write_text(path, body.rstrip("\n") + "\n" + line + "\n")
    print(f"{line}\n  -> {path}")
    return 0


# --- the prompt part ---------------------------------------------------------------------------
IDEAS_INSTRUCTIONS = """=== IDEAS: one more part, after section 9 ===
After section 9, write one more part under the heading "## Ideas". It is not part of the summary's 600 words, and nothing above limits it: this part is open to every horizon.

List every product, business or project idea this video contains or implies, in any field, for consumers or for companies, big or small. An idea does not have to fit any of my projects. My age, my money, licences and the need for hired help are notes on a card, never a reason to leave an idea out, and neither is an idea being for consumers. A funding, merger or trend story counts when it points at something a small builder could make.

Before you write the cards, think these through (and do not write the thinking down):
- What is the shovel in this gold rush?
- What is the boring version that makes money?
- What would this look like for a different customer?
- What could a tiny team build in a month that a big company won't?
- What does this make newly possible?

Each idea is one card, in exactly this form:

### Idea 1: <the idea in one line>
- Who pays: <who would pay for it, or unknown>
- How much: <the price, deal size or revenue the video gives, then (shown) when it was on screen or sourced or (said) when it was only said; or unknown. Never a number the video did not give.>
- Why now: <one sentence>
- First step: <one thing {reader} could do this month with what they have>
- Also works for: <2 or 3 creative transfers, separated by semicolons: the same mechanism for a different customer or market, or carried onto one of my assets ({assets})>
- The catch: <one line>
- Where in the video: "<three to twelve words the video said, verbatim, where the idea comes up>"
- Project: none, or the one project it is for, written as in section 6's labels
- Same as: <an id from the board below, only when it is the same idea, the same thing for the same buyer, never because it is on the same topic; the card then says what this video adds>
- Killed: <a name from the killed list below, only when it is that idea> | new evidence: <what this video shows against the reason it was killed, or none>

Leave out the Same as and Killed lines when they do not apply. After the last card, one line:
Pick: Idea <n>, because <one line on why it is the best idea in this video>
If the video holds no idea at all, the part is the heading and the single word none.

KILLED IDEAS (each one was killed for the reason given; an idea that matches one gets a Killed line, and it is shown in full only when this video brings new evidence against that reason):
{killed}

IDEAS ALREADY ON THE BOARD (newest first; write "Same as: <id>" when this video's idea is one of them):
{board}

MY TASTE (ideas I marked; more like the likes, fewer like the nahs, and never a reason to drop an idea):
{taste}"""


def killed_lines_for_ideas():
    lines = []
    for name, _pattern in KILLED_IDEAS:
        k = killed_note(name)
        lines.append(f"- {name}: killed {k['date']}, because {k['because']}")
    return "\n".join(lines)


def board_lines_for_prompt(store=None):
    store = load_ideas() if store is None else store
    ideas = ideas_newest_first(store)[:IDEAS_ON_BOARD_IN_PROMPT]
    if not ideas:
        return "(none yet)"
    return "\n".join(f"- {i['id']}: {i['line']}" for i in ideas)


def taste_lines_for_prompt(store=None):
    store = load_ideas() if store is None else store
    taste = read_taste()[-TASTE_IN_PROMPT:]
    if not taste:
        return "(none yet)"
    out = []
    for iid, word, words in taste:
        line = (store["ideas"].get(iid) or {}).get("line") or "(no longer on the board)"
        out.append(f"- {word.upper()} {iid}: {line}" + (f" ({words})" if words else ""))
    return "\n".join(out)


def ideas_instructions():
    return IDEAS_INSTRUCTIONS.format(reader=READER,
                                     assets=", ".join(IDEA_ASSETS) or "none listed",
                                     killed=killed_lines_for_ideas() or "(none)",
                                     board=board_lines_for_prompt(),
                                     taste=taste_lines_for_prompt())


# --- the digest's ideas -------------------------------------------------------------------------
DIGEST_IDEAS_RE = re.compile(r"<!-- IDEAS-START -->.*?<!-- IDEAS-END -->\n*", re.DOTALL)


def day_ideas(day, store=None):
    """[(idea, evidence)] for every card a video written up on `day` added or backed, in the order
    the day met them."""
    store = load_ideas() if store is None else store
    out = []
    for idea in store["ideas"].values():
        for e in idea.get("evidence", []):
            if e.get("day") == day:
                out.append((idea, e))
    order = {vid: n for n, vid in enumerate(store["videos"])}
    return sorted(out, key=lambda pair: (order.get(pair[1].get("video"), 0), pair[1].get("card", 0)))


def ideas_today_lines(day, store=None):
    """"## Ideas today": the remix's picks once it has run, else each video's own pick in the
    order the day met them; at most IDEAS_TODAY_MAX, each with its one line on why. [] for a day
    with no ideas, so every older digest is written as it always was."""
    store = load_ideas() if store is None else store
    pairs = day_ideas(day, store)
    if not pairs:
        return []
    remix = (store["remixes"].get(day) or {})
    picks = [(p["id"], p["why"]) for p in remix.get("top", []) if p.get("id") in store["ideas"]]
    source = "the remix's picks" if picks else "each video's own pick, in the order they were met"
    if not picks:
        for vid, video in store["videos"].items():
            pick = (video or {}).get("pick")
            if video.get("day") == day and pick and pick.get("id") in store["ideas"]:
                if pick["id"] not in [p for p, _w in picks]:
                    picks.append((pick["id"], pick["why"]))
    lines = ["## Ideas today", "", f"Up to {IDEAS_TODAY_MAX}, {source}. Every card is on the "
                                   f"board, IDEAS.md.", ""]
    for n, (iid, why) in enumerate(picks[:IDEAS_TODAY_MAX], 1):
        idea = store["ideas"][iid]
        e = next((e for e in idea["evidence"] if e.get("day") == day), idea["evidence"][-1])
        lines.append(f"{n}. **{iid}** {idea['line']} Why: {why} "
                     f"[{short_title(e.get('title') or e.get('video'))}]"
                     f"({timed_link(e.get('url'), e.get('seconds'))})")
    if not picks:
        lines.append("No video picked one of its ideas today; every card is listed further down.")
    return lines + [""]


def every_idea_lines(day, store=None):
    """The day's other block: the remix's combos, then every card the day added or backed, one
    line each, then the killed ideas seen again."""
    store = load_ideas() if store is None else store
    pairs = day_ideas(day, store)
    seen = [s for s in store["seen_before"] if s.get("day") == day]
    if not pairs and not seen:
        return []
    lines = []
    remix = store["remixes"].get(day) or {}
    if remix.get("combos"):
        lines += ["## Remix of the day", "",
                  f"Up to {REMIX_COMBOS_MAX} combos of this day's and this week's cards, by "
                  f"{remix.get('model', 'the model')} at {remix.get('at', '')[11:16]}.", ""]
        for n, c in enumerate(remix["combos"], 1):
            lines.append(f"{n}. **{c['line']}** ({' + '.join(c['ids'])}) Why: {c['why']} "
                         f"First step: {c['first_step']}")
        lines.append("")
    lines += [f"## Every idea today ({len(pairs)})", ""]
    for idea, e in pairs:
        n = idea_backing(idea)
        back = f"; {n} videos back it" if n > 1 else ""
        own = e.get("line")
        head = (f"{own} (joins {idea['id']}: {idea['line']})" if own and own != idea["line"]
                else idea["line"])
        lines.append(f"- **{idea['id']}** {head} Who pays: {idea['who_pays']}; "
                     f"how much: {how_much_words(idea)}{back}. "
                     f"[{short_title(e.get('title') or e.get('video'))}]"
                     f"({timed_link(e.get('url'), e.get('seconds'))})")
    for s in seen:
        lines.append(f"- Seen before: {s.get('line')}. Killed on {s.get('date')} because "
                     f"{s.get('because')}. [{short_title(s.get('title') or s.get('video'))}]"
                     f"({s.get('url') or ''})")
    return lines + [""]


# --- the daily remix ----------------------------------------------------------------------------
REMIX_INSTRUCTIONS = """You are remixing a day's idea cards for {reader}, who wants new side projects beside the ones they already run and is open to every horizon. Below are the cards the day's videos produced, then the rest of the week's. Each card has an id.

THE DAY'S CARDS ({day}):
{today}

THE REST OF THE WEEK'S CARDS:
{week}

Do two things.
1. Pick the day's best ideas, at most {top_max}, from the day's cards only: the ones most worth their afternoon, any field. One line on why each.
2. Write up to {combo_max} combos: "idea A plus idea B", or "idea A for customer X", using any of the cards above. Each must be something {reader} could start this month with what they have, and say why it could work.

Reply with these lines and nothing else, no quote marks, no dashes used as punctuation, and no thinking aloud:
TOP: <id> | <why, one line>
(one TOP line per pick)
COMBO: <id> + <id> (or: <id> for <customer>) | <the combo in one line> | why: <one sentence> | first step: <one sentence>
(one COMBO line per combo)
Use only ids that appear above. Never invent a number."""


def how_much_words(card):
    """A card's figure with its mark, the mark once even when the model already wrote it."""
    how = card.get("how_much") or "unknown"
    mark = card.get("mark")
    return f"{how} ({mark})" if mark and f"({mark}" not in how else how


def remix_card_line(idea):
    return (f"- {idea['id']}: {idea['line']} (who pays: {idea['who_pays']}; how much: "
            f"{idea['how_much']}; catch: {idea['catch']})")


def remix_prompt(day, store):
    today = [idea for idea, _e in day_ideas(day, store)]
    today_ids = list(dict.fromkeys(i["id"] for i in today))
    monday = week_monday(day)
    week_days = {(monday + dt.timedelta(days=n)).isoformat() for n in range(7)}
    week = [i for i in ideas_newest_first(store) if i["id"] not in today_ids
            and any(e.get("day") in week_days and e.get("day") <= day for e in i["evidence"])]
    prompt = REMIX_INSTRUCTIONS.format(
        reader=READER,
        day=day, today="\n".join(remix_card_line(store["ideas"][i]) for i in today_ids),
        week="\n".join(remix_card_line(i) for i in week) or "(none)",
        top_max=REMIX_TOP_MAX, combo_max=REMIX_COMBOS_MAX)
    return prompt, today_ids, [i["id"] for i in week]


def parse_remix(text, today_ids, known_ids):
    """(top, combos) from the reply, checked: a TOP id must be one of the day's cards, every combo
    id a card that was in the prompt; anything else is dropped, never repaired. Pure."""
    top, combos = [], []
    for raw in normalize_punct(text or "").splitlines():
        line = raw.strip().lstrip("-* ").strip()
        m = re.match(r"^TOP\s*:\s*(.+?)\s*\|\s*(.+)$", line, re.I)
        if m:
            iid = normal_idea_id(m.group(1))
            if iid in today_ids and iid not in [t["id"] for t in top] and len(top) < REMIX_TOP_MAX:
                top.append({"id": iid, "why": _idea_value(m.group(2)).rstrip(".") + "."})
            continue
        m = re.match(r"^COMBO\s*:\s*(.+?)\s*\|\s*(.+?)\s*\|\s*why\s*:\s*(.+?)\s*\|\s*first\s+step\s*:"
                     r"\s*(.+)$", line, re.I)
        if m and len(combos) < REMIX_COMBOS_MAX:
            ids = [idea_id(x) for x in re.findall(r"\bI-?(\d{1,5})\b", m.group(1), re.I)]
            if ids and all(i in known_ids for i in ids):
                combos.append({"ids": ids, "how": _idea_value(m.group(1)),
                               "line": _idea_value(m.group(2)).rstrip("."),
                               "why": _idea_value(m.group(3)).rstrip(".") + ".",
                               "first_step": _idea_value(m.group(4)).rstrip(".") + "."})
    return top, combos


def claude_generate_with(prompt, model, timeout=600):
    """claude -p with a named model; the same handling as claude_generate."""
    claude_path = shutil.which("claude")
    if not claude_path:
        raise RuntimeError("claude CLI not found on PATH.")
    proc = subprocess.Popen([claude_path, "-p", "--model", model], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", **group_kwargs())
    try:
        stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise RuntimeError(f"claude CLI ({model}) timed out after {timeout}s and was killed.")
    if proc.returncode != 0 or not (stdout or "").strip():
        raise RuntimeError(f"claude CLI ({model}) failed (exit {proc.returncode}): "
                           f"{(stderr or '').strip()[:300]}")
    return stdout.strip()


def remix_day(day, store=None, now=None):
    """One remix of one day: one call (Opus, or Sonnet when Opus fails), checked, kept in the
    store, the day's digest and the board rewritten. Returns the remix entry."""
    own = store is None
    store = load_ideas() if own else store
    now = now or dt.datetime.now()
    prompt, today_ids, week_ids = remix_prompt(day, store)
    if not today_ids:
        entry = {"at": now.isoformat(timespec="seconds"), "model": None, "top": [], "combos": [],
                 "none": "no idea cards that day"}
        store["remixes"][day] = entry
        if own:
            save_ideas(store)
        return entry
    reply, model, errors = None, None, []
    # Claude: the strongest model first, the next if it fails. Every other brain: its writing
    # model, once.
    attempts = ([(name, lambda n=name: claude_generate_with(prompt, n,
                                                             timeout=REMIX_TIMEOUT_SECONDS))
                 for name in REMIX_MODELS] if BRAIN == "claude"
                else [(write_label(), lambda: write_generate(prompt, timeout=REMIX_TIMEOUT_SECONDS,
                                                             size="large"))])
    for name, call in attempts:
        try:
            reply, model = call(), name
            break
        except RuntimeError as e:
            errors.append(f"{name}: {one_line(str(e))[:160]}")
    if reply is None:
        print(f"remix of {day}: no model answered ({'; '.join(errors)}); a later run tries again")
        return None
    top, combos = parse_remix(reply, set(today_ids), set(today_ids) | set(week_ids))
    entry = {"at": now.isoformat(timespec="seconds"), "model": model, "top": top,
             "combos": combos, "errors": errors}
    store["remixes"][day] = entry
    if own:
        save_ideas(store)
        write_ideas_board(store)
        refresh_digest(day)
        refresh_week_board(day)
    print(f"remix of {day} by {model}: {len(top)} pick(s), {len(combos)} combo(s)")
    return entry


def remix_due_days(now=None, store=None):
    """The days whose remix is due, oldest first: a day with ideas and no remix, whose last
    scheduled run has started (today from REMIX_LAST_HOUR, any earlier day), at most
    REMIX_CATCH_UP_DAYS back."""
    now = now or dt.datetime.now()
    store = load_ideas() if store is None else store
    due = []
    for back in range(REMIX_CATCH_UP_DAYS, -1, -1):
        day = (now.date() - dt.timedelta(days=back)).isoformat()
        if back == 0 and now.hour < REMIX_LAST_HOUR:
            continue
        if day in store["remixes"] or not day_ideas(day, store):
            continue
        due.append(day)
    return due


def cmd_remix(argv=()):
    """`worthwatch remix`: the remix of every day that is due (a scheduled run can call it after
    --topics). `worthwatch remix <day>` or `worthwatch remix today` remixes that day now. No
    YouTube request; holds the lock while it writes."""
    days = [dt.date.today().isoformat() if a == "today" else a for a in argv
            if a == "today" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)]
    due = days or remix_due_days()
    if not due:
        print(f"remix: nothing due (today's remix waits until {REMIX_LAST_HOUR:02d}:00, and each "
              f"day is remixed once; `worthwatch remix today` runs it now)")
        return 0
    acquire_lock()
    try:
        for day in due:
            remix_day(day)
    finally:
        release_lock()
    return 0


# --- the Friday weekly's idea board -------------------------------------------------------------
WEEK_BOARD_RE = re.compile(r"<!-- IDEABOARD-START -->.*?<!-- IDEABOARD-END -->\n*", re.DOTALL)


def week_board_lines(day, store=None):
    """The week's top WEEK_BOARD_TOP ideas and its remixes, for the weekly file: liked first,
    then the most videos backing it this week, then picked by a remix, then newest."""
    store = load_ideas() if store is None else store
    end = dt.date.fromisoformat(day)
    days = {(end - dt.timedelta(days=n)).isoformat() for n in range(WEEK_LOOKBACK_DAYS)}
    taste = {iid: word for iid, word, _w in read_taste()}
    picked = collections.Counter(p["id"] for d, r in store["remixes"].items() if d in days
                                 for p in r.get("top", []))
    week = [i for i in store["ideas"].values() if any(e.get("day") in days for e in i["evidence"])]

    def rank(i):
        backing = len({e.get("video") for e in i["evidence"] if e.get("day") in days})
        return (taste.get(i["id"]) == "like", taste.get(i["id"]) != "nah", backing,
                picked.get(i["id"], 0), i.get("last_day") or "", int(i["id"][2:]))
    top = sorted(week, key=rank, reverse=True)[:WEEK_BOARD_TOP]
    lines = ["<!-- IDEABOARD-START -->", "## Idea board", "",
             f"The week's top {WEEK_BOARD_TOP} of {len(week)} idea card(s): liked first, then the "
             f"most videos backing it this week, then picked by a remix, then newest.", ""]
    for n, i in enumerate(top, 1):
        e = i["evidence"][-1]
        lines.append(f"{n}. **{i['id']}** {i['line']} Who pays: {i['who_pays']}; how much: "
                     f"{how_much_words(i)}. First step: {i['first_step']} "
                     f"[{short_title(e.get('title') or e.get('video'))}]"
                     f"({timed_link(e.get('url'), e.get('seconds'))})")
    if not top:
        lines.append("No idea cards this week.")
    combos = [(d, c) for d, r in sorted(store["remixes"].items()) if d in days
              for c in r.get("combos", [])]
    lines += ["", "### The week's remixes", ""]
    for d, c in combos:
        lines.append(f"- {d}: **{c['line']}** ({' + '.join(c['ids'])}) Why: {c['why']} "
                     f"First step: {c['first_step']}")
    if not combos:
        lines.append("No remix this week yet.")
    return lines + ["<!-- IDEABOARD-END -->", ""]


def refresh_week_board(day):
    """Rewrite the idea board inside the weekly file written on `day`, when there is one (a remix
    on a Friday lands after the weekly was written)."""
    path = OUTPUT_DIR / f"WEEK-{day}.md"
    if not path.exists():
        return None
    body = path.read_text(encoding="utf-8")
    board = "\n".join(week_board_lines(day)) + "\n"
    if WEEK_BOARD_RE.search(body):
        body = WEEK_BOARD_RE.sub(lambda _m: board, body, count=1)
    else:
        head, sep, rest = body.partition("\n\n")
        body = head + sep + board + "\n" + rest
    atomic_write_text(path, body)
    return path


# --- the triage keep rule -----------------------------------------------------------------------
# The brief: "the local model must keep any video with a business idea, a revenue or price
# number, or a founder story, even if it fits no live project." The triage prompt says so; this
# is the code behind it, because the prompt alone is a request. It only ever lifts a NO to MAYBE,
# so it cannot make a YES or an OPPORTUNITY, and it reads the words, not the model.
KEEP_MONEY_RE = re.compile(
    r"(?:[$£€₹]|\brs\.?\s?)\s?\d[\d,.]*\s*(?:k|m|b|million|billion|thousand|lakh"
    r"|crore)?\b|\b\d[\d,.]*\s*(?:k|m)?\s*(?:dollars|bucks|rupees|euros|pounds)\b"
    r"|\b\d[\d,.]*\s*(?:k|m)?\s*(?:a|per|/)\s*(?:month|mo|year|yr)\b|\b(?:mrr|arr)\b"
    r"|\b(?:revenue|profit|per\s+month|a\s+month)\b[^.]{0,40}\d", re.I)
KEEP_FOUNDER_RE = re.compile(
    r"\b(?:i|we)\s+(?:built|started|launched|founded|bootstrapped|grew|sold|quit\s+my\s+job)\b"
    r"|\bmy\s+(?:startup|company|business|saas|app|agency|side\s+(?:project|hustle))\b"
    r"|\bco-?founders?\b|\bfounders?\b|\bfirst\s+(?:paying\s+)?customers?\b"
    r"|\b(?:business|startup|app|product|saas)\s+ideas?\b", re.I)


IDEAS_LANE_SECTION_RE = re.compile(r"^## The ideas lane[ \t]*\r?\n.*?(?=^## |\Z)", re.M | re.S)
# Sentences of your context file that are the ideas lane's business only, left out of what the
# triage reads, word for word. Empty by default.
LANE_ONLY_SENTENCES = ()


def context_for_triage(context_text):
    """The context file as the triage reads it: without a section headed "## The ideas lane" and
    without LANE_ONLY_SENTENCES. The triage gives the four project verdicts, which the ideas lane
    does not change, and a small local model moves on any wording, so what is only the lane's
    business is kept out of its prompt."""
    text = IDEAS_LANE_SECTION_RE.sub("", context_text or "", count=1)
    for sentence in LANE_ONLY_SENTENCES:
        words = [re.escape(w) for w in sentence.split()]
        text = re.sub(r"[ \t]*" + r"\s+".join(words), "", text, count=1)
    return text


def ideas_keep_reason(details):
    """The words that keep a video at MAYBE at least, or None: a money figure or a founder's
    story, in the transcript when there is one, else in the title and the description."""
    text = details.get("transcript") or " ".join(str(details.get(k) or "")
                                                 for k in ("title", "description"))
    for what, pattern in (("a money figure", KEEP_MONEY_RE), ("a founder's story", KEEP_FOUNDER_RE)):
        m = pattern.search(text)
        if m:
            return f"{what} ({one_line(m.group(0))[:40]})"
    return None


# RULE 3. Every "Try this week" line is checked against the standing decisions, and a line
# that contradicts one is replaced by the contradiction. The prose version of this list is
# your context file's "Decisions already made" and is what the model reads; this is the
# machine half: the [[decision]] rules of rules.toml, as (name, pattern, unless pattern or
# None), first match wins. `worthwatch doctor` says when a name is not in that section, so the
# two cannot drift apart silently.
#
# `unless` exists for cases like this: cold email can be closed as a FIRST move while following
# up with somebody already written to is open. A rule that cannot tell those apart would strike
# the correct action. apply_profile() sets it.
STANDING_DECISIONS = ()
# Every decision's name should be a phrase that appears VERBATIM in the context file's
# "Decisions already made" section, lowercased: the name is the sentence a summary prints in
# place of your action, and a tool that tells you that you broke a decision you cannot find
# written down is worse than one that says nothing.
CONTRADICTS_LINE = "Not tried: contradicts a standing decision ({name})."
# A decision that does not hold for the projects named here (a rule's `exempt`), name ->
# projects. A line whose project is not known is held to it.
DECISION_EXEMPT = {}
# And the other way round: a decision that holds only for the projects named here (a rule's
# `only`). A line whose project is not known is not held to it.
DECISION_ONLY = {}

# What a YES has to be: one live project, one action, and nothing that the context file records
# as done or decided, and not generic advice. Everything else is MAYBE.
#
# It reads the finished write-up and never the triage prompt: a bound like "within two weeks"
# put into the triage prompt was measured costing keeps, because a small model reads any bound
# on the reader's circumstances as a licence to veto. Here the action is the write-up's own
# section 9 or section 8, which is already one thing to do in an afternoon, so "doable within
# two weeks" is the write-up's to state and this only checks that there is one.
LIVE_PROJECTS = tuple(p for p in PROJECTS if p != "a road not taken")
# Done or decided: the [[done]] rules of rules.toml, as (name, the one project it applies to or
# None, the pattern). Each name should be a phrase of the context file, so the row points at a
# sentence you can find. apply_profile() sets it.
DONE_OR_DECIDED = ()
# Generic advice, never a YES: the [[generic]] rules of rules.toml, as (name, the one project
# it applies to or None for every project, the pattern). apply_profile() sets it.
GENERIC_ADVICE = ()
# A YES filed under the work label (the [you] work_label setting, "how I work" by default) has
# to be a change to your own way of working, not a general tip filed there. Each [[work]] rule
# is a part of how you work, named as your context file's "How I work" names it, and what a
# write-up says when it means that part: the write-up's action or its section 6 entry has to
# name one. SETUP_PROJECT is the label, OWN_SETUP the parts; apply_profile() sets both.
SETUP_PROJECT = "how I work"
OWN_SETUP = ()
# And at most this many keeps a day under the label; the rest become MAYBE, first come first
# kept, with the reason in the row and a line in the log (the [you] work_label_daily_cap
# setting).
LABEL_DAILY_CAP = 3
STRUCK_RE = re.compile(r"contradicts a standing decision \((.+?)\)\.?\s*$", re.I)


def yes_problem(record):
    """Why a YES is not one, as a phrase for the row, or None. Pure: it reads the record."""
    project = record.get("project")
    if project not in LIVE_PROJECTS:
        return f"{project or 'no project'} is not a live project"
    try_line = one_line(record.get("try_this_week") or "")
    if NOT_TRIED_RE.match(try_line):
        struck = STRUCK_RE.search(try_line)
        return (f"its action contradicts a standing decision ({struck.group(1)})" if struck
                else "its action was struck as contradicting a standing decision")
    action = header_sentence(record)
    if not action:
        return "the write-up names no action to take"
    entry = unquoted(APPLIES_LABEL_RE.sub("", one_line(record.get("takeaway") or "")))
    texts = (action, entry)
    for text in texts:
        name = try_line_contradiction(text, project)
        if name:
            return f"it contradicts a standing decision ({name})"
    for name, only, pattern in DONE_OR_DECIDED:
        if (only is None or project == only) and any(pattern.search(t) for t in texts):
            return f"it repeats what is already done or decided ({name})"
    for name, only, pattern in GENERIC_ADVICE:
        if (only is None or project == only) and any(pattern.search(t) for t in texts):
            return f"it is generic advice ({name})"
    # Under the work label, a change to your own way of working.
    if project == SETUP_PROJECT and not any(pattern.search(t) for _name, pattern in OWN_SETUP
                                            for t in texts):
        parts = ", ".join(name for name, _pattern in OWN_SETUP[:7]) or "none are listed"
        return (f"it names no part of {SETUP_PROJECT} ({parts}), so it is a general tip")
    if record.get("label_cap"):
        return record["label_cap"]
    return None


def label_cap_reason(kept_before):
    return (f"the day's keep number {kept_before + 1} under {SETUP_PROJECT}, and at most "
            f"{LABEL_DAILY_CAP} a day are kept there")


def label_cap_for(video_id, project, settled):
    """Why this write-up's YES is not kept, when it is a keep under the label and the run's
    day already has LABEL_DAILY_CAP of them written; else None. Brief part 3."""
    if settled != "YES" or project != SETUP_PROJECT:
        return None
    day = today_key()
    before = 0
    for record in load_records().values():
        if (record.get("id") != video_id and record.get("project") == SETUP_PROJECT
                and record.get("written_day") == day and record.get("summary_state") == "WRITTEN"
                and record.get("reason_code") in REASON_CODES
                and "kept" in final_verdict(record)[2]):
            before += 1
    return label_cap_reason(before) if before >= LABEL_DAILY_CAP else None


def apply_label_cap_to_days(records):
    """Brief part 3: the day's keeps under the label past LABEL_DAILY_CAP, marked, in the order
    given, which is the order they were written. For a set of records scored together, such as
    the golden set; a run marks each record as it is written (summary_written)."""
    kept = {}
    for record in records:
        if record.get("project") != SETUP_PROJECT or record.get("summary_state") != "WRITTEN":
            continue
        record.pop("label_cap", None)
        if "kept" not in final_verdict(record)[2]:
            continue
        day = record.get("written_day") or record.get("day")
        if kept.get(day, 0) >= LABEL_DAILY_CAP:
            record["label_cap"] = label_cap_reason(kept[day])
        else:
            kept[day] = kept.get(day, 0) + 1
    return records


def demote_yes_without_a_live_action(verdict, summary_text):
    """(verdict, why or None): the YES rule on a finished summary, so the verdict history.jsonl
    keeps is the one the digest shows. final_verdict decides it again from the record."""
    if verdict != "YES" or not summary_text:
        return verdict, None
    _applies, project, takeaway = section6_state(summary_text)
    if not project:
        return verdict, None       # rule 1 says it, in its own words
    why = yes_problem({"project": project, "takeaway": takeaway,
                       "try_this_week": extract_try_this_week(summary_text),
                       "header": writeup_header(summary_text)})
    return ("MAYBE", f"demoted from YES: {why}") if why else (verdict, None)


def settle_verdict(verdict, summary_text):
    """(verdict, text, notes): rules 1 and 2 together, in one place, so no call site can run
    one and skip the other. Order matters. The OPPORTUNITY gate goes first, because a failed
    gate deletes the Opportunity line and a demoted verdict must not still be carrying one;
    then the nothing-applies rule, which catches what is left, including a YES."""
    notes = []
    verdict, summary_text, why = gate_opportunity_verdict(verdict, summary_text)
    if why:
        notes.append(why)
    verdict, why = demote_if_nothing_applies(verdict, summary_text)
    if why:
        notes.append(why)
    verdict, why = demote_yes_without_a_live_action(verdict, summary_text)   # fix 4, 2026-09-25
    if why:
        notes.append(why)
    return verdict, summary_text, notes


def try_line_contradiction(line, project=None):
    """The first standing decision a "Try this week" line contradicts, or None. `project` is the
    one the write-up files the line under, when it is known: a decision in DECISION_EXEMPT does
    not hold for its exempt projects, and one in DECISION_ONLY holds for its projects alone."""
    if not line or is_nothing_useful(line):
        return None
    for name, pattern, unless in STANDING_DECISIONS:
        if project and project in DECISION_EXEMPT.get(name, ()):
            continue
        if name in DECISION_ONLY and project not in DECISION_ONLY[name]:
            continue
        if pattern.search(line) and not (unless and unless.search(line)):
            return name
    return None


def enforce_try_against_decisions(summary_text):
    """(text, the decision it hit or None). Runs last on section 8, after the one-sentence
    rule, so what is checked is the line that actually reaches history.jsonl and
    TRY-NEXT.md."""
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, TRY_HEADING_RE)
    if bounds is None:
        return summary_text, None
    start, end = bounds
    project = section6_state(summary_text)[1]    # 2026-09-26: a decision may not hold for it
    for i in range(start + 1, end):
        line = lines[i].strip()
        if not line:
            continue
        name = try_line_contradiction(line, project)
        if name is None:
            return summary_text, None
        lines[i] = CONTRADICTS_LINE.format(name=name)
        return "\n".join(lines), name
    return summary_text, None


def enforce_try_this_week(summary_text, kept, checked):
    """Section 8 is the line that reaches history.jsonl and TRY-NEXT.md, so it has to
    follow section 6. Without this the invented bridge simply moves down two headings.

    The trigger is "nothing in section 6 survived", not "the top item fell": a video whose
    best point was invented can still have had a real second point, and that one is a
    perfectly good thing to try this week. And if there was no section 6 to check at all,
    nothing is forced - that summary already carries its own banner, and overwriting a
    line we could not check would be the same sin in the other direction."""
    if kept or not checked:
        return summary_text, False
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, TRY_HEADING_RE)
    if bounds is None:
        return summary_text, False
    start, end = bounds
    body = [l for l in lines[start + 1:end] if l.strip()]
    already = len(body) == 1 and is_nothing_useful(body[0].strip())
    lines[start + 1:end] = ["", "nothing", ""]
    # Rewritten even when it already said nothing, because "Nothing", "None." and
    # "nothing" are three different strings to everything downstream: history.jsonl keeps
    # this line verbatim, and TRY-NEXT.md used to collect the ones that said "None."
    return "\n".join(tidy_blanks(lines)), not already


# 2026-09-27. Section 9 follows section 6. The prompt already says section 9 is "nothing" when
# section 8 is, and section 8 is when section 6 is, but every guard that empties section 6
# rewrote section 8 alone: one write-up
# thought aloud twice, sections 6, 7 and 8 were set to nothing, and its section 9 still said
# "Try asking Claude to write and refine your prompt before running it", in the write-up and in
# the digest. writeup_header already kept that sentence out of the record, because section 8
# held no action; this makes the write-up itself say the same.
HEADER_NOTHING_NOTE = "    (section 6 came to nothing, so section 9, the header, is nothing too)"


def enforce_header_follows_section6(summary_text):
    """(text, rewritten). A write-up whose section 6 came to nothing has the header "nothing",
    whatever the model wrote there. Only section 9 changes, and a write-up with no section 9 is
    not given one. finalize_summary runs it last, on the local path too, so no guard that
    empties section 6 has to know that section 9 exists."""
    if section6_state(summary_text)[0] != "nothing":
        return summary_text, False
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, HEADER_HEADING_RE)
    if bounds is None:
        return summary_text, False
    start, end = bounds
    if [l.strip() for l in lines[start + 1:end] if l.strip()] == ["nothing"]:
        return summary_text, False
    lines[start + 1:end] = ["", "nothing", ""]
    return "\n".join(tidy_blanks(lines)), True


def enforce_try_one_sentence(summary_text, model):
    """Section 8 is one sentence a person could do in an afternoon, or "nothing". A line
    that runs to a second sentence or past 200 characters is handed to the local model to
    compress rather than cut, because a mechanical cut can take the point and leave the
    padding. Quoted spans are ignored when counting sentence ends: a line that quotes the
    video's question is still one sentence. Returns (text, compressed)."""
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, TRY_HEADING_RE)
    if bounds is None:
        return summary_text, False
    start, end = bounds
    for i in range(start + 1, end):
        line = lines[i].strip()
        if not line:
            continue
        if is_nothing(line) or is_nothing_useful(line):
            if line != "nothing":
                lines[i] = "nothing"      # one spelling of nothing reaches history.jsonl
                return "\n".join(lines), False
            return summary_text, False
        probe = unquoted(line)
        if len(re.findall(r"[.!?](?:\s|$)", probe)) <= 1 and len(line) <= 200:
            return summary_text, False
        compressed = ensure_one_sentence(line, model or DEFAULT_LOCAL_MODEL)
        if compressed and compressed.strip() != line:
            lines[i] = compressed.strip()
            return "\n".join(lines), True
        return summary_text, False
    return summary_text, False


def force_nothing(summary_text, headings):
    """Blank out whole judgment sections, for the case where the model would not stop
    reasoning at me. Used only after a re-ask has already failed."""
    lines = summary_text.splitlines()
    for heading in headings:
        bounds = section_bounds(lines, heading)
        if bounds is None:
            continue
        start, end = bounds
        lines[start + 1:end] = ["", "nothing", ""]
    return "\n".join(tidy_blanks(lines))


MISSING_SECTIONS = ((BELIEFS_HEADING_RE, "## 7. What contradicts what I believe",
                     NO_CONTRADICTION_LINE),
                    (TRY_HEADING_RE, "## 8. Try this week", "nothing"))


def ensure_judgment_sections(summary_text):
    """A heading the model simply did not write gets written, saying nothing.

    On 2026-09-10 a summary put this week's action under heading 7 and left heading 8 off
    the page altogether, and everything downstream read that as "no section 8 to check"
    rather than "no action" - which is the difference between a guard that measured and a
    guard that shrugged. Section 6 is not in this list on purpose: a missing section 6
    already raises its own SECTION 6 NOT VERIFIED banner, which is louder than this."""
    lines = summary_text.splitlines()
    added = 0
    for heading_re, heading, filler in MISSING_SECTIONS:
        if section_bounds(lines, heading_re) is not None:
            continue
        block = ["", heading, "", filler]
        # In numeric order where there is a higher heading to sit in front of, so a summary
        # missing only section 7 does not end up reading 6, 8, 7.
        after = section_bounds(lines, TRY_HEADING_RE) if heading_re is BELIEFS_HEADING_RE else None
        if after:
            lines[after[0]:after[0]] = block
        else:
            lines += block
        added += 1
    return ("\n".join(lines) if added else summary_text), added


def clean_summary(summary_text, transcript_text, model=None, verdict=None, meta=None):
    """Every rule applied once, in order, with no model calls of its own. Returns
    (text, report)."""
    text, added_sections = ensure_judgment_sections(normalize_punct(summary_text))
    haystack, bigrams = transcript_index(transcript_text)
    text, invented = enforce_quotes_are_the_video(text, *document_index(transcript_text, meta))
    text, (kept, struck, checked) = enforce_applies_citations(text, transcript_text,
                                                              haystack, bigrams)
    text, collapsed = enforce_contradictions(text)
    text, dropped_opportunity = enforce_opportunity_line(text, verdict)
    text, forced = enforce_try_this_week(text, kept, checked)
    return text, {"kept": kept, "struck": struck, "checked": checked, "invented": invented,
                  "collapsed": collapsed, "dropped_opportunity": dropped_opportunity,
                  "forced": forced, "added_sections": added_sections,
                  "aloud": len(thinking_aloud_lines(text))}


LOCAL_JUDGMENT_BANNER = (
    "> **Sections 6, 7 and 8 are blank: a local summary does not write them.** Across all "
    "31 summaries the local model has written, the number of \"What applies to me\" items "
    "that survive being read by a stranger who was told only the project and the quote is "
    "zero. Sections 1 to 5 below are its own work and are what it is good at.")


JUDGMENT_SECTIONS = (
    (APPLIES_HEADING_RE, "## 6. What applies to me", "nothing"),
    (BELIEFS_HEADING_RE, "## 7. What contradicts what I believe", NO_CONTRADICTION_LINE),
    (TRY_HEADING_RE, "## 8. Try this week", "nothing"),
)


def local_judgment_unavailable(summary_text):
    """What a local summary is allowed to publish.

    Sections 1 to 5 are reporting - what the video is, what it claimed, what was shown
    versus said, the method, the exact words worth keeping - and the local model does them
    well and fast. Sections 6, 7 and 8 are judgment, and it cannot do them: not on vague
    content, where it invented an item in eleven of fifteen measured runs, and not on
    concrete content either, where every one of the 2026-09-02 app-revenue summaries bent a
    real quote onto a project out of CONTEXT.md. The automatic pipeline no longer sends it
    anything (see choose_backend); this is the door that is left, and it is shut here so
    the failure cannot come back through a flag.

    Blanking the sections in place is not enough, because the model does not always number
    its own headings right: on 2026-09-10 a local summary put "Most applicable: ... Applies
    to: this tool" under heading 5 and then labelled the next three headings 6, 7 and 8 with
    the wrong titles. Blanking by number left the judgment sitting in section 5 where
    nothing was looking for it. So the three sections are cut out wherever they landed and
    rewritten from scratch, and any stray item line is deleted from what is left."""
    lines = summary_text.splitlines()
    drop = set()
    for heading_re, _, _ in JUDGMENT_SECTIONS:
        bounds = section_bounds(lines, heading_re)
        if bounds:
            drop.update(range(*bounds))
    kept = [l for i, l in enumerate(lines)
            if i not in drop
            and not MOST_APPLICABLE_RE.match(l) and not APPLIES_LABEL_RE.search(l)]
    for _, heading, filler in JUDGMENT_SECTIONS:
        kept += ["", heading, "", filler]
    return LOCAL_JUDGMENT_BANNER + "\n\n" + "\n".join(tidy_blanks(kept))


def finalize_summary(summary_text, transcript_text, model=None, verdict=None,
                     regenerate=None, meta=None, backend=None):
    """Everything that has to happen to a summary between the model returning it and
    anyone reading it, in one place so no backend or entry point can skip a step.

    The one model call this makes on its own is the re-ask: a summary that thought aloud
    is asked for again, once, with the defect named. If the second answer has the same
    shape the judgment sections are set to "nothing" - which is the honest reading of a
    model that cannot say what applies without narrating its own deliberation."""
    text = normalize_punct(summary_text)
    if backend and not writes_judgment(backend):
        # No re-ask and no guards to run on sections that are about to be blank; a local
        # summary is sections 1 to 5 and nothing else.
        text, _ = clean_summary(text, transcript_text, model, verdict, meta)
        print("    (local summary: sections 6, 7 and 8 left blank, see the banner)")
        text, headed = enforce_header_follows_section6(local_judgment_unavailable(text))
        if headed:
            print(HEADER_NOTHING_NOTE)
        return text
    reasked = False
    if thinking_aloud_lines(text) and regenerate is not None:
        print("    (the summary reasoned aloud instead of answering; asking once more)")
        try:
            second = normalize_punct(regenerate(REASK_NOTE))
            reasked = True
            if second.strip() and not thinking_aloud_lines(second):
                text = second
            elif second.strip():
                text = second     # no better, but no worse; the sections below are forced
        except Exception as e:  # noqa: BLE001 - a failed re-ask must not lose the summary
            print(f"    (could not re-ask: {e.__class__.__name__}; keeping the first answer)")

    text, report = clean_summary(text, transcript_text, model, verdict, meta)

    aloud = thinking_aloud_lines(text)
    if aloud:
        # Still talking to itself after the rules ran. Delete the lines wherever they are,
        # and give up on the judgment sections rather than publish a deliberation.
        lines = text.splitlines()
        text = "\n".join(tidy_blanks([l for i, l in enumerate(lines) if i not in set(aloud)]))
        text = force_nothing(text, (APPLIES_HEADING_RE, BELIEFS_HEADING_RE, TRY_HEADING_RE))
        text, _ = enforce_contradictions(text)   # section 7's "nothing" in the agreed words
        report["kept"] = 0
        report["forced"] = True
        print(f"    ({len(aloud)} line(s) of thinking-aloud removed"
              f"{' after a re-ask' if reasked else ''}; sections 6, 7 and 8 set to nothing)")
    elif reasked:
        print("    (the second answer was clean)")

    text, compressed = enforce_try_one_sentence(text, model)
    # Rule 3 runs last on section 8, after the one-sentence rule, so what is checked is the
    # line that actually reaches history.jsonl and TRY-NEXT.md and not a draft of it. It is
    # inside finalize_summary rather than at a call site for the reason test P pins: every
    # backend and every entry point comes through here.
    text, contradicted = enforce_try_against_decisions(text)
    if contradicted:
        print(f"    (this week's action contradicted a standing decision, {contradicted}; "
              f"replaced with the contradiction)")
    if report["invented"]:
        print(f"    ({report['invented']} phrase(s) lost their quote marks: the video does "
              f"not say them in those words)")
    if report["struck"]:
        also = ", and this week's action with them" if report["forced"] else ""
        print(f"    ({report['struck']} item(s) cut from 'What applies to me'{also}; "
              f"{report['kept']} kept)")
    if report["added_sections"]:
        print(f"    ({report['added_sections']} heading(s) the summary never wrote added, "
              f"saying nothing)")
    if report["collapsed"]:
        print("    (section 7 explained that nothing contradicts; replaced with one line)")
    if report["dropped_opportunity"]:
        print(f"    (Opportunity line dropped: {report['dropped_opportunity']})")
    if compressed:
        print("    (this week's action ran past one sentence; compressed to one)")
    # Last, after every guard that can empty section 6: the header follows it (27 September).
    text, headed = enforce_header_follows_section6(text)
    if headed:
        print(HEADER_NOTHING_NOTE)
    return text


def extract_top_takeaway(summary_text):
    # Scoped to section 6, not searched document-wide. A model that echoes the instruction
    # back inside section 5 ("Worth keeping verbatim") used to hand the digest that echo
    # instead of the real line - and worse, the echo sits outside everything
    # enforce_applies_citations rewrites, so an item the guard had just struck could still
    # reach the digest from a heading above it. One summary in the 53 already on disk does
    # exactly this. Falls back to the whole document only when there is no section 6.
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, APPLIES_HEADING_RE)
    scope = summary_text if bounds is None else "\n".join(lines[bounds[0]:bounds[1]])
    m = re.search(r"Most applicable:\s*(.+)", scope)
    if not m:
        # Since 2026-09-10 a section 6 that survived nothing is the bare word "nothing"
        # with no "Most applicable:" line at all. That is an answer, and INDEX.md should
        # carry it as one rather than the "(see summary)" that means "go and look".
        body = [l.strip() for l in lines[bounds[0] + 1:bounds[1]]] if bounds else []
        body = [l for l in body if l and not l.startswith(">")]   # the guard's own banner
        return "nothing" if body and all(is_nothing_useful(l) for l in body) else None
    # smaller models sometimes run "Ignore:" onto the same line instead of a new one
    return re.split(r"\s+Ignore:", m.group(1))[0].strip()


def extract_try_this_week(summary_text):
    # Same reasoning as extract_top_takeaway: read section 8 itself, not the first thing
    # in the document that happens to look like it.
    lines = summary_text.splitlines()
    bounds = section_bounds(lines, TRY_HEADING_RE)
    if bounds is None:
        m = re.search(r"##\s*8\.[^\n]*\n+\s*([^\n]+)", summary_text)
        return m.group(1).strip() if m else None
    for i in range(bounds[0] + 1, bounds[1]):
        if lines[i].strip():
            return lines[i].strip()
    return None


def is_nothing(line):
    return line.strip().lower().rstrip(".") == "nothing"


def is_nothing_useful(text):
    if not text:
        return True
    return text.strip().lower().startswith(("none", "nothing", "n/a", "not applicable"))


ONE_SENTENCE_INSTRUCTIONS = """Restate this as exactly one sentence, under 25 words, keeping the single most useful concrete point and dropping the rest:

{text}

Reply with just that one sentence, nothing else."""


def ensure_one_sentence(text, model):
    if not text:
        return text
    text = text.strip()
    # cheap heuristic: more than one sentence-ending, or just long - ask the model
    # to compress it rather than mechanically truncating, which could cut the
    # actual point instead of the padding around it.
    if len(re.findall(r"[.!?](?:\s|$)", text)) <= 1 and len(text) <= 200:
        return text
    try:
        compressed = quick_generate(ONE_SENTENCE_INSTRUCTIONS.format(text=text), model=model,
                                    num_ctx=NUM_CTX, num_predict=100)
        compressed = normalize_punct(compressed.strip().splitlines()[0].strip())
        return compressed or text
    except Exception as e:  # noqa: BLE001 - Ollama down is not a reason to lose the line
        print(f"    (could not compress to one sentence: {e.__class__.__name__}; left as written)")
        return text


def copy_to_clipboard(text, platform=None, which=None, run=None):
    """(copied, how): the text on the clipboard with the system's own tool, clip on Windows,
    pbcopy on macOS, wl-copy, xclip or xsel on Linux. With none of them, nothing runs and the
    second value says what to install."""
    platform = platform or sys.platform
    which = which or shutil.which
    run = run or subprocess.run
    if platform.startswith("win"):
        choices = [["clip"]]
    elif platform == "darwin":
        choices = [["pbcopy"]]
    else:
        choices = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for argv in choices:
        if which(argv[0]):
            try:
                run(argv, input=text.encode("utf-8"), check=True)
                return True, argv[0]
            except (OSError, subprocess.CalledProcessError) as e:
                return False, f"{argv[0]} failed: {e}"
    return False, ("no clipboard tool was found; install wl-clipboard or xclip to copy "
                   "automatically" if not platform.startswith(("win", "darwin"))
                   else "no clipboard tool was found")


def save_writeup(meta, summary_text, backend_label, elapsed, chunked):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = today_key()      # the run's day, brief part 2.12
    slug = slugify(meta["title"])
    md_path = OUTPUT_DIR / f"{date_str}-{slug}.md"
    n = 1
    while md_path.exists():
        n += 1
        md_path = OUTPUT_DIR / f"{date_str}-{slug}-{n}.md"

    chunk_note = ", chunked" if chunked else ""
    header = (f"# {meta['title']}\n\n"
              f"Source: {meta['url']}\n"
              f"Uploader: {meta.get('uploader', 'UNKNOWN')}\n"
              f"Backend: {backend_label} ({elapsed:.1f}s{chunk_note})\n"
              f"Date: {date_str}\n\n---\n\n")
    # 2026-10-08 night, the ideas lane: the write-up's checked Ideas section, last. A write-up
    # with no Ideas part (a local one, every older stub) is saved exactly as before.
    ideas = ideas_for_writeup(meta)
    body = summary_text.rstrip("\n") + "\n\n" + ideas.rstrip("\n") if ideas else summary_text
    atomic_write_text(md_path, header + body + "\n")

    top = extract_top_takeaway(summary_text) or "(see summary)"
    with open(INDEX_PATH, "a", encoding="utf-8") as f:
        f.write(f"- {date_str} | {meta['title']} | {top}\n")

    try_line = extract_try_this_week(summary_text)
    # is_nothing() only matches the exact word, and a summary that answered "None." used to
    # walk straight past it into TRY-NEXT.md as this week's action.
    if try_line and not is_nothing_useful(try_line):
        with open(TRYNEXT_PATH, "a", encoding="utf-8") as f:
            f.write(f"- {date_str} ({meta['title']}): {try_line}\n")

    return md_path


# --- OPPORTUNITIES.md ---------------------------------------------------------------
# One line per OPPORTUNITY video, and the file only grows. The line is what the weekly's
# "what is working for people right now" table is built from, and what the triage is
# reminded of so a road already recorded is not called new twice.
OPPORTUNITY_LINE_RE = re.compile(
    r"^- (\d{4}-\d{2}-\d{2}) \| works: (.*?) \| for: (.*?) \| evidence: (.*?) \| \[(.*?)\]\((\S+)\)\s*$")
SUMMARY_OPPORTUNITY_RE = re.compile(
    r"Opportunity line:\s*works:\s*(.*?)\s*\|\s*for:\s*(.*?)\s*\|\s*evidence:\s*(.*)", re.IGNORECASE)
OPPORTUNITIES_HEADER = (
    "# Opportunities\n\n"
    "One line per OPPORTUNITY video: the date, what works, for whom, the evidence shown or "
    "claimed, the link. Written by worthwatch as videos are read, and it only grows. The weekly's "
    "\"What is working for people right now\" table is built from these lines.\n\n")


def _opportunity_field(text):
    text = re.sub(r"\*\*|__", "", str(text or "")).replace("|", "/")
    return re.sub(r"\s+", " ", text).strip() or "UNKNOWN"


def parse_opportunity_line(summary_text):
    """The summary's Opportunity line as {works, for, evidence}, or None if it wrote none."""
    for line in (summary_text or "").splitlines():
        m = SUMMARY_OPPORTUNITY_RE.search(line)
        if m:
            return {"works": _opportunity_field(m.group(1)), "for": _opportunity_field(m.group(2)),
                    "evidence": _opportunity_field(m.group(3))}
    return None


def section_text(summary_text, heading_pattern):
    """The body of one numbered section, or ""."""
    lines = (summary_text or "").splitlines()
    bounds = section_bounds(lines, re.compile(heading_pattern))
    if bounds is None:
        return ""
    return "\n".join(lines[bounds[0] + 1:bounds[1]]).strip()


def opportunities_recorded():
    """Every line OPPORTUNITIES.md holds, oldest first, as dicts."""
    path = opportunities_path()
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = OPPORTUNITY_LINE_RE.match(line.strip())
        if m:
            rows.append({"date": m.group(1), "works": m.group(2), "for": m.group(3),
                         "evidence": m.group(4), "title": m.group(5), "url": m.group(6)})
    return rows


def append_opportunity(meta, summary_text):
    """One line in OPPORTUNITIES.md for a video read as OPPORTUNITY. The fields come from the
    summary's own Opportunity line; if it wrote none, the first sentence of its section 2
    stands in and the evidence is UNKNOWN, said so rather than guessed."""
    parsed = parse_opportunity_line(summary_text)
    if parsed is None:
        claim = section_text(summary_text, r"^##\s*2\b")
        first = re.split(r"(?<=[.!?])\s+", claim)[0] if claim else "UNKNOWN"
        parsed = {"works": _opportunity_field(first[:200]), "for": "UNKNOWN",
                  "evidence": "UNKNOWN (the summary wrote no Opportunity line)"}
    date_str = today_key()      # the run's day, brief part 2.12
    title = _opportunity_field(meta["title"]).replace("[", "(").replace("]", ")")
    line = (f"- {date_str} | works: {parsed['works']} | for: {parsed['for']} | "
            f"evidence: {parsed['evidence']} | [{title}]({meta['url']})")
    path = opportunities_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        atomic_write_text(path, OPPORTUNITIES_HEADER)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"    -> OPPORTUNITIES.md: {parsed['works'][:90]}")
    return line


def process_video(url, context_text, use_claude, compare, model_override):
    """Returns a small outcome dict so the caller can record the result in
    seen.json: {"status": "ok"|"pending"|"no_captions", "id": ..., "title": ...}.
    "pending" is the transient case (captions listed but not generated yet), the
    same one --channels retries; the other two are settled.

    It goes through the slot's plan, like every other fetching mode. It used to call
    fetch_transcript() directly, so a hand-run URL asked /api/timedtext whatever the gate
    had just decided - which on this address meant `worthwatch<url>` could not work at all, and
    is the reason `worthwatch audio <url>` had to be written as a separate command."""
    print(f"\nFetching transcript: {url} (via {slot_plan()['primary'] or 'no path'})")
    meta, err = fetch_transcript_by_plan(url)
    if err:
        print(f"SKIPPED, no usable transcript: {err}")
        status = "pending" if "could not be downloaded" in err else "no_captions"
        return {"status": status, "id": video_id_from_url(url), "title": None, "err": err}
    word_count = len(meta["transcript"].split())
    print(f"  \"{meta['title']}\"  (id={meta['id']}, lang={meta['lang']}, {word_count} words)")

    if compare:
        model = model_override or DEFAULT_LOCAL_MODEL
        ensure_local_model(model)
        print(f"Running local ({model})...")
        t0 = time.monotonic()
        local_summary, local_chunked = summarize_local(model, context_text, meta, meta["transcript"])
        local_elapsed = time.monotonic() - t0
        local_summary = finalize_summary(
            local_summary, meta["transcript"], model, verdict=meta.get("verdict"), meta=meta,
            backend=quick_label(model))

        print(f"Running the writing brain ({write_label()})...")
        t0 = time.monotonic()
        claude_summary, claude_chunked = summarize_claude(context_text, meta, meta["transcript"])
        claude_elapsed = time.monotonic() - t0
        claude_summary = finalize_summary(
            claude_summary, meta["transcript"], model, verdict=meta.get("verdict"), meta=meta,
            backend=write_label(),
            regenerate=lambda note: summarize_claude(context_text, meta,
                                                     meta["transcript"], extra=note)[0])

        bar = "=" * 70
        print(f"\n{bar}\nLOCAL ({model}) -- {local_elapsed:.1f}s{' [chunked]' if local_chunked else ''}\n{bar}")
        print(local_summary)
        print(f"\n{bar}\nCLAUDE (sonnet) -- {claude_elapsed:.1f}s{' [chunked]' if claude_chunked else ''}\n{bar}")
        print(claude_summary)

        # Technical videos never keep the local model's summary as the record on disk,
        # even in --compare (see TECHNICAL_KEYWORDS): both still run and print above
        # so the comparison itself stays visible, but the saved/copied one is Sonnet's.
        if is_technical_video(meta):
            default_summary, default_label = claude_summary, write_label()
            default_elapsed, default_chunked = claude_elapsed, claude_chunked
        else:
            default_summary, default_label = local_summary, quick_label(model)
            default_elapsed, default_chunked = local_elapsed, local_chunked
        md_path = save_writeup(meta, default_summary, default_label, default_elapsed, default_chunked)
        copied, how = copy_to_clipboard(default_summary)
        print(f"\nSaved ({default_label}, the default backend): {md_path}")
        print(f"Raw transcript: {meta['raw_path']}")
        print(f"{'Claude' if is_technical_video(meta) else 'Local'} summary copied to clipboard."
              if copied else f"(not copied to the clipboard: {how})")
        return {"status": "ok", "id": meta["id"], "title": meta["title"]}

    writing = bool(use_claude or is_technical_video(meta))
    if writing:
        if not use_claude:
            print("Technical video: routing it to the writing brain, not the quick one.")
        model_label = write_label()
        print(f"Running the writing brain ({model_label})...")
        t0 = time.monotonic()
        summary, chunked = summarize_claude(context_text, meta, meta["transcript"])
        elapsed = time.monotonic() - t0
    else:
        model = model_override or DEFAULT_LOCAL_MODEL
        ensure_local_model(model)
        print(f"Running the quick brain ({quick_label(model)})...")
        t0 = time.monotonic()
        summary, chunked = summarize_local(model, context_text, meta, meta["transcript"])
        elapsed = time.monotonic() - t0
        model_label = quick_label(model)

    _local_model = model_override or DEFAULT_LOCAL_MODEL
    if writing:
        _again = lambda note: summarize_claude(context_text, meta, meta["transcript"], extra=note)[0]
    else:
        _again = lambda note: summarize_local(_local_model, context_text, meta,
                                              meta["transcript"], extra=note)[0]
    summary = finalize_summary(summary, meta["transcript"], _local_model,
                               verdict=meta.get("verdict"), regenerate=_again, meta=meta,
                               backend=model_label)
    # A hand-run URL usually has no verdict at all, and then this changes nothing. It is here
    # so that when one does carry a verdict, `worthwatch<url>` and the scheduled path cannot disagree
    # about what it is.
    settled, summary, gate_notes = settle_verdict(meta.get("verdict"), summary)
    for note in gate_notes:
        print(f"  ({note})")
    bar = "=" * 70
    print(f"\n{bar}")
    print(summary)
    print(bar)
    print(f"({model_label}, {elapsed:.1f}s{' [chunked]' if chunked else ''})")

    md_path = save_writeup(meta, summary, model_label, elapsed, chunked)
    copied, how = copy_to_clipboard(summary)
    print(f"Saved: {md_path}")
    print(f"Raw transcript: {meta['raw_path']}")
    print("Copied to clipboard." if copied else f"(not copied to the clipboard: {how})")
    return {"status": "ok", "id": meta["id"], "title": meta["title"]}


def count_unfinished(urls, seen):
    """How many of these URLs still need work. Not just len(): a batch file is
    mostly already-done entries by its second run, and "31 left" when 30 of them
    are finished is a number that is always wrong."""
    n = 0
    for u in urls:
        state = seen.get(video_id_from_url(u)) or {}
        if state.get("verdict") in (None, "pending"):
            n += 1
    return n


def run_urls(urls, context_text, args):
    """Plain URLs and --file.

    Until 2026-09-02 this path obeyed none of the tool's rate-limit policy and
    kept no record of what it had done: `worthwatch --file` re-fetched every URL in the
    file on every run, back to back with no gap, and a 35-URL file took an HTTP
    429 on video 22. It now resumes from seen.json and shares one budget and one
    spacing clock with --channels and --topics.
    """
    budget = new_budget()
    if budget["remaining"] <= 0:
        report_daily_cap("this run")
        write_last_run("file", note="every transcript path had spent today's budget")
        return
    if not slot_may_proceed("this run", getattr(args, "no_canary", False)):
        write_last_run("file", note=f"slot gate: {slot_plan()['why']}")
        return

    seen = load_seen()
    done = skipped = failed = retrying = left = 0
    stopped_early = False

    for i, url in enumerate(urls):
        vid = video_id_from_url(url)
        state = seen.get(vid) if vid else None

        # Already settled (summarized, screened out, or given up on) - skip it.
        if state and state.get("verdict") not in (None, "pending"):
            skipped += 1
            print(f"  already done {state.get('date', '')}: "
                  f"{state.get('title') or url} - skipping")
            continue

        if budget["remaining"] <= 0:
            left = count_unfinished(urls[i:], seen)
            print(f"\nThis run's transcript budget is spent. {left} URL(s) left; "
                  f"re-run the same command and it picks up where it stopped.")
            break

        budget["remaining"] -= 1
        # No wait_for_caption_slot()/note_caption_fetch() here: the gap and the count
        # belong to whichever path fetch_transcript_by_plan() ends up using, and it
        # spends them itself. Doing it here charged the caption path for audio fetches.
        try:
            outcome = process_video(url, context_text, args.claude, args.compare, args.model)
        except RateLimitError as e:
            print(f"YouTube is rate-limiting requests (HTTP 429). Stopping here.\n  {e}")
            left = count_unfinished(urls[i:], seen)
            stopped_early = True
            break
        except RuntimeError as e:
            print(f"ERROR on {url}: {e}")
            failed += 1
            continue

        vid = outcome.get("id") or vid
        if not vid:
            failed += 1
            continue

        if outcome["status"] == "ok":
            mark_seen(seen, vid, "manual", outcome["title"])
            done += 1
        elif outcome["status"] == "pending":
            # Captions listed but YouTube hasn't finished generating them: retry
            # on later runs, same three-strike rule --channels uses.
            retry = int((state or {}).get("retry_count", 0)) + 1
            if retry >= PENDING_MAX_RETRIES:
                seen[vid] = {"date": today_key(), "verdict": "gave_up", "title": url}
                print(f"    (gave up after {retry} attempts: captions never finished)")
                failed += 1
            else:
                seen[vid] = {"date": today_key(), "verdict": "pending", "title": url, "url": url,
                             "pending_verdict": "MAYBE", "pending_reason": "queued by a --file run",
                             "retry_count": retry}
                print(f"    (captions not ready yet, retry {retry}/{PENDING_MAX_RETRIES} next run)")
                retrying += 1
            save_seen(seen)
        else:
            # No caption track at all: a permanent property of the video, so
            # record it and never spend a request on it again.
            mark_seen(seen, vid, "no_captions", url)
            failed += 1

    note = (f"{done} summarized, {skipped} skipped (already done), "
            f"{failed} without a usable transcript, {retrying} waiting on captions, "
            f"{left} left for the next run"
            + (" (stopped on a 429)" if stopped_early else ""))
    print(f"\n{note}")
    if left:
        print(f"Re-run the same command to continue; {MAX_CAPTION_FETCHES_PER_RUN} per run, "
              f"{new_budget()['remaining']} left in today's budget.")
    write_last_run("file", note=note)


# --- worthwatch doctor -------------------------------------------------------------------------
# One read-only command: green or red for each thing a run needs, and for anything red the exact
# thing to do about it. It writes nothing and changes no system setting; tests/test_doctor.py
# holds it to that with a spy.
# Whisper large-v3 in float16 holds about 3 GB on the GPU with room to decode; under 4 GiB free
# a transcription is likely to run out of memory. Only checked when the audio path is on.
DOCTOR_GPU_FREE_MIB = 4096
CONTEXT_STALE_DAYS = 7
# Your labels, for "score the tool against your own labels": labels.json in the config folder
# (see scoring.py). When it is there, doctor scores the stored write-ups against it, and a
# score under the floor you set ([labels] floor_yes_precision and floor_yes_recall) is red.
GOLDEN_FLOOR = {}
# Which brain judges the videos (brains.py); settings.toml [brain] kind sets it.
BRAIN = "agent"
# The first lines of the example person's context file say this; a context file that still
# does is not the reader's own yet.
EXAMPLE_CONTEXT_MARKER = "> EXAMPLE."
REINSTALL_FIX = "uv tool install --reinstall worthwatch   (or pip install --upgrade worthwatch)"


def doctor_ytdlp():
    """yt-dlp's installed version, or None."""
    return package_version("yt-dlp")


def doctor_js_runtime():
    """(found, where) for the JavaScript runtime yt-dlp will use: the one settings name, else
    Deno, looked for where yt-dlp looks first (this Python's scripts folder), then on the PATH.
    Starts no program."""
    import sysconfig
    name = JS_RUNTIME or "deno"
    exe = name + (sysconfig.get_config_var("EXE") or "")
    beside = Path(sysconfig.get_path("scripts")) / exe
    if beside.is_file():
        return True, f"{name}, in this Python's scripts folder (installed with worthwatch)"
    found = shutil.which(name)
    if found:
        return True, f"{name}, at {found}"
    return False, f"{name} was not found in this Python's scripts folder or on the PATH"


def doctor_brains():
    from . import brains
    return brains.detect()


def doctor_ollama_models():
    """The models Ollama has pulled, or OllamaDown when it does not answer."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise OllamaDown(ollama_failure_words(e, 3)) from e
    return [m.get("name") for m in data.get("models", [])]


def doctor_gpu():
    """{name, total_mib, free_mib} from nvidia-smi, or None when it does not answer."""
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                            "--format=csv,noheader,nounits"], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = p.stdout.decode("utf-8", errors="replace").strip().splitlines()[:1]
    if p.returncode != 0 or not first:
        return None
    parts = [x.strip() for x in first[0].split(",")]
    try:
        return {"name": parts[0], "total_mib": int(float(parts[1])),
                "free_mib": int(float(parts[2]))}
    except (IndexError, ValueError):
        return None


def doctor_whisper_ready():
    """(ok, what) for the transcriber's software and weights, without loading either."""
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is None:
        return False, "faster-whisper is not installed (it comes with worthwatch[whisper])"
    weights = [p for p in WHISPER_MODEL_DIR.glob(f"*{WHISPER_MODEL}*/**/model.bin")] \
        if WHISPER_MODEL_DIR.exists() else []
    if not weights:
        return False, f"faster-whisper is installed, but the {WHISPER_MODEL} weights are not in " \
                      f"{WHISPER_MODEL_DIR}"
    return True, f"faster-whisper and {WHISPER_MODEL} ready"


def doctor_golden():
    """{yes_precision, yes_recall, projects_found, projects_total}, scored in process from the
    stored write-ups against your labels file, as `worthwatch score --rules` does, writing
    nothing. FileNotFoundError when there is no labels file."""
    from . import scoring
    return scoring.rules_score(LABELS_PATH)


def doctor_checks(now=None):
    """[(ok, name, detail, fix)] for `worthwatch doctor`. Read only."""
    now = now or dt.datetime.now()
    out = []

    version = doctor_ytdlp()
    out.append((bool(version), "yt-dlp",
                f"{version} is installed (it reads YouTube for worthwatch)" if version
                else "yt-dlp is not installed, so nothing can be read from YouTube",
                None if version else REINSTALL_FIX))
    ok, what = ejs_status()
    out.append((ok, "YouTube challenge solver", what, None if ok else REINSTALL_FIX))
    ok, what = doctor_js_runtime()
    out.append((ok, "JavaScript runtime", what + ("" if ok else "; yt-dlp needs one to read YouTube"),
                None if ok else "Deno comes with worthwatch, so reinstall it: " + REINSTALL_FIX
                + ". Or install Deno from https://deno.com, or, if you have Node.js, put "
                  "js_runtime = \"node\" under [youtube] in settings.toml"))

    found = doctor_brains()
    ok, how = found.get(BRAIN, (False, f"{BRAIN} is not a brain worthwatch knows"))
    if BRAIN == "agent":
        how = ("agent mode: your own AI agent does the judging, through `worthwatch mcp`; "
               "worthwatch calls no model itself")
    detail = f"{BRAIN}: {how}"
    if quick_brain() != BRAIN:
        quick_ok, quick_how = found.get(quick_brain(), (False, "not a brain worthwatch knows"))
        ok = ok and quick_ok
        detail += f"; quick jobs: {quick_brain()}: {quick_how}"
    out.append((ok, "brain", detail,
                None if ok else f"install it, or choose another brain with `worthwatch init "
                                f"--brain NAME` (agent, claude, codex, api, ollama)"))
    if OPPORTUNITY_SCREEN_BACKEND == "jev":
        from . import jev as _jev
        has_key = bool((os.environ.get(_jev.KEY_ENV) or "").strip())
        out.append((has_key, "Jev",
                    f"on, with a key in {_jev.KEY_ENV}, at most ${JEV_DAILY_CAP_USD:.2f} a day"
                    if has_key else f"switched on, but {_jev.KEY_ENV} is not set",
                    None if has_key else f"set {_jev.KEY_ENV} in your environment, or switch Jev "
                                         f"off ([jev] enabled = false in settings.toml)"))

    missing = [f"{n} ({p})" for n, p in (("config", CONFIG_DIR), ("data", DATA_DIR),
                                          ("output", OUTPUT_DIR)) if not Path(p).is_dir()]
    unwritable = [str(p) for p in (CONFIG_DIR, DATA_DIR, OUTPUT_DIR)
                  if Path(p).is_dir() and not os.access(p, os.W_OK)]
    ok = not missing and not unwritable
    out.append((ok, "folders",
                f"config {CONFIG_DIR}, data {DATA_DIR}, output {OUTPUT_DIR}" if ok
                else ("not made yet: " + ", ".join(missing) if missing
                      else "cannot be written to: " + ", ".join(unwritable)),
                None if ok else ("run `worthwatch init`" if missing
                                 else "give your user permission to write there, or move the "
                                      "folder with [paths] in settings.toml")))

    if "ollama" in (BRAIN, quick_brain()):
        wanted = (QUICK_MODEL if quick_brain() == "ollama" else BRAIN_MODEL) or DEFAULT_LOCAL_MODEL
        try:
            models = doctor_ollama_models()
            ok = wanted in models
            out.append((ok, "Ollama", f"answers; {wanted} " + ("pulled" if ok else "not pulled"),
                        None if ok else f"ollama pull {wanted}"))
        except OllamaDown as e:
            out.append((False, "Ollama", f"no answer: {e}",
                        "start the Ollama app, or run: ollama serve"))

    if AUDIO_PATH_ENABLED:
        ready, what = doctor_whisper_ready()
        what = f"{what} (you switched the audio path on; downloading may break YouTube's terms)"
        gpu = doctor_gpu() if ready else None
        if not ready:
            out.append((False, "transcriber", what,
                        "uv tool install --reinstall 'worthwatch[whisper]'   (or switch the "
                        "audio path off)"))
        elif gpu is None:
            out.append((True, "transcriber", f"{what}; no NVIDIA GPU answered, so it runs on "
                        f"the CPU, slowly", None))
        else:
            free = gpu["free_mib"]
            ok = free >= DOCTOR_GPU_FREE_MIB
            out.append((ok, "transcriber",
                        f"{what}; {free / 1024:.1f} GB free of {gpu['total_mib'] / 1024:.1f} GB "
                        f"on the GPU"
                        + ("" if ok else f", under the {DOCTOR_GPU_FREE_MIB / 1024:.0f} GB needed"),
                        None if ok else "nvidia-smi   (it lists what holds the GPU; close it)"))

    free = disk_free_bytes()
    ok = free >= DISK_MIN_FREE_BYTES
    out.append((ok, "disk", f"{free / 1e9:.1f} GB free ({DISK_MIN_FREE_BYTES / 1e9:.1f} GB "
                            f"needed)",
                None if ok else "free some space on that drive; nothing is fetched until then"))

    if CONTEXT_PATH.exists():
        days = (now.date() - dt.date.fromtimestamp(CONTEXT_PATH.stat().st_mtime)).days
        if EXAMPLE_CONTEXT_MARKER in CONTEXT_PATH.read_text(encoding="utf-8", errors="replace")[:600]:
            out.append((False, "context file", "still the example person's, so every verdict "
                                               "would be judged for someone who does not exist",
                        f"open {CONTEXT_PATH} and replace the example with yourself: your "
                        f"projects, what you have tried, what you have decided"))
        else:
            ok = days <= CONTEXT_STALE_DAYS
            out.append((ok, "context file", f"last updated {context_age_words(days)}",
                        None if ok else f"update {CONTEXT_PATH} with what has changed; the "
                                        f"verdicts are judged against it"))
    else:
        out.append((False, "context file", f"missing ({CONTEXT_PATH})",
                    "run `worthwatch init`, then make the context file about you"))

    channels = len(read_channel_urls())
    searches = (len([l for l in TOPICS_PATH.read_text(encoding="utf-8").splitlines()
                     if l.strip() and not l.strip().startswith("#")])
                if TOPICS_PATH.exists() else 0)
    out.append((bool(channels or searches), "channels",
                f"{channels} channel(s) and {searches} search(es) to check",
                None if channels or searches else
                f"put the channels you follow in {CHANNELS_PATH}, one link a line, or searches "
                f"in {TOPICS_PATH}, one a line"))

    if LABELS_PATH.exists():
        try:
            score = doctor_golden()
            floor_p = GOLDEN_FLOOR.get("yes_precision", 0.0)
            floor_r = GOLDEN_FLOOR.get("yes_recall", 0.0)
            ok = (score["yes_precision"] >= floor_p - 1e-9
                  and score["yes_recall"] >= floor_r - 1e-9
                  and score["projects_found"] == score["projects_total"])
            out.append((ok, "score against your labels",
                        f"YES {score['yes_precision'] * 100:.1f}% precision, "
                        f"{score['yes_recall'] * 100:.1f}% recall; project keeps "
                        f"{score['projects_found']} of {score['projects_total']}"
                        + ("" if ok else ", under the floor you set"),
                        None if ok else "worthwatch score --rules   (it lists every verdict that "
                                        "moved, and why)"))
        except Exception as e:  # noqa: BLE001 - a score that cannot be taken is red, not a crash
            out.append((False, "score against your labels",
                        f"could not be scored: {e.__class__.__name__}: "
                        f"{one_line(str(e))[:100]}", "worthwatch score --rules"))

    damaged = []
    for path in (RECORDS_PATH, SEEN_PATH, RATE_STATE_PATH):
        try:
            read_state_json(path)
        except StateFileDamaged:
            damaged.append(Path(path).name)
    out.append((not damaged, "state files",
                "records, seen and rate-state read cleanly" if not damaged
                else f"{', '.join(damaged)} cannot be read; no run will write over it",
                None if not damaged else "mend the file by hand, or put back a good copy"))
    return out


def context_age_words(days):
    """0, 1 and N days in plain words."""
    return "today" if days <= 0 else "1 day ago" if days == 1 else f"{days} days ago"


def cmd_doctor(now=None):
    """`worthwatch doctor`: every check in plain words, and how to fix anything that needs it.
    Read only."""
    now = now or dt.datetime.now()
    checks = doctor_checks(now=now)
    print(f"worthwatch doctor, {now:%Y-%m-%d %H:%M}. It only looks; it changes nothing.")
    for ok, name, detail, fix in checks:
        print(f"  {'OK ' if ok else 'FIX'}  {name}: {detail}")
        if fix:
            print(f"       How to fix: {fix}")
    need = sum(1 for c in checks if not c[0])
    print("Everything is ready. Nothing was changed." if not need else
          f"{need} thing(s) need fixing; each says how above. Nothing was changed.")
    return 0 if not need else 1


# --- your settings ---------------------------------------------------------------------------
# Everything personal comes from two files in the config folder (settings.toml and rules.toml,
# see settings.py), read once by configure() when the command line or the MCP server starts.
# Until then every setting is the empty default above, which is what a test suite that has not
# called apply_profile() sees.
PROFILE = _settings.Profile()
READER = "the reader"


def apply_profile(profile):
    """Put one Profile's projects, names, channels and rules where the code reads them."""
    g = globals()
    g["PROFILE"] = profile
    g["READER"] = (profile.reader if profile.reader and profile.reader.lower() != "you"
                   else "the reader")
    g["PROJECTS"] = profile.labels
    g["PROJECT_ALIASES"] = profile.alias_table
    g["LIVE_PROJECTS"] = tuple(p for p in profile.labels if p != _settings.ROAD_NOT_TAKEN)
    g["PEOPLE_IN_CONTEXT"] = profile.people
    g["IDEA_ASSETS"] = profile.assets
    g["SIGN_WORD"] = profile.sign_word
    g["BRAIN"] = profile.brain
    g["QUICK_BRAIN"] = profile.quick_brain
    g["BRAIN_MODEL"] = profile.brain_model
    g["QUICK_MODEL"] = profile.quick_model
    g["API"] = {"provider": profile.api_provider, "base_url": profile.api_base_url,
                "key_env": profile.api_key_env}
    g["AUDIO_PATH_ENABLED"] = profile.audio
    g["OPPORTUNITY_SCREEN_BACKEND"] = "jev" if profile.jev_enabled else "local"
    g["JEV_DAILY_CAP_USD"] = profile.jev_daily_cap
    g["JS_RUNTIME"] = profile.js_runtime
    g["FAVOURITE_CHANNELS"] = profile.priority_channels
    g["FAVOURITE_DAILY_RESERVE"] = profile.priority_daily_reserve
    g["CHANNEL_DAILY_CAP"] = profile.channel_daily_cap
    g["TOPIC_KEYWORDS"] = list(profile.on_topic_keywords)
    g["TECHNICAL_KEYWORDS"] = list(profile.technical_keywords)
    g["DISCOVER_TOPICS"] = list(profile.discover_queries)
    g["KNOWN_DISCOVER_CANDIDATES"] = list(profile.discover_candidates)
    g["WHISPER_INITIAL_PROMPT"] = profile.whisper_vocabulary
    g["STANDING_DECISIONS"] = profile.decisions
    g["DECISION_EXEMPT"] = dict(profile.decision_exempt)
    g["DECISION_ONLY"] = dict(profile.decision_only)
    g["DONE_OR_DECIDED"] = profile.done
    g["GENERIC_ADVICE"] = profile.generic
    g["SETUP_PROJECT"] = profile.work_label or None
    g["OWN_SETUP"] = profile.work_parts
    g["LABEL_DAILY_CAP"] = profile.work_label_daily_cap
    g["KILLED_IDEAS"] = profile.killed
    g["KILLED_IDEA_NOTES"] = dict(profile.killed_notes)
    return profile


def project_list_words():
    """Your live projects for a prompt: "Ledgerleaf, Tidepool", or a plain "none listed"."""
    projects = [p for p in PROJECTS if p not in (SETUP_PROJECT, _settings.THIS_TOOL,
                                                  _settings.ROAD_NOT_TAKEN)]
    return ", ".join(projects) or "none listed yet"


def applies_choices():
    """The closed list as the write-up prompt says it: "Applies to: Ledgerleaf", ..., or
    "Applies to: this tool" (the digest you are writing for)."""
    labels = [p for p in PROJECTS if p not in (_settings.THIS_TOOL, _settings.ROAD_NOT_TAKEN)]
    parts = [f'"Applies to: {label}"' for label in labels]
    parts.append(f'"Applies to: {_settings.THIS_TOOL}" (the digest you are writing for)')
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + ", or " + parts[-1]


def configure(env=None):
    """Read settings.toml and rules.toml from the config folder and point every path and
    setting at them. Returns the settings as read. A file that cannot be read stops with a
    plain sentence (settings.SettingsError)."""
    config_dir, _data, _output = _settings.resolve_dirs(env)
    settings = _settings.read_toml(Path(config_dir) / "settings.toml")
    apply_paths(*_settings.resolve_dirs(env, settings))
    apply_profile(_settings.profile_from(settings, _settings.read_toml(RULES_PATH)))
    return settings


def main():
    # stdout/stderr are fully buffered (not line-buffered) once redirected to a file or
    # pipe, e.g. under the scheduled task's logging or this tool's own background runs.
    # Without this, a log can sit empty for the whole run and lose everything on a crash.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    # Before the settings are read: --version needs none, and init makes them.
    if sys.argv[1:2] == ["--version"]:
        print(f"worthwatch {__version__}")
        sys.exit(0)
    if sys.argv[1:2] == ["init"]:
        from . import firstrun
        try:
            sys.exit(firstrun.cmd_init(sys.argv[2:]))
        except _settings.SettingsError as e:
            print(f"STOPPED: {e}")
            sys.exit(2)
    if sys.argv[1:2] == ["mcp"]:
        # The MCP server speaks its protocol on standard output, so anything it has to say
        # before it starts goes to standard error.
        try:
            configure()
        except _settings.SettingsError as e:
            print(f"STOPPED: {e}", file=sys.stderr)
            sys.exit(2)
        from . import mcpserver
        mcpserver.serve()
        return
    try:
        configure()
    except _settings.SettingsError as e:
        print(f"STOPPED: {e}")
        sys.exit(2)

    # Bare pseudo-subcommands, handled before argparse: none of these words is ever
    # a valid YouTube URL, so there's no real ambiguity with the positional urls.
    bare = {"pause": cmd_pause, "resume": cmd_resume, "status": cmd_status,
            "cooldown": cmd_cooldown, "requests": cmd_requests, "paths": cmd_paths,
            "doctor": cmd_doctor}
    if len(sys.argv) >= 2 and sys.argv[1] in bare:
        if sys.argv[1] == "requests" and len(sys.argv) >= 3 and sys.argv[2].isdigit():
            sys.exit(cmd_requests(int(sys.argv[2])) or 0)
        sys.exit(bare[sys.argv[1]]() or 0)
    # 2026-10-08 night, the ideas lane: taste and the daily remix. None makes a YouTube request.
    if len(sys.argv) >= 2 and sys.argv[1] in ("like", "nah"):
        sys.exit(cmd_taste(sys.argv[1], sys.argv[2:]) or 0)
    if len(sys.argv) >= 2 and sys.argv[1] == "remix":
        sys.exit(cmd_remix(sys.argv[2:]) or 0)
    if len(sys.argv) >= 2 and sys.argv[1] == "audio":
        acquire_lock()
        try:
            sys.exit(cmd_audio(sys.argv[2:]) or 0)
        finally:
            release_lock()
    # No lock and no cooldown gate: it reads raw/ and makes no request, like --rollup.
    if len(sys.argv) >= 2 and sys.argv[1] in ("resummarize", "resummarise"):
        sys.exit(cmd_resummarize(sys.argv[2:]) or 0)

    parser = argparse.ArgumentParser(
        prog="worthwatch",
        description="Is this video worth watching, for you? Judges videos against your own "
                    "context file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="commands:\n"
               "  worthwatch init      make the folders and starter files, choose a brain\n"
               "  worthwatch doctor    check everything a run needs, in plain words (read only)\n"
               "  worthwatch mcp       serve worthwatch's tools to your AI agent, over stdio\n"
               "  worthwatch status    what the tool is doing and waiting for\n"
               "  worthwatch pause | resume   stop or restart the runs\n"
               "  worthwatch like | nah ID    tell the idea board what you think of an idea\n"
               "  worthwatch remix [today]    remix the day's ideas\n"
               "  worthwatch --version")
    parser.add_argument("urls", nargs="*", help="one or more YouTube URLs")
    parser.add_argument("--file", help="path to a text file of URLs, one per line "
                                        "(with --rollup: which batch to roll up)")
    parser.add_argument("--claude", action="store_true",
                        help="write a URL's write-up with the writing brain, not the quick one")
    parser.add_argument("--compare", action="store_true",
                        help="write it with both brains, side by side")
    parser.add_argument("--model", default=None,
                        help="the local model to use when the quick brain is Ollama")
    parser.add_argument("--channels", action="store_true", help="check channels.txt for videos you haven't seen")
    parser.add_argument("--days", type=int, default=None,
                        help="with --channels, only videos published in the last N days; "
                             "with --rollup, only summaries written in the last N days")
    parser.add_argument("--search", default=None, help="triage the top YouTube results for a query")
    parser.add_argument("--topics", action="store_true", help="triage the top results for every query in topics.txt")
    parser.add_argument("--discover", action="store_true", help="propose channels to add, into CHANNEL-CANDIDATES.md")
    parser.add_argument("--weekly", action="store_true", help="write this week's WEEK-<date>.md rollup from history.jsonl")
    parser.add_argument("--rollup", default=None, metavar="NAME",
                        help="roll a batch of existing summaries into one ROLLUP-<name>-<date>.md "
                             "(narrow it with --file, --from-channel, or --days)")
    parser.add_argument("--from-channel", default=None, metavar="TEXT",
                        help="with --rollup, only summaries whose uploader matches TEXT")
    parser.add_argument("--local-only", action="store_true",
                        help="write everything with the quick brain, even YES verdicts, except "
                             "technical videos, which always go to the writing brain")
    parser.add_argument("--ignore-cooldown", action="store_true",
                        help="fetch even during a post-429 cooldown (you are the rate limit now)")
    parser.add_argument("--canary", action="store_true",
                        help="open the run with one canary request, to measure whether this "
                             "network is being refused (off by default; it costs a request)")
    parser.add_argument("--no-canary", action="store_true",
                        help="never open the run with a canary request (the default)")
    parser.add_argument("--captions", action="store_true",
                        help="allow the caption path this run even if it is switched off. "
                             "`worthwatch paths` shows what it last did.")
    parser.add_argument("--captions-first", action="store_true",
                        help="with --captions, put the caption path in front of the audio path, "
                             "so the slot's canary is a caption request again")
    args = parser.parse_args()

    # Agent mode: worthwatch calls no model, so nothing that judges or writes runs from here;
    # your agent does it through `worthwatch mcp`. Said before any request is made.
    needs_a_model = (args.weekly or args.rollup or args.discover or args.channels or args.search
                     or args.topics or args.urls or args.file)
    if BRAIN == "agent" and needs_a_model:
        print(AGENT_MODE_WORDS)
        sys.exit(2)
    # The audio path runs only when it is switched on and its transcriber is installed; when it
    # runs, the run says plainly what downloading may mean.
    if AUDIO_PATH_ENABLED and needs_a_model:
        ready, why = audio_path_ready()
        if ready:
            print(AUDIO_TERMS_WARNING)
        else:
            print(f"The audio path is switched on, but {why}. This run uses captions only.")
            globals()["AUDIO_PATH_ENABLED"] = False

    if args.weekly:
        run_weekly()
        return

    if args.captions or args.captions_first:
        globals()["CAPTION_PATH_ENABLED"] = True
    if args.captions_first:
        globals()["PATH_PREFERENCE"] = ("captions", "audio")
    if args.captions_first and not args.captions:
        print("--captions-first implies --captions.")

    context_text = load_context()
    model = args.model or DEFAULT_LOCAL_MODEL

    # --rollup reads files already on disk and never touches YouTube, so it runs
    # during a cooldown and without the lock.
    if args.rollup:
        run_rollup(args.rollup, context_text, url_file=args.file,
                   channel=args.from_channel, days=args.days)
        return

    urls = list(args.urls)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

    fetching = args.discover or args.channels or args.search or args.topics or urls
    if not fetching:
        parser.error("no URLs given (pass one or more URLs, --file urls.txt, --channels, --search, --topics, "
                     "--discover, --weekly, --rollup NAME, or one of pause/resume/status)")

    # A cooldown stops the run only when it leaves NOTHING that can fetch: a cooldown on one
    # path is a fact about that path, and the other may still run. It exits 0 either way so a
    # scheduled task does not look like it failed.
    mode_label = ("--discover" if args.discover else "--channels" if args.channels
                  else "--topics" if args.topics else
                  f'--search "{args.search}"' if args.search else "this run")
    if args.ignore_cooldown:
        _IGNORE_COOLDOWN["on"] = True
    live = [p for p in path_order() if cooldown_remaining(p) <= 0]
    cooling = [p for p in path_order() if cooldown_remaining(p) > 0]
    if not live:
        if not args.ignore_cooldown:
            report_no_path(mode_label)
            return
        print(f"WARNING: --ignore-cooldown given; fetching into an active rate-limit "
              f"cooldown on {', '.join(cooling) or 'nothing that is switched on'}.")
    elif cooling:
        for path in cooling:
            print(f"The {path} path is in cooldown until "
                  f"{cooldown_until(path):%Y-%m-%d %H:%M}; this run uses {', '.join(live)} "
                  f"and makes no {path} request.")

    # The canary costs a request, so it runs only when asked for (--canary, or CANARY_ENABLED).
    args.no_canary = args.no_canary or not (args.canary or CANARY_ENABLED)

    start_this_run()
    if args.discover:
        ensure_local_model(model)
    elif args.channels or args.search or args.topics:
        # Without the triage model nothing new can be triaged, but the write-ups waiting in the
        # retry queue need only the writing brain, so the run carries on, lists nothing, and its
        # digest section says why.
        try:
            ensure_local_model(model)
        except OllamaDown as e:
            print(f"{e}\nCarrying on without it: the retry queue is written up, and nothing "
                  f"new is listed or fetched in this run.")

    # The lock now covers plain URLs and --file too: a hand-run batch landing on
    # top of the hourly --channels run is two processes spending one IP's budget.
    acquire_lock()
    # This invocation's wall-clock budget starts here, and only for the modes a scheduler runs.
    # A hand-run URL or --file has no deadline: you are at the terminal waiting for it, and a
    # scheduled run you are not in has no claim on your time.
    if args.discover or args.channels or args.search or args.topics:
        start_run_clock()
    # 2026-09-30: the opportunity lane runs where videos are read on a schedule, and only there.
    if args.channels or args.search or args.topics:
        _LANE["on"] = True
    try:
        # FIX 2: the retry queue first, before anything is fetched. It is here rather than
        # inside each mode because every one of them has early returns (the daily cap, the
        # slot gate, a cooldown) and a summary that needs no request should not be skipped
        # by a gate about requests. It writes its own digest section and costs nothing.
        if args.channels or args.topics or args.search:
            run_retry_phase(context_text, model, args.local_only)
        if args.discover:
            run_discover(context_text, model, skip_canary=args.no_canary)
        elif args.channels:
            run_channels(context_text, args.days, model, args.local_only, skip_canary=args.no_canary)
        elif args.topics:
            run_topics(context_text, model, args.local_only, skip_canary=args.no_canary)
        elif args.search:
            run_search(args.search, context_text, model, args.local_only, skip_canary=args.no_canary)
        else:
            run_urls(urls, context_text, args)
    finally:
        _LANE["on"] = False
        release_lock()


if __name__ == "__main__":
    try:
        main()
    except StateFileDamaged as e:
        # 2026-09-26 night: a state file that cannot be read stops the run, said plainly, with
        # nothing written over it (read_state_json).
        print(f"\nSTOPPED: {e}")
        sys.exit(2)
