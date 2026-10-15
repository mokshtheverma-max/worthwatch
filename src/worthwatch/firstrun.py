"""`worthwatch init`: the first thing a new user runs.

It makes the config, data and output folders, copies the invented example person's files into
the config folder for you to replace with your own, never overwrites a file that is already
there, and asks which brain does the judging (see brains.py), recording the answer in
settings.toml.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

from . import brains
from . import settings as _settings

EXAMPLE_FILES = ("settings.toml", "rules.toml", "context.md", "channels.txt", "topics.txt")
BRAIN_LINE_RE = re.compile(r'^(\s*kind\s*=\s*)"[^"]*"', re.M)


def recorded_brain(settings_path):
    """The brain settings.toml names under [brain], or None."""
    data = _settings.read_toml(settings_path)
    kind = (data.get("brain") or {}).get("kind")
    return kind if kind in brains.BRAIN_KINDS else None


def set_brain(settings_path, kind):
    """Record `kind` under [brain] in settings.toml, changing that one line and nothing else."""
    path = Path(settings_path)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    head, sep, tail = text.partition("[brain]")
    if sep:
        if BRAIN_LINE_RE.search(tail.split("\n[", 1)[0]):
            section, rest = (tail.split("\n[", 1) + [""])[:2]
            section = BRAIN_LINE_RE.sub(lambda m: f'{m.group(1)}"{kind}"', section, count=1)
            tail = section + ("\n[" + rest if rest else "")
        else:
            tail = f'\nkind = "{kind}"' + tail
        text = head + sep + tail
    else:
        text = text.rstrip("\n") + f'\n\n[brain]\nkind = "{kind}"\n'
    path.write_text(text, encoding="utf-8")


def choose_brain(found):
    """The brain to use when nobody chooses: the first one this machine has, in
    brains.SUGGEST_ORDER, which ends with agent mode because that needs nothing."""
    return next(k for k in brains.SUGGEST_ORDER if found.get(k, (False, ""))[0])


def ask_brain(found, input_fn=input, print_fn=print):
    """Ask which brain to use, showing which ones this machine has. An empty answer takes the
    suggestion; an answer that is not on the list is asked again."""
    suggested = choose_brain(found)
    print_fn("\nWhich brain should judge the videos?")
    for n, kind in enumerate(brains.BRAIN_KINDS, 1):
        ok, how = found.get(kind, (False, ""))
        print_fn(f"  {n}. {kind:<7} {brains.BRAIN_WORDS[kind]}")
        print_fn(f"     {'found' if ok else 'not found'}: {how}")
    while True:
        try:
            answer = input_fn(f"Type a number or a name [{suggested}]: ").strip().lower()
        except EOFError:
            # Nobody there to answer (on Windows an empty input claims to be a terminal).
            print_fn(f"\n  No answer, so {suggested}.")
            return suggested
        if not answer:
            return suggested
        if answer.isdigit() and 1 <= int(answer) <= len(brains.BRAIN_KINDS):
            return brains.BRAIN_KINDS[int(answer) - 1]
        if answer in brains.BRAIN_KINDS:
            return answer
        print_fn(f"  {answer!r} is not one of them.")


def cmd_init(argv, env=None):
    parser = argparse.ArgumentParser(prog="worthwatch init",
                                     description="Make worthwatch's folders and starter files, "
                                                 "and choose the brain that does the judging.")
    parser.add_argument("--brain", choices=brains.BRAIN_KINDS,
                        help="the brain to use, without being asked")
    args = parser.parse_args(argv)
    config, data, output = _settings.resolve_dirs(env)
    for folder in (config, data, output):
        folder.mkdir(parents=True, exist_ok=True)
    made, kept = [], []
    for name in EXAMPLE_FILES:
        target = config / name
        if target.exists():
            kept.append(name)
        else:
            shutil.copyfile(_settings.EXAMPLES_DIR / name, target)
            made.append(name)
    settings_path = config / "settings.toml"
    if args.brain:
        kind, why = args.brain, "as you asked"
    elif "settings.toml" in kept and recorded_brain(settings_path):
        kind, why = recorded_brain(settings_path), "already in your settings"
    else:
        found = brains.detect()
        if sys.stdin is not None and sys.stdin.isatty():
            kind, why = ask_brain(found), "your choice"
        else:
            kind = choose_brain(found)
            why = f"the first one this machine has ({found[kind][1]})"
    if recorded_brain(settings_path) != kind:
        set_brain(settings_path, kind)
    print(f"worthwatch's folders:\n  config  {config}\n  data    {data}\n  output  {output}")
    if made:
        print(f"Wrote {', '.join(made)} into the config folder.")
    if kept:
        print(f"Already there, so left alone: {', '.join(kept)}.")
    print(f"brain: {kind} ({why}). Change it any time with `worthwatch init --brain NAME`.")
    print("\nNext:\n"
          f"  1. Open {config / 'context.md'} and replace the example person with yourself: "
          "your projects, what you have tried, what you have decided. Every verdict is judged "
          "against this file.\n"
          f"  2. Put the channels you follow in {config / 'channels.txt'}, one link a line.\n"
          "  3. Run `worthwatch doctor`. It says in plain words what is still missing.\n"
          "  4. Run `worthwatch --channels` to check those channels for new videos.")
    return 0
