"""Write examples/sample-digest.md by running worthwatch's own code on invented videos for the
invented example person (src/worthwatch/examples). No YouTube request and no model call: the
write-ups below stand in for a model's. tests/test_docs.py runs this the same way and checks the
file in examples/ is still exactly what the code makes.

    python scripts/make_sample_digest.py            writes examples/sample-digest.md
    python scripts/make_sample_digest.py --print    prints it instead
"""
import datetime as dt
import os
import sys
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DIR / "tests"))

DAY = "2026-03-02"
NOTE = ("<!-- Made by scripts/make_sample_digest.py from worthwatch's own code. Mara Quinn, her "
        "projects and these videos are invented. -->\n\n")


def writeup(applies, try_line):
    return (f"## 1. What it is\nA founder interview.\n\n## 2. The claim\nIt works.\n\n"
            f"## 3. Evidenced versus asserted\nShown on screen.\n\n## 4. The method\n1. Do it.\n\n"
            f"## 5. Worth keeping verbatim\n\"a quote\"\n\n## 6. What applies to me\n\n{applies}\n\n"
            f"## 7. What contradicts what I believe\n\nnothing in this video touches the beliefs "
            f"list\n\n## 8. Try this week\n\n{try_line}\n\n## 9. Header\n\nnothing\n")


def make():
    import _tmpsandbox
    ww = _tmpsandbox.import_core()
    _tmpsandbox.sandbox(ww, "sample-digest")
    ww.today_key = lambda: DAY
    ww.todays_totals = lambda: (5, 4, 0)
    when = dt.datetime(2026, 3, 1, 18, 0).timestamp()
    os.utime(ww.CONTEXT_PATH, (when, when))

    def read(vid, title, channel, verdict, summary, created):
        ww.update_record(vid, url=f"https://www.youtube.com/watch?v={vid}", title=title,
                         channel=channel, listing_verdict="MAYBE", read_verdict=verdict,
                         summary_state="READ_NOT_WRITTEN", reason_code="summary_pending",
                         read_day=DAY, created=created)
        ww.summary_written(vid, summary, verdict)

    read("SampleVid01", "We Got 40 Paying Bakeries Without Ads | Small Software Show",
         "Small Software Show", "YES",
         writeup('Most applicable: "ask the ones who left before you ask the ones who stayed" - '
                 "talk to the bakeries that churned before the ones that stayed. Applies to: "
                 "Ledgerleaf.",
                 "Email the three bakeries that churned last month and ask which report they "
                 "missed."), f"{DAY}T09:04:10")
    read("SampleVid02", "The Welcome Email Every Newsletter Gets Wrong", "Inbox Craft", "YES",
         writeup('Most applicable: "the first email is the one they all open" - rewrite the '
                 "welcome email around the issues readers already like. Applies to: Tidepool.",
                 "Rewrite the Tidepool welcome email so it links the three most-read issues."),
         f"{DAY}T09:11:42")
    read("SampleVid03", "Growth Hacks for 2026 (Number 7 Will Shock You)", "Hype Daily", "MAYBE",
         writeup("nothing", "nothing"), f"{DAY}T10:02:05")
    read("SampleVid04", "Boost Your Posts: The Paid Ads Playbook", "Ad Lab", "YES",
         writeup('Most applicable: "put money behind the post that already works" - boost the '
                 "post that already works. Applies to: Ledgerleaf.",
                 "Run a $10 a day Google Ads campaign for Ledgerleaf."), f"{DAY}T10:40:19")
    ww.update_record("SampleVid05", url="https://www.youtube.com/watch?v=SampleVid05",
                     title="Lofi beats to study to", channel="Lo-fi Beats", listing_verdict="NO",
                     summary_state="NOT_READ", reason_code="screened_out",
                     state_reason="screened out from its listing: a music stream",
                     created=f"{DAY}T10:41:00")
    ww.write_day_digest(DAY, "", create=True)
    return NOTE + (ww.OUTPUT_DIR / f"DIGEST-{DAY}-short.md").read_text(encoding="utf-8")


if __name__ == "__main__":
    text = make()
    if "--print" in sys.argv:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text, end="")
    else:
        target = DIR / "examples" / "sample-digest.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {target.relative_to(DIR).as_posix()}")
