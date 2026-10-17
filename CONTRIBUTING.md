# Contributing to worthwatch

Thank you for helping. A few rules keep the project honest and kind to YouTube.

## Set up

```
git clone https://github.com/mokshtheverma-max/worthwatch
cd worthwatch
uv venv
uv pip install -e ".[dev]"
```

Run every test, one file at a time:

```
python tests/run_all.py
```

Each test file is a plain script: it prints a PASS or FAIL line for every check and exits 1 if
any check failed. `python tests/run_all.py doctor` runs only the files whose name has "doctor"
in it.

CI also runs every test with Python's clock a year and five years ahead
(`python tests/run_all.py --clock +365`), so a test must never give a fixture a fixed date that
the code compares with today: count it back from today instead. When a test file fails on
GitHub, `run_all.py` also prints it as an `::error` line, so the run's own page lists the file
and the last lines of its output under Annotations, where anyone can read them without signing
in (a job's log needs a sign-in).

## Where the code lives

Most of the tool is still one file, `src/worthwatch/core.py`. The checks every write-up must
pass (the quote check, the verdict gates) are in `src/worthwatch/summaryrules.py`, and the
settings and folders, the brains, `worthwatch init`, the MCP server, the scoring and Jev each
have a module of their own.

Moving more code out of `core.py` is welcome, with one rule: a name may move only if no test
swaps it on core and core never rebinds it while it runs (your settings and folders are put
into core's names when it starts). A moved function would see neither change.
`tests/test_modules.py` checks every module split out so far; add yours to its list.

## The rules

- **Test first.** Write the check that fails, see it fail, then write the fix. Never weaken,
  skip or delete a check to make it pass.
- **Prove a new check can fail.** Break the code on purpose in a copy, watch the check go red,
  and say in your pull request how you did it.
- **Tests never touch the network.** `tests/_tmpsandbox.py` refuses every connection that
  leaves the machine, and every suite writes only inside its own temporary folder. Stub the
  model and YouTube.
- **Invented data only.** Fixtures use the invented example person in
  `src/worthwatch/examples`, invented videos and invented transcripts. Never commit a real
  transcript, a real digest, or your own context file.
- **Polite to YouTube.** No change may disguise a request, use a browser's cookies, choose a
  YouTube client, or answer a refusal by trying another way. `tests/test_polite_client.py`
  checks this.
- **No secrets.** Keys come from environment variables. Never put one in a file, a test, a log
  or an error message.
- **Plain words, no em-dashes.** `scripts/check_emdash.py` runs in CI and, if you turn the hook
  on with `git config core.hooksPath .githooks`, before every commit.

## Pull requests

Say what changed and why, list every test file you changed and why, and paste the last line of
`python tests/run_all.py`.
