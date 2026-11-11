"""Windows argv regression: paths in kanban commands must survive the host shell.

Run: python3 tests/test_kanban_windows_paths.py

Every board call leaves kanban.py as ONE shell string handed to
``ctx.dispatch_tool("terminal")``. On Windows the host shell collapses each
doubled path separator to a single one before the CLI sees the argument, so a
parent-card ``--metadata`` payload quoted from a raw Windows path arrives with
an unescaped separator and the kanban parser rejects it:

    kanban: --metadata: Invalid \\escape: line 1 column 33 (char 32)

The FIGHT parent card then never reaches ``done`` and the bout records an
error on its last write. ``kanban._cli_path`` normalises paths to forward
slashes, which no shell layer rewrites.

The dispatcher stub below models that shell layer (one collapse pass over the
whole command) so the assertions fail if the normalisation is ever removed.
Backslashes are built from ``chr(92)`` so this file stays readable in any
editor and patch tool.
"""

import atexit
import json
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TEST_HOME = tempfile.mkdtemp(prefix="kombat-test-home-")
os.environ["HERMES_HOME"] = _TEST_HOME
atexit.register(shutil.rmtree, _TEST_HOME, True)

import kanban  # noqa: E402

BS = chr(92)
WIN_PATH = "C:" + BS.join(["", "Users", "me", "plugin-data", "reports", "fight.html"])
POSIX_PATH = "C:/Users/me/plugin-data/reports/fight.html"


def host_shell_layer(command: str) -> str:
    """Model the Windows host shell: every doubled separator becomes one."""
    return str(command).replace(BS * 2, BS)


class ShellStub:
    """Kanban dispatcher stub that applies the host shell layer first."""

    def __init__(self):
        self.commands = []
        self.metadata = None
        self.completed = []
        self.attached = []

    def __call__(self, command):
        """Mirror kanban._dispatch: returns (ok, output_text)."""
        raw_command = str(command or "")
        self.commands.append(raw_command)
        tokens = shlex.split(host_shell_layer(raw_command))
        assert tokens[:2] == ["hermes", "kanban"], raw_command
        tokens = tokens[2:]
        if tokens[:1] == ["--board"]:
            tokens = tokens[2:]
        if not tokens:
            return False, "{}"
        verb, args = tokens[0], tokens[1:]

        if verb == "complete":
            if "--metadata" in args:
                raw = args[args.index("--metadata") + 1]
                try:
                    self.metadata = json.loads(raw)
                except json.JSONDecodeError as exc:
                    return False, f"kanban: --metadata: {exc}"
            self.completed.append(args[0])
            return True, f"Completed {args[0]}"

        if verb == "attach":
            self.attached.append(args[1] if len(args) > 1 else "")
            return True, f"Attached to {args[0]}"

        if verb == "show":
            return True, json.dumps({"task": {"id": args[0], "status": "ready"}})

        return True, "ok"



ROUNDS_OUT = [
    {
        "id": "R1",
        "name": "Warm-up",
        "points": 1,
        "points_a": 0.5,
        "points_b": 0.5,
        "tasks": [
            {
                "id": "T1",
                "a": {"score": 1, "max": 1},
                "b": {"score": 1, "max": 1},
                "points_a": 0.5,
                "points_b": 0.5,
                "ko": False,
                "excluded": None,
            }
        ],
    }
]


def test_cli_path_normalises_windows_paths():
    assert kanban._cli_path(WIN_PATH) == POSIX_PATH
    assert BS not in kanban._cli_path(WIN_PATH)
    # Already-posix and Path objects are passed through unchanged.
    assert kanban._cli_path(POSIX_PATH) == POSIX_PATH
    assert kanban._cli_path(Path(WIN_PATH)) == POSIX_PATH


def test_raw_windows_path_metadata_is_rejected_by_the_shell_layer():
    """The old payload form is what produced the reported error."""
    old_payload = json.dumps({"winner": "draw", "report": WIN_PATH})
    assert BS * 2 in old_payload  # json escapes the separator once
    try:
        json.loads(host_shell_layer(old_payload))
    except json.JSONDecodeError:
        return
    raise AssertionError("expected the collapsed payload to be invalid JSON")


def test_finish_completes_parent_with_parseable_metadata():
    stub = ShellStub()
    original = kanban._dispatch
    kanban._dispatch = stub
    try:
        board = kanban.Scoreboard("fighter/a", "fighter/b", {"rounds": []})
        board.ids = {"parent": "t_parent", "rounds": {"R1": "t_round1"}}
        board.finish(ROUNDS_OUT, "draw", WIN_PATH)
    finally:
        kanban._dispatch = original

    assert board.errors == [], board.errors
    assert "t_parent" in stub.completed, stub.completed
    assert stub.metadata == {"winner": "draw", "report": POSIX_PATH}, stub.metadata
    assert stub.attached and stub.attached[0] == POSIX_PATH, stub.attached
    for command in stub.commands:
        assert BS not in command, command


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            else:
                print(f"ok   {name}")
    print(f"\n{'FAILED' if failures else 'PASSED'} ({failures} failure(s))")
    raise SystemExit(1 if failures else 0)
