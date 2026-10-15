"""The documents a stranger reads say what the code does, and nothing it does not.

README.md has every section the project promised: what it is in two lines, a sample digest,
what makes it different, a quickstart, brains free first, use from an AI agent, privacy,
responsible use, troubleshooting, and a marked spot for the author's own words that is left
empty for him to fill. Every `worthwatch` command the README or the other documents give is one
the command line knows, every local link points at a file that exists, and the sample digest in
examples/ is exactly what the code makes today (scripts/make_sample_digest.py, run here again).
LICENSE is the Apache-2.0 text; CONTRIBUTING, SECURITY, CHANGELOG and the issue templates exist.

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

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
