"""core.py is split into modules only where the split cannot change what runs.

A name may leave core.py for a module of its own only when nothing replaces it at run time: no
test swaps it on core (ww.X = ..., setattr), and core never rebinds it (apply_profile's
g["X"] = ..., apply_paths' tables, a global statement). A moved function calls its neighbours
inside its own module, so a test that swapped one of them on core would no longer reach those
calls, and a setting rebound on core would never be seen there. core imports back every moved
name it uses, so core.X keeps working for the rest of the code.

For each module split out so far (summaryrules.py) this checks that:
  - it never imports core (a leaf module: no import cycle, and nothing it reads can be rebound
    under it);
  - core no longer defines any of its names itself (moved, not copied);
  - every name core imports from it is the very same object on core;
  - it binds and reads none of the names core rebinds at run time;
  - no test swaps one of its names on core.

No YouTube request, no model call.

Watch it go red: add `from . import core` to the top of src/worthwatch/summaryrules.py, or
define `PROJECTS = ()` in it, and run this file.
"""
import ast
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _tmpsandbox  # src on the path, the network refused

DIR = _tmpsandbox.DIR
PKG = _tmpsandbox.SRC / "worthwatch"
MODULES = ("summaryrules",)
fails = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def bound_names(tree):
    """Top-level names a module defines itself (functions, classes, assignments)."""
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out |= {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
    return out


def imports_core(tree):
    """Line numbers of every import of core in a module."""
    lines = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            module, names = n.module or "", [a.name for a in n.names]
            if ((n.level and module.split(".")[0] == "core") or (n.level and not module and "core" in names)
                    or module == "worthwatch.core" or (module == "worthwatch" and "core" in names)):
                lines.append(n.lineno)
        elif isinstance(n, ast.Import) and any(a.name == "worthwatch.core" for a in n.names):
            lines.append(n.lineno)
    return lines


core_src = (PKG / "core.py").read_text(encoding="utf-8")
core_tree = ast.parse(core_src)
rebound = set(re.findall(r'\bg\["([A-Za-z_][A-Za-z0-9_]*)"\]\s*=', core_src))
rebound |= set(re.findall(r'globals\(\)\["([A-Za-z_][A-Za-z0-9_]*)"\]\s*=', core_src))
rebound |= {name for node in ast.walk(core_tree) if isinstance(node, ast.Global) for name in node.names}
for table in ("_CONFIG_FILES", "_DATA_FILES", "_OUTPUT_FILES"):
    m = re.search(rf"^{table} = \{{(.*?)\}}", core_src, re.S | re.M)
    if m:
        rebound |= set(re.findall(r'"([A-Z_]+)":', m.group(1)))
rebound |= {"CONFIG_DIR", "DATA_DIR", "OUTPUT_DIR"}
tests_text = {p.name: p.read_text(encoding="utf-8") for p in sorted((DIR / "tests").glob("*.py"))
              if p.name != Path(__file__).name}

from worthwatch import core  # noqa: E402

print("=== what core rebinds at run time ===")
check({"PROJECTS", "PEOPLE_IN_CONTEXT", "OUTPUT_DIR", "RECORDS_PATH"} <= rebound,
      f"read from core.py itself: {len(rebound)} names, PROJECTS and OUTPUT_DIR among them")

for module_name in MODULES:
    print(f"\n=== {module_name}.py ===")
    path = PKG / f"{module_name}.py"
    check(path.is_file(), f"src/worthwatch/{module_name}.py exists")
    if not path.is_file():
        continue
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = imports_core(tree)
    check(not found, f"it never imports core (import lines: {found or 'none'})")
    names = bound_names(tree)
    check(len(names) > 0, f"it defines {len(names)} names")
    twice = sorted(names & bound_names(core_tree))
    check(not twice, f"core.py defines none of them itself any more ({twice or 'none'})")
    imported = sorted({a.asname or a.name for n in core_tree.body if isinstance(n, ast.ImportFrom)
                       and n.level == 1 and n.module == module_name for a in n.names})
    module = __import__(f"worthwatch.{module_name}", fromlist=["_"])
    same = [n for n in imported if getattr(core, n, None) is getattr(module, n, object())]
    check(bool(imported) and same == imported,
          f"every name core imports from it is the same object on core ({len(same)} of "
          f"{len(imported)})")
    reads = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    check(not (names & rebound), f"it binds none of the names core rebinds at run time "
                                 f"({sorted(names & rebound) or 'none'})")
    check(not (reads & rebound), f"and reads none of them ({sorted(reads & rebound) or 'none'})")
    swapped = sorted({f"{t}: {n}" for t, text in tests_text.items() for n in names
                      if re.search(rf"\bww\.{re.escape(n)}\s*=(?!=)", text)
                      or re.search(rf"[\"']{re.escape(n)}[\"']", text)})
    check(not swapped, f"no test swaps one of its names on core ({swapped or 'none'})")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILURES: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
