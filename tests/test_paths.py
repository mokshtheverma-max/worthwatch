"""Every suite tests the code beside it, and no file names anyone's home folder.

A suite that imports an installed copy of worthwatch instead of the source beside it proves
nothing about the source. And a full path into somebody's home folder in a file of this
repository is a privacy leak and a test that passes on one machine only. This fails while any
text file in the repository names a home folder (a drive's Users folder, /Users/<name> or
/home/<name>), and checks that the core module the suites import is the one in this checkout.

No YouTube request, no model call, nothing written.

Watch it go red: put a line naming a home folder into any file under tests/ and run this file.
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox
ww = _tmpsandbox.import_core()

DIR = Path(__file__).resolve().parent.parent
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


# A home folder with a real-looking name in it. A placeholder in angle brackets ("<name>",
# "<you>") is how documentation shows a path without naming anyone, so it does not count.
SEP = r"[\\/]+"
HOME = re.compile(r"(?:\b[A-Za-z]:" + SEP + r"Users|(?<![\w.])/Users|(?<![\w.])/home)" + SEP
                  + r"(?!<)[^\\/\s\"'<>|*?:]+", re.IGNORECASE)
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "build", "dist", "node_modules"}

print("A. no text file in the repository names a home folder")
named, read = [], 0
stack = [DIR]
while stack:
    for entry in stack.pop().iterdir():
        if entry.is_dir():
            if entry.name not in SKIP_DIRS:
                stack.append(entry)
            continue
        raw = entry.read_bytes()
        if b"\x00" in raw:
            continue
        read += 1
        text = raw.decode("utf-8", errors="replace")
        hits = [n for n, line in enumerate(text.splitlines(), 1) if HOME.search(line)]
        if hits:
            named.append(f"{entry.relative_to(DIR).as_posix()}:{hits[0]}")
check(read >= 20, f"the walk read the repository ({read} text files)")
check(not named, f"{len(named)} file(s) name a home folder"
      + (f": {', '.join(named[:8])}" if named else ""))

print("B. the control: the pattern finds a home folder and only a home folder")
drive = "C:"
check(bool(HOME.search(drive + "\\Users\\someone\\Documents\\notes.txt")),
      "a Windows path into a home folder matches")
check(bool(HOME.search("/" + "Users/someone/Library/x")), "a macOS home folder matches")
check(bool(HOME.search("/" + "home/someone/.config/x")), "a Linux home folder matches")
check(not HOME.search(drive + "\\Users\\<you>\\AppData\\Local\\worthwatch"),
      "a placeholder in angle brackets does not")
check(not HOME.search("https://example.com/home/page") and not HOME.search("~/.config/worthwatch"),
      "nor a web address with /home/ in it, nor a path that starts at ~")

print("C. the core module the suites import is the one in this checkout")
here = Path(ww.__file__).resolve()
check(DIR / "src" in here.parents, f"core is imported from {here.relative_to(DIR).as_posix()}")

print("D. the sandbox holds for the command line too")
# main() starts by calling configure(), which reads the folders from the environment. Inside a
# suite that must land in the sandbox, never in the real home folder of whoever runs the tests.
root = _tmpsandbox.sandbox(ww, "paths")
ww.configure()
outside = []
for attr, value in sorted(vars(ww).items()):
    if isinstance(value, Path) and attr not in _tmpsandbox.READ_ONLY:
        try:
            value.resolve().relative_to(root.resolve())
        except ValueError:
            outside.append(attr)
check(not outside, f"after configure(), every path is still inside the sandbox "
                   f"({len(outside)} outside: {outside[:5]})")
check(ww.PROJECTS[:2] == ("Ledgerleaf", "Tidepool"),
      f"and the settings it read are the example person's ({ww.PROJECTS[:2]})")

print()
if fails:
    print(f"{len(fails)} FAILURES: " + "; ".join(fails))
    sys.exit(1)
print("ALL PASS")
