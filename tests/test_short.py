"""2026-09-26 night, brief part 7: a short digest to paste.

DIGEST-<date>-short.md beside each digest: the header, the day's table, and for each kept video
only its header line and its "Try this week", so the reader can paste one short file. The full
digest stays exactly as it is: checked byte for byte against the digest written with the short
one switched off. Everything is written inside a fresh folder under the OS temp folder, with
the invented example person's profile.
"""
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "short")
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


DAY = "2026-09-26"
WRITEUP = ("## 1. What it is\nA talk about {what}.\n\n## 6. What applies to me\n\nMost applicable: "
           "\"match the bank export twice\" - {what} for Ledgerleaf. Applies to: Ledgerleaf.\n\n"
           "## 7. What contradicts what I believe\n\nnothing in this video touches the beliefs "
           "list\n\n## 8. Try this week\n\n{try_line}\n\n## 9. Header\n\n{header}\n")
KEEPS = (("keepvideo01", "Export matched twice", "Match one Ledgerleaf bakery's bank export twice "
          "and compare the two totals.", "Match one Ledgerleaf bakery's bank export twice."),
         ("keepvideo02", "A check after each step", "Add a balance check after each import step "
          "of one Ledgerleaf bakery's books.", "Add balance checks after each import step in "
          "Ledgerleaf."))
run_section = ["## Run at 10:00 (topics)", "", "### Summaries", ""]
for vid, title, try_line, header in KEEPS:
    summary = WRITEUP.format(what=title.lower(), try_line=try_line, header=header)
    ww.update_record(vid, url=f"https://www.youtube.com/watch?v={vid}", title=title, channel="C",
                     summary_state="READ_NOT_WRITTEN", reason_code="summary_pending",
                     read_verdict="YES", read_day=DAY, day=DAY)
    ww._RUN_DAY["day"] = DAY
    ww.summary_written(vid, summary, "YES")
    run_section += [f"#### [YES] {title} (C, claude:sonnet, 10.0s)", "", summary, ""]
ww.update_record("skipvideo01", url="https://www.youtube.com/watch?v=skipvideo01",
                 title="Read and skipped", channel="C", summary_state="WRITTEN",
                 reason_code="written", read_verdict="MAYBE", settled_verdict="MAYBE", day=DAY,
                 written_day=DAY, applies="nothing")
ww.update_record("notread0001", url="https://www.youtube.com/watch?v=notread0001",
                 title="Not read at all", channel="C", summary_state="NOT_READ",
                 reason_code="over_ceiling", state_reason="audio 67.0 MB over the 63 MB ceiling",
                 day=DAY)
SECTION = "\n".join(run_section) + "\n\n---\n\n"

print("=== the full digest is exactly what it was ===")
real_short = getattr(ww, "write_short_digest", None)
ww.write_short_digest = lambda day: None
ww.write_day_digest(DAY, SECTION)
without = (ww.OUTPUT_DIR / f"DIGEST-{DAY}.md").read_bytes()
(ww.OUTPUT_DIR / f"DIGEST-{DAY}.md").unlink()
if real_short:
    ww.write_short_digest = real_short
ww.write_day_digest(DAY, SECTION)
full = (ww.OUTPUT_DIR / f"DIGEST-{DAY}.md").read_bytes()
check(full == without, "the full digest is byte for byte the one written without the short file")

print("\n=== the short one, beside it ===")
short_path = ww.OUTPUT_DIR / f"DIGEST-{DAY}-short.md"
check(short_path.exists(), f"DIGEST-{DAY}-short.md is written next to the digest")
short = short_path.read_text(encoding="utf-8") if short_path.exists() else ""
text = full.decode("utf-8").replace("\r\n", "\n")      # the file on disk has Windows endings
header = text.split("<!-- HEADER-START -->")[1].split("<!-- HEADER-END -->")[0].strip()
table = text.split("<!-- TABLE-START -->")[1].split("<!-- TABLE-END -->")[0].strip()
check(header and header in short, "it holds the header, word for word")
check(table and table in short, "and the day's table, word for word")
for vid, title, try_line, header_sentence in KEEPS:
    line = next((l for l in header.splitlines() if vid in l and l.startswith("- **")), None)
    check(line is not None and line in short, f"{title}: its header line")
    check(f"Try this week: {try_line}" in short, f"{title}: and its Try this week")
check("## 1. What it is" not in short and "## 6. What applies" not in short
      and "## 9. Header" not in short and "## Run at" not in short,
      "and nothing else of the write-ups or the run sections")
check("Read and skipped" not in short.split("## Kept")[-1] and
      "Not read at all" not in short.split("## Kept")[-1],
      "only kept videos are listed after the table")
check("<!--" not in short, "without the page's hidden markers, so it pastes clean")
check(len(short) < len(text), f"and it is shorter than the digest: {len(short)} characters "
                              f"of {len(text)}, with write-ups here only a few lines long")

print("\n=== a day with nothing kept ===")
ww.update_record("keepvideo01", summary_state="NOT_READ", reason_code="over_ceiling",
                 state_reason="gone", day=DAY)
ww.update_record("keepvideo02", summary_state="NOT_READ", reason_code="over_ceiling",
                 state_reason="gone", day=DAY)
ww.refresh_digest(DAY)
short = short_path.read_text(encoding="utf-8")
check("Nothing was kept on this day." in short, "it says nothing was kept")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
