"""Simulate the rotation state machine over several days without touching YouTube."""
import io, sys, json, datetime as dt
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "rotation")
if ww.ROTATION_PATH.exists():
    ww.ROTATION_PATH.unlink()

CH = [f"https://www.youtube.com/@ch{i:02d}" for i in range(1, 12)]  # 11 channels
short = lambda u: u.rsplit("/", 1)[-1]

DAY = ["2026-09-05"]
ww.today_key = lambda: DAY[0]

fails = []
def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)

print("=== A. a clean day: 6 slots, 2 channels each, 11 channels ===")
covered = []
for slot in range(1, 7):
    picked = ww.channels_for_this_slot(CH)
    for c in picked:
        ww.mark_channel_scanned(CH, c)
    covered += picked
    done = ww.day_scan_complete(CH)
    print(f"  slot {slot}: {[short(c) for c in picked]}  complete={done}")
check(len(set(covered)) == 11, "all 11 channels covered exactly once in a day")
check(len(covered) == 11, f"no channel scanned twice (scanned {len(covered)} times)")
check(ww.day_scan_complete(CH), "day_scan_complete() true after the pass")
print(f"  slot 6 picked {len(ww.channels_for_this_slot(CH))} (nothing left) "
      f"-> backlog phase is what runs in the leftover slot capacity")

print("\n=== B. next day: cursor does NOT restart at channel 1 ===")
before = json.loads(ww.ROTATION_PATH.read_text())["index"]
DAY[0] = "2026-09-06"
picked = ww.channels_for_this_slot(CH)
print(f"  cursor carried over = {before}; day 2 slot 1 = {[short(c) for c in picked]}")
check(picked[0] == CH[before % 11], "day 2 resumes at the cursor, not at channel 1")
check(ww.day_scan_complete(CH) is False, "new day resets scanned_today")

print("\n=== C. a 429 mid-slot leaves the unfinished channel next in line ===")
DAY[0] = "2026-09-07"
ww.save_rotation({"day": DAY[0], "index": 0, "scanned_today": []})
picked = ww.channels_for_this_slot(CH)
ww.mark_channel_scanned(CH, picked[0])            # first done, then 429 before the second
print(f"  slot picked {[short(c) for c in picked]}, only {short(picked[0])} finished")
nxt = ww.channels_for_this_slot(CH)
check(nxt[0] == picked[1], f"next slot resumes on {short(picked[1])}, the one that was cut off")

print("\n=== D. channels.txt edited mid-cycle ===")
DAY[0] = "2026-09-08"
ww.save_rotation({"day": DAY[0], "index": 9, "scanned_today": CH[:9]})
SHRUNK = CH[:5]                                    # six channels deleted from the file
picked = ww.channels_for_this_slot(SHRUNK)
print(f"  file shrank 11 -> 5, cursor was 9; picked {[short(c) for c in picked]}")
check(all(c in SHRUNK for c in picked), "never picks a channel no longer in the file")
check(ww.day_scan_complete(SHRUNK), "already-scanned survivors still count as covered")
GROWN = CH + ["https://www.youtube.com/@ch12"]
ww.save_rotation({"day": DAY[0], "index": 9, "scanned_today": CH})
check(not ww.day_scan_complete(GROWN), "adding a channel re-opens the day's pass")
check(ww.channels_for_this_slot(GROWN)[0] == "https://www.youtube.com/@ch12",
      "a newly added channel is picked next")

print("\n=== E. corrupt / missing rotation.json ===")
ww.ROTATION_PATH.write_text("{ this is not json", encoding="utf-8")
picked = ww.channels_for_this_slot(CH)
check(len(picked) == 2 and picked[0] == CH[0], "corrupt rotation.json falls back to channel 1")
ww.ROTATION_PATH.unlink()
picked = ww.channels_for_this_slot(CH)
check(len(picked) == 2 and picked[0] == CH[0], "missing rotation.json starts at channel 1")

print("\n=== F. one channel only, and CHANNELS_PER_SLOT > len(channels) ===")
ww.ROTATION_PATH.unlink(missing_ok=True)
ONE = CH[:1]
picked = ww.channels_for_this_slot(ONE)
check(picked == ONE, "a single-channel file yields exactly that channel")
ww.mark_channel_scanned(ONE, ONE[0])
check(ww.day_scan_complete(ONE), "single channel completes the day's pass")
check(ww.channels_for_this_slot(ONE) == [], "and is not scanned twice in the same day")

print("\n=== G. days to a full pass, real numbers ===")
per_pass = (11 + ww.CHANNELS_PER_SLOT - 1) // ww.CHANNELS_PER_SLOT
print(f"  CHANNELS_PER_SLOT={ww.CHANNELS_PER_SLOT}, 11 channels -> {per_pass} slots, "
      f"6 slots a day -> a full pass every {per_pass / 6:.2f} day(s)")
check(per_pass == 6, "a full pass is exactly one day of slots")

ww.ROTATION_PATH.unlink(missing_ok=True)
print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAILURES: ' + '; '.join(fails)}")
sys.exit(1 if fails else 0)
