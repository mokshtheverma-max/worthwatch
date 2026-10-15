"""The `worthwatch` command a stranger types first: --help, --version and `worthwatch init`.

`worthwatch init` makes the three folders, copies the invented example person's files into the
config folder (settings, rules, context, channels, topics), never overwrites a file that is
already there, and asks which brain does the judging: agent (your own AI agent through MCP, no
model call from worthwatch), claude (Claude Code's `claude -p`), codex (`codex exec`), api (an
OpenAI-compatible API whose key is in an environment variable) or ollama (a local model). With
--brain it asks nothing; without a terminal to ask in, it takes the first brain this machine has
and says which.

The command runs in a child process here, exactly as a person would run it, with every folder in
a temp folder. No YouTube request, no model call.

Watch it go red: make `worthwatch init` overwrite an existing context.md in a copy of the
package and run this file against the copy.
"""
import io
import os
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
ROOT = _tmpsandbox.sandbox(ww, "cli")
from worthwatch import brains, firstrun

fails = []
SRC = Path(__file__).resolve().parent.parent / "src"


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def worthwatch(*args, home, stdin=subprocess.DEVNULL):
    """Run `python -m worthwatch ARGS` with WORTHWATCH_HOME pointing at `home`."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("WORTHWATCH_") and k not in brains.API_KEY_VARIABLES}
    env.update(WORTHWATCH_HOME=str(home), PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8",
               WORTHWATCH_NO_NETWORK_CHECKS="1")
    proc = subprocess.run([sys.executable, "-m", "worthwatch", *args], env=env, stdin=stdin,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


print("=== A. --help and --version ===")
home = ROOT / "home-a"
code, out = worthwatch("--help", home=home)
check(code == 0 and "usage: worthwatch" in out, f"--help exits 0 and shows the usage ({code})")
check("init" in out and "doctor" in out, "and names init and doctor")
code, out = worthwatch("--version", home=home)
check(code == 0 and out.strip() == f"worthwatch {ww.__version__}", f"--version: {out.strip()!r}")
check(not home.exists(), "neither writes anything")

print("\n=== B. worthwatch init, with a brain named ===")
home = ROOT / "home-b"
code, out = worthwatch("init", "--brain", "claude", home=home)
check(code == 0, f"init exits 0 ({code}): {out.strip()[-200:]}")
config = home
for name in ("settings.toml", "rules.toml", "context.md", "channels.txt", "topics.txt"):
    check((config / name).is_file(), f"it writes {name} into the config folder")
check((home / "data").is_dir() and (home / "output").is_dir(), "and makes the data and output folders")
settings_text = (config / "settings.toml").read_text(encoding="utf-8")
check('kind = "claude"' in settings_text.split("[brain]", 1)[-1],
      "the brain it was given is recorded under [brain]")
check("context.md" in out and "worthwatch doctor" in out,
      "and the next steps name the context file and the doctor")
written = sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))
check(all(p.split("/")[0] in ("settings.toml", "rules.toml", "context.md", "channels.txt",
                               "topics.txt", "data", "output") for p in written),
      f"nothing else is written ({written})")

print("\n=== C. init again: nothing is overwritten ===")
mine = "# My own context\n\nI make pottery and sell it online.\n"
(config / "context.md").write_text(mine, encoding="utf-8")
before = {p: p.read_bytes() for p in config.glob("*.*")}
code, out = worthwatch("init", "--brain", "claude", home=home)
check(code == 0, f"a second init exits 0 ({code})")
check((config / "context.md").read_text(encoding="utf-8") == mine,
      "your own context file is left exactly as it was")
check(all(p.read_bytes() == b for p, b in before.items()), "and so is every other file")
check("left alone" in out, "and it says which files it left alone")

print("\n=== D. init with no terminal to ask in ===")
home = ROOT / "home-d"
code, out = worthwatch("init", home=home)
check(code == 0, f"init with no --brain and no terminal exits 0 ({code})")
kind = firstrun.recorded_brain(home / "settings.toml")
check(kind in brains.BRAIN_KINDS, f"and records one of the brains ({kind})")
check(f"brain: {kind}" in out, f"and says which it chose: {out.strip()[-160:]!r}")

print("\n=== E. choosing a brain ===")
none_found = {k: (k == "agent", "") for k in brains.BRAIN_KINDS}
check(firstrun.choose_brain(none_found) == "agent",
      "with nothing found, agent mode, which needs no model at all")
some = dict(none_found, codex=(True, "codex found"), ollama=(True, "ollama answered"))
check(firstrun.choose_brain(some) == "codex", "otherwise the first found, in the listed order")
answers = iter(["9", "seven", "", "3"])
asked = []
got = firstrun.ask_brain(some, input_fn=lambda prompt: (asked.append(prompt), next(answers))[1],
                         print_fn=lambda *a, **k: None)
check(got == "codex", f"an empty answer takes the suggested brain ({got})")
check(len(asked) == 3, f"and an answer that is not on the list is asked again ({len(asked)} asks)")
answers = iter(["5"])
check(firstrun.ask_brain(some, input_fn=lambda p: next(answers), print_fn=lambda *a, **k: None)
      == brains.BRAIN_KINDS[4], "a number picks that brain")


def no_answer(prompt):
    raise EOFError


check(firstrun.ask_brain(some, input_fn=no_answer, print_fn=lambda *a, **k: None) == "codex",
      "and nobody there to answer (end of input) takes the suggestion instead of crashing")

print("\n=== F. finding the brains on this machine ===")
found = brains.detect(env={"GEMINI_API_KEY": "secret-value-1234"},
                      which=lambda name: f"/bin/{name}" if name == "claude" else None,
                      ollama_models=lambda: (_ for _ in ()).throw(OSError("refused")))
check(found["agent"][0], "agent mode is always there")
check(found["claude"][0] and not found["codex"][0], "claude on the PATH is found; codex is not")
check(found["api"][0] and "GEMINI_API_KEY" in found["api"][1]
      and "secret-value-1234" not in found["api"][1],
      f"a key in the environment is found by its name, never shown ({found['api'][1]})")
check(not found["ollama"][0] and found["ollama"][1], f"Ollama not answering says so ({found['ollama'][1]})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
