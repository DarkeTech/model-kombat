"""
Adversarial referee tests for engine_checks.py (SPEC §9 non-negotiables).
Run: python3 tests/test_referee_engine_checks.py
"""

import os
import sys
import tempfile
import time
import socket
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import engine_checks as ec


def run(check, answer, allow=False):
    return ec.score_check(check, answer, allow)


# ------------------------------------------------------------------ code_exec escape attempts

def _write_canary(path: Path, content: str = "canary"):
    path.write_text(content, encoding="utf-8")
    return path


def test_code_exec_write_outside_tempdir_via_open():
    """Write outside tempdir via open() - should be blocked by audit hook."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-open-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
def leak():
    with open({str(leak)!r}, 'w') as fh:
        fh.write('escaped')
    return True
```"""
    out = run(check, code, True)
    # Should raise PermissionError -> score 1.0 (expected raise)
    assert out["score"] == 1.0, f"write via open() allowed: {out}"
    assert not leak.exists(), f"sandbox leaked a write to {leak}"


def test_code_exec_write_outside_tempdir_via_os_open():
    """Write outside tempdir via os.open() - should be blocked."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-osopen-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
import os
def leak():
    fd = os.open({str(leak)!r}, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.write(fd, b'escaped')
    os.close(fd)
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"write via os.open() allowed: {out}"
    assert not leak.exists(), f"sandbox leaked a write to {leak}"


def test_code_exec_write_outside_tempdir_via_pathlib():
    """Write outside tempdir via pathlib.Path.write_text() - should be blocked."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-pathlib-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
from pathlib import Path
def leak():
    Path({str(leak)!r}).write_text('escaped')
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"write via pathlib allowed: {out}"
    assert not leak.exists(), f"sandbox leaked a write to {leak}"


def test_code_exec_write_outside_tempdir_via_shutil():
    """Write outside tempdir via shutil.copy2() - should be blocked."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-shutil-{os.getpid()}.txt"
    src = Path(tempfile.gettempdir()) / f"kombat-ref-src-{os.getpid()}.txt"
    src.write_text("source", encoding="utf-8")
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
import shutil
def leak():
    shutil.copy2({str(src)!r}, {str(leak)!r})
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"write via shutil allowed: {out}"
    assert not leak.exists(), f"sandbox leaked a write to {leak}"
    src.unlink(missing_ok=True)


def test_code_exec_winapi_native_copy_cannot_escape():
    """Native _winapi.CopyFile2 fires no audit events - must be unreachable.

    Regression for the Windows escape where shutil.copy2's CopyFile2 fast path
    copied a file out of the tempdir without ever raising an audit event.
    """
    if os.name != "nt":
        print("  [_winapi copy] skipped (Windows-only vector)")
        return
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-winapi-{os.getpid()}.txt"
    src = Path(tempfile.gettempdir()) / f"kombat-ref-src-winapi-{os.getpid()}.txt"
    src.write_text("source", encoding="utf-8")
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "AttributeError"}],
        "timeout_s": 5,
    }
    code = f"""```python
def leak():
    import _winapi
    _winapi.CopyFile2({str(src)!r}, {str(leak)!r}, 0)
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"_winapi.CopyFile2 reachable: {out}"
    assert not leak.exists(), f"sandbox leaked a native copy to {leak}"
    src.unlink(missing_ok=True)


def test_code_exec_write_outside_tempdir_via_tempfile():
    """Write outside tempdir via tempfile.mkstemp() with dir override - should be blocked."""
    # The harness sets tempfile.tempdir = ROOT, so mkstemp() without dir= should stay inside.
    # But if they pass dir= explicitly pointing outside, it should be blocked.
    leak_dir = Path(tempfile.gettempdir())
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
import tempfile, os
def leak():
    fd, path = tempfile.mkstemp(dir={str(leak_dir)!r})
    os.close(fd)
    with open(path, 'w') as fh:
        fh.write('escaped')
    return True
```"""
    out = run(check, code, True)
    # Note: tempfile.mkstemp(dir=...) may bypass the tempdir redirect.
    # The audit hook on 'open' should catch the subsequent write.
    assert out["score"] == 1.0, f"write via tempfile.mkstemp(dir=) allowed: {out}"


def test_code_exec_write_outside_tempdir_via_ctypes():
    """Write outside tempdir via ctypes (low-level) - may bypass Python audit hooks."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-ctypes-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "expect": False}],  # we expect it to fail or be blocked
        "timeout_s": 5,
    }
    code = f"""```python
import ctypes, ctypes.util
def leak():
    libc = ctypes.CDLL(ctypes.util.find_library('c'))
    fd = libc.open({str(leak)!r}, 0x200 | 0x400 | 0x1, 0o644)  # O_WRONLY|O_CREAT|O_TRUNC
    if fd >= 0:
        libc.write(fd, b'escaped', 7)
        libc.close(fd)
        return True
    return False
```"""
    out = run(check, code, True)
    # ctypes bypasses Python's audit hooks entirely - this is a known limitation
    # The test documents current behavior (may be PARTIALLY blocked by RLIMIT_FSIZE if file created)
    print(f"  [ctypes] result: {out['detail']}")
    # We don't assert score - just document
    if leak.exists():
        print(f"  [ctypes] LEAKED WRITE to {leak}")
        leak.unlink()


def test_code_exec_write_outside_tempdir_via_io_open():
    """Write outside tempdir via io.open() - should be blocked (same as open)."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-io-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
import io
def leak():
    with io.open({str(leak)!r}, 'w') as fh:
        fh.write('escaped')
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"write via io.open() allowed: {out}"
    assert not leak.exists(), f"sandbox leaked a write to {leak}"


def test_code_exec_write_outside_tempdir_via_os_rename():
    """Write outside tempdir via os.rename() - audit hook covers os.rename in _WRITE_EVENTS."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-rename-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
import os
def leak():
    with open('local.txt', 'w') as fh:
        fh.write('data')
    os.rename('local.txt', {str(leak)!r})
    return True
```"""
    out = run(check, code, True)
    # os.rename IS in _WRITE_EVENTS - should be blocked
    if out["score"] == 1.0:
        assert not leak.exists(), f"sandbox leaked a write via rename to {leak}"
    else:
        print(f"  [os.rename] NOT blocked: {out['detail']}")
        # Document current behavior
        if leak.exists():
            leak.unlink()


def test_code_exec_write_outside_tempdir_via_os_link():
    """Write outside tempdir via os.link() - audit hook covers os.link in _WRITE_EVENTS."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-link-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
import os
def leak():
    with open('local.txt', 'w') as fh:
        fh.write('data')
    os.link('local.txt', {str(leak)!r})
    return True
```"""
    out = run(check, code, True)
    # os.link IS in _WRITE_EVENTS - should be blocked
    if out["score"] == 1.0:
        assert not leak.exists(), f"sandbox leaked a write via link to {leak}"
    else:
        print(f"  [os.link] NOT blocked: {out['detail']}")
        if leak.exists():
            leak.unlink()


def test_code_exec_write_outside_tempdir_via_os_symlink():
    """Write outside tempdir via os.symlink() - audit hook covers os.symlink in _WRITE_EVENTS."""
    leak = Path(tempfile.gettempdir()) / f"kombat-ref-leak-symlink-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {
        "type": "code_exec",
        "entrypoint": "leak",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = f"""```python
import os
def leak():
    os.symlink('local.txt', {str(leak)!r})
    return True
```"""
    out = run(check, code, True)
    # os.symlink IS in _WRITE_EVENTS - should be blocked
    if out["score"] == 1.0:
        assert not leak.exists(), f"sandbox leaked a write via symlink to {leak}"
    else:
        print(f"  [os.symlink] NOT blocked: {out['detail']}")
        if leak.exists():
            leak.unlink()


def test_code_exec_read_outside_tempdir():
    """Read files outside tempdir (e.g., /etc/passwd, canary in $TMPDIR) - should be ALLOWED (reads are not blocked)."""
    # Create a canary file in $TMPDIR to test read access
    canary = Path(tempfile.gettempdir()) / f"kombat-ref-canary-{os.getpid()}.txt"
    canary.write_text("SECRET_CANARY_DATA", encoding="utf-8")

    check = {
        "type": "code_exec",
        "entrypoint": "read_canary",
        "cases": [{"args": [], "expect": "SECRET_CANARY_DATA"}],
        "timeout_s": 5,
    }
    code = f"""```python
def read_canary():
    with open({str(canary)!r}, 'r') as fh:
        return fh.read()
```"""
    out = run(check, code, True)
    canary.unlink(missing_ok=True)

    # Reads outside tempdir are NOT blocked by the audit hook (only writes are)
    # This is a known design decision - the runner is "not a sandbox"
    if out["score"] == 1.0:
        print("  [read] READS ALLOWED outside tempdir (by design - not a sandbox)")
    else:
        print(f"  [read] READS BLOCKED: {out['detail']}")
    # Document current behavior - don't assert


def test_code_exec_read_etc_passwd():
    """Read /etc/passwd - should be ALLOWED (reads not blocked)."""
    check = {
        "type": "code_exec",
        "entrypoint": "read_passwd",
        "cases": [{"args": [], "expect": "root:"}],  # expect root: to be in output
        "timeout_s": 5,
    }
    code = """```python
def read_passwd():
    try:
        with open('/etc/passwd', 'r') as fh:
            return fh.read()
    except Exception as e:
        return f"ERROR: {e}"
```"""
    out = run(check, code, True)
    # Reads are not blocked - but file might not exist in all environments
    print(f"  [/etc/passwd] result: {out['detail']}")


def test_code_exec_read_hermes_auth():
    """Read ~/.hermes/auth.json - should be ALLOWED (reads not blocked, but env is stripped so HOME not set)."""
    # Since env is stripped, ~ won't expand. But if they use os.path.expanduser it might work.
    check = {
        "type": "code_exec",
        "entrypoint": "read_auth",
        "cases": [{"args": [], "expect": "ERROR"}],  # expect error since HOME not set
        "timeout_s": 5,
    }
    code = """```python
import os
def read_auth():
    path = os.path.expanduser('~/.hermes/auth.json')
    try:
        with open(path, 'r') as fh:
            return fh.read()
    except Exception as e:
        return f"ERROR: {e}"
```"""
    out = run(check, code, True)
    print(f"  [~/.hermes/auth.json] result: {out['detail']}")
    # With empty env, expanduser may not work reliably


def test_code_exec_spawn_subprocess():
    """Spawn subprocess via subprocess.run - import is NOW BLOCKED by import blocker."""
    check = {
        "type": "code_exec",
        "entrypoint": "spawn",
        "cases": [{"args": [], "raises": "ImportError"}],
        "timeout_s": 5,
    }
    code = """```python
def spawn():
    import subprocess
    subprocess.run(['true'], check=False)
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"subprocess import not blocked: {out}"


def test_code_exec_spawn_os_system():
    """Spawn process via os.system - should be blocked by audit hook."""
    check = {
        "type": "code_exec",
        "entrypoint": "spawn",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = """```python
import os
def spawn():
    os.system('true')
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"os.system() allowed: {out}"


def test_code_exec_spawn_os_fork():
    """Spawn process via os.fork - should be blocked by audit hook."""
    if os.name == "nt":
        # POSIX-only primitive: on Windows os.fork does not exist at all, so the
        # blocked-spawn assertion cannot be expressed here (AttributeError is the
        # correct outcome, not PermissionError).
        print("  [os.fork] skipped on Windows (POSIX-only primitive)")
        return
    check = {
        "type": "code_exec",
        "entrypoint": "spawn",
        "cases": [{"args": [], "raises": "PermissionError"}],
        "timeout_s": 5,
    }
    code = """```python
import os
def spawn():
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"os.fork() allowed: {out}"


def test_code_exec_spawn_multiprocessing():
    """Spawn process via multiprocessing - import is NOW BLOCKED by import blocker."""
    check = {
        "type": "code_exec",
        "entrypoint": "spawn",
        "cases": [{"args": [], "raises": "ImportError"}],
        "timeout_s": 5,
    }
    code = """```python
def spawn():
    import multiprocessing
    p = multiprocessing.Process(target=lambda: None)
    p.start()
    p.join()
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"multiprocessing import not blocked: {out}"


def test_code_exec_network_socket():
    """Network socket connect to 127.0.0.1 - audit hook doesn't block network, but no listener."""
    # Start a throwaway listener
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('127.0.0.1', 0))
    port = server_socket.getsockname()[1]
    server_socket.listen(1)
    server_socket.settimeout(2)

    check = {
        "type": "code_exec",
        "entrypoint": "connect",
        "cases": [{"args": [], "expect": True}],
        "timeout_s": 5,
    }
    # Use double braces to escape in the f-string
    code = (
        "```python\n"
        "import socket\n"
        "def connect():\n"
        f"    try:\n"
        f"        s = socket.create_connection(('127.0.0.1', {port}), timeout=1)\n"
        f"        s.close()\n"
        f"        return True\n"
        f"    except Exception as exc:\n"
        f'        return "ERROR: {{exc}}"\n'
        "```"
    )
    out = run(check, code, True)
    server_socket.close()

    # Network is NOT blocked by audit hook - but RLIMIT_NPROC/AS may interfere
    # This documents current behavior
    print(f"  [network] socket connect result: {out['detail']}")


def test_code_exec_fork_bomb_bounded():
    """Fork bomb attempt bounded by RLIMIT_NPROC=32."""
    check = {
        "type": "code_exec",
        "entrypoint": "bomb",
        "cases": [{"args": [], "expect": 0}],  # should fail/timeout
        "timeout_s": 5,
    }
    code = """```python
import os
def bomb():
    count = 0
    for _ in range(1000):
        try:
            pid = os.fork()
            if pid == 0:
                os._exit(0)
            count += 1
        except (OSError, PermissionError):
            break
    return count
```"""
    out = run(check, code, True)
    print(f"  [fork bomb] result: {out['detail']}, score: {out['score']}")
    # RLIMIT_NPROC=32 should limit forks


def test_code_exec_memory_bomb():
    """Memory bomb attempt - bounded by RLIMIT_AS=512MB."""
    check = {
        "type": "code_exec",
        "entrypoint": "bomb",
        "cases": [{"args": [], "expect": "OOM"}],
        "timeout_s": 5,
    }
    code = """```python
def bomb():
    # Try to allocate >512MB
    chunks = []
    try:
        for _ in range(100):
            chunks.append(bytearray(10 * 1024 * 1024))  # 10MB chunks
        return "allocated"
    except MemoryError:
        return "OOM"
```"""
    out = run(check, code, True)
    print(f"  [memory bomb] result: {out['detail']}, score: {out['score']}")
    # RLIMIT_AS=512MB should OOM kill


def test_code_exec_infinite_loop():
    """Infinite loop - bounded by wall timeout (EXEC_WALL_CAP_S=12s)."""
    check = {
        "type": "code_exec",
        "entrypoint": "spin",
        "cases": [{"args": [], "expect": None}],
        "timeout_s": 2,
    }
    code = """```python
def spin():
    while True:
        pass
```"""
    started = time.monotonic()
    out = run(check, code, True)
    elapsed = time.monotonic() - started
    assert out["status"] == "scored" and out["score"] == 0.0, out
    assert "timed out" in out["detail"], out
    assert elapsed < 9, f"runner took {elapsed:.1f}s (should be ~2s wall cap)"


def test_code_exec_huge_stdout():
    """Huge stdout - capped at MAX_STDOUT=64KB."""
    check = {
        "type": "code_exec",
        "entrypoint": "spew",
        "cases": [{"args": [], "expect": "ok"}],
        "timeout_s": 5,
    }
    code = """```python
def spew():
    print('x' * 1_000_000)  # 1MB
    return 'ok'
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, out
    # Output is capped at 64KB in the harness, but the subprocess can write more
    # The parent only reads 64KB


def test_code_exec_import_ctypes():
    """Import ctypes - NOW BLOCKED by import blocker + audit hook."""
    check = {
        "type": "code_exec",
        "entrypoint": "import_ctypes",
        "cases": [{"args": [], "raises": "ImportError"}],
        "timeout_s": 5,
    }
    code = """```python
def import_ctypes():
    import ctypes
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"import ctypes not blocked: {out}"
    print("  [ctypes import] BLOCKED by import blocker")


def test_code_exec_import_mmap():
    """Import mmap - NOW BLOCKED by import blocker + audit hook."""
    check = {
        "type": "code_exec",
        "entrypoint": "import_mmap",
        "cases": [{"args": [], "raises": "ImportError"}],
        "timeout_s": 5,
    }
    code = """```python
def import_mmap():
    import mmap
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"import mmap not blocked: {out}"


def test_code_exec_monkeypatch_builtins():
    """Monkeypatch builtins to forge results (e.g., printing fake JSON report)."""
    # The harness writes result.json itself; candidate code can't easily forge it
    # But they could try to monkeypatch open/print to interfere
    check = {
        "type": "code_exec",
        "entrypoint": "forge",
        "cases": [{"args": [], "expect": "forged"}],
        "timeout_s": 5,
    }
    code = """```python
import builtins
_original_open = open
def forge():
    # Try to monkeypatch open to fake result.json
    def fake_open(path, *args, **kwargs):
        if path == 'result.json':
            return _original_open('/dev/null', 'w')
        return _original_open(path, *args, **kwargs)
    builtins.open = fake_open
    return 'forged'
```"""
    out = run(check, code, True)
    # Monkeypatching builtins.open won't work because harness uses open() before exec
    # and writes result.json AFTER _run() returns
    print(f"  [monkeypatch] result: {out['detail']}")


def test_code_exec_exit_tricks():
    """Exit tricks (sys.exit(0), os._exit) that could fake a pass."""
    check = {
        "type": "code_exec",
        "entrypoint": "exit_early",
        "cases": [{"args": [], "expect": "should not reach"}],
        "timeout_s": 5,
    }
    code = """```python
import sys, os
def exit_early():
    sys.exit(0)  # or os._exit(0)
    return 'unreachable'
```"""
    out = run(check, code, True)
    # sys.exit raises SystemExit which is caught as an exception -> case fails
    # os._exit kills the process -> harness reports fatal or timeout
    print(f"  [sys.exit] result: {out['detail']}, score: {out['score']}")
    assert out["score"] == 0.0, f"exit trick passed: {out}"


def test_code_exec_class_eq_always_true():
    """Class __eq__ returning True always to fake equality in case comparisons."""
    check = {
        "type": "code_exec",
        "entrypoint": "always_equal",
        "cases": [{"args": [], "expect": "wrong"}],
        "timeout_s": 5,
    }
    code = """```python
class AlwaysEqual:
    def __eq__(self, other):
        return True

def always_equal():
    return AlwaysEqual()
```"""
    out = run(check, code, True)
    # The harness uses _eq() which checks type equality first
    # AlwaysEqual() != "wrong" because type differs
    print(f"  [__eq__ hack] result: {out['detail']}, score: {out['score']}")
    assert out["score"] == 0.0, f"__eq__ hack passed: {out}"


def test_code_exec_entrypoint_missing():
    """Missing entrypoint - should error cleanly."""
    check = {
        "type": "code_exec",
        "entrypoint": "nonexistent",
        "cases": [{"args": [], "expect": 1}],
        "timeout_s": 5,
    }
    code = """```python
def other_func():
    return 1
```"""
    out = run(check, code, True)
    assert out["score"] == 0.0, out
    assert "entrypoint" in out["detail"].lower() or "not found" in out["detail"].lower()


# ------------------------------------------------------------------ sql_exec attacks

SQL_SETUP = ["CREATE TABLE t (a INTEGER, b TEXT)",
             "INSERT INTO t VALUES (1, 'a'), (2, 'b')"]

def test_sql_exec_multiple_statements():
    """Multiple statements in one query - sqlite3.execute only runs first statement."""
    check = {
        "type": "sql_exec",
        "setup": SQL_SETUP,
        "expect_rows": [[1, "a"]],
        "ordered": True,
    }
    # sqlite3.execute only executes the first statement; rest ignored with error
    out = run(check, "```sql\nSELECT a, b FROM t WHERE a=1; DROP TABLE t\n```")
    # Should fail because only first statement runs but it expects one statement
    assert out["score"] == 0.0, f"multiple statements behavior: {out}"
    assert "one statement at a time" in out["detail"] or "rejected" in out["detail"]


def test_sql_exec_comments_smuggle_write():
    """Comments to smuggle a write (-- or /* */)."""
    check = {
        "type": "sql_exec",
        "setup": SQL_SETUP,
        "expect_rows": [],
        "ordered": True,
    }
    # Comment followed by write - the strip_leading_comments removes leading comments
    # but the keyword check is on the remaining statement
    out = run(check, "```sql\n-- comment\nINSERT INTO t VALUES (3, 'c')\n```")
    assert out["score"] == 0.0, f"comment smuggle allowed: {out}"
    assert "rejected" in out["detail"], out


def test_sql_exec_attach():
    """ATTACH DATABASE - should be rejected (not SELECT/WITH)."""
    check = {
        "type": "sql_exec",
        "setup": SQL_SETUP,
        "expect_rows": [],
        "ordered": True,
    }
    out = run(check, "```sql\nATTACH DATABASE ':memory:' AS x\n```")
    assert out["score"] == 0.0, f"ATTACH allowed: {out}"
    assert "rejected" in out["detail"], out


def test_sql_exec_pragma():
    """PRAGMA - should be rejected (not SELECT/WITH)."""
    check = {
        "type": "sql_exec",
        "setup": SQL_SETUP,
        "expect_rows": [],
        "ordered": True,
    }
    out = run(check, "```sql\nPRAGMA query_only = OFF\n```")
    assert out["score"] == 0.0, f"PRAGMA allowed: {out}"
    assert "rejected" in out["detail"], out


def test_sql_exec_load_extension():
    """load_extension - should be rejected (fails with not authorized)."""
    check = {
        "type": "sql_exec",
        "setup": SQL_SETUP,
        "expect_rows": [],
        "ordered": True,
    }
    out = run(check, "```sql\nSELECT load_extension('x')\n```")
    assert out["score"] == 0.0, f"load_extension allowed: {out}"
    assert "not authorized" in out["detail"] or "rejected" in out["detail"], out


def test_sql_exec_recursive_cte_bomb():
    """Recursive CTE bomb - should terminate or be bounded by timeout."""
    check = {
        "type": "sql_exec",
        "setup": SQL_SETUP,
        "expect_rows": [],  # we don't care about results
        "ordered": True,
    }
    # Recursive CTE that generates many rows
    out = run(check, """```sql
WITH RECURSIVE bomb(n) AS (
    SELECT 1 UNION ALL SELECT n+1 FROM bomb WHERE n < 1000000
) SELECT n FROM bomb
```""")
    # Should either timeout (wall cap) or complete within limits
    print(f"  [recursive CTE] result: {out['detail']}, score: {out['score']}")
    # Either way, it should not hang indefinitely


def test_sql_exec_huge_cross_join():
    """Huge cross join - bounded by resources/timeout."""
    # Create a larger table first
    setup = SQL_SETUP + [
        "INSERT INTO t SELECT a+2, b || 'x' FROM t",
        "INSERT INTO t SELECT a+4, b || 'y' FROM t",
        "INSERT INTO t SELECT a+8, b || 'z' FROM t",
    ]
    check = {
        "type": "sql_exec",
        "setup": setup,
        "expect_rows": [],
        "ordered": True,
    }
    # Cross join t with itself multiple times
    out = run(check, """```sql
SELECT * FROM t t1, t t2, t t3, t t4
```""")
    print(f"  [cross join] result: {out['detail']}, score: {out['score']}")
    # Should be bounded by RLIMIT_AS/timeout


# ------------------------------------------------------------------ other check types adversarial

def test_constraints_sentence_counting():
    """Sentence counting on abbreviations/decimals/ellipsis."""
    # Decimal points should not count as sentence boundaries
    check = {"type": "constraints", "sentences": 1}
    assert run(check, "Pi is 3.14 today.")["score"] == 1.0
    assert run(check, "Version 1.2.3 is out.")["score"] == 1.0
    # Ellipsis - "Wait... what?" splits into ["Wait...", "what?"] = 2 sentences
    check2 = {"type": "constraints", "sentences": 2}
    assert run(check2, "Wait... what?")["score"] == 1.0
    # Abbreviations - "Dr. Smith left." splits into ["Dr.", "Smith left."] = 2 sentences
    check3 = {"type": "constraints", "sentences": 2}
    assert run(check3, "Dr. Smith left.")["score"] == 1.0
    # This is a known limitation of the simple regex


def test_json_fields_multiple_json_objects():
    """Multiple JSON objects / nested fences - should pick first valid one."""
    check = {
        "type": "json_fields",
        "expected": {"a": 1},
        "tol": 0.0,
    }
    # Multiple fenced blocks - should pick first
    answer = '''```json
{"a": 1}
```
```json
{"a": 2}
```'''
    assert run(check, answer)["score"] == 1.0

    # Nested object inside fenced block
    answer2 = '''```json
{"outer": {"inner": {"a": 1}}}
```'''
    # This has a nested object but the leaf path "a" is at outer.inner.a not a
    # So it won't match expected {"a": 1}
    out = run(check, answer2)
    assert out["score"] == 0.0, f"nested object should not match flat expected: {out}"

    # Bare object followed by fenced - _first_json_object checks fenced blocks first
    # so the fenced one wins, but we can test bare only
    answer3 = '{"a": 1}'
    assert run(check, answer3)["score"] == 1.0


def test_numeric_thousands_separators():
    """Numeric with thousands separators (1,000) and negative numbers."""
    check = {"type": "numeric", "expected": [1000, -500], "tol": 0.1}
    # The regex _NUM_RE = r"[-+]?\d+(?:\.\d+)?" does NOT match 1,000
    assert run(check, "Total: 1,000 and -500")["score"] == 0.5  # only -500 found
    assert run(check, "Total: 1000 and -500")["score"] == 1.0
    print("  [numeric] thousands separators NOT parsed (by design)")


def test_phrases_unicode_case():
    """Phrases with unicode/case variations."""
    check = {
        "type": "phrases",
        "expected": [["café"], ["naïve"], ["RÉSUMÉ"]]
    }
    assert run(check, "I went to the café and it was naïve")["score"] == round(2/3, 6)
    assert run(check, "CAFÉ and NAÏVE and RÉSUMÉ")["score"] == 1.0  # case insensitive


def test_exact_trailing_punctuation():
    """Exact match with trailing punctuation."""
    check = {"type": "exact", "expected": "hello", "normalize": True}
    # normalize=True strips whitespace and lowercases
    assert run(check, "hello.")["score"] == 0.0  # punctuation not stripped
    assert run(check, "hello")["score"] == 1.0
    assert run(check, "Hello!")["score"] == 0.0


def test_regex_catastrophic_backtracking():
    """ReDoS in a check pattern - is it bounded?"""
    # The pattern is compiled once per check, then search() is called on answer
    # Python's re engine is vulnerable to catastrophic backtracking
    # But the answer length is limited by model output; check spec is controlled
    check = {
        "type": "regex",
        "pattern": r"(a+)+b",  # classic ReDoS pattern
        "flags": "",
        "must_match": True,
    }
    # Short answer - should be fast
    out = run(check, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaac")
    assert out["status"] in ("scored", "errored")
    print(f"  [ReDoS] short evil input: {out['detail']}")

    # The check pattern itself is from the task author, not the model
    # So ReDoS risk is on the task author, not the model answer
    # But if a model could influence the pattern... not applicable here


# ------------------------------------------------------------------ hardening defeat probes (KNOWN_GAP_* = currently allowed, documents gaps)

def test_KNOWN_GAP_importlib_import_module_blocked():
    """importlib.import_module('ctypes') - blocked by import audit hook."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    import importlib
    importlib.import_module('ctypes')
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"importlib.import_module('ctypes') not blocked: {out}"
    print("  [importlib.import_module] BLOCKED")


def test_KNOWN_GAP_dunder_import_blocked():
    """__import__('ctypes') - blocked by import audit hook."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    __import__('ctypes')
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"__import__('ctypes') not blocked: {out}"
    print("  [__import__] BLOCKED")


def test_KNOWN_GAP_importlib_util_spec_blocked():
    """importlib.util.spec_from_file_location on stdlib ctypes path - blocked by open audit hook (read) before import."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = """```python
def probe():
    import importlib.util, sys
    # Try to load ctypes from its stdlib path
    spec = importlib.util.spec_from_file_location('ctypes', '/usr/lib/python3.12/ctypes/__init__.py')
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules['ctypes'] = mod
        return True
    return False
```"""
    out = run(check, code, True)
    # open audit hook fires on reading the .py file -> PermissionError (not in allowed read roots)
    assert out["score"] == 1.0, f"importlib.util.spec_from_file_location not blocked: {out}"
    print("  [importlib.util.spec] BLOCKED (via open audit hook)")


def test_KNOWN_GAP_sys_modules_alias_lookup():
    """Access blocked module via sys.modules['socket'] if another stdlib module already imported it."""
    # os module imports socket internally in CPython
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": None}], "timeout_s": 5}
    code = """```python
def probe():
    import os, sys
    # os module may have imported socket
    sock_mod = sys.modules.get('socket')
    if sock_mod:
        return sock_mod  # got socket module!
    return None
```"""
    out = run(check, code, True)
    # os doesn't always expose socket in sys.modules; but if it does, this is a gap
    # Current behavior: os doesn't put socket in sys.modules by default
    print(f"  [sys.modules alias] result: {out['detail']}")
    # Don't assert - document current behavior


def test_KNOWN_GAP_read_via_os_open():
    """Read outside tempdir via os.open/os.read - should be BLOCKED by audit hook on open (read)."""
    import tempfile
    canary = Path(tempfile.gettempdir()) / f"kombat-probe-read-{os.getpid()}.txt"
    canary.write_text("SECRET_VIA_OS_OPEN", encoding="utf-8")
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
import os
def probe():
    fd = os.open({str(canary)!r}, os.O_RDONLY)
    data = os.read(fd, 100)
    os.close(fd)
    return data.decode()
```"""
    out = run(check, code, True)
    canary.unlink(missing_ok=True)
    assert out["score"] == 1.0, f"os.open/os.read outside tempdir not blocked: {out}"
    print("  [os.open read] BLOCKED")


def test_KNOWN_GAP_read_via_io_fileio():
    """Read outside tempdir via io.FileIO - should be BLOCKED by audit hook on open."""
    import tempfile
    canary = Path(tempfile.gettempdir()) / f"kombat-probe-fileio-{os.getpid()}.txt"
    canary.write_text("SECRET_VIA_FILEIO", encoding="utf-8")
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
import io
def probe():
    with io.FileIO({str(canary)!r}, 'r') as fh:
        return fh.readall().decode()
```"""
    out = run(check, code, True)
    canary.unlink(missing_ok=True)
    assert out["score"] == 1.0, f"io.FileIO read outside tempdir not blocked: {out}"
    print("  [io.FileIO read] BLOCKED")


def test_KNOWN_GAP_read_via_pathlib():
    """Read outside tempdir via pathlib.Path.read_text - should be BLOCKED by audit hook on open."""
    import tempfile
    canary = Path(tempfile.gettempdir()) / f"kombat-probe-pathlib-{os.getpid()}.txt"
    canary.write_text("SECRET_VIA_PATHLIB", encoding="utf-8")
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
from pathlib import Path
def probe():
    return Path({str(canary)!r}).read_text()
```"""
    out = run(check, code, True)
    canary.unlink(missing_ok=True)
    assert out["score"] == 1.0, f"pathlib read outside tempdir not blocked: {out}"
    print("  [pathlib read] BLOCKED")


def test_KNOWN_GAP_read_via_linecache():
    """Read outside tempdir via linecache.getline - may be blocked or allowed depending on cache."""
    import tempfile
    canary = Path(tempfile.gettempdir()) / f"kombat-probe-linecache-{os.getpid()}.txt"
    canary.write_text("line1\nSECRET_VIA_LINECACHE\nline3", encoding="utf-8")
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "SECRET_VIA_LINECACHE"}], "timeout_s": 5}
    code = f"""```python
import linecache
def probe():
    return linecache.getline({str(canary)!r}, 2).strip()
```"""
    out = run(check, code, True)
    canary.unlink(missing_ok=True)
    # linecache may or may not be blocked; document behavior
    if out["score"] == 1.0:
        print("  [linecache read] ALLOWED (gap: linecache bypasses audit hook?)")
    else:
        print(f"  [linecache read] BLOCKED: {out['detail']}")
    # Don't assert - document current behavior


def test_KNOWN_GAP_read_via_proc_self():
    """Read /proc/self/... - should be BLOCKED by audit hook on open (not in stdlib/site)."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = """```python
def probe():
    with open('/proc/self/status', 'r') as fh:
        return fh.read()
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"/proc/self read not blocked: {out}"
    print("  [/proc/self read] BLOCKED")


def test_KNOWN_GAP_write_via_os_open_creat():
    """Write outside tempdir via os.open with O_CREAT - should be BLOCKED by audit hook on open (write)."""
    import tempfile
    leak = Path(tempfile.gettempdir()) / f"kombat-probe-write-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
import os
def probe():
    fd = os.open({str(leak)!r}, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.write(fd, b'escaped')
    os.close(fd)
    return True
```"""
    out = run(check, code, True)
    leaked = leak.exists()
    if leaked:
        leak.unlink()
    assert out["score"] == 1.0, f"os.open O_CREAT write outside tempdir not blocked: {out}"
    assert not leaked, f"sandbox leaked write via os.open O_CREAT to {leak}"
    print("  [os.open O_CREAT write] BLOCKED")


def test_KNOWN_GAP_chdir_then_relative_write():
    """chdir out of tempdir then write via relative path - should be BLOCKED (audit hook resolves realpath)."""
    import tempfile
    leak_dir = Path(tempfile.gettempdir())
    leak = leak_dir / f"kombat-probe-chdir-{os.getpid()}.txt"
    if leak.exists():
        leak.unlink()
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
import os
def probe():
    os.chdir({str(leak_dir)!r})
    with open('leak.txt', 'w') as fh:
        fh.write('escaped')
    return True
```"""
    out = run(check, code, True)
    leaked = leak.exists()
    if leaked:
        leak.unlink()
    assert out["score"] == 1.0, f"chdir + relative write not blocked: {out}"
    assert not leaked, f"sandbox leaked write via chdir to {leak}"
    print("  [chdir + relative write] BLOCKED")


def test_KNOWN_GAP_os_fdopen_inherited():
    """os.fdopen on inherited fd (stdin/stdout/stderr) - fds 0,1,2 are DEVNULL/pipe, not real files."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "error"}], "timeout_s": 5}
    code = """```python
import os
def probe():
    # Try to fdopen stdout (fd 1) and write
    try:
        fh = os.fdopen(1, 'w')
        fh.write('escaped')
        fh.close()
        return 'wrote to fd 1'
    except Exception as e:
        return f'error: {e}'
```"""
    out = run(check, code, True)
    # fd 1 is connected to the log pipe; writing may work but goes to log, not outside
    print(f"  [os.fdopen inherited] result: {out['detail']}")
    # Don't assert - document behavior


def test_KNOWN_GAP_os_execv():
    """os.execv - should be BLOCKED by exec audit hook."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = """```python
import os
def probe():
    os.execv('/bin/true', ['true'])
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"os.execv not blocked: {out}"
    print("  [os.execv] BLOCKED")


def test_KNOWN_GAP_signal_tricks():
    """Signal handlers to escape - signals deliver in process, but can't easily escape sandbox."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "caught"}], "timeout_s": 5}
    code = """```python
import signal, os
def probe():
    caught = []
    def handler(signum, frame):
        caught.append(signum)
    signal.signal(signal.SIGUSR1, handler)
    os.kill(os.getpid(), signal.SIGUSR1)
    return 'caught' if caught else 'not caught'
```"""
    out = run(check, code, True)
    print(f"  [signal tricks] result: {out['detail']}")
    # Document behavior


def test_KNOWN_GAP_threading_outlive():
    """threading.Thread to outlive the entrypoint - harness waits for function return, thread may continue but process exits."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "started"}], "timeout_s": 5}
    code = """```python
import threading, time
def probe():
    def bg():
        time.sleep(10)
        # Can't write result.json after main returns
    t = threading.Thread(target=bg, daemon=False)
    t.start()
    return 'started'
```"""
    out = run(check, code, True)
    # Non-daemon thread keeps process alive but wall timeout kills it
    print(f"  [threading outlive] result: {out['detail']}, score: {out['score']}")
    # Should not affect scoring


def test_KNOWN_GAP_os_kill():
    """os.kill to kill self or parent - should be blocked? (not in audit events currently)."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "error"}], "timeout_s": 5}
    code = """```python
import os, signal
def probe():
    try:
        os.kill(os.getpid(), signal.SIGKILL)
        return 'killed self?'
    except Exception as e:
        return f'error: {e}'
```"""
    out = run(check, code, True)
    print(f"  [os.kill] result: {out['detail']}")
    # Document behavior


def test_KNOWN_GAP_resource_setrlimit_raise():
    """resource.setrlimit to raise limits - should fail (no CAP_SYS_RESOURCE)."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "error"}], "timeout_s": 5}
    code = """```python
import resource
def probe():
    try:
        resource.setrlimit(resource.RLIMIT_AS, (1024*1024*1024, 1024*1024*1024))
        return 'raised!'
    except Exception as e:
        return f'error: {e}'
```"""
    out = run(check, code, True)
    print(f"  [resource.setrlimit raise] result: {out['detail']}")
    # Should fail with EPERM


def test_KNOWN_GAP_audit_hook_removal():
    """Try to remove audit hook via gc/inspect monkeypatching."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "hooked"}], "timeout_s": 5}
    code = """```python
import sys, gc, inspect
def probe():
    # Try to find and disable the audit hook
    # sys.addaudithook cannot be removed, but maybe we can patch _audit?
    # The hook is in the harness module, not accessible from candidate namespace
    # But let's try to mess with sys.__audit_hook__ or similar
    try:
        # This won't work - hooks are in C-level list
        sys.addaudithook(lambda *a: None)  # adding another does nothing
        return 'hooked'
    except Exception as e:
        return f'error: {e}'
```"""
    out = run(check, code, True)
    print(f"  [audit hook removal] result: {out['detail']}")
    # Document behavior


def test_KNOWN_GAP_sys_settrace():
    """sys.settrace to intercept execution - may allow observation but not escape."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "traced"}], "timeout_s": 5}
    code = """```python
import sys
def probe():
    events = []
    def tracer(frame, event, arg):
        events.append(event)
        return tracer
    sys.settrace(tracer)
    x = 1 + 1
    sys.settrace(None)
    return f'traced: {events[:5]}'
```"""
    out = run(check, code, True)
    print(f"  [sys.settrace] result: {out['detail']}")
    # Document behavior


def test_KNOWN_GAP_forge_result_file():
    """Try to forge result.json before harness writes it."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "forged"}], "timeout_s": 5}
    code = """```python
import os, json
def probe():
    # Harness writes result.json AFTER _run() returns
    # But maybe we can write it early?
    with open('result.json', 'w') as fh:
        json.dump({"cases": [{"i": 0, "passed": True, "detail": "forged"}], "fatal": None}, fh)
    return 'forged'
```"""
    out = run(check, code, True)
    # Harness overwrites result.json after _run() returns
    print(f"  [forge result.json] result: {out['detail']}, score: {out['score']}")
    # Should score 0.0 because harness overwrites with real results


def test_KNOWN_GAP_print_forged_json():
    """Print forged JSON to stdout - harness reads result.json, not stdout."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "expect": "ignored"}], "timeout_s": 5}
    code = """```python
import json, sys
def probe():
    # Print fake report to stdout
    fake = {"cases": [{"i": 0, "passed": True, "detail": "forged"}], "fatal": None}
    print(json.dumps(fake))
    return 'ignored'
```"""
    out = run(check, code, True)
    print(f"  [print forged JSON] result: {out['detail']}, score: {out['score']}")
    # Should not affect scoring - harness reads result.json file


def test_KNOWN_GAP_import_socket_via_urllib():
    """Try to import socket via urllib (which imports socket) - blocked by import blocker on urllib."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    import urllib.request  # imports socket internally
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"urllib.request import not blocked: {out}"
    print("  [urllib.request import] BLOCKED")


def test_KNOWN_GAP_import_ssl():
    """Import ssl - blocked by import blocker."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    import ssl
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"ssl import not blocked: {out}"
    print("  [ssl import] BLOCKED")


def test_KNOWN_GAP_import_http():
    """Import http - blocked by import blocker."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    import http.client
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"http.client import not blocked: {out}"
    print("  [http.client import] BLOCKED")


def test_KNOWN_GAP_import_ftplib():
    """Import ftplib - blocked by import blocker."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    import ftplib
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"ftplib import not blocked: {out}"
    print("  [ftplib import] BLOCKED")


def test_KNOWN_GAP_import_subprocess_direct():
    """Direct import subprocess - blocked."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    import subprocess
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"subprocess import not blocked: {out}"
    print("  [subprocess import] BLOCKED")


def test_KNOWN_GAP_import_pty():
    """Import pty - blocked."""
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "ImportError"}], "timeout_s": 5}
    code = """```python
def probe():
    import pty
    return True
```"""
    out = run(check, code, True)
    assert out["score"] == 1.0, f"pty import not blocked: {out}"
    print("  [pty import] BLOCKED")


def test_KNOWN_GAP_shutil_move_both_sides():
    """shutil.move with both src and dst outside - should be BLOCKED (both sides check)."""
    import tempfile
    src = Path(tempfile.gettempdir()) / f"kombat-probe-src-{os.getpid()}.txt"
    dst = Path(tempfile.gettempdir()) / f"kombat-probe-dst-{os.getpid()}.txt"
    src.write_text("data", encoding="utf-8")
    if dst.exists():
        dst.unlink()
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
import shutil
def probe():
    shutil.move({str(src)!r}, {str(dst)!r})
    return True
```"""
    out = run(check, code, True)
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)
    assert out["score"] == 1.0, f"shutil.move both outside not blocked: {out}"
    print("  [shutil.move both sides] BLOCKED")


def test_KNOWN_GAP_shutil_copy_dst_outside():
    """shutil.copyfile with dst outside - should be BLOCKED (dst check)."""
    import tempfile
    src = Path(tempfile.gettempdir()) / f"kombat-probe-src-{os.getpid()}.txt"
    dst = Path(tempfile.gettempdir()) / f"kombat-probe-dst-{os.getpid()}.txt"
    src.write_text("data", encoding="utf-8")
    if dst.exists():
        dst.unlink()
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
import shutil
def probe():
    shutil.copyfile({str(src)!r}, {str(dst)!r})
    return True
```"""
    out = run(check, code, True)
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)
    assert out["score"] == 1.0, f"shutil.copyfile dst outside not blocked: {out}"
    print("  [shutil.copyfile dst] BLOCKED")


def test_KNOWN_GAP_os_replace_both_sides():
    """os.replace with both sides outside - should be BLOCKED."""
    import tempfile
    src = Path(tempfile.gettempdir()) / f"kombat-probe-src-{os.getpid()}.txt"
    dst = Path(tempfile.gettempdir()) / f"kombat-probe-dst-{os.getpid()}.txt"
    src.write_text("data", encoding="utf-8")
    if dst.exists():
        dst.unlink()
    check = {"type": "code_exec", "entrypoint": "probe", "cases": [{"args": [], "raises": "PermissionError"}], "timeout_s": 5}
    code = f"""```python
import os
def probe():
    os.replace({str(src)!r}, {str(dst)!r})
    return True
```"""
    out = run(check, code, True)
    src.unlink(missing_ok=True)
    dst.unlink(missing_ok=True)
    assert out["score"] == 1.0, f"os.replace both outside not blocked: {out}"
    print("  [os.replace both sides] BLOCKED")


# ------------------------------------------------------------------ meta

def test_score_check_never_raises():
    """score_check must never raise, always return dict with score/status/detail."""
    for check in (
        {"type": "exact", "expected": "x", "normalize": "yes"},
        {"type": "numeric", "expected": [1], "tol": -1},
        {"type": "json_fields", "expected": {}, "tol": 0},
        {"type": "sql_exec", "setup": [], "expect_rows": [], "ordered": "no"},
        {"type": "code_exec", "entrypoint": "x", "cases": [{}], "timeout_s": -1},
        "not a dict",
        None,
        123,
    ):
        out = ec.score_check(check, "x", False)  # type: ignore[arg-type]
        assert isinstance(out, dict), f"non-dict return: {out}"
        assert set(out.keys()) == {"score", "status", "detail"}, f"keys: {out.keys()}"
        assert out["status"] in ("scored", "skipped", "errored"), f"bad status: {out}"


def test_errored_not_failed():
    """errored != failed: errored = malformed spec (score=None), failed = wrong answer (score=0.0)."""
    # Malformed spec -> errored
    out = run({"type": "exact"}, "answer")
    assert out["status"] == "errored" and out["score"] is None

    # Wrong answer -> scored with 0.0
    out = run({"type": "exact", "expected": "yes"}, "no")
    assert out["status"] == "scored" and out["score"] == 0.0


if __name__ == "__main__":
    import traceback

    tests = [
        # code_exec escape attempts
        ("write via open()", test_code_exec_write_outside_tempdir_via_open),
        ("write via os.open()", test_code_exec_write_outside_tempdir_via_os_open),
        ("write via pathlib", test_code_exec_write_outside_tempdir_via_pathlib),
        ("write via shutil", test_code_exec_write_outside_tempdir_via_shutil),
        ("_winapi native copy", test_code_exec_winapi_native_copy_cannot_escape),
        ("write via tempfile.mkstemp(dir=)", test_code_exec_write_outside_tempdir_via_tempfile),
        ("write via ctypes (KNOWN_GAP)", test_code_exec_write_outside_tempdir_via_ctypes),
        ("write via io.open()", test_code_exec_write_outside_tempdir_via_io_open),
        ("write via os.rename()", test_code_exec_write_outside_tempdir_via_os_rename),
        ("write via os.link()", test_code_exec_write_outside_tempdir_via_os_link),
        ("write via os.symlink()", test_code_exec_write_outside_tempdir_via_os_symlink),
        ("read outside tempdir (KNOWN_GAP - reads allowed)", test_code_exec_read_outside_tempdir),
        ("read /etc/passwd", test_code_exec_read_etc_passwd),
        ("read ~/.hermes/auth.json", test_code_exec_read_hermes_auth),
        ("spawn subprocess.run", test_code_exec_spawn_subprocess),
        ("spawn os.system", test_code_exec_spawn_os_system),
        ("spawn os.fork", test_code_exec_spawn_os_fork),
        ("spawn multiprocessing", test_code_exec_spawn_multiprocessing),
        ("network socket connect", test_code_exec_network_socket),
        ("fork bomb bounded", test_code_exec_fork_bomb_bounded),
        ("memory bomb", test_code_exec_memory_bomb),
        ("infinite loop", test_code_exec_infinite_loop),
        ("huge stdout", test_code_exec_huge_stdout),
        ("import ctypes", test_code_exec_import_ctypes),
        ("import mmap", test_code_exec_import_mmap),
        ("monkeypatch builtins", test_code_exec_monkeypatch_builtins),
        ("exit tricks", test_code_exec_exit_tricks),
        ("__eq__ hack", test_code_exec_class_eq_always_true),
        ("missing entrypoint", test_code_exec_entrypoint_missing),
        # hardening defeat probes
        ("importlib.import_module blocked", test_KNOWN_GAP_importlib_import_module_blocked),
        ("__import__ blocked", test_KNOWN_GAP_dunder_import_blocked),
        ("importlib.util.spec blocked", test_KNOWN_GAP_importlib_util_spec_blocked),
        ("sys.modules alias lookup", test_KNOWN_GAP_sys_modules_alias_lookup),
        ("read via os.open", test_KNOWN_GAP_read_via_os_open),
        ("read via io.FileIO", test_KNOWN_GAP_read_via_io_fileio),
        ("read via pathlib", test_KNOWN_GAP_read_via_pathlib),
        ("read via linecache", test_KNOWN_GAP_read_via_linecache),
        ("read via /proc/self", test_KNOWN_GAP_read_via_proc_self),
        ("write via os.open O_CREAT", test_KNOWN_GAP_write_via_os_open_creat),
        ("chdir + relative write", test_KNOWN_GAP_chdir_then_relative_write),
        ("os.fdopen inherited", test_KNOWN_GAP_os_fdopen_inherited),
        ("os.execv", test_KNOWN_GAP_os_execv),
        ("signal tricks", test_KNOWN_GAP_signal_tricks),
        ("threading outlive", test_KNOWN_GAP_threading_outlive),
        ("os.kill", test_KNOWN_GAP_os_kill),
        ("resource.setrlimit raise", test_KNOWN_GAP_resource_setrlimit_raise),
        ("audit hook removal", test_KNOWN_GAP_audit_hook_removal),
        ("sys.settrace", test_KNOWN_GAP_sys_settrace),
        ("forge result.json", test_KNOWN_GAP_forge_result_file),
        ("print forged JSON", test_KNOWN_GAP_print_forged_json),
        ("import socket via urllib", test_KNOWN_GAP_import_socket_via_urllib),
        ("import ssl", test_KNOWN_GAP_import_ssl),
        ("import http.client", test_KNOWN_GAP_import_http),
        ("import ftplib", test_KNOWN_GAP_import_ftplib),
        ("import subprocess direct", test_KNOWN_GAP_import_subprocess_direct),
        ("import pty", test_KNOWN_GAP_import_pty),
        ("shutil.move both sides", test_KNOWN_GAP_shutil_move_both_sides),
        ("shutil.copyfile dst outside", test_KNOWN_GAP_shutil_copy_dst_outside),
        ("os.replace both sides", test_KNOWN_GAP_os_replace_both_sides),
        # sql_exec
        ("multiple statements", test_sql_exec_multiple_statements),
        ("comments smuggle write", test_sql_exec_comments_smuggle_write),
        ("ATTACH", test_sql_exec_attach),
        ("PRAGMA", test_sql_exec_pragma),
        ("load_extension", test_sql_exec_load_extension),
        ("recursive CTE bomb", test_sql_exec_recursive_cte_bomb),
        ("huge cross join", test_sql_exec_huge_cross_join),
        # other checks
        ("constraints sentence counting", test_constraints_sentence_counting),
        ("json_fields multiple objects", test_json_fields_multiple_json_objects),
        ("numeric thousands separators", test_numeric_thousands_separators),
        ("phrases unicode/case", test_phrases_unicode_case),
        ("exact trailing punctuation", test_exact_trailing_punctuation),
        ("regex ReDoS", test_regex_catastrophic_backtracking),
        # meta
        ("never raises", test_score_check_never_raises),
        ("errored != failed", test_errored_not_failed),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✓ {name}")
            passed += 1
        except Exception as e:
            print(f"✗ {name}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)