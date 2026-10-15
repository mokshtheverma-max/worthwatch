"""The em-dash check's own test, on the files it has to catch.

The check is scripts/check_emdash.py, run by .githooks/pre-commit and by CI. Three files, and
the third is the one that matters:

  cp1252, byte 0x97   must be REFUSED as unreadable, by name
  UTF-8, U+2014       must be reported as a violation, with its line
  a clean file        must pass, because a checker that fails on everything is as useless as
                      one that fails on nothing

A decode with errors="replace" is run over the same cp1252 file, so the test shows the defect a
lenient reader has rather than describing it.

Watch it go red: make read_text decode with errors="replace" in a copy of
scripts/check_emdash.py and run this file against the copy.
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DIR / "scripts"))
import check_emdash as gate

fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


EM = chr(0x2014)
LINE = f"a line with an {EM} in it\n"
CLEAN = "a line with a comma, in it\n"

tmp = Path(tempfile.mkdtemp(prefix="ww-emdash-"))
cp1252_file = tmp / "cp1252.txt"
utf8_file = tmp / "utf8.txt"
clean_file = tmp / "clean.txt"
cp1252_file.write_bytes(LINE.encode("cp1252"))
utf8_file.write_bytes(LINE.encode("utf-8"))
clean_file.write_bytes(CLEAN.encode("utf-8"))

print("=== A. the planted controls really are what they claim to be ===")
check(cp1252_file.read_bytes() == b"a line with an \x97 in it\n",
      "the cp1252 control carries the em-dash as the single byte 0x97")
check(b"\xe2\x80\x94" in utf8_file.read_bytes(),
      "the UTF-8 control carries it as the three bytes e2 80 94")
check(EM not in clean_file.read_text(encoding="utf-8"), "and the clean control has none")

print("\n=== B. a cp1252 em-dash is refused, and named ===")
text, why = gate.read_text(cp1252_file)
check(text is None and why is not None,
      "the check refuses to speak for a file whose bytes are not UTF-8")
check(why and "0x97" in why and "U+2014" in why,
      f"and says which byte and what it could not look for: {why}")

print("\n=== C. and this is the decode that would let it through ===")
lenient = cp1252_file.read_bytes().decode("utf-8", errors="replace")
check(EM not in lenient, "with errors=\"replace\" the em-dash is absent from the decoded text")
check(gate.violations_in(lenient, "cp1252.txt") == [],
      "so a lenient reader finds no violation in a file with an em-dash in it")
check(chr(0xFFFD) in lenient, "the byte became U+FFFD, which is not the character banned")

print("\n=== D. a UTF-8 em-dash is a violation ===")
text, why = gate.read_text(utf8_file)
check(why is None and text is not None, "the file reads")
check(gate.violations_in(text, "utf8.txt") == ["utf8.txt:1"],
      "and the em-dash on line 1 is reported with its line number")

print("\n=== E. CONTROL: a clean file passes ===")
text, why = gate.read_text(clean_file)
check(why is None and gate.violations_in(text, "clean.txt") == [],
      "a file with a comma where an em-dash would go is clean")
check(gate.detector_reproduces_the_defect(),
      "and the check's own control fires on a planted em-dash and not on a clean line")

print("\n=== F. the check is wired to the commit, and the repository passes it ===")
hook = (DIR / ".githooks" / "pre-commit").read_text(encoding="utf-8")
check("check_emdash.py" in hook and "--staged" in hook,
      "the pre-commit hook runs the staged scan")
real_out, sys.stdout = sys.stdout, io.StringIO()
try:
    code = gate.main(["check_emdash.py", "--tree"])
    said = sys.stdout.getvalue()
finally:
    sys.stdout = real_out
check(code == 0, f"the whole repository is clean ({said.strip()[:200]})")

for p in (cp1252_file, utf8_file, clean_file):
    p.unlink()
tmp.rmdir()

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
