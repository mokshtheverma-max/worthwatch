"""The sandbox every suite uses: every path worthwatch writes to is pointed inside one fresh
folder under the OS temp folder, removed when the suite ends, and the invented example person
(src/worthwatch/examples) is the profile every check reads.

A suite may write only inside its temp folder. The helper refuses to hand back a sandbox while
any path the module can write to still points anywhere else, so a path constant added later is
caught the same way: it is neither on the read-only list below nor inside the sandbox, and the
suite stops before it runs.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent
SRC = DIR / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from worthwatch import settings as _settings  # noqa: E402

EXAMPLES = _settings.EXAMPLES_DIR
# Read, never written, by anything a suite drives.
READ_ONLY = {"WHISPER_MODEL_DIR"}


def no_network():
    """Refuse every connection that leaves this machine, for the rest of the process. No suite
    may reach YouTube, a model's API or anything else: a stub that was forgotten fails loudly
    here instead of spending a request. Connections to this machine (a suite's own fake
    server) still work."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and (host in ("localhost", "::1") or host.startswith("127.")):
            return real_connect(self, address)
        raise ConnectionRefusedError(f"a test tried to reach {host}; tests never use the network")

    socket.socket.connect = guarded


no_network()


def sandbox(ww, name, profile=True):
    """Point every path of the imported core module into a new temp folder, give it the
    example person's settings, rules and context file, and return that folder."""
    root = Path(tempfile.mkdtemp(prefix=f"ww-{name}-"))
    atexit.register(shutil.rmtree, root, True)
    config, data, output = root / "config", root / "data", root / "output"
    for folder in (config, data, output):
        folder.mkdir()
    for example in EXAMPLES.iterdir():
        if example.is_file():
            shutil.copyfile(example, config / example.name)
    # main() calls configure(), which reads the folders from the environment: point that at the
    # sandbox too, in this process only, so a suite that drives the command line stays inside.
    os.environ.update({"WORTHWATCH_HOME": str(root), "WORTHWATCH_CONFIG_DIR": str(config),
                       "WORTHWATCH_DATA_DIR": str(data), "WORTHWATCH_OUTPUT_DIR": str(output)})
    ww.apply_paths(config, data, output)
    ww.RAW_DIR.mkdir(parents=True, exist_ok=True)
    if profile:
        ww.apply_profile(_settings.load_profile(config))
    for attr, value in sorted(vars(ww).items()):
        if isinstance(value, Path) and attr not in READ_ONLY:
            try:
                value.resolve().relative_to(root.resolve())
            except ValueError:
                raise SystemExit(f"_tmpsandbox: ww.{attr} still points at {value}, outside the "
                                 f"temp folder {root}; refusing to run") from None
    ww._RUN_CLOCK["deadline"] = None
    return root


def import_core():
    """The core module, imported from the source tree rather than from anything installed."""
    from worthwatch import core
    return core
