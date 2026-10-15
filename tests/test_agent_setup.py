"""The files that connect worthwatch to AI agents agree with each other and with the code.

  .claude-plugin/marketplace.json            a Claude Code plugin marketplace with one plugin
  plugins/worthwatch/.claude-plugin/plugin.json
  plugins/worthwatch/.mcp.json               the plugin starts `worthwatch mcp`
  plugins/worthwatch/skills/worthwatch/SKILL.md   the skill, for Claude Code
  .agents/skills/worthwatch/SKILL.md         the same skill where Codex reads it
  docs/agents.md                             the setup lines for Claude Code, Claude Desktop,
                                             Codex CLI and Cursor

Checked here: the JSON parses and names what Claude Code's docs require; the plugin's source is
a relative path inside the repository; the two copies of the skill are the same file; the skill
follows the Agent Skills specification (agentskills.io); every MCP tool the skill names is one
the server serves, and every tool the server serves is in the skill; every command the docs give
for worthwatch is one the command line knows. When `claude` is installed, `claude plugin
validate` is run on the marketplace and on the plugin; when it is not, that check is reported as
not run, never as passed.

Watch it go red: rename a tool in src/worthwatch/mcpserver.py's TOOLS (or edit one copy of the
skill) and run this file.
"""
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
from worthwatch import mcpserver

DIR = Path(__file__).resolve().parent.parent
fails, not_run = [], []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


print("=== A. the marketplace and the plugin ===")
market = json.loads((DIR / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
check(market.get("name") and market.get("owner", {}).get("name") and market.get("plugins"),
      "marketplace.json has a name, an owner and plugins")
entry = market["plugins"][0]
source = entry.get("source", "")
check(source.startswith("./") and ".." not in source and (DIR / source).is_dir(),
      f"the plugin's source is a relative path inside the repository ({source})")
plugin_dir = DIR / source
manifest = json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
check(manifest.get("name") == entry.get("name") == "worthwatch",
      "the entry's name and the plugin's own name are the same, as the docs ask")
check(manifest.get("version") == ww.__version__ and manifest.get("license") == "Apache-2.0",
      f"the plugin's version is the package's ({manifest.get('version')})")
servers = json.loads((plugin_dir / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
check(servers.get("worthwatch") == {"command": "worthwatch", "args": ["mcp"]},
      f"the plugin starts `worthwatch mcp` ({servers})")

print("\n=== B. the skill ===")
copies = [plugin_dir / "skills" / "worthwatch" / "SKILL.md",
          DIR / ".agents" / "skills" / "worthwatch" / "SKILL.md"]
texts = [p.read_text(encoding="utf-8") for p in copies]
check(texts[0] == texts[1], "the copy Claude Code reads and the copy Codex reads are the same")
skill = texts[0]
front = re.match(r"---\n(.*?)\n---\n", skill, re.S)
check(bool(front), "it starts with YAML frontmatter")
fields = dict(re.findall(r"^([a-z-]+):\s*(.+)$", front.group(1) if front else "", re.M))
name, description = fields.get("name", ""), fields.get("description", "")
check(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) and len(name) <= 64
      and name == copies[0].parent.name,
      f"name follows the Agent Skills rules and matches its folder ({name})")
check(0 < len(description) <= 1024, f"description is 1 to 1024 characters ({len(description)})")
check(len(fields.get("compatibility", "")) <= 500, "compatibility is at most 500 characters")
check(len(skill.splitlines()) < 500, f"the file is under 500 lines ({len(skill.splitlines())})")
served = {fn.__name__ for fn in mcpserver.TOOLS}
# A tool's name has an underscore in it, and the skill backticks nothing else that has one.
named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", skill))
check(named == served, f"the skill names exactly the tools the server serves "
                       f"(missing {sorted(served - named)}, unknown {sorted(named - served)})")
check("wait" in skill.lower() and "get around" in skill.lower(),
      "and tells the agent that a refusal from YouTube means wait")

print("\n=== C. the setup lines name commands that exist ===")
docs = (DIR / "docs" / "agents.md").read_text(encoding="utf-8")
for line in ("claude mcp add worthwatch -- worthwatch mcp",
             "codex mcp add worthwatch -- worthwatch mcp",
             "claude plugin marketplace add", "claude plugin install worthwatch@worthwatch",
             "claude_desktop_config.json", ".cursor/mcp.json", "$HOME/.agents/skills"):
    check(line in docs, f"docs/agents.md gives: {line}")
known = {"init", "doctor", "mcp", "status", "pause", "resume", "like", "nah", "remix",
         "cooldown", "requests", "paths", "audio", "resummarize"}
for text, where in ((docs, "docs/agents.md"), (skill, "SKILL.md")):
    used = set(re.findall(r"`worthwatch ([a-z]+)", text))
    check(used <= known, f"{where}: every `worthwatch <command>` is one main() knows "
                         f"({sorted(used - known) or 'all known'})")
check("not tested" in docs.lower(), "and the lines that could not be tried here say so")

print("\n=== D. claude plugin validate ===")
claude = shutil.which("claude")
if not claude:
    not_run.append("D: `claude plugin validate` (claude is not installed here)")
    print("  NOT RUN: " + not_run[-1])
else:
    for target in (DIR, plugin_dir):
        proc = subprocess.run([claude, "plugin", "validate", str(target)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
        check(proc.returncode == 0 and "Validation passed" in out and "warning" not in out.lower(),
              f"claude plugin validate {target.relative_to(DIR).as_posix() or '.'}: "
              f"{out.strip().splitlines()[-1] if out.strip() else proc.returncode}")

tail = f" ({len(not_run)} check not run: {'; '.join(not_run)})" if not_run else ""
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)) + tail)
sys.exit(1 if fails else 0)
