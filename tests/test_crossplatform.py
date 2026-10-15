"""The same code runs on Windows, macOS and Linux.

yt-dlp runs from the installed Python package (`python -m yt_dlp`), never from a hard-coded .exe.
Its JavaScript runtime follows yt-dlp's own README: Deno is recommended and is the only runtime
yt-dlp switches on by default, and worthwatch installs it through yt-dlp's `deno` extra, so by
default no runtime flag is passed at all; a runtime you choose in settings (node, bun, quickjs) is
passed the way the README says, --no-js-runtimes first. A timeout kills the whole process tree
with each operating system's own means. Windows-only steps (the CUDA library preload, clip.exe)
run on Windows only, and the clipboard copy uses each system's own tool.

Every operating system is checked here by naming it to the function under test, so the Windows
answers are checked on a Mac and the other way round. No YouTube request, no model call.

Watch it go red: put back the hard-coded path, YT_DLP = "yt-dlp.exe" with no argv expansion, in
a copy of src/worthwatch/core.py and run this file against the copy.
"""
import io
import subprocess
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "crossplatform")

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


launched = []


class FakeProc:
    def __init__(self, argv, **kw):
        launched.append((list(argv), kw))
        self.pid = 4242
        self.returncode = 0

    def communicate(self, timeout=None):
        return "{}", ""

    def wait(self, timeout=None):
        return 0


real_subprocess = ww.subprocess
ww.subprocess = types.SimpleNamespace(
    Popen=FakeProc, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired,
    CompletedProcess=subprocess.CompletedProcess,
    run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
ww.wait_for_request_slot = lambda: None

print("=== A. yt-dlp runs from the installed package ===")
ww.run_yt_dlp([ww.YT_DLP, "--dump-json", "https://www.youtube.com/watch?v=abcdefghijk"])
argv, kw = launched[-1]
check(argv[:3] == [sys.executable, "-m", "yt_dlp"],
      f"the command starts with this Python and -m yt_dlp ({argv[:3]})")
check(not any(str(a).lower().endswith(".exe") and "yt-dlp" in str(a).lower() for a in argv),
      "and names no yt-dlp.exe anywhere")
check(argv[-1] == "https://www.youtube.com/watch?v=abcdefghijk" and "--dump-json" in argv,
      "the call's own arguments follow, unchanged")
check(ww.yt_dlp_argv() == [sys.executable, "-m", "yt_dlp"], "yt_dlp_argv() says the same")

print("\n=== B. the JavaScript runtime, as yt-dlp's README describes it ===")
check(ww.JS_RUNTIME == "", f"by default no runtime is named ({ww.JS_RUNTIME!r}): yt-dlp uses Deno")
check("--js-runtimes" not in argv and "--no-js-runtimes" not in argv,
      "so the default command carries no runtime flag")
check(ww.js_runtime_flags("") == [] and ww.js_runtime_flags("deno") == [],
      "naming Deno is the same as naming nothing")
check(ww.js_runtime_flags("node") == ["--no-js-runtimes", "--js-runtimes", "node"],
      f"another runtime clears the default first ({ww.js_runtime_flags('node')})")
saved = ww.JS_RUNTIME
ww.JS_RUNTIME = "bun"
ww.run_yt_dlp([ww.YT_DLP, "--dump-json", "https://www.youtube.com/watch?v=abcdefghijk"])
argv_bun, _kw = launched[-1]
ww.JS_RUNTIME = saved
i = argv_bun.index("--no-js-runtimes") if "--no-js-runtimes" in argv_bun else -1
check(i > 0 and argv_bun[i + 1:i + 3] == ["--js-runtimes", "bun"],
      "and the setting reaches the command line in that order")

print("\n=== C. a timeout kills the whole process tree, on each system ===")
check(ww.group_kwargs("nt") == {}, "Windows starts the child as it is")
check(ww.group_kwargs("posix") == {"start_new_session": True},
      "macOS and Linux start it in its own process group, so the group can be killed")
calls = []
fake_os = types.SimpleNamespace(getpgid=lambda pid: pid + 1,
                                killpg=lambda pgid, sig: calls.append(("killpg", pgid, sig)))
ww.kill_tree(types.SimpleNamespace(pid=4242, kill=lambda: calls.append(("kill",))), "posix",
             run=lambda argv, **k: calls.append(("run", argv)), os_module=fake_os)
check(calls and calls[0][0] == "killpg" and calls[0][1] == 4243,
      f"on macOS and Linux it kills the child's process group ({calls})")
check(not any(c[0] == "run" for c in calls), "and never runs taskkill there")
calls.clear()
ww.kill_tree(types.SimpleNamespace(pid=4242, kill=lambda: calls.append(("kill",))), "nt",
             run=lambda argv, **k: calls.append(("run", argv)), os_module=fake_os)
check(calls and calls[0] == ("run", ["taskkill", "/F", "/T", "/PID", "4242"]),
      f"on Windows it runs taskkill /T, which takes the children too ({calls})")
src = Path(ww.__file__).read_text(encoding="utf-8")
body = src[src.index("def kill_tree("):]
body = body[:body.index("\ndef ", 1)]
check(src.count("taskkill") == body.count("taskkill"),
      "and kill_tree is the only place that names taskkill")

print("\n=== D. Windows-only steps run on Windows only ===")
check(ww._add_cuda_dll_dirs("posix") == [], "the CUDA library preload does nothing elsewhere")
clip_body = src[src.index("def copy_to_clipboard("):]
clip_body = clip_body[:clip_body.index("\ndef ", 1)]
check(src.count('["clip"]') == clip_body.count('["clip"]') == 1 and "clip.exe" not in src,
      "Windows' clip is named once, inside copy_to_clipboard")
ran = []


def fake_run(argv, **k):
    ran.append(argv)
    return subprocess.CompletedProcess(argv, 0, b"", b"")


for system, tools, want in (("win32", {"clip"}, ["clip"]),
                            ("darwin", {"pbcopy"}, ["pbcopy"]),
                            ("linux", {"wl-copy", "xclip"}, ["wl-copy"]),
                            ("linux", {"xclip"}, ["xclip", "-selection", "clipboard"])):
    ran.clear()
    ok, how = ww.copy_to_clipboard("some text", platform=system,
                                   which=lambda name, t=tools: name if name in t else None,
                                   run=fake_run)
    check(ok and ran and ran[0] == want, f"{system} with {sorted(tools)}: {ran[0] if ran else None}")
ran.clear()
ok, how = ww.copy_to_clipboard("some text", platform="linux", which=lambda name: None, run=fake_run)
check(not ok and not ran and "xclip" in how,
      f"CONTROL: with no clipboard tool, nothing runs and it says what to install ({how})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
