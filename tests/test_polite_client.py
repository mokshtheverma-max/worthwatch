"""worthwatch asks YouTube plainly, and stops when it is told to.

Every yt-dlp call goes through one function with one set of flags. None of them pretends to be a
browser (--impersonate), sends cookies, carries a proof-of-origin token, picks a different
YouTube player client, or goes through a proxy, and the source holds no switch that would. A
refusal (HTTP 429) stops at once: the video waits for another day instead of being tried
another way.

Patches subprocess.Popen, so nothing here touches the network. Writes only inside its temp
folder.

Watch it go red: add "--impersonate", "chrome" to the flags client_flags() returns, in a copy of
src/worthwatch/core.py, and run this file against the copy.
"""
import io
import re
import subprocess
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "polite-client")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


launched = []
ANSWER = {"stdout": "{}", "stderr": "", "code": 0}


class FakeProc:
    def __init__(self, argv):
        launched.append([str(a) for a in argv])
        self.pid = 1
        self.returncode = ANSWER["code"]

    def communicate(self, timeout=None):
        return ANSWER["stdout"], ANSWER["stderr"]

    def wait(self, timeout=None):
        return 0


ww.subprocess = types.SimpleNamespace(
    Popen=lambda argv, **kw: FakeProc(argv),
    PIPE=subprocess.PIPE,
    TimeoutExpired=subprocess.TimeoutExpired,
    CompletedProcess=subprocess.CompletedProcess,
    run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""),
)
ww.wait_for_request_slot = lambda: None

# Anything that would make a request look like something it is not: a browser's name, a
# browser's cookies, a proof-of-origin token, a chosen player client, a proxy or a forged
# address. An honest User-Agent naming worthwatch is fine; a browser's is not.
FORBIDDEN = re.compile(r"impersonate|cookies|po_token|pot[_-]?provider|player_client|"
                       r"player-client|--proxy|geo-verification-proxy|--xff|--user-agent|"
                       r"--add-headers?|bgutil|mozilla/|applewebkit|like gecko", re.I)

print("=== A. every entry point into yt-dlp asks plainly ===")
CASES = [
    ("get_channel_info", lambda: ww.get_channel_info("https://www.youtube.com/@example-channel")),
    ("list_channel_videos", lambda: ww.list_channel_videos("https://www.youtube.com/@example-channel")),
    ("search_videos", lambda: ww.search_videos("a query")),
    ("fetch_video_basic_info", lambda: ww.fetch_video_basic_info("abcdefghijk")),
    ("fetch_transcript", lambda: ww.fetch_transcript("https://www.youtube.com/watch?v=abcdefghijk")),
]
for name, fn in CASES:
    launched.clear()
    try:
        fn()
    except Exception:  # noqa: BLE001 - the fake returns junk; only the command line matters here
        pass
    check(bool(launched), f"{name} launched at least one yt-dlp call")
    bad = [a for argv in launched for a in argv if FORBIDDEN.search(a)]
    check(not bad, f"{name}: nothing that disguises the request ({bad})")
    check(all("--js-runtimes" in argv for argv in launched),
          f"{name}: and the JavaScript runtime yt-dlp's own README asks for is named")

print("\n=== B. the audio path asks plainly too ===")
launched.clear()
with ww.media_client():
    try:
        ww.run_yt_dlp([ww.YT_DLP, "--dump-json", "https://www.youtube.com/watch?v=abcdefghijk"])
    except Exception:  # noqa: BLE001
        pass
bad = [a for argv in launched for a in argv if FORBIDDEN.search(a)]
check(bool(launched) and not bad, f"inside the media client, nothing that disguises the request "
                                  f"({bad})")
check(ww.client_config().get("path") == "captions" and "player_client" not in ww.client_config(),
      f"the request log records which path asked, and no client name ({ww.client_config()})")

print("\n=== C. no switch for any of it in the source ===")
SRC = Path(ww.__file__).resolve().parent
for path in sorted(SRC.glob("*.py")):
    code = [(n, l) for n, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if not l.lstrip().startswith("#")]
    hits = [n for n, l in code if FORBIDDEN.search(l)]
    check(not hits, f"{path.name}: no line of code names a way to disguise a request "
                    f"(lines {hits[:8]})")

print("\n=== D. a refusal stops at once, and is not tried another way ===")
launched.clear()
ANSWER.update(stdout="", stderr="ERROR: unable to download: HTTP Error 429: Too Many Requests\n",
              code=1)
raised = None
try:
    ww.run_yt_dlp([ww.YT_DLP, "--dump-json", "https://www.youtube.com/watch?v=abcdefghijk"])
except Exception as e:  # noqa: BLE001 - which exception is part of what is checked
    raised = e
check(isinstance(raised, ww.RateLimitError), f"a 429 raises RateLimitError ({raised!r})")
check(len(launched) == 1, f"after exactly one request ({len(launched)})")
state = ww.load_rate_state()
check(any(k.endswith("cooldown_until") and state.get(k) for k in state),
      f"and a cooldown is written, so the next run waits ({sorted(state)})")
ANSWER.update(stdout="{}", stderr="", code=0)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
