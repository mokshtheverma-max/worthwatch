"""The documents a stranger reads say what the code does, and nothing it does not.

README.md has every section the project promised: what it is in two lines, a sample digest,
what makes it different, a quickstart, brains free first, use from an AI agent, privacy,
responsible use, troubleshooting, and a spot for the author's own words (marked and left empty
until he writes them). Every `worthwatch` command the README or the other documents give is one
the command line knows, every local link points at a file that exists, and the sample digest in
examples/ is exactly what the code makes today (scripts/make_sample_digest.py, run here again).
LICENSE is the Apache-2.0 text; CONTRIBUTING, SECURITY, CHANGELOG and the issue templates exist.
Every line, in any file outside tests/, that installs or upgrades worthwatch takes it from its
GitHub repository, because it is not on PyPI yet, and every link to the repository names the
real account.

No YouTube request, no model call.

Watch it go red: change one word of a verdict line in examples/sample-digest.md and run this
file.
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DIR / "tests"))
sys.path.insert(0, str(DIR / "scripts"))
import _tmpsandbox  # noqa: F401  (src on the path, the network refused)
import make_sample_digest

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


readme = (DIR / "README.md").read_text(encoding="utf-8")
headings = [h.strip() for h in re.findall(r"^## (.+)$", readme, re.M)]

print("=== A. the README has what was promised ===")
first = [p for p in readme.split("\n\n")[1:3]]
check(readme.startswith("# worthwatch\n") and len(" ".join(first).split(".")) <= 4,
      "it opens with the name and two short sentences of what it is")
for wanted in ("What a day looks like", "Why it exists", "What makes it different",
               "Quickstart", "Pick your brain", "Use it from your AI agent", "Privacy",
               "Responsible use", "Troubleshooting"):
    check(any(h.startswith(wanted) for h in headings), f"a section: {wanted}")
why = readme.split("## Why it exists", 1)[-1].split("\n## ", 1)[0]
uncommented = re.sub(r"<!--.*?-->", "", why, flags=re.S)
if "<!-- AUTHOR:" in why:
    check("left empty" in why.lower(), "the author's spot is clearly marked as his to write")
    prose = [l for l in uncommented.splitlines() if l.strip() and not l.strip().startswith("_")]
    check(not prose, f"and nothing is written there for him ({prose[:1]})")
else:
    sentences = re.findall(r"[^.!?]+[.!?]", " ".join(uncommented.split()))
    check(1 <= len(sentences) <= 5 and "Left empty" not in uncommented,
          f"the author has written his own few sentences there ({len(sentences)})")
check("| YES |" in readme and "demoted from YES" in readme,
      "the sample in the README shows a kept video and a demoted one")
check("/plugin install worthwatch --marketplace" in readme and "claude mcp add worthwatch -- worthwatch mcp" in readme,
      "the one-step plugin install line and the MCP line are in it")
check("free of charge" in readme.lower() and "ai.google.dev/gemini-api/docs/pricing" in readme,
      "the free Gemini tier is stated with where it was read")
quick = readme.split("## Quickstart", 1)[-1].split("\n## ", 1)[0]
check("uv tool update-shell" in quick and "new terminal" in quick,
      "the quickstart says what to do when uv's tool folder is not on PATH")

print("\n=== B. commands and links that exist ===")
known = {"init", "doctor", "mcp", "status", "pause", "resume", "like", "nah", "remix",
         "cooldown", "requests", "paths", "audio", "resummarize"}
docs = {p: p.read_text(encoding="utf-8") for p in
        [DIR / "README.md", DIR / "CONTRIBUTING.md", DIR / "SECURITY.md", DIR / "docs" / "agents.md",
         DIR / "examples" / "README.md"]}
for path, text in docs.items():
    used = set(re.findall(r"`worthwatch ([a-z]+)", text))
    check(used <= known, f"{path.name}: every `worthwatch <command>` exists "
                         f"({sorted(used - known) or 'all known'})")
    for target in re.findall(r"\]\((?!https?://|#|mailto:)([^)#\s]+)", text):
        check((path.parent / target).exists(), f"{path.name}: the link {target} points at a file")
flags = set(re.findall(r"`worthwatch (--[a-z-]+)", readme))
import subprocess
helptext = subprocess.run([sys.executable, "-m", "worthwatch", "--help"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env={**__import__("os").environ, "PYTHONPATH": str(DIR / "src"),
                               "WORTHWATCH_HOME": str(_tmpsandbox.DIR / "no-such-home")}).stdout
check(all(f in helptext for f in flags), f"every flag the README uses is in --help ({sorted(flags)})")

print("\n=== C. the sample digest is the code's own output ===")
made = make_sample_digest.make()
kept = (DIR / "examples" / "sample-digest.md").read_text(encoding="utf-8")
check(made == kept, "examples/sample-digest.md is exactly what scripts/make_sample_digest.py "
                    "makes from the code today")
sample_lines = [l for l in kept.splitlines() if l.startswith("- **YES**")]
check(sample_lines and all(l in readme for l in sample_lines),
      "and the README shows the same header lines")

print("\n=== D. the other files ===")
license_text = (DIR / "LICENSE").read_text(encoding="utf-8")
check("Apache License" in license_text and "Version 2.0, January 2004" in license_text,
      "LICENSE is the Apache License 2.0")
for name in ("CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
             ".github/ISSUE_TEMPLATE/bug_report.md", ".github/ISSUE_TEMPLATE/feature_request.md"):
    check((DIR / name).is_file() and (DIR / name).stat().st_size > 200, f"{name} exists")
check("Report a vulnerability" in (DIR / "SECURITY.md").read_text(encoding="utf-8"),
      "SECURITY.md says how to report privately")
bug = (DIR / ".github" / "ISSUE_TEMPLATE" / "bug_report.md").read_text(encoding="utf-8")
check("context file" in bug and "key" in bug, "the bug template warns not to paste a context file or a key")

print("\n=== E. install lines and the repository's account ===")
# worthwatch is not on PyPI yet, and anyone could register the name there, so a line that
# installs or upgrades it by name could hand a reader a stranger's package. Until the first PyPI
# release every such line names the GitHub repository. Switch this the day it is on PyPI.
ACCOUNT = "mokshtheverma-max"
REPO = f"git+https://github.com/{ACCOUNT}/worthwatch"
INSTALL = re.compile(r"(?:uv tool (?:install|upgrade)|uvx|pipx? install)\b[^`()\n]*")
SOURCE = re.compile(r"(?:github\.com/|--marketplace |marketplace add )([\w.-]+)/worthwatch")
by_name, wrong_account = [], []
for root, dirs, names in __import__("os").walk(DIR):
    dirs[:] = [d for d in dirs if d not in {".git", ".venv", "venv", "build", "dist",
                                             "__pycache__", "tests"}]
    for name in names:
        if not name.endswith((".md", ".py", ".toml", ".json", ".yml", ".txt")):
            continue
        path = Path(root) / name
        where = path.relative_to(DIR).as_posix()
        for n, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            by_name += [f"{where}:{n}" for cmd in INSTALL.findall(text)
                        if "worthwatch" in cmd and REPO not in cmd]
            wrong_account += [f"{where}:{n}" for acct in SOURCE.findall(text) if acct != ACCOUNT]
check(not by_name, f"no line installs or upgrades worthwatch by name ({by_name[:5] or 'none'})")
check(f"uv tool install {REPO}" in quick, "the quickstart installs it from GitHub")
check(not wrong_account, f"every link to the repository names {ACCOUNT} ({wrong_account[:5] or 'all'})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
