# Security

## Reporting a problem

Please report a security problem privately: on this repository's GitHub page, open the Security
tab and choose "Report a vulnerability". Do not open a public issue for it, and never paste an
API key, your context file or a transcript into a report.

You can expect a first answer within a week.

## What is in scope

- The MCP server (`worthwatch mcp`): a tool argument that reaches a command line, a file path
  outside worthwatch's folders, or YouTube in any way other than a video id inside a link
  worthwatch builds itself.
- Keys: anything that prints, logs, writes or sends an API key anywhere other than to the service
  it belongs to.
- Files: anything that writes outside worthwatch's config, data and output folders.

## What worthwatch does to stay safe

- Every video argument must be a YouTube video link or an 11-character id; anything else is
  refused before a request is made.
- No tool runs a shell command, and model command lines (claude, codex) get the prompt on standard
  input, never on the command line.
- Keys are read from environment variables only and are masked in error messages.
- The tests refuse every network connection and check these rules.
