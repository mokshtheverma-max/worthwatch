"""YES is a live project, a concrete action, and nothing done or decided.

A YES must name one live project and one concrete action doable within two weeks, and must not
repeat anything the context file records as already done or decided; everything else is MAYBE.
It lives in final_verdict and nowhere else, and it reads the finished write-up, never the triage
prompt: a bound about the reader's circumstances put into the triage prompt was measured costing
keeps. Each check below demotes an example and, as its control, leaves a keep alone. Then the
score against labels, in-process: on an invented labels file, today's rules raise YES precision
above the recorded verdicts' and lose none of the keeps.

All of it runs on the invented example person (src/worthwatch/examples): her projects, her
decisions, her done-or-decided list, her generic-advice list and the parts of how she works. No
YouTube request, no model call; writes nothing but its sandbox.

Watch it go red: make yes_problem return None in a copy of src/worthwatch/core.py and run this
file against the copy.
"""
import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "yes")
from worthwatch import scoring

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def yes(vid, project, try_line, takeaway, header=None, day="2026-03-02"):
    """A written YES record, the way summary_written stores one."""
    return {"id": vid, "summary_state": "WRITTEN", "reason_code": "written",
            "read_verdict": "YES", "settled_verdict": "YES", "applies": "items",
            "project": project, "takeaway": takeaway, "try_this_week": try_line,
            "header": header, "listing_verdict": "MAYBE", "written_day": day, "day": day}


def verdict(record):
    v, text, _tags = ww.final_verdict(record)
    return v, text


def demoted(record, why, label):
    v, text = verdict(record)
    check(v == "MAYBE" and why in text, f"{label}: MAYBE, because {why} ({v}: {text[:110]})")


def stays(record, label):
    v, text = verdict(record)
    check(v == "YES", f"CONTROL {label}: still YES ({v}: {text[:90]})")


CONTEXT = ww.CONTEXT_PATH.read_text(encoding="utf-8").lower()

# A good Ledgerleaf keep: a quote, one project, one afternoon's action.
GOOD = yes("MaraVid0101", "Ledgerleaf",
           "Email the three bakeries that churned last month and ask which report they missed.",
           '"ask the ones who left before you ask the ones who stayed" - talk to churned bakeries '
           "first. Applies to: Ledgerleaf.")

# ==========================================================================================
print("=== a. the project is a live one ===")
demoted(dict(GOOD, project="a road not taken"), "not a live project",
        "a YES filed under a road not taken")
stays(GOOD, "the same write-up filed under Ledgerleaf")

print("\n=== b. the write-up names an action ===")
demoted(dict(GOOD, try_this_week="nothing", header=None), "no action",
        "a YES whose section 8 is nothing")
demoted(dict(GOOD, try_this_week="Not tried: contradicts a standing decision (cold email is "
                                 "closed as a first move)."),
        "cold email is closed as a first move", "a kept YES whose action rule 3 had struck")
stays(yes("MaraVid0102", "Tidepool", "Write the second sponsor slot's one-line pitch and send it "
          "to the studio that asked last month.",
          '"sell the slot to the people already reading" - pitch sponsors who are readers. '
          "Applies to: Tidepool."), "a Tidepool keeper with its action")

print("\n=== c. it contradicts no standing decision ===")
demoted(yes("MaraVid0103", "Ledgerleaf", "Run a small Google Ads campaign aimed at bakery owners.",
            '"put money behind the post that already works" - boost what works. Applies to: '
            "Ledgerleaf."), "no paid ads", "a YES whose action is paid ads")
# A rule a project is exempt from: Tidepool prices its sponsor slots as it likes, so the same
# action filed under Ledgerleaf breaks the price rule and filed under Tidepool does not.
PRICED = yes("MaraVid0104", "Ledgerleaf", "Add a price for a second plan to the pricing page this "
             "week.", '"two prices make the first one look fair" - add a second plan. Applies to: '
             "Ledgerleaf.")
demoted(PRICED, "nothing new is priced without a test first",
        "'add a price for a second plan', filed under Ledgerleaf")
check("nothing new is priced" not in verdict(dict(PRICED, project="Tidepool"))[1],
      f"CONTROL: filed under Tidepool, which is exempt, the price rule does not name it "
      f"({verdict(dict(PRICED, project='Tidepool'))[0]})")
# A rule that holds for one project only: no free plan on Ledgerleaf.
FREE = yes("MaraVid0105", "Ledgerleaf", "Offer bakeries a free plan for their first month.",
           '"free gets them through the door" - a free first month. Applies to: Ledgerleaf.')
demoted(FREE, "no free plan on ledgerleaf", "a free plan, filed under Ledgerleaf")
check("no free plan on ledgerleaf" not in verdict(dict(FREE, project="Tidepool"))[1],
      "CONTROL: the same free plan filed under Tidepool is not held to Ledgerleaf's rule")
stays(GOOD, "the churned-bakeries keep")

print("\n=== d. it repeats nothing done or decided ===")
demoted(yes("MaraVid0106", "Ledgerleaf", "Publish the price on a pricing page so bakeries can "
            "see it before they write.", '"show the price before the call" - publish the price. '
            "Applies to: Ledgerleaf."),
        "the ledgerleaf price page is already live", "a YES that builds the price page again")
demoted(yes("MaraVid0107", "Tidepool", "Start a referral program that gives readers a sticker "
            "for three sign-ups.", '"readers bring readers if you thank them" - a referral '
            "program. Applies to: Tidepool."),
        "the tidepool referral program already ran", "a YES that runs the referral program again")
stays(yes("MaraVid0108", "Tidepool", "Rewrite the welcome email so it links the three most-read "
          "issues.", '"the first email is the one they all open" - fix the welcome email. '
          "Applies to: Tidepool."), "the welcome email, which is the next move, not a done one")
for _name, _only, _pattern in ww.DONE_OR_DECIDED:
    check(_name in CONTEXT, f"'{_name}' is a sentence the context file really says, so the row "
                            f"points at something you can find")
check(len(ww.DONE_OR_DECIDED) >= 2, "CONTROL: and there are names to check")

print("\n=== e. it is not generic advice ===")
demoted(yes("MaraVid0109", "Ledgerleaf", "Rewrite the home page to sell the outcome, not the "
            "features.", '"nobody buys software, they buy the evening back" - sell outcomes. '
            "Applies to: Ledgerleaf."),
        "generic advice", "'sell the outcome, not the features'")
demoted(yes("MaraVid0110", "how I work", "Start the next coding session at medium effort.",
            '"you rarely need the biggest setting" - lower the effort. Applies to: how I work.'),
        "generic advice", "'start at medium effort', filed under how I work")
check("generic advice" not in verdict(yes("MaraVid0111", "this tool", "Switch this tool's "
      "triage step to a cheaper model and compare a week of verdicts.", '"use the small model '
      'for the sorting" - a cheaper model for one step. Applies to: this tool.'))[1],
      "CONTROL: a cheaper model for one step of this tool is a design choice, not advice on how "
      "you work, so the work-label rule does not name it")
stays(GOOD, "the churned-bakeries keep is not generic")

print("\n=== the note says why, and a written OPPORTUNITY is left to its own gate ===")
v, text = verdict(yes("MaraVid0109", "Ledgerleaf", "Rewrite the home page to sell the outcome, "
                      "not the features.", "x. Applies to: Ledgerleaf."))
check(text.startswith("demoted from YES: "), f"the row's note says it was demoted and why: {text}")
opp = dict(GOOD, read_verdict="OPPORTUNITY", settled_verdict="OPPORTUNITY",
           try_this_week="Rewrite the home page to sell the outcome, not the features.")
check(verdict(opp)[0] == "OPPORTUNITY", "an OPPORTUNITY is not judged by the YES rule")

# ==========================================================================================
print("\n=== f. a YES under the work label is a change to your own way of working ===")
LABEL = ww.SETUP_PROJECT
check(LABEL == "how I work", f"the example's work label is 'how I work' ({LABEL!r})")
for record, label in (
        (yes("MaraVid0112", LABEL, "Install a local model server and point a coding tool at it.",
             '"you can run models on your own machine now" - a local model server. Applies to: '
             "how I work."), "a local model server, a general tip"),
        (yes("MaraVid0113", LABEL, "Save one prompt that works as a reusable file you can refine.",
             '"save the prompt that worked" - keep good prompts. Applies to: how I work.'),
         "a saved prompt file, a general tip")):
    demoted(record, "general tip", label)
demoted(yes("MaraVid0114", LABEL, "Ask the coding tool to dispatch sub-agents to research three "
            "parts of the code in parallel before building.", '"split the research across five '
            'agents" - parallel sub-agents. Applies to: how I work.'),
        "one coding agent at a time", "parallel sub-agents, against a standing decision")
demoted(yes("MaraVid0115", "Ledgerleaf", "Build two Ledgerleaf features at once in separate git "
            "worktrees so the builds stop overwriting each other.", '"give each agent its own '
            'worktree" - parallel builds. Applies to: Ledgerleaf.'),
        "one coding agent at a time", "parallel builds in worktrees, filed under Ledgerleaf")
for record, label in (
        (yes("MaraVid0116", LABEL, "Add one line to the morning checklist for the backup check.",
             '"the checklist is the boss" - add to the checklist. Applies to: how I work.'),
         "the morning checklist, a part of how she works"),
        (yes("MaraVid0117", LABEL, "Write release notes for this Friday's change before shipping.",
             '"write what changed before you ship it" - release notes. Applies to: how I work.'),
         "release notes, a part of how she works")):
    stays(record, label)
check(ww.try_line_contradiction("Write the morning checklist as its own file.") is None,
      "CONTROL: one checklist file is not a decision hit")
check(ww.try_line_contradiction("Run the four models as agents on the import test, four tries "
                                "each.", "Ledgerleaf") is None,
      "CONTROL: agents a test grades are not agents she builds with")
check(all(name in CONTEXT for name, _p in ww.OWN_SETUP),
      "every part of how she works the rule looks for is named as the context file names it")
check(len(ww.OWN_SETUP) >= 3, "CONTROL: and there are names to check")

print("\n=== g. at most three keeps a day under the work label ===")
ww.RECORDS_PATH.unlink(missing_ok=True)
for n, vid in enumerate(("labelcap001", "labelcap002", "labelcap003", "labelcap004")):
    ww.update_record(vid, url=f"https://www.youtube.com/watch?v={vid}", title=f"Label keep {n + 1}",
                     summary_state="READ_NOT_WRITTEN", reason_code="summary_pending",
                     read_verdict="YES", read_day=ww.today_key())
    summary = ("## 6. What applies to me\n\nMost applicable: add one line to the morning checklist "
               "for the backup check. Applies to: how I work.\n\n## 7. What contradicts what I "
               "believe\n\nnothing in this video touches the beliefs list\n\n## 8. Try this week\n\n"
               f"Add one line to the morning checklist, number {n + 1}.\n")
    ww.summary_written(vid, summary, "YES")
verdicts = [ww.final_verdict(ww.load_records()[v])[0:2]
            for v in ("labelcap001", "labelcap002", "labelcap003", "labelcap004")]
check([v for v, _t in verdicts] == ["YES", "YES", "YES", "MAYBE"],
      f"the first three keep their YES, the fourth is MAYBE ({[v for v, _t in verdicts]})")
check("keep number 4" in verdicts[3][1] and "at most 3 a day" in verdicts[3][1],
      f"and its row says why: {verdicts[3][1]}")


# ==========================================================================================
def done_name(text, project):
    """The first done-or-decided name that holds for the project and matches the text, read the
    way yes_problem reads a section 6 entry (its label cut, its quotes removed)."""
    text = ww.unquoted(ww.APPLIES_LABEL_RE.sub("", ww.one_line(text or "")))
    return next((name for name, only, pattern in ww.DONE_OR_DECIDED
                 if only in (None, project) and pattern.search(text)), None)


print("\n=== h. what one project has already done is not a YES, for that project only ===")
PRICE_PAGE = yes("MaraVid0120", "Ledgerleaf",
                 "Put a pricing page live so bakeries see the price before they write.",
                 '"a price on the page saves a call" - publish the price on a pricing page, for '
                 "Ledgerleaf. Applies to: Ledgerleaf.",
                 header="Put a pricing page live for Ledgerleaf before the next sales call.")
NAME = "the ledgerleaf price page is already live"
demoted(PRICE_PAGE, NAME, "a pricing page for Ledgerleaf, which is already live")
for field in ("header", "try_this_week", "takeaway"):
    got = done_name(PRICE_PAGE[field], "Ledgerleaf")
    check(got == NAME, f"    and its {field} on its own is named by it ({got})")
stays(dict(PRICE_PAGE, project="Tidepool",
           takeaway=PRICE_PAGE["takeaway"].replace("Ledgerleaf", "Tidepool"),
           header=None),
      "the same pricing page filed under Tidepool: Ledgerleaf's done list does not hold for it")
for line, label in (
        ("Check the pricing page loads on a phone in under two seconds.",
         "checking the live page, which builds nothing new"),
        ("Ask three bakeries what they expected the price to be before they saw it.",
         "asking about the price, which publishes nothing")):
    check(done_name(line, "Ledgerleaf") is None,
          f"CONTROL {label}: named by none of them ({done_name(line, 'Ledgerleaf')})")
    stays(yes("controlline", "Ledgerleaf", line, line + " Applies to: Ledgerleaf."), label)

print("\n=== i. a done rule with no project holds for every project ===")
HUNT = yes("MaraVid0121", "Tidepool", "Launch Tidepool on Product Hunt next Tuesday.",
           '"launch day is a week of work" - a Product Hunt launch. Applies to: Tidepool.')
demoted(HUNT, "a product hunt launch", "a Product Hunt launch for Tidepool")
demoted(dict(HUNT, project="Ledgerleaf"), "a product hunt launch",
        "the same launch filed under Ledgerleaf: the rule holds for every project")
check(done_name("Read three Hacker News threads about launches.", "Tidepool") is None,
      "CONTROL: another launch site is not the done launch")

# ==========================================================================================
print("\n=== the score against labels, in-process ===")
# An invented labels file and the write-ups stored for it: four keeps that today's rules must
# leave alone, and four skips the recorded verdicts called positive that today's rules catch.


def writeup(project, quote_line, try_line, opportunity=None):
    opp = f"\nOpportunity line: {opportunity}" if opportunity else ""
    return (f"## 1. What it is\nAn invented video.\n\n## 3. Evidenced versus asserted\nSaid.{opp}\n\n"
            f"## 6. What applies to me\n\nMost applicable: {quote_line} Applies to: {project}.\n\n"
            f"## 7. What contradicts what I believe\n\nnothing in this video touches the beliefs "
            f"list\n\n## 8. Try this week\n\n{try_line}\n\n## 9. Header\n\n{try_line}\n")


STORED = {
    "MaraVid0201": ("YES", "KEEP", writeup("Ledgerleaf", '"ask the ones who left" - talk to churned '
                    'bakeries.', "Email the three bakeries that churned last month.")),
    "MaraVid0202": ("YES", "KEEP", writeup("Tidepool", '"sell the slot to readers" - pitch reader '
                    'sponsors.', "Pitch the second sponsor slot to the studio that asked.")),
    "MaraVid0203": ("YES", "KEEP", writeup("how I work", '"the checklist is the boss" - add to the '
                    'checklist.', "Add the backup check to the morning checklist.")),
    "MaraVid0204": ("YES", "KEEP", writeup("Ledgerleaf", '"show the export first" - demo with a '
                    'real bank export.', "Record a two-minute demo that imports one bank export.")),
    "MaraVid0205": ("YES", "SKIP", writeup("Ledgerleaf", '"buy the evening back" - sell outcomes.',
                    "Rewrite the home page to sell the outcome, not the features.")),
    "MaraVid0206": ("YES", "SKIP", writeup("Tidepool", '"readers bring readers" - referrals.',
                    "Start a referral program for Tidepool readers.")),
    "MaraVid0207": ("YES", "SKIP", writeup("how I work", '"run models at home" - a local model.',
                    "Install a local model server and point a coding tool at it.")),
    "MaraVid0208": ("OPPORTUNITY", "SKIP", writeup(
        "a road not taken", '"they bought the rival" - an acquisition.', "nothing",
        opportunity="works: a bakery chain buys a family-run rival | for: a national chain | "
                    "evidence: claimed: $7.5 million")),
}
history = [{"date": "2026-03-02", "verdict": tool, "channel": "Example Channel",
            "title": f"An invented video {vid}", "url": f"https://www.youtube.com/watch?v={vid}",
            "reason": "", "summary": text, "backend": "claude-cli:sonnet",
            "try_this_week": ww.extract_try_this_week(text), "source": "channels",
            "confidence": 90} for vid, (tool, _y, text) in STORED.items()]
ww.HISTORY_PATH.write_text("".join(json.dumps(r) + "\n" for r in history), encoding="utf-8")
labels = {vid: {"id": vid, "date": "2026-03-02", "channel": "Example Channel",
                "title": f"An invented video {vid}",
                "url": f"https://www.youtube.com/watch?v={vid}", "tool_verdict": tool,
                "listing_verdict": "MAYBE", "tool_reason": "", "applies": "items",
                "has_summary": True, "your_verdict": yours}
          for vid, (tool, yours, _t) in STORED.items()}
before = scoring.score(labels, scoring.recorded(labels))["by_verdict"]["YES"]
rows, moved = scoring.rules_only(ww, labels, scoring.history_by_id(ww.HISTORY_PATH))
after_all = scoring.score(labels, {r["id"]: r for r in rows})
after = after_all["by_verdict"]["YES"]
both = after_all["by_verdict"]["YES+OPPORTUNITY"]
print(f"    recorded: YES called {before['called']}, kept {before['tp']}, precision "
      f"{before['precision'] * 100:.1f}%")
print(f"    today's rules: YES called {after['called']}, kept {after['tp']}, precision "
      f"{after['precision'] * 100:.1f}%, recall {after['recall'] * 100:.1f}%")
check(after["precision"] > before["precision"],
      f"YES precision rises above the recorded {before['precision'] * 100:.1f}% "
      f"({after['precision'] * 100:.1f}%)")
check(after["recall"] == 1.0, f"and YES recall does not fall ({after['recall'] * 100:.1f}%)")
check(both["fp"] == 0, f"every skip the recorded verdicts called positive is caught ({both['fp']} left)")
check(sorted(g["id"] for g, _a, _n in moved) == ["MaraVid0205", "MaraVid0206", "MaraVid0207",
                                                  "MaraVid0208"],
      f"the verdicts that moved are exactly the four skips ({sorted(g['id'] for g, _a, _n in moved)})")
ww.LABELS_PATH.write_text(json.dumps({"videos": list(labels.values())}), encoding="utf-8")
doctor_score = scoring.rules_score(ww.LABELS_PATH, core=ww)
check(doctor_score["projects_found"] == doctor_score["projects_total"] == 3,
      f"the project keeps doctor counts are all found ({doctor_score['projects_found']} of "
      f"{doctor_score['projects_total']})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
