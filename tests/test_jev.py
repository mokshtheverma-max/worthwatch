"""The optional Jev client holds its spending and its key in code.

A hard cap for a run and a daily cap inside the tool, both enforced before a call is sent; only
public video text in a request; the key read from the OPENROUTER_API_KEY environment variable
only, and never printed, logged, written or put in an error. Every check is made on a stub
opener: no request leaves this machine, and the suite's network guard refuses it besides. The
real environment's key, if there is one, is removed from this process before anything runs, so
the test never reads it. Each rule has a control that shows it can fail. Writes only in its temp
folder.

Watch it go red: make Ledger.room_for return True in a copy of src/worthwatch/jev.py and run this
file against the copy.
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
SANDBOX = _tmpsandbox.sandbox(ww, "jev")
from worthwatch import jev

os.environ.pop(jev.KEY_ENV, None)      # in this process only: the real key is never read here
jev.forget_key()
TMP = Path(tempfile.mkdtemp(prefix="ww-jev-"))
fails = []
KEY = "sk-or-v1-" + "TESTKEY" * 6


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


SENT = []


def opener_with(cost=0.0000412, answers=None):
    def opener(request, timeout=None):
        SENT.append(request)
        return Response({"model": "typesafe/jev-1.13-20260917", "id": "gen-dec-test",
                         "provider": "TypeSafe", "answers": answers or {
                             "opportunity": {"type": "noul", "noul": 0.91}},
                         "usage": {"input_tokens": 980, "output_tokens": 10, "cost": cost}})
    return opener


QUESTIONS = {"opportunity": {"type": "noul", "instructions": "Is there a paid business in it?",
                             "criteria": {"true": "A buyer pays.", "false": "Nobody pays."}}}
STATE = jev.public_state("How we sold schedules to bakery chains", "C", "",
                         "chains pay per shop " * 40)

print("=== A. a call goes through, and the ledger is charged what it cost ===")
ledger = jev.Ledger(TMP / "run.json", 1.00)
out = jev.ask(STATE, QUESTIONS, ledger, opener=opener_with(), key=KEY)
check(abs(out["answers"]["opportunity"]["noul"] - 0.91) < 1e-9, "the answer is read")
check(abs(ledger.spent() - 0.0000412) < 1e-12, f"the ledger holds OpenRouter's own cost ({ledger.spent()})")
sent = json.loads(SENT[-1].data.decode("utf-8"))
check(sent["model"] == "typesafe/jev-1.13" and set(sent) == {"model", "state", "questions"},
      "the body is model, state and questions, nothing else")
check(SENT[-1].full_url == "https://openrouter.ai/api/v1/systemone", "to the System One endpoint")
check(SENT[-1].get_header("Authorization") == f"Bearer {KEY}", "with the key as a bearer token")

print("\n=== B. the run's cap: a call that could cross it is refused before it is sent ===")
small = jev.Ledger(TMP / "small.json", 0.0001)
small.charge(0.000099)
n = len(SENT)
try:
    jev.ask(STATE, QUESTIONS, small, opener=opener_with(), key=KEY)
    refused = False
except jev.SpendCapReached as e:
    refused = "cap" in str(e)
check(refused, "a ledger with no room refuses the call")
check(len(SENT) == n, "and nothing was sent")
roomy = jev.Ledger(TMP / "roomy.json", 1.00)
jev.ask(STATE, QUESTIONS, roomy, opener=opener_with(), key=KEY)
check(len(SENT) == n + 1, "CONTROL: the same call with room is sent")
check(abs(jev.Ledger(TMP / "run.json", 1.00).spent() - ledger.spent()) < 1e-12,
      "the cap holds across processes: a new ledger on the same file reads the same spend")

print("\n=== C. the daily cap: each day its own, and a full day refuses ===")
daily = jev.Ledger(TMP / "daily.json", 0.0002, per_day=True)
daily.charge(0.0002)
try:
    jev.ask(STATE, QUESTIONS, daily, opener=opener_with(), key=KEY)
    ok = False
except jev.SpendCapReached:
    ok = True
check(ok, "a day that has spent its cap refuses the next call")
data = json.loads((TMP / "daily.json").read_text(encoding="utf-8"))
check(list(data["spent"]) == [jev.dt.date.today().isoformat()], "spend is kept by the day")
data["spent"] = {"2026-03-01": 0.0002}
(TMP / "daily.json").write_text(json.dumps(data), encoding="utf-8")
check(jev.Ledger(TMP / "daily.json", 0.0002, per_day=True).room_for(0.0001),
      "CONTROL: an earlier day's spend does not count against today")

print("\n=== D. a failed call is charged its estimate, never nothing ===")


def failing(request, timeout=None):
    raise urllib.error.URLError("refused")


fl = jev.Ledger(TMP / "fail.json", 1.00)
try:
    jev.ask(STATE, QUESTIONS, fl, opener=failing, key=KEY)
except jev.JevError:
    pass
check(fl.spent() > 0, f"a call that did not answer still counts its estimate ({fl.spent():.8f})")

print("\n=== E. only public video text goes in ===")
for bad in ({"context": "the reader's own context file"}, {"reader": "x"}, {"notes": "private"}):
    try:
        jev.ask(dict(STATE, **bad), QUESTIONS, roomy, opener=opener_with(), key=KEY)
        refused = False
    except jev.JevError as e:
        refused = "only public video text" in str(e)
    check(refused, f"a state carrying {sorted(bad)} is refused")
check(set(jev.public_state("t", "c", "d", "x", ("Ledgerleaf",), ("never consumers",)))
      <= jev.PUBLIC_STATE_KEYS, "CONTROL: what public_state builds is all public")
huge = jev.public_state("t", "c", "", "word " * 100_000)
check(len(huge["transcript_excerpt"]) <= jev.TRANSCRIPT_EXCERPT_CHARS + 80,
      "a long transcript is cut to fit the 32,000-token state")

print("\n=== F. the key comes from the environment only, and is never in an error or a file ===")


def http_error(request, timeout=None):
    raise urllib.error.HTTPError(request.full_url, 401, "no", {},
                                 io.BytesIO(f"bad key {KEY}".encode("utf-8")))


try:
    jev.ask(STATE, QUESTIONS, jev.Ledger(TMP / "k.json", 1.00), opener=http_error, key=KEY)
    msg = ""
except jev.JevError as e:
    msg = str(e)
check(msg and KEY not in msg and "<key>" in msg, f"an error that echoed the key has it masked ({msg[:80]})")
files = [p for p in TMP.rglob("*") if p.is_file()]
check(files and all(KEY not in p.read_text(encoding="utf-8", errors="replace") for p in files),
      f"no file this client wrote holds the key ({len(files)} files)")
try:
    jev.read_key(env={})
    got = None
except jev.KeyUnavailable as e:
    got = str(e)
check(got is not None and jev.KEY_ENV in got and "not set" in got,
      f"no key in the environment says so ({got})")
try:
    jev.read_key(env={jev.KEY_ENV: "two secret words"})
    got = None
except jev.KeyUnavailable as e:
    got = str(e)
check(got is not None and "secret" not in got,
      f"a malformed key is refused without its content in the message ({got})")
check(jev.read_key(env={jev.KEY_ENV: KEY}) == KEY, "CONTROL: a key in the environment is read")
jev.forget_key()
# A missing key is looked for once a process, not once a video.
outcomes = []
for _ in range(3):
    try:
        jev.read_key()
        outcomes.append("a key")
    except jev.KeyUnavailable as e:
        outcomes.append(str(e))
os.environ[jev.KEY_ENV] = KEY
try:
    jev.read_key()
    still_refused = False
except jev.KeyUnavailable:
    still_refused = True
jev.forget_key()
read_after_forget = jev.read_key()
jev.forget_key()
os.environ.pop(jev.KEY_ENV, None)
check(len(outcomes) == 3 and all("not set" in o for o in outcomes) and still_refused,
      "a key that was missing is not looked for again in the same run")
check(read_after_forget == KEY, "CONTROL: forget_key() lets the next read look again")
src = Path(jev.__file__).read_text(encoding="utf-8")
check("print(" not in src, "the client prints nothing at all")
code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
check("subprocess" not in code and "wsl" not in code.lower() and ".openrouter_key" not in code,
      "and reads the key from the environment, never from a file or another program")

print("\n=== G. the tool's own daily cap ===")
check(Path(ww.JEV_SPEND_PATH).resolve().is_relative_to(Path(SANDBOX).resolve()),
      "the tool's ledger is inside this suite's sandbox, like every data file")
tool = ww.jev_ledger()
check(tool.per_day and tool.cap == ww.JEV_DAILY_CAP_USD and ww.JEV_DAILY_CAP_USD <= 1.00,
      f"one cap a day, ${ww.JEV_DAILY_CAP_USD:.2f}, no more than $1.00")
tool.charge(ww.JEV_DAILY_CAP_USD)
n = len(SENT)
try:
    jev.ask(STATE, QUESTIONS, ww.jev_ledger(), opener=opener_with(), key=KEY)
    refused = False
except jev.SpendCapReached:
    refused = True
check(refused and len(SENT) == n, "a day that has spent the tool's cap sends nothing more")
core_src = Path(ww.__file__).read_text(encoding="utf-8")
screen = core_src[core_src.index("def screen_opportunity_jev("):
                  core_src.index("def screen_opportunity_local(")]
check(core_src.count("jev.ask(") == 1 and "jev.ask(" in screen and "jev_ledger()" in screen,
      "the screen is the one place a run asks Jev anything, through the tool's daily ledger")
check(ww.OPPORTUNITY_SCREEN_BACKEND == "local", "and Jev is off unless switched on")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
