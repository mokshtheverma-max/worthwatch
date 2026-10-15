"""The brains, behind one interface, and the defaults that need no GPU.

Two jobs need a model: quick ones (the triage of a listing and of a transcript, short screens)
and the write-up. settings.toml [brain] names the brain for the write-up (kind) and, if you want a
different one for the quick jobs, quick. Each brain is called the way its own documentation says:

  claude  `claude -p --model M`, the prompt on stdin (haiku for quick jobs, sonnet to write)
  codex   `codex exec --skip-git-repo-check --ephemeral --sandbox read-only --color never
          [-m MODEL] -`, the prompt on stdin, the final message alone on stdout, run in an empty
          folder so it reads none of your files
  api     POST <base>/chat/completions with Authorization: Bearer <key>, the key read from its
          environment variable (Gemini, OpenRouter, OpenAI or Groq, or any base_url you name)
  ollama  the local Ollama server
  agent   no model at all: your own agent judges through `worthwatch mcp`

Captions come first; the audio path is off until you switch it on, and says plainly that
downloading may break YouTube's terms. Jev is off until you switch it on.

No YouTube request and no model call: every brain's last step is a stub here, and the suite's
network guard refuses anything else. No key is ever real.

Watch it go red: make quick_generate always call ollama_generate in a copy of
src/worthwatch/core.py and run this file against the copy.
"""
import io
import json
import subprocess
import sys
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "brains")
from worthwatch import brains, settings as wsettings

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


CALLS = []
real_codex, real_api = ww.codex_generate, ww.api_generate    # sections C and D test these
ww.ollama_generate = lambda model, prompt, **kw: (CALLS.append(("ollama", model)), "from ollama")[1]
ww.claude_generate = lambda prompt, timeout=600: (CALLS.append(("claude", "sonnet")), "from claude")[1]
ww.claude_generate_with = lambda prompt, model, timeout=600: (CALLS.append(("claude", model)),
                                                              "from claude")[1]
ww.codex_generate = lambda prompt, model=None, timeout=600: (CALLS.append(("codex", model)),
                                                             "from codex")[1]
ww.api_generate = lambda prompt, size, timeout=600: (CALLS.append(("api", size)), "from api")[1]


def route(kind, quick=""):
    ww.BRAIN, ww.QUICK_BRAIN = kind, quick
    CALLS.clear()
    q = ww.quick_generate("a quick prompt", model="local-model:1b")
    w = ww.write_generate("a write-up prompt")
    return q, w, list(CALLS)


print("=== A. each brain is called for each job ===")
q, w, calls = route("claude")
check(calls == [("claude", "haiku"), ("claude", "sonnet")],
      f"claude: the quick job on haiku, the write-up on sonnet ({calls})")
q, w, calls = route("codex")
check(calls == [("codex", None), ("codex", None)], f"codex: both jobs, its own model ({calls})")
q, w, calls = route("api")
check(calls == [("api", "quick"), ("api", "write")], f"api: a quick model and a writing model ({calls})")
q, w, calls = route("ollama")
check(calls == [("ollama", "local-model:1b"), ("ollama", ww.DEFAULT_LOCAL_MODEL)],
      f"ollama: the model asked for, and the default to write ({calls})")
q, w, calls = route("claude", quick="ollama")
check(calls == [("ollama", "local-model:1b"), ("claude", "sonnet")],
      f"a different quick brain: the quick job local, the write-up on claude ({calls})")
ww.BRAIN, ww.QUICK_BRAIN = "agent", ""
for job in (lambda: ww.quick_generate("p"), lambda: ww.write_generate("p")):
    try:
        job()
        said = None
    except ww.AgentMode as e:
        said = str(e)
    check(said and "worthwatch mcp" in said and "--brain" in said,
          f"agent: no model is called, and it says how to judge instead ({said})")
ww.BRAIN, ww.QUICK_BRAIN, ww.BRAIN_MODEL, ww.QUICK_MODEL = "claude", "", "opus", "sonnet"
CALLS.clear()
ww.quick_generate("p")
ww.write_generate("p")
check(CALLS == [("claude", "sonnet"), ("claude", "opus")],
      f"models named in settings replace the defaults ({CALLS})")
ww.BRAIN_MODEL = ww.QUICK_MODEL = ""

print("\n=== B. labels say which brain wrote what ===")
ww.BRAIN, ww.QUICK_BRAIN = "claude", "ollama"
check(ww.write_label() == "claude:sonnet" and ww.quick_label("m:1b") == "local:m:1b",
      f"claude and a local quick brain keep the labels the digests always had "
      f"({ww.write_label()}, {ww.quick_label('m:1b')})")
ww.BRAIN, ww.QUICK_BRAIN = "codex", ""
check(ww.write_label() == "codex" and ww.quick_label("m:1b") == "codex",
      f"codex: {ww.write_label()}")
ww.BRAIN = "api"
ww.API.update(provider="gemini", base_url="", key_env="")
check(ww.write_label().startswith("api:gemini/"), f"api: {ww.write_label()}")
ww.BRAIN, ww.QUICK_BRAIN = "claude", "ollama"

print("\n=== B2. a write-up from the writing brain keeps its judgment, whichever brain it is ===")
# A small local model's write-up has sections 6 to 8 blanked on purpose (it was measured
# inventing what applies). That must follow the job, not the word "claude" in a label.
SIX = ("## 1. What it is\nA talk.\n\n## 6. What applies to me\n\nMost applicable: \"a quote\" - "
       "do the thing. Applies to: Ledgerleaf\n\n## 7. What contradicts what I believe\n\nnothing in "
       "this video touches the beliefs list\n\n## 8. Try this week\n\nDo the thing.\n")
for kind, label in (("codex", "codex"), ("api", None), ("claude", "claude:sonnet"),
                    ("ollama", None)):
    ww.BRAIN, ww.QUICK_BRAIN = kind, ""
    ww.API.update(provider="gemini", base_url="", key_env="")
    label = label or ww.write_label()
    check(ww.writes_judgment(label), f"{kind} writing ({label}): its judgment is kept")
ww.BRAIN = "claude"
check(ww.writes_judgment("agent"), "your agent's write-up keeps its judgment")
ww.BRAIN, ww.QUICK_BRAIN = "claude", "ollama"
check(not ww.writes_judgment(ww.quick_label("m:1b")),
      "CONTROL: a local quick brain's write-up, while claude writes, is blanked as before")
ww.API.update(provider="", base_url="", key_env="")

print("\n=== C. codex exec, exactly as its documentation says ===")
seen = {}


class FakeCodex:
    def __init__(self, argv, **kw):
        seen.update(argv=list(argv), kw=kw)
        self.pid, self.returncode = 77, 0

    def communicate(self, input=None, timeout=None):
        seen["input"] = input
        return "The final message only.\n", "progress goes to stderr\n"


real_sub, real_which = ww.subprocess, ww.shutil.which
ww.subprocess = types.SimpleNamespace(Popen=FakeCodex, PIPE=subprocess.PIPE,
                                      TimeoutExpired=subprocess.TimeoutExpired,
                                      run=subprocess.run)
ww.shutil.which = lambda name: f"/opt/bin/{name}" if name == "codex" else None
out = real_codex("Judge this video.", model="a-model", timeout=30)
ww.subprocess, ww.shutil.which = real_sub, real_which
check(seen["argv"] == ["/opt/bin/codex", "exec", "--skip-git-repo-check", "--ephemeral",
                       "--sandbox", "read-only", "--color", "never", "-m", "a-model", "-"],
      f"the command line ({seen['argv']})")
check(seen["input"] == "Judge this video.", "the prompt goes in on stdin, never on the command line")
check(out == "The final message only.", "the answer is stdout, the final message alone")
cwd = Path(seen["kw"].get("cwd") or "")
check(cwd.is_dir() and not any(cwd.iterdir()) and Path(ww.DATA_DIR) in cwd.parents,
      f"it runs in an empty folder inside the data folder, so it can read none of your files ({cwd})")

print("\n=== D. an OpenAI-compatible API, its key from the environment ===")
api = real_api
sent = []


class Reply:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def opener(request, timeout=None):
    sent.append(request)
    return Reply({"choices": [{"message": {"role": "assistant", "content": "A verdict."}}]})


KEY = "test-key-" + "x" * 20
ww.API.update(provider="", base_url="", key_env="")
out = api("Judge this.", "write", timeout=30, env={"GROQ_API_KEY": KEY}, opener=opener)
req = sent[-1]
check(req.full_url == "https://api.groq.com/openai/v1/chat/completions",
      f"with only a Groq key set, Groq is used ({req.full_url})")
check(req.get_header("Authorization") == f"Bearer {KEY}", "with the key as a bearer token")
body = json.loads(req.data.decode("utf-8"))
check(body["messages"] == [{"role": "user", "content": "Judge this."}]
      and body["model"] == brains.API_PROVIDERS["groq"]["write"],
      f"the prompt as one user message, to the writing model ({body['model']})")
check(out == "A verdict.", "and the answer is the first choice's message")
both = {"GEMINI_API_KEY": KEY, "GROQ_API_KEY": KEY}
api("q", "quick", timeout=30, env=both, opener=opener)
check(sent[-1].full_url.startswith("https://generativelanguage.googleapis.com/v1beta/openai/")
      and json.loads(sent[-1].data)["model"] == brains.API_PROVIDERS["gemini"]["quick"],
      "with several keys, the first in the list (Gemini, which has a free key) is used")
ww.API.update(provider="openrouter")
api("q", "quick", timeout=30, env={"OPENROUTER_API_KEY": KEY, "GEMINI_API_KEY": KEY}, opener=opener)
check(sent[-1].full_url == "https://openrouter.ai/api/v1/chat/completions",
      "a provider named in settings wins")
ww.API.update(provider="", base_url="https://models.example.org/v1/", key_env="EXAMPLE_KEY")
try:
    api("q", "write", timeout=30, env={"EXAMPLE_KEY": KEY}, opener=opener)
    said = ""
except RuntimeError as e:
    said = str(e)
check("model" in said and "[brain]" in said,
      f"an endpoint worthwatch does not know needs a model named in settings ({said})")
ww.BRAIN_MODEL = "example-model"
api("q", "write", timeout=30, env={"EXAMPLE_KEY": KEY}, opener=opener)
check(sent[-1].full_url == "https://models.example.org/v1/chat/completions"
      and json.loads(sent[-1].data)["model"] == "example-model",
      "and with one, any other OpenAI-compatible endpoint, the key from the variable you name")
ww.BRAIN_MODEL = ""
ww.API.update(provider="", base_url="", key_env="")
try:
    api("q", "write", timeout=30, env={}, opener=opener)
    said = ""
except RuntimeError as e:
    said = str(e)
check("GEMINI_API_KEY" in said and "OPENAI_API_KEY" in said,
      f"no key at all says which variables it looked in ({said[:100]})")
import urllib.error


def refused(request, timeout=None):
    raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {},
                                 io.BytesIO(f"slow down, key {KEY}".encode("utf-8")))


try:
    api("q", "write", timeout=30, env={"OPENAI_API_KEY": KEY}, opener=refused)
    said = ""
except RuntimeError as e:
    said = str(e)
check("429" in said and "wait" in said and KEY not in said,
      f"a refusal says wait, and never shows the key ({said[:120]})")
for name, conf in brains.API_PROVIDERS.items():
    check(conf["env"] in brains.API_KEY_VARIABLES and conf["base"].startswith("https://")
          and conf["quick"] and conf["write"], f"the {name} entry is complete")

print("\n=== E. agent mode on the command line calls no model and fetches nothing ===")


class Captured(io.StringIO):
    def reconfigure(self, **kw):
        pass


real_argv, real_out, real_err = sys.argv, sys.stdout, sys.stderr
buf = Captured()
sys.argv, sys.stdout, sys.stderr = ["worthwatch", "--channels"], buf, buf
ww.run_yt_dlp = lambda *a, **k: (_ for _ in ()).throw(AssertionError("a YouTube request"))
code = None
try:
    ww.main()
except SystemExit as e:
    code = e.code
finally:
    sys.argv, sys.stdout, sys.stderr = real_argv, real_out, real_err
said = buf.getvalue()
check(ww.BRAIN == "agent", f"the example settings say agent ({ww.BRAIN})")
check(code == 2 and "worthwatch mcp" in said and "init --brain" in said,
      f"--channels stops at once, and says how to judge with an agent or choose a brain ({code})")
check("a YouTube request" not in said, "and no YouTube request was made")

print("\n=== F. captions first; audio only when you switch it on ===")
fresh = wsettings.load_profile(ROOT / "config")
check(fresh.audio is False, "the example settings leave the audio path off")
ww.apply_profile(fresh)
check(ww.CAPTION_PATH_ENABLED is True and ww.AUDIO_PATH_ENABLED is False
      and ww.PATH_PREFERENCE[0] == "captions", "captions on, audio off, captions first")
on = wsettings.profile_from({"transcripts": {"audio": True}}, {})
check(on.audio is True, "[transcripts] audio = true switches it on")
ww.apply_profile(on)
check(ww.AUDIO_PATH_ENABLED is True, "and the run sees it")
check("YouTube's terms" in ww.AUDIO_TERMS_WARNING and "switched it on" in ww.AUDIO_TERMS_WARNING,
      f"the warning is plain: {ww.AUDIO_TERMS_WARNING}")
ww.whisper_installed = lambda: False
ok, why = ww.audio_path_ready()
check(not ok and "worthwatch[whisper]" in why,
      f"without the whisper extra the audio path is not used, and it says how to add it ({why})")
ww.whisper_installed = lambda: True
check(ww.audio_path_ready()[0], "CONTROL: with it, the audio path can run")
ww.apply_profile(fresh)

print("\n=== G. Jev is off until you switch it on, with its spending cap ===")
check(ww.OPPORTUNITY_SCREEN_BACKEND == "local", "the opportunity screen uses your own quick brain")
jev_on = wsettings.profile_from({"jev": {"enabled": True, "daily_cap_usd": 0.25}}, {})
ww.apply_profile(jev_on)
check(ww.OPPORTUNITY_SCREEN_BACKEND == "jev" and ww.JEV_DAILY_CAP_USD == 0.25,
      "[jev] enabled = true turns it on, with the cap you set")
check(wsettings.profile_from({}, {}).jev_daily_cap == 1.00, "and the cap is $1.00 a day by default")
try:
    wsettings.profile_from({"jev": {"enabled": True, "daily_cap_usd": 50}}, {})
    said = ""
except wsettings.SettingsError as e:
    said = str(e)
check("daily_cap_usd" in said, f"a cap over $5.00 a day is refused, in plain words ({said[:90]})")
ww.apply_profile(fresh)

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
