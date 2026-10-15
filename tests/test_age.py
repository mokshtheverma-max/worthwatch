"""FIX 6, 2026-09-22. Ages stay approximate, and the two-year rule holds.

A search listing gives no upload date. YouTube shows "8 years ago", and yt-dlp turns that
into a date by taking N years off the moment of the listing, in UTC, and rounding to the
nearest day, so every run after 12:00 UTC lands on tomorrow's month and day: all
seventeen "uploaded" dates in the too-old list of 2026-09-22 fell on 09-23 of some year.
Printed as dates, they read as facts nobody had.

Worse, the rule was (today - date).days > 730, and "2 years ago" comes out 729 or 730 days
back, so a "2 years ago" result could never be skipped. YouTube says "2 years ago" for
anything 24 to 35 months old. Four of that day's topic results were "2 years ago" and all
four were kept, and three were read, one of them the day's first header line.

So: an age is shown the way the source gave it ("about 8 years ago") unless an exact date
is already known from fetched metadata, and "N years ago" counts as at least N years old.

Everything runs in a fresh folder under the OS temp folder, with invented videos. Every listing
moment is a fixed UTC time, never the clock. No YouTube request, no model call.
"""
import datetime as dt
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "age")
ww.INFO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
ww.named_channel_handles = lambda: set()

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def reset():
    for f in list(ww.DATA_DIR.glob("*.json*")) + list(ww.INFO_CACHE_DIR.glob("*")):
        f.unlink()


UTC = dt.timezone.utc


def local_iso(moment):
    """A fixed UTC moment as the naive local time a record's "created" holds."""
    return moment.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


# A topics run of 2026-09-22 at 16:03 UTC: after 12:00 UTC, so yt-dlp's rounding lands on the
# next day.
AT_1603_UTC = dt.datetime(2026, 9, 22, 16, 3, 47, tzinfo=UTC)
listing_age = getattr(ww, "listing_age", None)
min_age = getattr(ww, "min_age_months", None)
check(listing_age is not None and min_age is not None,
      "one function recovers the age the source gave, and one says how old that makes it")

# ==========================================================================================
print("\n=== A. the age as the source gave it, recovered from yt-dlp's date ===")
CASES = [("20180923", (8, "year")), ("20240923", (2, "year")), ("20250923", (1, "year")),
         ("20260223", (7, "month")), ("20260423", (5, "month")), ("20260823", (1, "month")),
         ("20260902", (3, "week")), ("20260919", (4, "day"))]
for date, want in CASES:
    got = listing_age(date, AT_1603_UTC) if listing_age else None
    check(got == want, f"{date} listed at 16:03 UTC on 2026-09-22 is {want[0]} {want[1]}(s) "
                       f"ago ({got})")
early = dt.datetime(2026, 9, 22, 4, 0, tzinfo=UTC)
got = listing_age("20240922", early) if listing_age else None
check(got == (2, "year"), f"before 12:00 UTC the rounding goes down, and 2024-09-22 is "
                          f"'2 years ago' then ({got})")
check((listing_age("20230114", AT_1603_UTC) if listing_age else "x") is None,
      "CONTROL: a date no 'N units ago' produces is not an age from a listing")

# ==========================================================================================
print("\n=== B. 'N years ago' is at least N years old, and the rule holds at the edges ===")
bound = 12 * (ww.TOPIC_MAX_AGE_DAYS // 365)
check(bound == 24, f"two years is 24 months ({bound})")
if min_age:
    for date, months, skipped in (("20240923", 24, True),     # "2 years ago"
                                  ("20250923", 12, False),    # "1 year ago"
                                  ("20251023", 11, False)):   # "11 months ago"
        got = min_age(date, AT_1603_UTC)
        check(got == months and (got >= bound) == skipped,
              f"{date}: at least {got} months old, so {'skipped' if skipped else 'kept'}")
    # "24 months ago" and "23 months ago", which YouTube does not print but the rule must
    # still get right: yt-dlp's arithmetic for each, then the age back out of it.
    m24 = ww.yt_dlp_listing_date(AT_1603_UTC, 24, "month")
    m23 = ww.yt_dlp_listing_date(AT_1603_UTC, 23, "month")
    check(min_age(m24, AT_1603_UTC) >= bound, f"24 months ago ({m24}) is skipped")
    check(min_age(m23, AT_1603_UTC) < bound, f"23 months ago ({m23}) is kept")
    # a listing moment in a time zone two hours east of UTC: its own calendar day counts
    on_day = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    check(min_age("20240922", on_day, exact=True) == 24,
          "an exact date exactly two years back is two years old: skipped")
    check(min_age("20240923", on_day, exact=True) == 23,
          "and one day short of it is not: kept")
    leap = dt.datetime(2028, 2, 29, 16, 0, tzinfo=UTC)
    check(min_age("20260228", leap, exact=True) == 24 and
          min_age("20260301", leap, exact=True) == 23,
          "a 29 February listing: 28 February two years back is two years, 1 March is not")
    check(listing_age(ww.yt_dlp_listing_date(leap, 2, "year"), leap) == (2, "year"),
          "and '2 years ago' listed on 29 February is still recovered as two years")

print("\n  four '2 years ago' results like that day's are skipped now, and the '1 year ago' kept")
# Invented results with the property of that day's: listed after 12:00 UTC on 2026-09-22, so
# yt-dlp put "2 years ago" on 2024-09-23 and "1 year ago" on 2025-09-23.
REAL = [("MaraAge0001", "20240923", dt.datetime(2026, 9, 22, 16, 40, 12, tzinfo=UTC)),
        ("MaraAge0002", "20240923", dt.datetime(2026, 9, 22, 18, 22, 5, tzinfo=UTC)),
        ("MaraAge0003", "20240923", dt.datetime(2026, 9, 22, 19, 10, 30, tzinfo=UTC)),
        ("MaraAge0004", "20240923", dt.datetime(2026, 9, 22, 17, 55, 41, tzinfo=UTC)),
        ("MaraAge0005", "20250923", dt.datetime(2026, 9, 22, 16, 5, 3, tzinfo=UTC))]
reset()
for vid, date, moment in REAL:
    kept, skipped, _undated = ww.drop_stale_search_results(
        [{"id": vid, "title": vid, "upload_date": date, "uploader_id": "@someone"}], now=moment)
    want_skip = date == "20240923"
    check(bool(skipped) == want_skip,
          f"{vid}, {date} at {moment:%H:%M} UTC: {'skipped' if skipped else 'kept'}")
check((dt.date(2026, 9, 22) - dt.date(2024, 9, 23)).days == 729,
      "CONTROL: under the old rule, (today - date).days > 730, each of the four was 729 days "
      "old on 2026-09-22 and kept, which is the defect")

# ==========================================================================================
print("\n=== C. shown the way the source gave it ===")
reset()
ww.drop_stale_search_results([{"id": "MaraAge0006", "upload_date": "20180923",
                               "uploader_id": "@smallshopclips",
                               "title": "Selling Bookkeeping To Bakeries - Know Your Value"}],
                             now=AT_1603_UTC)
record = ww.load_records().get("MaraAge0006", {})
check(record.get("upload_age") == "8 years ago",
      f"the record keeps the age the source gave: {record.get('upload_age')!r}")
lines = ww.day_table_lines([record]) if record else []
aged = next((l for l in lines if l.startswith("Dropped on age")), "")
check("(about 8 years ago)" in aged and "2018-09-23" not in aged,
      f"the line under the table says so, and prints no invented date: {aged[-70:]!r}")
check("8 years ago" in (record.get("state_reason") or ""),
      f"and so does its reason: {record.get('state_reason')!r}")

stored = {"id": "MaraAge0007", "title": "How to Sell a Newsletter to Indie Game Studios",
          "summary_state": "NOT_READ", "reason_code": "too_old", "upload_date": "20230923",
          "created": local_iso(dt.datetime(2026, 9, 22, 17, 1, 0, tzinfo=UTC))}
check(ww.age_label(stored) == "about 3 years ago",
      f"a record from before this fix: the age comes back from its listing time "
      f"({ww.age_label(stored)!r})")
(ww.INFO_CACHE_DIR / "MaraAge0008.json").write_text(json.dumps({"upload_date": "20210401"}),
                                                    encoding="utf-8")
exact = dict(stored, id="MaraAge0008", upload_date="20210923")
check(ww.age_label(exact) == "uploaded 2021-04-01",
      f"an exact date already fetched is shown as the date ({ww.age_label(exact)!r})")

# ==========================================================================================
print("\n=== D. the filter never rewrites a video this tool has already met ===")
reset()
ww.update_record("MaraAge0001", url="https://www.youtube.com/watch?v=MaraAge0001",
                 title="Pricing Lessons From a Bakery Owner", summary_state="WRITTEN",
                 reason_code="written", read_verdict="YES", applies="items",
                 project="Ledgerleaf", day="2026-09-22", upload_date="20240923")
later = dt.datetime(2026, 9, 25, 17, 0, tzinfo=UTC)
kept, skipped, _u = ww.drop_stale_search_results(
    [{"id": "MaraAge0001", "title": "Pricing Lessons From a Bakery Owner", "upload_date": "20240926",
      "uploader_id": "@pricingnotes"},
     {"id": "seenseen001", "title": "Seen before", "upload_date": "20180926",
      "uploader_id": "@x"},
     {"id": "newnewnew01", "title": "Never met", "upload_date": "20180926",
      "uploader_id": "@x"}], now=later, seen={"seenseen001": {"verdict": "NO"}})
store = ww.load_records()
check(len(skipped) == 3, "all three are too old for this search, and skipped")
check(store["MaraAge0001"]["summary_state"] == "WRITTEN"
      and store["MaraAge0001"]["day"] == "2026-09-22",
      "but the video read on 2026-09-22 keeps its record, its state and its day")
check("seenseen001" not in store, "a video seen.json already has gets no record from a search")
check(store.get("newnewnew01", {}).get("reason_code") == "too_old",
      "CONTROL: a result never met before is recorded as too old")

reset()
print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
