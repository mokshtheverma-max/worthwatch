"""Where worthwatch keeps your files, on each operating system.

Three folders: config (your settings, rules, context file, channels and topics), data (the
tool's own records, transcripts, logs and caches) and output (the digests and write-ups you
read). Each has a sensible default on Windows, macOS and Linux; WORTHWATCH_HOME puts all three
in one folder; each has its own environment variable; and settings.toml [paths] can move data
and output. An environment variable beats the settings file, which beats the default.

Every system is checked by naming it, so Linux's answers are checked on Windows too. Nothing is
written.

Watch it go red: make default_dirs return the same folder on every system (the old ~/worthwatch)
in a copy of src/worthwatch/settings.py and run this file against the copy.
"""
import io
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox  # noqa: F401 - puts src/ on the path and refuses the network
from worthwatch import settings as s

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def same(a, b):
    """Two paths are the same path, whatever separators this machine prints them with."""
    return Path(a).as_posix() == Path(b).as_posix()


# Invented home folders, built from parts so this file names no real-looking home folder.
print("=== A. Windows ===")
home = Path("H:/profiles/someone")
env = {"APPDATA": "H:/profiles/someone/AppData/Roaming",
       "LOCALAPPDATA": "H:/profiles/someone/AppData/Local"}
c, d, o = s.default_dirs("win32", env, home)
check(same(c, "H:/profiles/someone/AppData/Roaming/worthwatch"), f"config in Roaming AppData ({c})")
check(same(d, "H:/profiles/someone/AppData/Local/worthwatch"), f"data in Local AppData ({d})")
check(same(o, "H:/profiles/someone/Documents/worthwatch"), f"output in Documents ({o})")
c2, d2, _o = s.default_dirs("win32", {}, home)
check(same(c2, "H:/profiles/someone/AppData/Roaming/worthwatch")
      and same(d2, "H:/profiles/someone/AppData/Local/worthwatch"),
      "and the same places when the APPDATA variables are not set")

print("\n=== B. macOS ===")
home = Path("/" + "Users/someone")
c, d, o = s.default_dirs("darwin", {}, home)
check(same(c, home / "Library/Application Support/worthwatch"), f"config in Application Support ({c})")
check(same(d, home / "Library/Application Support/worthwatch/data"), f"data beside it ({d})")
check(same(o, home / "Documents/worthwatch"), f"output in Documents ({o})")

print("\n=== C. Linux ===")
home = Path("/" + "home/someone")
c, d, o = s.default_dirs("linux", {}, home)
check(same(c, home / ".config/worthwatch"), f"config in ~/.config ({c})")
check(same(d, home / ".local/share/worthwatch"), f"data in ~/.local/share ({d})")
check(same(o, home / "Documents/worthwatch"), f"output in Documents ({o})")
c, d, _o = s.default_dirs("linux", {"XDG_CONFIG_HOME": "/xdg/config", "XDG_DATA_HOME": "/xdg/data"},
                          home)
check(same(c, "/xdg/config/worthwatch") and same(d, "/xdg/data/worthwatch"),
      "and the XDG folders when they are set")
c, _d, _o = s.default_dirs("linux", {"XDG_CONFIG_HOME": "relative/path"}, home)
check(same(c, home / ".config/worthwatch"),
      "a relative XDG folder is ignored, as the XDG rules say")

print("\n=== D. WORTHWATCH_HOME: one portable folder ===")
c, d, o = s.resolve_dirs({"WORTHWATCH_HOME": "/tmp/ww-portable"}, platform="linux",
                         home=Path("/" + "home/someone"))
check(same(c, "/tmp/ww-portable") and same(d, "/tmp/ww-portable/data")
      and same(o, "/tmp/ww-portable/output"), f"all three under it ({c}, {d}, {o})")

print("\n=== E. one variable per folder, and settings.toml [paths] ===")
env = {"WORTHWATCH_CONFIG_DIR": "/c1", "WORTHWATCH_DATA_DIR": "/d1", "WORTHWATCH_OUTPUT_DIR": "/o1"}
c, d, o = s.resolve_dirs(env, platform="linux", home=Path("/" + "home/someone"))
check(same(c, "/c1") and same(d, "/d1") and same(o, "/o1"), "each variable moves its folder")
paths = {"paths": {"data": "/d2", "output": "/o2"}}
c, d, o = s.resolve_dirs({}, paths, platform="linux", home=Path("/" + "home/someone"))
check(same(d, "/d2") and same(o, "/o2"), "settings.toml moves data and output")
check(same(c, Path("/" + "home/someone") / ".config/worthwatch"),
      "but not config, which is where settings.toml lives")
c, d, o = s.resolve_dirs({"WORTHWATCH_DATA_DIR": "/d1"}, paths, platform="linux",
                         home=Path("/" + "home/someone"))
check(same(d, "/d1") and same(o, "/o2"), "a variable beats the settings file")

print("\n=== F. this machine ===")
c, d, o = s.resolve_dirs({})
check(all(p.is_absolute() and "worthwatch" in p.parts for p in (c, d, o)),
      f"on this machine every default is an absolute path to a worthwatch folder ({c}, {d}, {o})")
check(c != d or d != o, "and the three are not all one folder")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
