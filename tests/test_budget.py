"""The adaptive daily cap in rate-state.json: a clean day raises it a little, any 429 halves
it at once, both bounded, every change written with its reason. No network."""
import io, sys, json, datetime as dt
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "budget")
ww.RATE_STATE_PATH.unlink(missing_ok=True)

fails = []
def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond: fails.append(msg)

REAL_TODAY = dt.date.today()
DAY = [REAL_TODAY]
ww.today_key = lambda: DAY[0].isoformat()
def advance(days=1):
    DAY[0] = DAY[0] + dt.timedelta(days=days)
def state():
    return ww.load_rate_state()

print("=== A. a missing rate-state.json means the starting cap, and says so ===")
cap = ww.daily_caption_cap()
check(cap == ww.CAPTION_CAP_START, f"first cap is CAPTION_CAP_START={ww.CAPTION_CAP_START} (got {cap})")
check(state().get("cap_day") == DAY[0].isoformat(), "cap_day is today")
check("first day" in state().get("cap_reason", ""), f"reason written: {state().get('cap_reason')!r}")
check(len(state().get("cap_history", [])) == 1, "cap_history has the one entry")
check(ww.daily_caption_cap() == cap and len(state().get("cap_history", [])) == 1,
      "asking again the same day changes nothing")

print("\n=== B. a clean day with fetches raises tomorrow's cap by CAPTION_CAP_STEP ===")
for _ in range(3):
    ww.note_caption_fetch()
advance()
cap2 = ww.daily_caption_cap()
check(cap2 == cap + ww.CAPTION_CAP_STEP, f"{cap} -> {cap2}")
check("clean day" in state().get("cap_reason", "") and "3 caption" in state().get("cap_reason", ""),
      f"reason names the clean day and its count: {state().get('cap_reason')!r}")

print("\n=== C. a day that fetched nothing proves nothing: cap kept ===")
advance()
cap3 = ww.daily_caption_cap()
check(cap3 == cap2, f"cap stays {cap2} after a silent day")
check("proved nothing" in state().get("cap_reason", ""), f"and says why: {state().get('cap_reason')!r}")

print("\n=== D. a 429 halves the cap at once, with the strike in the reason ===")
DAY[0] = REAL_TODAY + dt.timedelta(days=2)
# arm_cooldown stamps strikes_by_day with the real clock's date, so judge that date next
real_today_key = dt.date.today().isoformat()
ww.note_caption_fetch()
before = ww.daily_caption_cap()
until, wait, strikes = ww.arm_cooldown("HTTP Error 429: Too Many Requests")
after = int(state()["daily_cap"])
check(after == max(ww.CAPTION_CAP_MIN, before // 2), f"{before} -> {after}")
check("429" in state().get("cap_reason", "") and f"strike {strikes}" in state().get("cap_reason", ""),
      f"reason: {state().get('cap_reason')!r}")
check(state().get("strikes_by_day", {}).get(real_today_key) == 1, "the day's strike count is recorded")
check(ww.caption_budget_left_today() <= after, "the rest of today's budget shrinks to the new cap")
check(ww.new_budget()["remaining"] <= min(ww.MAX_CAPTION_FETCHES_PER_RUN, after),
      "new_budget() respects the halved cap")

print("\n=== E. the day after a 429 is not a clean day: the halved cap carries over ===")
# make the judged day the one that took the strike
st = state(); st["cap_day"] = real_today_key; st["caption_day"] = real_today_key; st["caption_count"] = 4
ww.save_rate_state(st)
DAY[0] = dt.date.today() + dt.timedelta(days=1)
cap5 = ww.daily_caption_cap()
check(cap5 == after, f"cap stays {after} the day after a refusal (no raise)")
check("refusal" in state().get("cap_reason", ""), f"reason: {state().get('cap_reason')!r}")

print("\n=== F. bounds: never below CAPTION_CAP_MIN, never above CAPTION_CAP_MAX ===")
for _ in range(8):
    ww.arm_cooldown("429 again")
check(int(state()["daily_cap"]) == ww.CAPTION_CAP_MIN, f"floor holds at {ww.CAPTION_CAP_MIN}")
st = state(); st["strikes_by_day"] = {}; st["last_429"] = None; ww.save_rate_state(st)
for i in range(40):
    ww.note_caption_fetch()
    advance()
    ww.daily_caption_cap()
check(int(state()["daily_cap"]) == ww.CAPTION_CAP_MAX, f"ceiling holds at {ww.CAPTION_CAP_MAX}")
check(len(state()["cap_history"]) <= 20, "cap_history is kept to its last 20 entries")

print("\n=== G. the reason is always a sentence a human can read tomorrow ===")
for h in state()["cap_history"]:
    if not (isinstance(h.get("reason"), str) and h["reason"] and "at" in h and "to" in h):
        check(False, f"malformed history entry: {h}")
        break
else:
    check(True, "every history entry has a timestamp, a from/to and a reason")

ww.RATE_STATE_PATH.unlink(missing_ok=True)
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
