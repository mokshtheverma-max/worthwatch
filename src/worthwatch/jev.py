"""Jev, a small decision model on OpenRouter: an optional client with its spending held in code.

Off unless you switch it on ([jev] enabled in settings.toml), because every call is paid. The
API, as OpenRouter documents it: POST https://openrouter.ai/api/v1/systemone with {model, state,
questions}; a question is "noul" (criteria "true" and "false"; the answer is a probability),
"choice" (criteria per option; the answer is the choice, a confidence and every option's
probability) or "score"; the response carries usage.cost in dollars. The context is 32,000
tokens; input is billed (about $0.042 a million tokens when this was written) and output is
free.

Three rules this file enforces, each in code:

  The key comes from the OPENROUTER_API_KEY environment variable, into memory, and is never
  printed, logged, written to a file or put in an error message.

  Spending is capped before a call is made: every call is priced from its size first, and a
  call that could take the ledger past its cap is refused without being sent. After the call
  the ledger is charged what OpenRouter says it cost, or the estimate when it says nothing.
  The tool's own calls go through a ledger capped per day (core.JEV_DAILY_CAP_USD).

  Only public video text goes in: the state may hold a title, a channel, a description, a
  transcript excerpt and the short list of project names and rules the caller passes; anything
  else is refused before the request is built. Never your context file.
"""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://openrouter.ai/api/v1/systemone"
MODEL = "typesafe/jev-1.13"
PRICE_PER_INPUT_TOKEN = 0.042 / 1_000_000     # dollars; output is free
ESTIMATE_SAFETY = 2.0                          # a call is priced at twice its estimated size
CHARS_PER_TOKEN = 3.0                          # cautious: fewer characters a token prices higher
STATE_TOKEN_LIMIT = 32_000
TRANSCRIPT_EXCERPT_CHARS = 60_000              # about 15,000 tokens, well inside the limit
PUBLIC_STATE_KEYS = frozenset({"title", "channel", "description", "transcript_excerpt",
                               "projects", "rules"})
KEY_ENV = "OPENROUTER_API_KEY"


class JevError(RuntimeError):
    """A call that did not happen or did not answer. Its message never carries the key."""


class SpendCapReached(JevError):
    pass


class KeyUnavailable(JevError):
    pass


_KEY = {"value": None, "failed": None}


def read_key(env=None):
    """The OpenRouter key, read once into memory from the OPENROUTER_API_KEY environment
    variable. Raises KeyUnavailable, whose message never holds what was read. A missing key is
    not looked for again in the same process until forget_key()."""
    if _KEY["value"]:
        return _KEY["value"]
    if env is None and _KEY["failed"]:
        raise KeyUnavailable(_KEY["failed"])
    key = ((os.environ if env is None else env).get(KEY_ENV) or "").strip()
    if key and not any(c.isspace() for c in key):
        _KEY["value"] = key
        return key
    why = (f"the {KEY_ENV} environment variable is not set" if not key
           else f"the {KEY_ENV} environment variable is not one word")
    if env is None:
        _KEY["failed"] = why
    raise KeyUnavailable(why)


def forget_key():
    _KEY["value"] = None
    _KEY["failed"] = None


class Ledger:
    """Dollars spent against a cap, kept in a small JSON file so a cap holds across processes.
    `per_day` caps each calendar day separately (the tool's daily cap); otherwise the cap is for
    the whole life of the file (a run's cap)."""

    def __init__(self, path, cap_usd, per_day=False):
        self.path = Path(path)
        self.cap = float(cap_usd)
        self.per_day = per_day

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"spent": {}, "calls": 0}

    def _key(self):
        return dt.date.today().isoformat() if self.per_day else "run"

    def spent(self):
        return float(self._load()["spent"].get(self._key(), 0.0))

    def room_for(self, estimate):
        return self.spent() + estimate <= self.cap + 1e-12

    def charge(self, dollars):
        data = self._load()
        key = self._key()
        data["spent"][key] = round(float(data["spent"].get(key, 0.0)) + float(dollars), 9)
        data["calls"] = int(data.get("calls", 0)) + 1
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        return data["spent"][key]


def estimate_cost(body_text):
    """The most a request of this size should cost, with the safety factor applied."""
    return len(body_text) / CHARS_PER_TOKEN * PRICE_PER_INPUT_TOKEN * ESTIMATE_SAFETY


def public_state(title, channel, description="", transcript="", projects=(), rules=()):
    """The only state this client sends: public video text, cut to fit, and the caller's short
    list of project names and rules."""
    body = (transcript or "").strip()
    if len(body) > TRANSCRIPT_EXCERPT_CHARS:
        head = int(TRANSCRIPT_EXCERPT_CHARS * 2 / 3)
        body = (body[:head] + "\n[... middle of the transcript cut for length ...]\n"
                + body[-(TRANSCRIPT_EXCERPT_CHARS - head):])
    state = {"title": title or "", "channel": channel or "", "description": (description or "")[:2000],
             "transcript_excerpt": body}
    if projects:
        state["projects"] = list(projects)
    if rules:
        state["rules"] = list(rules)
    return state


def ask(state, questions, ledger, model=MODEL, timeout=90, opener=None, key=None):
    """One System One request. Returns {"answers", "usage", "cost", "seconds", "model"}.

    Refused before anything is sent when the state holds anything but public video text, when
    it would not fit the context, or when the ledger has no room for its estimated cost."""
    extra = set(state) - PUBLIC_STATE_KEYS
    if extra:
        raise JevError(f"refused: the state may hold only public video text, not {sorted(extra)}")
    body = json.dumps({"model": model, "state": state, "questions": questions},
                      ensure_ascii=False)
    if len(body) / CHARS_PER_TOKEN > STATE_TOKEN_LIMIT:
        raise JevError(f"refused: about {len(body) / CHARS_PER_TOKEN:,.0f} tokens, over the "
                       f"{STATE_TOKEN_LIMIT:,} the model takes")
    estimate = estimate_cost(body)
    if not ledger.room_for(estimate):
        raise SpendCapReached(f"refused: ${ledger.spent():.4f} spent of the ${ledger.cap:.2f} cap, "
                              f"and this call could cost up to ${estimate:.5f}")
    token = key or read_key()
    request = urllib.request.Request(ENDPOINT, data=body.encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
    t0 = time.monotonic()
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        ledger.charge(estimate)      # a refused call may still be billed; count the worst case
        detail = e.read().decode("utf-8", errors="replace")[:200].replace(token, "<key>")
        raise JevError(f"OpenRouter answered HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        ledger.charge(estimate)
        raise JevError(f"no answer from OpenRouter ({e.__class__.__name__})") from None
    seconds = time.monotonic() - t0
    usage = data.get("usage") or {}
    cost = usage.get("cost")
    ledger.charge(float(cost) if isinstance(cost, (int, float)) else estimate)
    return {"answers": data.get("answers") or {}, "usage": usage,
            "cost": float(cost) if isinstance(cost, (int, float)) else None,
            "seconds": round(seconds, 2), "model": data.get("model") or model}
