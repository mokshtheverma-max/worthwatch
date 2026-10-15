"""What worthwatch knows about you, and where it keeps things.

Two small TOML files hold everything personal, and both live in your config folder:

  settings.toml  your projects, the names to keep out of labels, your priority channels, which
                 brain to use, and the folders. Every key is optional.
  rules.toml     your own checks, in code, on every write-up: standing decisions, things already
                 done or decided, generic advice, parts of how you work, and ideas you killed.

Nothing here is read at import time. core.apply_profile(load_profile(...)) and
core.apply_paths(...) are called by the command line and the MCP server when they start, and by
every test suite with the example profile of an invented person.
"""
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

APP = "worthwatch"
ROAD_NOT_TAKEN = "a road not taken"
THIS_TOOL = "this tool"
EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


class SettingsError(ValueError):
    """A settings or rules file that is there and cannot be used, said in plain words."""


def read_toml(path):
    """The file's contents as a dict, {} when it does not exist, SettingsError when it cannot
    be read."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise SettingsError(f"{path} cannot be read: {e}. Fix that line, or delete the file and "
                            f"run `worthwatch init` again.") from None


def phrase_pattern(text):
    """A rule with no pattern of its own matches its name as a phrase: whole words, any case,
    any run of spaces or hyphens between them."""
    words = [re.escape(w) for w in re.findall(r"[\w']+", text or "")]
    if not words:
        raise SettingsError(f"a rule needs a name with at least one word in it, not {text!r}")
    return re.compile(r"(?<!\w)" + r"[\s-]+".join(words) + r"(?!\w)", re.I)


def compile_pattern(rule, what):
    """The rule's pattern compiled, case-insensitive, or its name as a phrase. `patterns`, a
    list, is the same as one pattern that matches when any of them does."""
    raw = rule.get("patterns")
    if raw is not None:
        parts = [p.strip() for p in _strings(raw, f"the {what} rule's patterns")]
        raw = "|".join(f"(?:{p})" for p in parts)
    else:
        raw = (rule.get("pattern") or "").strip()
    if not raw:
        return phrase_pattern(rule.get("name"))
    try:
        return re.compile(raw, re.I)
    except re.error as e:
        raise SettingsError(f"the {what} rule {rule.get('name')!r} has a pattern that is not a "
                            f"valid regular expression ({e})") from None


def _strings(value, what):
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise SettingsError(f"{what} should be a list of words in quotes, like [\"a\", \"b\"]")
    return tuple(v.strip() for v in value if v.strip())


@dataclass
class Profile:
    """Everything the checks and the prompts need to know about the person reading."""
    reader: str = "you"
    projects: tuple = ()
    work_label: str = "how I work"
    work_label_daily_cap: int = 3
    aliases: tuple = ()                    # (alias in lower case, canonical label)
    people: tuple = ()                     # lower case
    assets: tuple = ()
    sign_word: str = ""
    brain: str = "agent"                   # see brains.py: agent, claude, codex, api, ollama
    js_runtime: str = ""                   # empty: yt-dlp's default, Deno
    priority_channels: tuple = ()
    priority_daily_reserve: int = 3
    channel_daily_cap: int = 3
    on_topic_keywords: tuple = ()
    technical_keywords: tuple = ()
    discover_queries: tuple = ()
    discover_candidates: tuple = ()
    whisper_vocabulary: str = ""
    decisions: tuple = ()                  # (name, pattern, unless pattern or None)
    decision_exempt: dict = field(default_factory=dict)   # name -> projects it does not hold for
    decision_only: dict = field(default_factory=dict)     # name -> the only projects it holds for
    done: tuple = ()                       # (name, project or None, pattern)
    generic: tuple = ()                    # (name, project or None, pattern)
    work_parts: tuple = ()                 # (name, pattern)
    killed: tuple = ()                     # (name, pattern)
    killed_notes: dict = field(default_factory=dict)      # name -> (date or None, because or None)

    @property
    def labels(self):
        """The closed list an item of section 6 may name: your projects, the label for how you
        work, this tool, and a road not taken. In that order, which is also the header's."""
        out = list(self.projects)
        if self.work_label:
            out.append(self.work_label)
        out += [THIS_TOOL, ROAD_NOT_TAKEN]
        return tuple(dict.fromkeys(out))

    @property
    def alias_table(self):
        """(alias, canonical) for every label, longest alias first: each label is its own
        alias, "road not taken" is one more, and then your own."""
        table = {label.lower(): label for label in self.labels}
        table["road not taken"] = ROAD_NOT_TAKEN
        for alias, canonical in self.aliases:
            table[alias] = canonical
        return tuple(sorted(table.items(), key=lambda kv: (-len(kv[0]), kv[0])))


def profile_from(settings, rules):
    """A Profile from the two files' contents. SettingsError says what is wrong in plain
    words."""
    you = settings.get("you") or {}
    channels = settings.get("channels") or {}
    topics = settings.get("topics") or {}
    discover = settings.get("discover") or {}
    whisper = settings.get("whisper") or {}
    p = Profile()
    p.reader = (you.get("name") or "").strip() or "you"
    p.projects = _strings(you.get("projects"), "[you] projects")
    if "work_label" in you:
        p.work_label = (you.get("work_label") or "").strip()
    p.work_label_daily_cap = int(you.get("work_label_daily_cap", 3))
    p.people = tuple(n.lower() for n in _strings(you.get("people"), "[you] people"))
    p.assets = _strings(you.get("assets"), "[you] assets")
    p.sign_word = (you.get("sign_word") or "").strip()
    brain = settings.get("brain") or {}
    kind = (brain.get("kind") or "agent").strip().lower()
    kinds = ("agent", "claude", "codex", "api", "ollama")
    if kind not in kinds:
        raise SettingsError(f"[brain] kind = \"{kind}\" is not one of {', '.join(kinds)}. Run "
                            f"`worthwatch init --brain NAME` to choose one.")
    p.brain = kind
    p.js_runtime = ((settings.get("youtube") or {}).get("js_runtime") or "").strip().lower()
    labels = set(p.labels)
    aliases = you.get("aliases") or {}
    if not isinstance(aliases, dict):
        raise SettingsError("[you] aliases should look like { \"ledger leaf\" = \"Ledgerleaf\" }")
    pairs = []
    for alias, canonical in aliases.items():
        if canonical not in labels:
            raise SettingsError(f"[you] aliases: {alias!r} points at {canonical!r}, which is not "
                                f"one of your projects ({', '.join(sorted(labels))})")
        pairs.append((alias.strip().lower(), canonical))
    p.aliases = tuple(pairs)
    p.priority_channels = _strings(channels.get("priority"), "[channels] priority")
    p.priority_daily_reserve = int(channels.get("priority_daily_reserve", 3))
    p.channel_daily_cap = int(channels.get("daily_cap_per_channel", 3))
    p.on_topic_keywords = tuple(k.lower() for k in _strings(
        topics.get("on_topic_keywords"), "[topics] on_topic_keywords"))
    p.technical_keywords = tuple(k.lower() for k in _strings(
        topics.get("technical_keywords"), "[topics] technical_keywords"))
    p.discover_queries = _strings(discover.get("queries"), "[discover] queries")
    p.discover_candidates = _strings(discover.get("candidates"), "[discover] candidates")
    p.whisper_vocabulary = (whisper.get("vocabulary") or "").strip()

    decisions, exempt, only = [], {}, {}
    for rule in rules.get("decision") or []:
        name = (rule.get("name") or "").strip().lower()
        unless = (rule.get("unless") or "").strip()
        decisions.append((name, compile_pattern(rule, "decision"),
                          re.compile(unless, re.I) if unless else None))
        for key, into in (("exempt", exempt), ("only", only)):
            projects = _strings(rule.get(key), f"the decision {name!r}'s {key}")
            for project in projects:
                if project not in labels:
                    raise SettingsError(f"the decision {name!r} names {project!r} under {key}, "
                                        f"which is not one of your projects")
            if projects:
                into[name] = projects
    p.decisions, p.decision_exempt, p.decision_only = tuple(decisions), exempt, only

    def scoped(kind):
        out = []
        for rule in rules.get(kind) or []:
            name = (rule.get("name") or "").strip().lower()
            project = (rule.get("project") or "").strip() or None
            if project and project not in labels:
                raise SettingsError(f"the {kind} rule {name!r} names {project!r}, which is not "
                                    f"one of your projects")
            out.append((name, project, compile_pattern(rule, kind)))
        return tuple(out)

    p.done, p.generic = scoped("done"), scoped("generic")
    p.work_parts = tuple(((r.get("name") or "").strip().lower(), compile_pattern(r, "work"))
                         for r in rules.get("work") or [])
    killed, notes = [], {}
    for rule in rules.get("killed") or []:
        name = (rule.get("name") or "").strip().lower()
        killed.append((name, compile_pattern(rule, "killed")))
        notes[name] = ((str(rule.get("date")).strip() or None) if rule.get("date") else None,
                       (rule.get("because") or "").strip() or None)
    p.killed, p.killed_notes = tuple(killed), notes
    return p


def load_profile(config_dir):
    """The Profile in one config folder (settings.toml and rules.toml), defaults for anything
    missing."""
    config_dir = Path(config_dir)
    return profile_from(read_toml(config_dir / "settings.toml"),
                        read_toml(config_dir / "rules.toml"))


def example_profile():
    """The invented person's profile, shipped with the package for `worthwatch init` and the
    tests."""
    return load_profile(EXAMPLES_DIR)


def default_dirs(platform=None, env=None, home=None):
    """(config, data, output) when nothing is set, by operating system.

      Windows  config in %APPDATA%\\worthwatch, data in %LOCALAPPDATA%\\worthwatch
      macOS    both in ~/Library/Application Support/worthwatch (data in its data folder)
      Linux    $XDG_CONFIG_HOME/worthwatch (~/.config) and $XDG_DATA_HOME/worthwatch
               (~/.local/share); a relative XDG folder is ignored, as the XDG rules say
      and the output, the digests you read, in Documents/worthwatch on every system."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    output = home / "Documents" / APP
    if platform.startswith("win"):
        roaming = Path(env.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return roaming / APP, local / APP, output
    if platform == "darwin":
        support = home / "Library" / "Application Support" / APP
        return support, support / "data", output

    def xdg(name, fallback):
        value = env.get(name) or ""
        return Path(value) if value and PurePosixPath(value).is_absolute() else fallback

    return (xdg("XDG_CONFIG_HOME", home / ".config") / APP,
            xdg("XDG_DATA_HOME", home / ".local" / "share") / APP, output)


def resolve_dirs(env=None, settings=None, platform=None, home=None):
    """(config, data, output) folders. WORTHWATCH_HOME puts all three under one folder; each
    has its own environment variable too; settings.toml's [paths] can move data and output.
    Otherwise default_dirs() for this operating system."""
    env = os.environ if env is None else env
    portable = env.get("WORTHWATCH_HOME")
    if portable:
        base = Path(portable).expanduser()
        config, data, output = base, base / "data", base / "output"
    else:
        config, data, output = default_dirs(platform, env, home)
    config = Path(env.get("WORTHWATCH_CONFIG_DIR") or config).expanduser()
    paths = (settings or {}).get("paths") or {}
    data = Path(env.get("WORTHWATCH_DATA_DIR") or paths.get("data") or data).expanduser()
    output = Path(env.get("WORTHWATCH_OUTPUT_DIR") or paths.get("output") or output).expanduser()
    return config, data, output
