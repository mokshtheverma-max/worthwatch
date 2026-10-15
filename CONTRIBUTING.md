# Contributing to worthwatch

Thank you for helping. A few rules keep the project honest and kind to YouTube.

## Set up

```
git clone https://github.com/OWNER/worthwatch
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
