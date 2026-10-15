# worthwatch

worthwatch checks the YouTube channels you follow and tells you which new videos are worth your time.

It judges every video against a short file you write about your own projects, so the answer is about you.

## What a day looks like

Here is part of one day's digest for Mara Quinn, who sells bookkeeping software to bakeries and
writes a newsletter for indie game makers. She and these videos are invented, but the digest is
worthwatch's own output ([the whole file](examples/sample-digest.md)):

```
# Digest 2026-03-02

Context file last updated 1 day ago.

- **YES** Ledgerleaf: Email the three bakeries that churned last month and ask which report they missed. [We Got 40 Paying Bakeries Without Ads](https://www.youtube.com/watch?v=SampleVid01)
- **YES** Tidepool: Rewrite the Tidepool welcome email so it links the three most-read issues. [The Welcome Email Every Newsletter Gets Wrong](https://www.youtube.com/watch?v=SampleVid02)
2 kept, 2 read and skipped, 0 read but not written, 1 not read, 0 waiting.

| Verdict | Channel | Title | Reason |
|---|---|---|---|
| YES | Small Software Show | We Got 40 Paying Bakeries Without Ads |  |
| YES | Inbox Craft | The Welcome Email Every Newsletter Gets Wrong |  |
| MAYBE | Hype Daily | Growth Hacks for 2026 (Number 7 Will Shock You) |  |
| MAYBE | Ad Lab | Boost Your Posts: The Paid Ads Playbook | [demoted from YES: it contradicts a standing decision (no paid ads)] |
```

The fourth video was judged YES, then turned into a MAYBE by one of Mara's own rules: she has
decided never to pay for ads. worthwatch checks every write-up against rules like that in code,
after the model has written it.

## Why it exists

<!-- AUTHOR: write two or three sentences here, in your own words, about why you built
worthwatch. This spot is left empty on purpose; nothing has been written for you. Delete this
comment and the line under it when you are done. -->

_Left empty for the author's own words._

## What makes it different

- **It judges for you.** Every verdict is about your projects, read from your own context file,
  not a generic summary.
- **Your rules are code.** Decisions you have made ("no paid ads", "never a free plan") are checked
  after the model writes. A YES that breaks one becomes a MAYBE, and the digest says why.
- **No made-up quotes.** A quote in a write-up has to be in the transcript, or it is cut.
- **Polite to YouTube.** Captions first, small daily limits and a gap between requests. When
  YouTube says stop, worthwatch stops and waits. It never pretends to be a browser, never uses
  your cookies, and never tries another way in.
- **Any brain, free ones first.** Your own AI agent, a local model, Gemini's free API tier, or
  the AI tools you already use.
- **An idea board.** Every business idea a video contains, checked and kept in one place.
- **Made for AI agents.** An MCP server and a skill, so Claude Code, Codex, Cursor and others can
  use it.
- **No GPU needed.**

## Quickstart

1. Install uv, if you do not have it. On macOS or Linux:

   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   On Windows, in PowerShell:

   ```
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. Install worthwatch:

   ```
   uv tool install worthwatch
   ```

   Until worthwatch is on PyPI, install it from GitHub instead (OWNER is the account that holds
   this repository):

   ```
   uv tool install git+https://github.com/OWNER/worthwatch
   ```

   If uv warns that its tool folder is not on your PATH, run this once, then open a new terminal
   so that the `worthwatch` command is found:

   ```
   uv tool update-shell
   ```

3. Make your folders and starter files, and choose the brain that judges the videos:

   ```
   worthwatch init
   ```

4. Check that everything is ready. It says in plain words what is missing and how to fix it:

   ```
   worthwatch doctor
   ```

5. Judge one video:

   ```
   worthwatch https://www.youtube.com/watch?v=VIDEO_ID
   ```

   This needs a brain worthwatch can call (claude, codex, api or ollama). In agent mode, ask your
   agent instead (see below).

6. Make it yours: open `context.md` in your config folder and replace the example person with
   yourself, put the channels you follow in `channels.txt`, one link a line, and run
   `worthwatch --channels` once a day.

Your files live in three folders, which `worthwatch init` prints:

| | config (your settings) | data (records and transcripts) | output (digests you read) |
|---|---|---|---|
| Windows | `%APPDATA%\worthwatch` | `%LOCALAPPDATA%\worthwatch` | `Documents\worthwatch` |
| macOS | `~/Library/Application Support/worthwatch` | the same folder, `data` | `~/Documents/worthwatch` |
| Linux | `~/.config/worthwatch` | `~/.local/share/worthwatch` | `~/Documents/worthwatch` |

Set `WORTHWATCH_HOME` to keep all three in one folder instead.

## Pick your brain

`worthwatch init` asks; `worthwatch init --brain NAME` changes it later. Free ones first:

| Brain | What it costs you | Setup |
|---|---|---|
| `agent` | Nothing extra: worthwatch calls no model, and your own AI agent does the judging | Connect your agent (next section) |
| `ollama` | Nothing: the model runs on your computer | Install [Ollama](https://ollama.com), then `ollama pull qwen3.5:9b` |
| `api` with Gemini | Gemini's API has a free tier: Google's pricing page says "Start building free of charge with generous limits" ([ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)) | Put your key in `GEMINI_API_KEY` |
| `claude` | Uses the Claude Code plan you already have (`claude -p`) | Install Claude Code |
| `codex` | Uses the Codex plan you already have (`codex exec`) | Install the Codex CLI |
| `api` with OpenRouter, OpenAI or Groq | That service's own prices | Put the key in `OPENROUTER_API_KEY`, `OPENAI_API_KEY` or `GROQ_API_KEY` |

You can also give the quick jobs (sorting a listing, a first look at a transcript) to a different
brain from the write-ups, for example a free local model for the quick jobs and claude to write:
set `quick = "ollama"` under `[brain]` in `settings.toml`.

## Use it from your AI agent

`worthwatch mcp` is an MCP server with eight tools: check your channels for new videos, read a
transcript, hand your agent everything it needs to judge a video, save the verdict after your
rules have checked it, show today's digest, and list, add or kill ideas.

In Claude Code, one line installs the plugin (the MCP server and a skill that teaches Claude to
use it), on Claude Code 2.1.275 or later:

```
/plugin install worthwatch --marketplace OWNER/worthwatch
```

Or add only the server:

```
claude mcp add worthwatch -- worthwatch mcp
```

For Claude Desktop, Codex CLI, Cursor and others, see [docs/agents.md](docs/agents.md), which also
says which setups were tested.

## Privacy

- Your context file, channels, transcripts, records and digests stay in your folders, on your
  computer.
- What leaves your computer: requests to YouTube, to read channel listings and captions; and the
  transcript and your context file, to the brain you chose. With Ollama they stay on your
  computer; with claude, codex or an API they go to that company; in agent mode they go to your
  agent.
- Jev, an optional paid screen for business ideas, is off unless you switch it on, and is only
  ever sent public video text, never your context file.
- There are no accounts and no telemetry. API keys come from your environment, and worthwatch
  never writes them to a file.

## Responsible use

- worthwatch reads public videos for your own use. Respect YouTube's Terms of Service and the
  people who made the videos.
- It reads captions. It can also download a video's audio and transcribe it, but downloading may
  break YouTube's terms, so that is off unless you switch it on (`[transcripts] audio = true`, and
  the whisper extra: `uv tool install --reinstall "worthwatch[whisper]"`).
- Its limits are polite and on by default. If YouTube refuses, it waits and says so; there is no
  setting to get around a refusal, on purpose.
- Do not republish transcripts or write-ups as your own work.

## Troubleshooting

- **Something is not working:** run `worthwatch doctor`. Every line that needs fixing says how.
- **"YouTube refused the request for now (HTTP 429)":** your network is being told to slow down.
  Wait; `worthwatch status` shows when the next try is.
- **"Your brain is agent mode":** worthwatch calls no model in agent mode. Ask your agent, or run
  `worthwatch init --brain claude` (or codex, api, ollama).
- **A video has no captions:** it cannot be read without the audio path (see Responsible use).
- **"JavaScript runtime" in the doctor:** reinstall worthwatch, which brings Deno with it:
  `uv tool install --reinstall worthwatch`.
- **Claude Desktop cannot find `worthwatch`:** use the full path; `uv tool dir --bin` prints the
  folder it is in.

## More

- Settings: `settings.toml` and `rules.toml` in your config folder, made by `worthwatch init`,
  explain themselves line by line. The example person's files are in
  [src/worthwatch/examples](src/worthwatch/examples).
- [Contributing](CONTRIBUTING.md), [security](SECURITY.md), [changes](CHANGELOG.md).
- License: [Apache-2.0](LICENSE).
