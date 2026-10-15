"""Does the context file still agree with the settings and the code that read it?

The context file is pasted into the prompts, and the settings keep lists that have to match it:
the closed project list an item may claim, the standing decisions a "Try this week" is checked
against, and the section headings the prompts tell the model to look under. When one side moves
and the other does not, nothing breaks loudly: a project the prompt offers becomes one no item
can claim and every item for it is struck in silence, or a decision the prompt promises to
enforce is enforced against nothing.

This file holds the example person's three files (src/worthwatch/examples) to that, since they
are what `worthwatch init` gives a new user to copy; `worthwatch doctor` makes the same checks on
a user's own files.

Run it against a broken example to watch it go red: delete the "## Decisions already made"
heading from a copy of examples/context.md and point CONTEXT_FOR_TEST at the copy.
"""
import io
import os
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()
TMP = _tmpsandbox.sandbox(ww, "brain")
from worthwatch import settings as wsettings

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def flat(text):
    """Prose with every run of whitespace collapsed to one space. Every phrase check below
    goes through this, because the context file is wrapped and a phrase can land across a line
    break. A guard whose answer depends on the column a word lands in is measuring the wrong
    thing."""
    return re.sub(r"\s+", " ", text)


CONTEXT_FILE = Path(os.environ.get("CONTEXT_FOR_TEST", wsettings.EXAMPLES_DIR / "context.md"))
CONTEXT = flat(CONTEXT_FILE.read_text(encoding="utf-8"))
TOPICS = (wsettings.EXAMPLES_DIR / "topics.txt").read_text(encoding="utf-8")
RULES = wsettings.read_toml(wsettings.EXAMPLES_DIR / "rules.toml")
print(f"context file under test: {CONTEXT_FILE.name} ({len(CONTEXT)} chars)\n")


def section(heading):
    """The body of one "## heading" section of the context file, flattened, or ""."""
    raw = CONTEXT_FILE.read_text(encoding="utf-8")
    m = re.search(rf"^##\s*{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", raw, re.M | re.S)
    return flat(m.group(1)) if m else ""


print("=== A. every project the prompt offers is a project the context file names ===")
# The label is what a model writes; the context file is what it reads. A label the context file
# never mentions is one the model can only be guessing at.
for project in ww.PROJECTS:
    if project == "a road not taken":
        continue          # not a project: the OPPORTUNITY label, defined in the prompt
    check(project.lower() in CONTEXT.lower(), f"the context file names {project!r}")

print("\n=== B. a project that is not in the settings is not on offer as a label ===")
# An item filed under a project the settings do not list is an item filed under nothing.
for dropped in ("a recipe subscription box", "the old blog"):
    check(dropped not in [p.lower() for p in ww.PROJECTS]
          and dropped not in {alias for alias, _ in ww.PROJECT_ALIASES},
          f"{dropped!r} is not a label an item can claim")
check(ww.projects_named("Applies to: the old blog") == set(),
      "an item claiming a project nobody runs names no project, so it is struck")
check(ww.projects_named("Applies to: Ledgerleaf") == {"Ledgerleaf"},
      "CONTROL: an item claiming a real project names it")

print("\n=== C. the headings the prompts send the model to actually exist ===")
# Each of these is named inside a prompt. A prompt that points at a heading the context file
# does not have is a prompt asking the model to invent the section.
for heading in ("Projects and status today", "What's been tried", "Decisions already made",
                "Beliefs a video should test me on"):
    named_in_prompt = (heading in flat(ww.TRIAGE_INSTRUCTIONS)
                       or heading in flat(ww.SUMMARY_INSTRUCTIONS))
    check(heading in CONTEXT, f"the context file has a {heading!r} section")
    check(named_in_prompt, "and a prompt sends the model to it")

print("\n=== D. the example states one person's shape, with numbers ===")
for phrase, why in [
    ("41 paying bakeries", "Ledgerleaf's status, a number a video can be judged against"),
    ("1,900 subscribers", "Tidepool's status"),
    ("9 sign-ups, 1 paid", "what has been tried, and what it returned"),
]:
    check(phrase.lower() in CONTEXT.lower(), f"{phrase!r} is in the context file ({why})")

print("\n=== E. the example topics parse, and a retired topic stays out ===")
queries = [l.strip() for l in TOPICS.splitlines() if l.strip() and not l.strip().startswith("#")]
parsed = [ww.parse_topic_line(q) for q in queries]
check(len(parsed) >= 3, f"topics.txt holds searches ({len(parsed)})")
check(any(needs for _q, needs in parsed), "and at least one carries a needs list")
check(all(q and "|" not in q for q, _needs in parsed), "every query reads clean of its needs list")
check("podcast growth hacks" not in [q for q, _ in parsed], "a retired topic stays out")
check("# podcast growth hacks" in TOPICS, "  and topics.txt names it under its retirement note")

print("\n=== F. every rule's name is a phrase of the context file ===")
# The name is the sentence a demoted row prints; a tool that cites a decision you cannot find
# written down is worse than one that says nothing.
decisions = section("Decisions already made").lower()
check(bool(decisions), "the context file has a decisions section to look in")
for rule in RULES.get("decision", []):
    check(rule["name"].lower() in decisions, f"decision {rule['name']!r} is in that section")
for rule in RULES.get("done", []):
    check(rule["name"].lower() in CONTEXT.lower(), f"done {rule['name']!r} is in the context file")
work = section("How I work").lower()
for rule in RULES.get("work", []):
    check(rule["name"].lower() in work, f"work part {rule['name']!r} is in How I work")
killed = section("Ideas I killed").lower()
for rule in RULES.get("killed", []):
    check(rule["name"].lower() in killed, f"killed idea {rule['name']!r} is in Ideas I killed")

print("\n=== G. the verdict definitions in the context file are the ones in the prompt ===")
TRIAGE = flat(ww.TRIAGE_INSTRUCTIONS).lower()
check("within two weeks" in CONTEXT.lower() and "within two weeks" in TRIAGE,
      "YES carries the two-week bound in both places")
# OPPORTUNITY is its own lane, and its three tests are checked in both places.
for phrase, what in (("never consumers", "the buyer is a company or a developer, never consumers"),
                     ("shown or only said", "the figure is marked as shown or only said"),
                     ("first step toward a first customer", "and there is a first step")):
    check(phrase in CONTEXT.lower() and phrase in TRIAGE,
          f"OPPORTUNITY carries its test in both places: {what}")
check("a person in my position" not in CONTEXT.lower(),
      "and the context file does not make the reader's own situation the test")
for never in ("merger", "funding round", "hiring war"):
    check(never in CONTEXT.lower() and never in TRIAGE,
          f"and the {never!r} exclusion is in both places")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
