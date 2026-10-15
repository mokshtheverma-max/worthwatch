"""worthwatch's MCP server: `worthwatch mcp`, over stdio, on the official MCP Python SDK.

It gives an AI agent the things only worthwatch does: check the channels you follow for new
videos, read a video's transcript politely, hand the agent everything it needs to judge a video
for you (your context file, the transcript and the rules), store the agent's verdict after your
own rules have checked it in code, show today's digest, and keep the idea board.

What it will not do, in code:
  - take anything but a YouTube video link or an 11-character video id as a video: anything else
    is refused before any request, and the request is built from the id alone;
  - write outside worthwatch's own data and output folders, or to any path a tool is given;
  - run a command: no tool argument ever reaches a command line, except a video id inside a link
    worthwatch builds itself;
  - push YouTube: every request goes through the same gaps, daily limits and cooldowns as the
    command line, and a refusal means wait, never another way in.
"""
import datetime as dt
import re

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import core

VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
VIDEO_URL_RE = re.compile(
    r"(?:https?://)?(?:(?:www|m|music)\.)?"
    r"(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|shorts/|live/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?:[?&#/][^\s]*)?")
IDEA_ARG_RE = re.compile(r"(?i)I-?(\d{1,6})")
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
VERDICTS = ("YES", "OPPORTUNITY", "MAYBE", "NO")
TRANSCRIPT_MAX_CHARS = 200_000
WRITE_UP_MAX_CHARS = 60_000
TEXT_MAX_CHARS = 400

INSTRUCTIONS = (
    "worthwatch judges YouTube videos for one person, against their own context file. To judge "
    "new videos: call check_channels, then for a video worth a look call judge_video, follow its "
    "three steps, and finish with save_verdict. YouTube is asked politely and a refusal means "
    "wait: never try to get around it.")


def video_id(text):
    """The 11-character id of a YouTube video, from a video link or the id itself. Anything else
    raises ToolError, before any request."""
    text = (text or "").strip()
    if VIDEO_ID_RE.fullmatch(text):
        return text
    m = VIDEO_URL_RE.fullmatch(text)
    if m:
        return m.group(1)
    raise ToolError(f"{text[:80]!r} is not a YouTube video link or an 11-character video id")


def canonical_url(vid):
    return f"https://www.youtube.com/watch?v={vid}"


def plain(text, limit=TEXT_MAX_CHARS, field="text"):
    """A short text argument, one line, without control characters."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or "")).strip()
    if len(text) > limit:
        raise ToolError(f"{field} is {len(text)} characters; at most {limit}")
    return text


def _transcript(vid, fetch=True):
    """(text, info, record) for a video, from this machine or, if `fetch`, from YouTube."""
    record = core.load_records().get(vid) or {}
    text, info = core.saved_transcript(vid, record)
    if text is not None or not fetch:
        return text, info, record
    blocked = [f"{p}: {core.path_blocked(p)}" for p in core.path_order() if core.path_blocked(p)]
    if not core.usable_paths():
        raise ToolError("Nothing was fetched: " + ("; ".join(blocked) or "no transcript path is on")
                        + ". worthwatch waits rather than trying another way; try again later.")
    try:
        meta, err = core.fetch_transcript_by_plan(canonical_url(vid))
    except core.RateLimitError:
        raise ToolError("YouTube refused the request for now (HTTP 429). worthwatch waits: "
                        "nothing tries another way, and the next try is after the cooldown "
                        "`worthwatch status` shows.") from None
    except RuntimeError as e:
        raise ToolError(f"Could not fetch a transcript: {core.one_line(str(e))[:300]}") from None
    if not meta:
        raise ToolError(f"No transcript for {vid}: {core.one_line(err or '')[:300]}")
    core.save_transcript(meta, source="agent")
    core.update_record(vid, url=canonical_url(vid), title=meta.get("title") or vid,
                       channel=meta.get("channel") or meta.get("uploader") or "UNKNOWN",
                       source="agent", summary_state="READ_NOT_WRITTEN",
                       reason_code="summary_pending", read_day=core.today_key(),
                       state_reason="read; waiting for your agent's verdict")
    record = core.load_records().get(vid) or {}
    text, info = core.saved_transcript(vid, record)
    return text, info, record


def check_channels(days: int = 7, max_channels: int = 3) -> dict:
    """List new videos from the channels in your channels.txt, priority channels first. It fetches
    no transcript and calls no model. Each channel listed is one polite YouTube request, so at
    most max_channels are listed per call; call again to go on through the rest."""
    if not 1 <= int(days) <= 30:
        raise ToolError("days must be between 1 and 30")
    if not 1 <= int(max_channels) <= 10:
        raise ToolError("max_channels must be between 1 and 10")
    urls = core.read_channel_urls()
    if not urls:
        return {"videos": [], "channels_listed": [], "channels_left_today": 0,
                "note": f"No channels yet: put the ones you follow in {core.CHANNELS_PATH}, "
                        f"one link a line."}
    if not core.usable_paths():
        why = "; ".join(f"{p}: {core.path_blocked(p)}" for p in core.path_order())
        return {"videos": [], "channels_listed": [], "channels_left_today": len(urls),
                "note": f"Not listing now: {why or 'no transcript path is on'}. worthwatch waits."}
    favourites = core.favourite_channels(urls)
    rotation = [c for c in urls if c not in favourites]
    if rotation and core.day_scan_complete(rotation):
        core.start_new_pass(rotation)
    order = list(favourites) + [c for c in rotation
                                if c not in core.rotation_state(rotation)[1]]
    chosen = order[:int(max_channels)]
    records, seen = core.load_records(), core.load_seen()
    videos, listed, note = [], [], ""
    for curl in chosen:
        try:
            found = core.list_channel_videos(curl)
        except core.RateLimitError:
            note = "YouTube refused a listing for now (HTTP 429); worthwatch waits."
            break
        except core.ListingFailed as e:
            note = f"The listing of {curl} failed: {core.one_line(str(e))[:200]}"
            continue
        listed.append(curl)
        if curl in rotation:
            core.mark_channel_scanned(rotation, curl)
        for v in core.channel_videos_for_run(found, curl, int(days)):
            vid = v.get("id")
            if not vid or not VIDEO_ID_RE.fullmatch(vid):
                continue
            rec = records.get(vid) or {}
            verdict = core.final_verdict(rec)[0] if rec.get("reason_code") in core.REASON_CODES else None
            videos.append({"id": vid, "title": v.get("title") or vid,
                           "channel": v.get("channel") or curl.rsplit("/", 1)[-1],
                           "url": canonical_url(vid), "uploaded": v.get("upload_date"),
                           "already_read": bool(rec.get("read_day") or vid in seen),
                           "verdict": verdict})
    left = len([c for c in rotation if c not in core.rotation_state(rotation)[1]])
    return {"videos": videos, "channels_listed": listed, "channels_left_today": left,
            "note": note or (f"{left} channel(s) not listed yet today; call again for them."
                             if left else "Every channel has been listed today.")}


def get_transcript(video: str) -> dict:
    """The transcript of one YouTube video (a video link or its 11-character id): from this
    machine if worthwatch already has it, otherwise from YouTube's captions, politely. A refusal
    means wait."""
    vid = video_id(video)
    source = "on this machine"
    if core.saved_transcript(vid)[0] is None:
        source = "fetched now"
    text, info, record = _transcript(vid)
    clipped = text[:TRANSCRIPT_MAX_CHARS]
    return {"id": vid, "title": record.get("title") or vid,
            "channel": record.get("channel") or "UNKNOWN", "url": canonical_url(vid),
            "minutes": (info or {}).get("minutes"), "words": (info or {}).get("words"),
            "source": source, "transcript": clipped,
            "note": "" if len(text) <= TRANSCRIPT_MAX_CHARS else
                    f"cut to the first {TRANSCRIPT_MAX_CHARS} characters of {len(text)}"}


def judge_video(video: str) -> dict:
    """Everything an agent needs to judge one video for the user: step 1, the rules that decide
    the verdict (YES, OPPORTUNITY, MAYBE or NO) with the user's context; step 2, the write-up's
    exact format with the full transcript; step 3, how to save it. worthwatch calls no model
    here: you do the judging."""
    vid = video_id(video)
    text, info, record = _transcript(vid)
    context_text = core.load_context()
    title = record.get("title") or vid
    channel = record.get("channel") or "UNKNOWN"
    details = {"title": title, "channel": channel, "url": canonical_url(vid), "duration": None,
               "upload_date": record.get("upload_date"), "transcript": text}
    step1 = core.triage_prompt(context_text, channel, details)
    meta = {"id": vid, "title": title, "uploader": channel, "url": canonical_url(vid),
            "verdict": None}
    step2 = core.build_prompt(context_text, meta, text, ideas=True).replace(
        core.verdict_note(meta),
        "the one you decided in step 1. If it is OPPORTUNITY, section 3 ends with the Opportunity "
        "line and every item in section 6 says \"Applies to: a road not taken\".", 1)
    return {"id": vid, "title": title, "url": canonical_url(vid),
            "step_1_decide_the_verdict": step1 + "\n\n(Answer this for yourself: it decides the "
                                                 "verdict you will give save_verdict.)",
            "step_2_write_it_up": step2,
            "step_3_save": (f"Call save_verdict with video=\"{vid}\", verdict=YES, OPPORTUNITY, "
                            f"MAYBE or NO, and write_up= the write-up from step 2 (sections 1 to "
                            f"9, and the Ideas part if you wrote one). worthwatch checks it "
                            f"against the user's own rules, saves it, and adds it to today's "
                            f"digest.")}


def save_verdict(video: str, verdict: str, write_up: str) -> dict:
    """Store your verdict and write-up for one video. worthwatch first checks them against the
    user's own rules in code, exactly as it checks a model's: quotes must be in the transcript, a
    YES needs a live project and an action, and nothing may break a standing decision. The verdict
    that comes back is the one the digest will show."""
    vid = video_id(video)
    said = (verdict or "").strip().upper()
    if said not in VERDICTS:
        raise ToolError(f"{verdict!r} is not a verdict: use YES, OPPORTUNITY, MAYBE or NO")
    write_up = (write_up or "").strip()
    if not write_up:
        raise ToolError("write_up is empty: send the write-up from judge_video's step 2")
    if len(write_up) > WRITE_UP_MAX_CHARS:
        raise ToolError(f"write_up is {len(write_up)} characters; at most {WRITE_UP_MAX_CHARS}")
    text, info, record = _transcript(vid, fetch=False)
    if text is None:
        raise ToolError(f"There is no transcript of {vid} on this machine yet: call "
                        f"get_transcript or judge_video first")
    title = record.get("title") or vid
    channel = record.get("channel") or "UNKNOWN"
    meta = {"id": vid, "title": title, "uploader": channel, "channel": channel,
            "url": canonical_url(vid), "transcript": text, "lang": "en",
            "raw_path": core.RAW_DIR / f"{vid}.txt", "track_kind": "on disk",
            "verdict": said, "verdict_reason": "your agent's verdict",
            "minutes": (info or {}).get("minutes"), "favourite": bool(record.get("favourite"))}
    body, part = core.split_ideas_section(write_up)
    meta["ideas_part"] = part
    summary = core.finalize_summary(body, text, None, verdict=said, meta=meta, backend="agent")
    settled, summary, notes = core.settle_verdict(said, summary)
    cap = core.label_cap_for(vid, core.section6_state(summary)[1], settled)
    if cap:
        notes = list(notes) + [f"demoted from YES: {cap}"]
    shown = "MAYBE" if cap else settled
    reason = "judged by your agent" + (f" [{'; '.join(notes)}]" if notes else "")
    meta["verdict"] = shown
    core.append_history(shown, channel, title, canonical_url(vid), reason, summary=summary,
                        backend="agent", try_this_week=core.extract_try_this_week(summary),
                        source="agent", confidence=None)
    path = core.save_writeup(meta, summary, "agent", 0.0, False)
    if settled == "OPPORTUNITY":
        core.append_opportunity(meta, summary)
    if not record.get("read_day"):
        core.update_record(vid, url=canonical_url(vid), title=title, channel=channel,
                           source="agent", read_day=core.today_key())
    core.update_record(vid, read_verdict=said, read_reason="your agent's verdict")
    written = core.summary_written(vid, summary, settled)
    final, why, _tags = core.final_verdict(written)
    seen = core.load_seen()
    core.mark_seen(seen, vid, final, title)
    core.save_seen(seen)
    core.refresh_digest(core.today_key(), create=True)
    return {"id": vid, "verdict": final, "your_agent_said": said,
            "why": why or ("; ".join(notes) if notes else "kept as you said"),
            "write_up_file": str(path)}


def todays_digest(day: str = "") -> dict:
    """The digest of one day (today unless day is given as YYYY-MM-DD): every video read that day,
    its verdict, and the day's ideas."""
    day = (day or "").strip() or core.today_key()
    if not DAY_RE.fullmatch(day):
        raise ToolError("day must be a date written YYYY-MM-DD")
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        raise ToolError("day must be a real date written YYYY-MM-DD") from None
    core.refresh_digest(day, create=True)
    short = core.OUTPUT_DIR / f"DIGEST-{day}-short.md"
    full = core.OUTPUT_DIR / f"DIGEST-{day}.md"
    path = short if short.exists() else full
    text = path.read_text(encoding="utf-8") if path.exists() else f"Nothing was read on {day}."
    return {"day": day, "digest": text, "file": str(full) if full.exists() else ""}


def _idea_row(idea):
    return {"id": idea.get("id"), "idea": idea.get("line"),
            "who_pays": idea.get("who_pays") or "unknown",
            "how_much": idea.get("how_much") or "unknown",
            "first_step": idea.get("first_step") or "",
            "backed_by": len(idea.get("evidence") or []),
            "first_seen": idea.get("first_day"), "last_seen": idea.get("last_day"),
            "killed": idea.get("status") == "killed",
            "why_killed": idea.get("killed_reason") or ""}


def list_ideas(include_killed: bool = False) -> dict:
    """The idea board: every idea worthwatch has found in the videos or you added, with who pays,
    how much, a first step and how many videos back it. Killed ideas only when asked for."""
    store = core.load_ideas()
    rows = [_idea_row(i) for i in store["ideas"].values()
            if include_killed or i.get("status") != "killed"]
    return {"ideas": rows, "board_file": str(core.ideas_board_path())}


def add_idea(idea: str, who_pays: str = "", how_much: str = "", first_step: str = "",
             why_now: str = "") -> dict:
    """Put an idea of your own on the idea board."""
    line = plain(idea, field="idea")
    if not line:
        raise ToolError("idea is empty")
    store = core.load_ideas()
    iid = core.idea_id(store["next"])
    store["next"] += 1
    day = core.today_key()
    store["ideas"][iid] = {
        "id": iid, "line": line, "who_pays": plain(who_pays, field="who_pays") or "unknown",
        "how_much": plain(how_much, field="how_much") or "unknown", "mark": None,
        "why_now": plain(why_now, field="why_now"), "first_step": plain(first_step, field="first_step"),
        "transfers": [], "catch": "", "project": None, "killed": None,
        "first_day": day, "last_day": day, "evidence": [], "added_by": "you"}
    core.save_ideas(store)
    core.write_ideas_board(store)
    return {"id": iid, "idea": line, "added": True}


def kill_idea(idea_id: str, reason: str) -> dict:
    """Kill an idea on the board, with the reason, so it stops being suggested."""
    m = IDEA_ARG_RE.fullmatch((idea_id or "").strip())
    if not m:
        raise ToolError(f"{idea_id[:40]!r} is not an idea id like I-0001")
    iid = core.idea_id(m.group(1))
    why = plain(reason, field="reason")
    if not why:
        raise ToolError("say why the idea is killed, in reason")
    store = core.load_ideas()
    if iid not in store["ideas"]:
        raise ToolError(f"{iid} is not on the idea board; list_ideas shows what is")
    idea = store["ideas"][iid]
    idea.update(status="killed", killed_reason=why, killed_day=core.today_key())
    core.save_ideas(store)
    core.write_ideas_board(store)
    return {"id": iid, "killed": True, "reason": why}


TOOLS = (check_channels, get_transcript, judge_video, save_verdict, todays_digest, list_ideas,
         add_idea, kill_idea)


def build_server():
    server = MCPServer("worthwatch", instructions=INSTRUCTIONS, version=core.__version__)
    for fn in TOOLS:
        server.tool()(fn)
    return server


def serve():
    """Run the server over stdio until the client goes away."""
    build_server().run()
