"""worthwatch's MCP server, driven by a real MCP client.

`worthwatch mcp` serves these tools over stdio, on the official MCP Python SDK: check_channels,
get_transcript, judge_video, save_verdict, todays_digest, list_ideas, add_idea and kill_idea.

First in-process, through the SDK's own Client connected to the server object, with YouTube and
every model stubbed: each tool does its job, and the safety rules hold. A video argument must be a
YouTube video link or an 11-character id, refused otherwise before any request; the request is
built from the id alone; an agent's verdict goes through the same rules in code as a model's;
nothing is written outside worthwatch's data and output folders; no tool runs a shell.

Then the real thing: `python -m worthwatch mcp` started as a child process by the SDK's stdio
client, initialized, its tool list read and printed, and the tools that need no YouTube request
called over the wire. No YouTube request and no model call anywhere in this file.

Watch it go red: make video_id() accept any 11 characters (drop the check of the characters) in a
copy of src/worthwatch/mcpserver.py and run this file against the copy.
"""
import asyncio
import io
import json
import os
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "mcp")
from worthwatch import firstrun, mcpserver

from mcp import Client, StdioServerParameters

fails = []
SRC = Path(__file__).resolve().parent.parent / "src"
TOOLS = {"check_channels", "get_transcript", "judge_video", "save_verdict", "todays_digest",
         "list_ideas", "add_idea", "kill_idea"}


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


class Spy:
    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **k):
        raise AssertionError(f"the test called {self.name}; it must not")


for _name in ("run_yt_dlp", "ollama_generate", "claude_generate", "claude_generate_with",
              "codex_generate", "api_generate"):
    setattr(ww, _name, Spy(_name))
ww.polite_delay = lambda *a, **k: None

TALK = ("we closed the books for forty bakeries in one afternoon by importing the bank export "
        "and checking each total against the statement before anyone signed anything ")
FETCHED = []


def fake_fetch(url, plan=None):
    FETCHED.append(url)
    vid = ww.video_id_from_url(url)
    raw = ww.RAW_DIR / f"{vid}.txt"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(TALK * 40, encoding="utf-8")
    return {"id": vid, "title": "How we closed the books for forty bakeries", "uploader": "Shop Talk",
            "channel": "Shop Talk", "url": url, "transcript": TALK * 40, "raw_path": raw,
            "lang": "en", "track_kind": "auto"}, None


ww.fetch_transcript_by_plan = fake_fetch


def result_data(result):
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
    return json.loads(result.content[0].text)


def error_text(result):
    return result.content[0].text if result.content else ""


async def in_process():
    server = mcpserver.build_server()
    config_before = {p.name: p.read_bytes() for p in ww.CONFIG_DIR.iterdir() if p.is_file()}
    top_before = {p.name for p in ROOT.iterdir()}
    async with Client(server) as client:
        print("=== A. the tool list ===")
        listed = await client.list_tools()
        names = {t.name for t in listed.tools}
        check(names == TOOLS, f"exactly the eight tools ({sorted(names)})")
        check(all((t.description or "").strip() for t in listed.tools),
              "and every one says what it does")

        print("\n=== B. a video argument must be a YouTube video link or an id ===")
        for bad in ("https://example.com/watch?v=abcdefghijk", "rm -rf ~", "abc",
                    "../../secrets.txt", "abcdefghij;", "https://youtu.be/abcdefghijkl",
                    "file:///etc/passwd", ""):
            r = await client.call_tool("get_transcript", {"video": bad})
            check(r.is_error and "11-character" in error_text(r),
                  f"refused: {bad!r} ({error_text(r)[:70]})")
        check(not FETCHED, "and not one of them reached YouTube")
        for good, vid in (("https://www.youtube.com/watch?v=MaraMcp0001&t=30s", "MaraMcp0001"),
                          ("https://youtu.be/MaraMcp0001?si=share", "MaraMcp0001"),
                          ("https://www.youtube.com/shorts/MaraMcp0001", "MaraMcp0001"),
                          ("MaraMcp0001", "MaraMcp0001")):
            check(mcpserver.video_id(good) == vid, f"accepted: {good}")

        print("\n=== C. get_transcript ===")
        r = await client.call_tool("get_transcript",
                                   {"video": "https://youtu.be/MaraMcp0001?si=x&list=y"})
        data = result_data(r)
        check(not r.is_error and data["id"] == "MaraMcp0001" and "forty bakeries" in data["transcript"],
              f"the transcript comes back ({data.get('words')} words)")
        check(FETCHED == ["https://www.youtube.com/watch?v=MaraMcp0001"],
              f"fetched once, from a link built from the id alone ({FETCHED})")
        record = ww.load_records()["MaraMcp0001"]
        check(record.get("summary_state") == "READ_NOT_WRITTEN",
              "and recorded as read, waiting for a verdict")
        r = await client.call_tool("get_transcript", {"video": "MaraMcp0001"})
        check(not r.is_error and len(FETCHED) == 1,
              "asked again, it is read from this machine, with no second request")
        real_fetch = ww.fetch_transcript_by_plan

        def refused(url, plan=None):
            raise ww.RateLimitError("HTTP Error 429: Too Many Requests")
        ww.fetch_transcript_by_plan = refused
        r = await client.call_tool("get_transcript", {"video": "MaraMcp0002"})
        check(r.is_error and "wait" in error_text(r).lower(),
              f"a refusal says to wait, and nothing tries another way ({error_text(r)[:90]})")
        ww.fetch_transcript_by_plan = real_fetch

        print("\n=== D. judge_video hands the agent everything, and calls no model ===")
        r = await client.call_tool("judge_video", {"video": "MaraMcp0001"})
        data = result_data(r)
        context = ww.CONTEXT_PATH.read_text(encoding="utf-8")
        step1, step2 = data.get("step_1_decide_the_verdict", ""), data.get("step_2_write_it_up", "")
        check("forty bakeries" in step2 and "VERDICT" in step1,
              "the triage rules (step 1) and the write-up format with the transcript (step 2)")
        check("Ledgerleaf" in step2 and "## 6. What applies to me" in step2,
              "the context file and the write-up's sections are in it")
        check("save_verdict" in data.get("step_3_save", ""), "and step 3 says to call save_verdict")
        check(context.splitlines()[0] in step2, "the context is the user's own file, as it is")

        print("\n=== E. save_verdict: the agent's verdict meets the same rules as a model's ===")
        writeup = ("## 1. What it is\nA bakery bookkeeping talk.\n\n## 2. The claim\nImports work.\n\n"
                   "## 3. Evidenced versus asserted\nShown on screen.\n\n## 4. The method\n1. Import.\n\n"
                   "## 5. Worth keeping verbatim\n\"checking each total against the statement\"\n\n"
                   "## 6. What applies to me\n\nMost applicable: \"checking each total against the "
                   "statement\" - check every imported total against the bank statement. Applies "
                   "to: Ledgerleaf.\n\n## 7. What contradicts what I believe\n\nnothing in this video "
                   "touches the beliefs list\n\n## 8. Try this week\n\nRun a $10 a day Google Ads "
                   "campaign for Ledgerleaf.\n\n## 9. Header\n\nnothing\n")
        r = await client.call_tool("save_verdict", {"video": "MaraMcp0001", "verdict": "YES",
                                                     "write_up": writeup})
        data = result_data(r)
        check(not r.is_error and data["your_agent_said"] == "YES",
              f"saved ({data.get('verdict')}: {data.get('why', '')[:90]})")
        check(data["verdict"] == "MAYBE" and "no paid ads" in data["why"],
              "the agent's YES whose action is paid ads is demoted by the user's own rule")
        check(Path(data["write_up_file"]).is_file()
              and Path(ww.OUTPUT_DIR) in Path(data["write_up_file"]).parents,
              "the write-up is saved in the output folder")
        r = await client.call_tool("save_verdict", {"video": "MaraMcp0001", "verdict": "GREAT",
                                                     "write_up": writeup})
        check(r.is_error and "YES, OPPORTUNITY, MAYBE or NO" in error_text(r),
              f"a verdict that is not one of the four is refused ({error_text(r)[:80]})")
        r = await client.call_tool("save_verdict", {"video": "MaraMcp0009", "verdict": "YES",
                                                     "write_up": writeup})
        check(r.is_error and "get_transcript" in error_text(r),
              "a video with no transcript on this machine is refused, and says what to do first")

        print("\n=== F. todays_digest ===")
        r = await client.call_tool("todays_digest", {})
        data = result_data(r)
        check(not r.is_error and "How we closed the books" in data.get("digest", ""),
              "the day's digest shows the video")
        r = await client.call_tool("todays_digest", {"day": "../../2026-01-01"})
        check(r.is_error and "YYYY-MM-DD" in error_text(r), "a day that is not a date is refused")

        print("\n=== G. the idea board ===")
        r = await client.call_tool("add_idea", {"idea": "Receipt photos straight into a bakery's "
                                                         "books", "who_pays": "bakeries",
                                                 "first_step": "ask three bakeries how they file receipts"})
        added = result_data(r)
        check(not r.is_error and added.get("id", "").startswith("I-"), f"added as {added.get('id')}")
        r = await client.call_tool("list_ideas", {})
        listed = result_data(r)["ideas"]
        check(any(i["id"] == added["id"] for i in listed), "and listed")
        r = await client.call_tool("kill_idea", {"idea_id": added["id"],
                                                  "reason": "the bakeries keep paper on purpose"})
        check(not r.is_error and result_data(r).get("killed") is True, "killed, with a reason")
        r = await client.call_tool("list_ideas", {})
        check(all(i["id"] != added["id"] for i in result_data(r)["ideas"]),
              "a killed idea is off the open list")
        r = await client.call_tool("list_ideas", {"include_killed": True})
        killed = [i for i in result_data(r)["ideas"] if i["id"] == added["id"]]
        check(killed and killed[0]["killed"] and "paper" in killed[0]["why_killed"],
              "and shown, with why, when asked for")
        r = await client.call_tool("kill_idea", {"idea_id": "I-9999", "reason": "x"})
        check(r.is_error, "an idea that is not on the board is refused")
        r = await client.call_tool("kill_idea", {"idea_id": "../ideas.json", "reason": "x"})
        check(r.is_error and "I-" in error_text(r), "and so is anything that is not an idea id")

        print("\n=== H. check_channels lists, and fetches nothing ===")
        ww.CHANNELS_PATH.write_text("https://www.youtube.com/@example-shop-talk\n", encoding="utf-8")
        listings = []

        def fake_listing(curl, max_items=None):
            listings.append(curl)
            return [{"id": "MaraMcp0003", "title": "A new talk", "channel": "Shop Talk",
                     "url": "https://www.youtube.com/watch?v=MaraMcp0003",
                     "upload_date": ww.dt.date.today().strftime("%Y%m%d"), "duration": 900},
                    {"id": "MaraMcp0001", "title": "How we closed the books for forty bakeries",
                     "channel": "Shop Talk", "url": "https://www.youtube.com/watch?v=MaraMcp0001",
                     "upload_date": ww.dt.date.today().strftime("%Y%m%d"), "duration": 900}]
        ww.list_channel_videos = fake_listing
        before = len(FETCHED)
        r = await client.call_tool("check_channels", {"days": 7})
        data = result_data(r)
        ids = {v["id"]: v for v in data.get("videos", [])}
        check(not r.is_error and set(ids) == {"MaraMcp0003", "MaraMcp0001"},
              f"the channel's new videos are listed ({sorted(ids)})")
        check(ids.get("MaraMcp0001", {}).get("already_read") and not ids.get("MaraMcp0003", {}).get("already_read"),
              "with which ones are already read")
        check(len(FETCHED) == before and listings == ["https://www.youtube.com/@example-shop-talk"],
              "one listing, and no transcript fetched")
        r = await client.call_tool("check_channels", {"days": 0, "max_channels": 50})
        check(r.is_error, "days and max_channels outside their limits are refused")

    print("\n=== I. writes only inside worthwatch's data and output folders ===")
    config_after = {p.name: p.read_bytes() for p in ww.CONFIG_DIR.iterdir() if p.is_file()}
    changed = sorted(n for n in set(config_before) | set(config_after)
                     if config_before.get(n) != config_after.get(n) and n != "channels.txt")
    check(not changed, f"the config folder is untouched by every tool ({changed})")
    check({p.name for p in ROOT.iterdir()} == top_before, "and nothing new appeared beside the folders")
    src = Path(mcpserver.__file__).read_text(encoding="utf-8")
    check("subprocess" not in src and "os.system" not in src and "shell=True" not in src,
          "the server module runs no command at all")


asyncio.run(in_process())


print("\n=== J. the real thing: `python -m worthwatch mcp` as a child process, over stdio ===")
HOME = ROOT / "stdio-home"
env = {k: v for k, v in os.environ.items() if not k.startswith("WORTHWATCH_")}
env.update(WORTHWATCH_HOME=str(HOME), PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8",
           WORTHWATCH_NO_NETWORK_CHECKS="1")
firstrun.cmd_init(["--brain", "agent"], env=env)


async def over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "worthwatch", "mcp"], env=env)
    async with Client(params) as client:
        listed = await client.list_tools()
        print("  tools served by `worthwatch mcp`:")
        for t in listed.tools:
            print(f"    {t.name}: {(t.description or '').strip().splitlines()[0]}")
        check({t.name for t in listed.tools} == TOOLS, "the child serves the same eight tools")
        r = await client.call_tool("todays_digest", {})
        check(not r.is_error, "todays_digest answers over the wire")
        r = await client.call_tool("get_transcript", {"video": "not a video at all"})
        check(r.is_error and "11-character" in error_text(r),
              "a bad video argument is refused over the wire too, before any request")
        r = await client.call_tool("add_idea", {"idea": "A sourdough starter subscription for cafes"})
        check(not r.is_error and result_data(r)["id"].startswith("I-"), "add_idea works over the wire")
        r = await client.call_tool("list_ideas", {})
        check(len(result_data(r)["ideas"]) == 1, "and list_ideas sees it")


asyncio.run(over_stdio())
check((HOME / "data" / "ideas.json").is_file(), "the child wrote its idea into its own data folder")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
