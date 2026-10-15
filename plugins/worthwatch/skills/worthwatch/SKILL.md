---
name: worthwatch
description: Check the YouTube channels the user follows for new videos and judge each one against their own projects with worthwatch, then save the verdict and show the day's digest or the idea board. Use when the user asks what is new on their channels, whether a YouTube video is worth watching, for today's digest, or about their idea board.
license: Apache-2.0
compatibility: Needs worthwatch installed (uv tool install worthwatch) and its MCP server connected (the command worthwatch mcp).
---

# worthwatch

worthwatch reads YouTube for one person and judges each video against their own context file:
their projects, what they have tried, what they have decided. You do the judging; worthwatch
fetches politely, hands you everything you need, and checks your verdict against the user's own
rules in code before it is saved.

## The tools

- `check_channels`: lists new videos from the channels in the user's channels.txt, priority
  channels first. It fetches no transcript. Each channel is one polite YouTube request, so it
  lists a few per call; call it again to go on.
- `get_transcript`: the transcript of one video, from this machine or from YouTube's captions.
- `judge_video`: everything you need to judge one video: step 1 (the rules that decide the
  verdict, with the user's context), step 2 (the write-up's exact format, with the transcript),
  step 3 (how to save it).
- `save_verdict`: stores your verdict (YES, OPPORTUNITY, MAYBE or NO) and your write-up. The
  verdict it returns is the one that counts: the user's rules may change yours, and it says why.
- `todays_digest`: the day's digest, every video read that day and its verdict.
- `list_ideas`, `add_idea`, `kill_idea`: the idea board.

## How to judge new videos

1. Call `check_channels`. Pick the videos that are not already read.
2. For each one, call `judge_video` with its link or its 11-character id.
3. Do step 1 for yourself: decide YES, OPPORTUNITY, MAYBE or NO by the rules it gives. MAYBE is
   the honest answer when the transcript is too thin to tell.
4. Write the write-up exactly in step 2's format, sections 1 to 9. Quote only words that are in
   the transcript. Section 8 is one sentence the user could do this week, or "nothing".
5. Call `save_verdict` with the video, your verdict and the write-up. Tell the user the verdict
   it returns, and the reason when it differs from yours.
6. When you are done, call `todays_digest` and show the user the short version.

## Rules

- A video is a YouTube video link or an 11-character id. Never pass anything else.
- YouTube is asked politely. If a tool says YouTube refused or to wait, stop and tell the user
  when to try again. Never try to get around a refusal.
- Judge one video at a time, from its own transcript. Never invent a quote, a number or a name.
- The context file is the user's own. Use it to judge; do not copy it anywhere else.

## Without the MCP server

The same tool runs from the command line:

- `worthwatch doctor` checks everything a run needs and says how to fix what is missing.
- `worthwatch init` makes the folders and the starter files, and chooses a brain.
- `worthwatch status` says what the tool is doing and waiting for.
- `worthwatch mcp` starts the MCP server these tools come from.
