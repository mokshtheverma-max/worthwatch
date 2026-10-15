"""Blocking em-dash check: exit 1 on any U+2014 in a text file of this repository.

    python scripts/check_emdash.py --staged   the files staged for the commit being made
    python scripts/check_emdash.py --tree     every file in the repository

The project writes plain sentences with commas, colons and full stops, and this keeps it that
way. Comments count: the ban is on the file, not on what runs.

Three things make the check trustworthy:

  any file that is not binary is read   A whitelist of extensions misses a hook script with no
                                        extension. A file is binary when it holds a NUL byte,
                                        the same test git uses.
  the decode is strict                  An em-dash saved as cp1252 is the single byte 0x97,
                                        which is not UTF-8. Decoding with errors="replace"
                                        turns it into U+FFFD and reports a clean file with an
                                        em-dash in it. So a file that is not UTF-8 fails, by
                                        name, instead of being read as best it can be.
  a control before every scan           The detector must find a planted em-dash and pass a
                                        clean line before it may scan, and --tree must read at
                                        least SCAN_FLOOR files. A scan that scanned nothing
                                        does not get to say "clean".
"""
import subprocess
import sys
from pathlib import Path

EM_DASH = chr(0x2014)
ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "build", "dist", "node_modules",
             ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SCAN_FLOOR = 20


def violations_in(text, rel):
    return [f"{rel}:{i + 1}" for i, line in enumerate(text.split("\n")) if EM_DASH in line]


def read_text(path):
    """(text, None) for a text file, (None, None) for a binary one, or (None, why) for a file
    this check cannot read faithfully."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return None, f"could not be read: {e.__class__.__name__}"
    if b"\x00" in raw:
        return None, None
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as e:
        return None, (f"is not valid UTF-8 (byte 0x{raw[e.start]:02x} at offset {e.start}), "
                      f"so U+2014 cannot be looked for in it")


def detector_reproduces_the_defect():
    """The control: a planted em-dash is found, and a clean line is not."""
    return (len(violations_in(f"a line with an {EM_DASH} in it", "control")) == 1
            and violations_in("a line with a comma, in it", "control") == [])


def staged_files():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    if out.returncode != 0:
        print(f"FAILED: could not ask git what is staged: {(out.stderr or '').strip()}")
        sys.exit(1)
    return [l.strip() for l in out.stdout.splitlines() if l.strip()]


def tree_files():
    found, stack = [], [ROOT]
    while stack:
        for entry in stack.pop().iterdir():
            if entry.is_dir():
                if entry.name not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                found.append(entry.relative_to(ROOT).as_posix())
    return sorted(found)


def main(argv):
    mode = argv[1] if len(argv) > 1 else "--staged"
    if mode not in ("--staged", "--tree"):
        print(f"usage: {Path(argv[0]).name} [--staged | --tree]")
        return 2
    if not detector_reproduces_the_defect():
        print("FAILED: the detector cannot find a planted U+2014, or fires on a line without "
              "one. Refusing to scan.")
        return 1
    candidates = staged_files() if mode == "--staged" else tree_files()
    violations, unreadable, scanned, binary = [], [], 0, 0
    for rel in candidates:
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        text, why = read_text(ROOT / rel)
        if text is None:
            if why is None:
                binary += 1
            else:
                unreadable.append(f"{rel} {why}")
            continue
        scanned += 1
        violations.extend(violations_in(text, rel))
    if unreadable:
        print(f"UNREADABLE AS TEXT ({len(unreadable)}), so this check cannot speak for them:")
        for u in unreadable:
            print("  " + u)
        print("  Save the file as UTF-8 and try again.")
        return 1
    if violations:
        print(f"EM-DASH FOUND ({len(violations)}):")
        for v in violations:
            print("  " + v)
        print(f"  (looked for U+2014 in {scanned} file(s))")
        return 1
    if mode == "--tree" and scanned < SCAN_FLOOR:
        print(f"FAILED: --tree read only {scanned} file(s), under its floor of {SCAN_FLOOR}.")
        return 1
    print(f"em-dash check: clean. Looked for U+2014 in {scanned} text file(s) of "
          f"{len(candidates)} offered ({binary} binary).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
