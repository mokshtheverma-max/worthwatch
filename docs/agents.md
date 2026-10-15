# Use worthwatch from your AI agent

worthwatch has an MCP server, `worthwatch mcp`, that gives an AI agent eight tools: check your
channels for new videos, read a transcript, judge a video against your context file, save the
verdict after your own rules have checked it, show today's digest, and list, add or kill ideas on
the idea board. In agent mode (`worthwatch init --brain agent`) worthwatch calls no model at all:
your agent does the judging.

First install worthwatch and run `worthwatch init` (see the README). Every setup below starts the
same command, `worthwatch mcp`, over stdio.

## Claude Code

The plugin adds the MCP server and a skill that teaches Claude how to use it:

```
claude plugin marketplace add mokshtheverma-max/worthwatch
claude plugin install worthwatch@worthwatch
```

Inside a session, on Claude Code 2.1.275 or later, one line does both:

```
/plugin install worthwatch --marketplace mokshtheverma-max/worthwatch
```

Or add only the MCP server (add `--scope user` to have it in every project):

```
claude mcp add worthwatch -- worthwatch mcp
```

Tested: `claude plugin validate` passes on the marketplace and on the plugin, with `--strict`
too (Claude Code 2.1.300). Not tested: adding the marketplace and installing the plugin (they
change your Claude Code settings, so they were not run while building this), and `claude mcp add`
(checked against `claude mcp add --help` only).

## Claude Desktop

In Claude Desktop, Settings, then Developer, then Edit Config opens `claude_desktop_config.json`
(on macOS in `~/Library/Application Support/Claude/`, on Windows in `%APPDATA%\Claude\`). Add:

```json
{
  "mcpServers": {
    "worthwatch": {
      "command": "worthwatch",
      "args": ["mcp"]
    }
  }
}
```

Then quit Claude Desktop completely and start it again. If it cannot find `worthwatch`, give the
full path instead: `uv tool dir --bin` prints the folder it is in.

Not tested: Claude Desktop was not run while building this. The steps follow the MCP
documentation as read on 14 October 2026.

## Codex CLI

```
codex mcp add worthwatch -- worthwatch mcp
```

Codex keeps it in `~/.codex/config.toml`. For the skill, copy the folder `.agents/skills/worthwatch`
from this repository into `$HOME/.agents/skills/`, where Codex reads skills for every project.
Inside this repository Codex finds it on its own, in `.agents/skills`.

Not tested: Codex was not installed on the computer this was built on. The lines follow OpenAI's
Codex documentation as read on 14 October 2026.

## Cursor

Put this in `~/.cursor/mcp.json` (every project) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "worthwatch": {
      "command": "worthwatch",
      "args": ["mcp"]
    }
  }
}
```

Not tested: Cursor was not run while building this. The file follows Cursor's documentation as
read on 14 October 2026.

## Other agents

Any client that starts stdio MCP servers can use it: the command is `worthwatch` and its argument
is `mcp`. The skill in `.agents/skills/worthwatch/SKILL.md` follows the open Agent Skills format.

ChatGPT is left out on purpose: its app connects only to hosted servers, and worthwatch runs on
your own computer.
