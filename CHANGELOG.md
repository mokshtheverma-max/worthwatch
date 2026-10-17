# Changelog

## 0.1.0 (not released yet)

The first public version.

- Checks the YouTube channels you follow and judges each new video against your own context
  file: YES, OPPORTUNITY, MAYBE or NO, with a write-up and a daily digest.
- Your own rules (standing decisions, things already done, generic advice, ideas you killed),
  checked in code after every write-up.
- No made-up quotes: a quote in a write-up has to match one stretch of the transcript, in
  order, word for word or with a few words different (none under 8 words, then one more for
  every 8). Words stitched together from separate places are cut.
- Captions first; the audio path (download and transcribe) is off unless you switch it on, and
  needs the `whisper` extra.
- Polite to YouTube: limits on by default, and a refusal means wait.
- Any brain: your own AI agent through MCP, `claude -p`, `codex exec`, an OpenAI-compatible API
  (Gemini, OpenRouter, OpenAI, Groq or any other), or Ollama.
- `worthwatch init`, `worthwatch doctor` in plain words, and folders that follow each operating
  system's own conventions.
- `worthwatch mcp`, an MCP server with eight tools, a Claude Code plugin marketplace, and an
  Agent Skill.
- The idea board.
- Jev, an optional paid screen for business ideas, off by default, with a daily spending cap.
