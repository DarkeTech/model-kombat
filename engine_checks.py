"""Mechanical check-spec scorers for Model Kombat v0.4 (SPEC §3).

Public API:
    score_check(check: dict, answer: str, allow_exec: bool) -> dict

The returned dict is always
    {"score": float | None, "status": "scored" | "skipped" | "errored", "detail": str}

- "scored"  -> score is a fraction in 0..1 (a wrong answer scores 0.0, it does
               not error; malformed *answers* are failures, malformed *check
               specs* are errors).
- "skipped" -> score is None; the check was not run (code_exec with execution
               disabled). A skipped task must never count as a failure.
- "errored" -> score is None; the check spec itself is unusable.

Scoring is mechanical only: exact counts, tolerances, regexes, JSON leaf
equality, subprocess execution, and sqlite row comparison. No model judges
another model.

The code runner is hardened defense-in-depth but is **NOT a security
sandbox**. It uses a separate ``python -I`` subprocess with cwd in a fresh
tempdir, an empty environment, resource limits (CPU, address space, core
files, processes, file size), a wall timeout, capped output, a Python audit
hook, and an import blocker. Those layers refuse writes/changes outside the
tempdir, refuse process spawning, refuse imports of a blocklist of
networking/FFI/process modules (ctypes, socket, subprocess, ...), and refuse
reads of absolute paths outside the tempdir except the interpreter's own
stdlib/site directories.

None of that is a real confinement boundary: the hook is Python-level, so
C-extension paths (e.g. cffi, a preloaded shared object), direct syscalls,
``/proc`` quirks, environment/platform differences, and bugs in CPython's
audit coverage can still escape or misbehave. code_exec is **OFF by default**
(``allow_code_execution=false`` / ``allow_exec=False``); enable it only for
models you are willing to trust with code running as your user. Treat all
candidate code as untrusted.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

# ------------------------------------------------------------------ helpers

MAX_STDOUT = 64 * 1024          # bytes of runner output we keep for details
MAX_CASES = 200                 # refuse absurd case lists
MAX_SQL_BYTES = 100_000
EXEC_WALL_CAP_S = 12            # hard wall cap regardless of check timeout_s
EXEC_CPU_LIMIT_S = 10           # RLIMIT_CPU


def _result(score, status: str, detail: str) -> dict:
    return {"score": score, "status": status, "detail": detail}


def _scored(score: float, detail: str) -> dict:
    return _result(max(0.0, min(1.0, round(float(score), 6))), "scored", detail)


def _errored(detail: str) -> dict:
    return _result(None, "errored", detail[:500])


def _skipped(detail: str) -> dict:
    return _result(None, "skipped", detail[:500])


def _norm_space(text: str, lower: bool = True) -> str:
    out = re.sub(r"\s+", " ", str(text)).strip()
    return out.lower() if lower else out


def _as_list(value) -> list | None:
    return value if isinstance(value, list) else None


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _numbers_in(text: str) -> list[float]:
    out = []
    for raw in _NUM_RE.findall(text):
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


# ------------------------------------------------------------------ exact

def _check_exact(check: dict, answer: str) -> dict:
    expected = check.get("expected")
    if not isinstance(expected, str) or not expected.strip():
        return _errored("exact: missing or empty string 'expected'")
    normalize = check.get("normalize", True)
    if not isinstance(normalize, bool):
        return _errored("exact: 'normalize' must be a boolean")
    norm = (lambda s: _norm_space(s, lower=True)) if normalize else (lambda s: str(s).strip())

    whole = norm(answer)
    want = norm(expected)
    if whole == want:
        return _scored(1.0, "exact: whole answer matches")
    lines = [ln for ln in str(answer).splitlines() if ln.strip()]
    last = norm(lines[-1]) if lines else ""
    if last == want:
        return _scored(1.0, "exact: last non-empty line matches")
    return _scored(0.0, f"exact: answer does not match {expected!r}")


# ------------------------------------------------------------------ numeric

def _check_numeric(check: dict, answer: str) -> dict:
    expected = _as_list(check.get("expected"))
    if not expected or not all(_is_number(v) for v in expected):
        return _errored("numeric: 'expected' must be a non-empty list of numbers")
    try:
        tol = float(check.get("tol", 1e-9))
    except (TypeError, ValueError):
        return _errored("numeric: 'tol' must be a number")
    if tol < 0:
        return _errored("numeric: 'tol' must be >= 0")

    found = _numbers_in(answer)
    hits, missing = 0, []
    for want in expected:
        if any(abs(got - float(want)) <= tol + 1e-12 for got in found):
            hits += 1
        else:
            missing.append(want)
    score = hits / len(expected)
    detail = f"numeric: {hits}/{len(expected)} expected number(s) found"
    if missing:
        detail += f"; missing {missing[:5]}"
    return _scored(score, detail)


# ------------------------------------------------------------------ phrases

def _check_phrases(check: dict, answer: str) -> dict:
    expected = _as_list(check.get("expected"))
    if not expected:
        return _errored("phrases: 'expected' must be a non-empty list of groups")
    groups = []
    for entry in expected:
        alts = entry if isinstance(entry, list) else [entry]
        alts = [str(a) for a in alts if isinstance(a, str) and a.strip()]
        if not alts:
            return _errored("phrases: every group needs at least one non-empty string")
        groups.append(alts)

    low = _norm_space(answer, lower=True)
    hits = 0
    missing = []
    for alts in groups:
        if any(_norm_space(alt, lower=True) in low for alt in alts):
            hits += 1
        else:
            missing.append(alts[0])
    detail = f"phrases: {hits}/{len(groups)} group(s) hit"
    if missing:
        detail += f"; missing {missing[:5]}"
    return _scored(hits / len(groups), detail)


# ------------------------------------------------------------------ regex

def _check_regex(check: dict, answer: str) -> dict:
    pattern = check.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return _errored("regex: missing string 'pattern'")
    flags_raw = check.get("flags", "")
    if not isinstance(flags_raw, str):
        return _errored("regex: 'flags' must be a string like 'im'")
    flags = 0
    for ch in flags_raw.lower():
        flag = {"i": re.I, "m": re.M, "s": re.S, "x": re.X}.get(ch)
        if flag is None:
            return _errored(f"regex: unsupported flag {ch!r}")
        flags |= flag
    must_match = check.get("must_match", True)
    if not isinstance(must_match, bool):
        return _errored("regex: 'must_match' must be a boolean")
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return _errored(f"regex: invalid pattern: {exc}")

    matched = compiled.search(answer) is not None
    ok = matched if must_match else not matched
    mode = "matches" if must_match else "must not match"
    return _scored(1.0 if ok else 0.0,
                   f"regex: pattern {mode} — {'matched' if matched else 'no match'}")


# ------------------------------------------------------------------ constraints

def _within(value, spec) -> bool:
    """words/sentences spec: int (exact) or {min, max} (inclusive)."""
    if isinstance(spec, dict):
        if "min" in spec and value < float(spec["min"]):
            return False
        if "max" in spec and value > float(spec["max"]):
            return False
        return "min" in spec or "max" in spec
    if _is_number(spec):
        return value == float(spec)
    return False


def _sentence_count(text: str) -> int:
    """Count sentences split on terminal punctuation followed by whitespace.

    Whitespace-aware so decimals ("3.5") and version strings do not inflate
    the count, which a naive [.!?]+ split would do.
    """
    stripped = text.strip()
    if not stripped:
        return 0
    parts = [part for part in re.split(r"(?<=[.!?])\s+", stripped) if part.strip()]
    return len(parts) or 1


def _check_constraints(check: dict, answer: str) -> dict:
    text = str(answer).strip()
    checks: list[tuple[str, bool, str]] = []

    if "words" in check:
        words = text.split()
        ok = _within(len(words), check["words"])
        checks.append(("words", ok, f"{len(words)} words vs {check['words']}"))
    if "sentences" in check:
        sentences = _sentence_count(text)
        ok = _within(sentences, check["sentences"])
        checks.append(("sentences", ok, f"{sentences} sentences vs {check['sentences']}"))
    if "starts_with" in check:
        want = check["starts_with"]
        if not isinstance(want, str):
            return _errored("constraints: 'starts_with' must be a string")
        ok = text.lower().startswith(want.strip().lower())
        checks.append(("starts_with", ok, f"start {'ok' if ok else 'wrong'}: {want!r}"))
    if "ends_with" in check:
        want = check["ends_with"]
        if not isinstance(want, str):
            return _errored("constraints: 'ends_with' must be a string")
        ok = text.lower().endswith(want.strip().lower())
        checks.append(("ends_with", ok, f"end {'ok' if ok else 'wrong'}: {want!r}"))
    if "banned" in check:
        banned = check["banned"]
        if not isinstance(banned, list) or not all(isinstance(b, str) for b in banned):
            return _errored("constraints: 'banned' must be a list of strings")
        hits = [b for b in banned
                if re.search(r"(?<!\w)" + re.escape(b) + r"(?!\w)", text, re.I)]
        checks.append(("banned", not hits, f"banned found: {hits or 'none'}"))
    if "contains" in check:
        contains = check["contains"]
        if not isinstance(contains, list) or not all(isinstance(c, str) for c in contains):
            return _errored("constraints: 'contains' must be a list of strings")
        low = text.lower()
        missing = [c for c in contains if c.lower() not in low]
        checks.append(("contains", not missing, f"missing: {missing or 'none'}"))

    if not checks:
        return _errored("constraints: no recognised constraint keys present")
    passed = sum(1 for _, ok, _ in checks if ok)
    detail = f"constraints: {passed}/{len(checks)} passed — " + "; ".join(
        f"{label}: {msg}" for label, _, msg in checks
    )
    return _scored(passed / len(checks), detail)


# ------------------------------------------------------------------ json_fields

def _scan_json_object(text: str):
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _first_json_object(text: str):
    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I):
        blob = match.group(1).strip()
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            obj = _scan_json_object(blob)
        if isinstance(obj, dict):
            return obj
    return _scan_json_object(text)


def _flatten(obj, prefix: str = ""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten(value, child)
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            yield from _flatten(value, f"{prefix}[{idx}]")
    else:
        yield prefix, obj


def _get_path(obj, path: str):
    """Walk a flattened path (a.b[0].c). Returns (found, value)."""
    cur = obj
    for key, idx in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path):
        if idx:
            if not isinstance(cur, list):
                return False, None
            pos = int(idx)
            if pos >= len(cur):
                return False, None
            cur = cur[pos]
        else:
            if isinstance(cur, list):
                return False, None
            if not isinstance(cur, dict) or key not in cur:
                return False, None
            cur = cur[key]
    return True, cur


def _leaf_eq(got, want, tol: float) -> bool:
    if _is_number(got) and _is_number(want):
        return abs(float(got) - float(want)) <= tol + 1e-12
    if isinstance(want, bool) or isinstance(got, bool):
        return type(got) is type(want) and got == want
    return type(got) is type(want) and got == want


def _check_json_fields(check: dict, answer: str) -> dict:
    expected = check.get("expected")
    if not isinstance(expected, dict) or not expected:
        return _errored("json_fields: 'expected' must be a non-empty object")
    try:
        tol = float(check.get("tol", 1e-9))
    except (TypeError, ValueError):
        return _errored("json_fields: 'tol' must be a number")
    if tol < 0:
        return _errored("json_fields: 'tol' must be >= 0")

    parsed = _first_json_object(str(answer))
    if parsed is None:
        return _scored(0.0, "json_fields: no JSON object found in answer")

    leaves = list(_flatten(expected))
    if not leaves:
        return _errored("json_fields: 'expected' has no leaf fields")
    hits, misses = 0, []
    for path, want in leaves:
        found, got = _get_path(parsed, path)
        if found and _leaf_eq(got, want, tol):
            hits += 1
        else:
            misses.append(path)
    detail = f"json_fields: {hits}/{len(leaves)} field(s) matched"
    if misses:
        detail += f"; mismatched {misses[:6]}"
    return _scored(hits / len(leaves), detail)


# ------------------------------------------------------------------ code runner

_PY_FENCE_RE = re.compile(r"```(?:python|py)[ \t]*\r?\n(.*?)```", re.S | re.I)


def _extract_python(answer: str) -> str | None:
    block = _PY_FENCE_RE.search(answer)
    if block:
        code = block.group(1).strip()
        return code or None
    text = answer.strip()
    return text if "def " in text else None


# Runs inside the sandboxed subprocess. It loads the candidate source, calls
# the entrypoint once per case, and writes result.json (never stdout, so model
# prints cannot corrupt the report).
_HARNESS = r'''"""Model Kombat candidate harness. Runs with python -I in a tempdir."""
import builtins
import copy
import json
import os
import sys

RESULT = "result.json"
ROOT = os.path.realpath(os.getcwd())

# Keep legitimate scratch-file use inside the sandbox instead of /tmp.
import tempfile as _tempfile
_tempfile.tempdir = ROOT

# Never try to write .pyc files (the stdlib/site dirs are read-only to us).
sys.dont_write_bytecode = True

# Windows: shutil.copy2 takes a native _winapi.CopyFile2 fast path that raises
# no audit events at all, so the open() hook below never sees the write and
# candidate code could copy files out of the tempdir. Dropping the native entry
# points forces the auditable open()-based copyfile path (slower, but visible).
try:
    import _winapi as _winapi_mod
    for _attr in ("CopyFile2", "CopyFile"):
        try:
            delattr(_winapi_mod, _attr)
        except AttributeError:
            pass
except ImportError:
    pass

# Reads are allowed from the tempdir plus the interpreter's own stdlib/site
# directories so imports (and the harness itself) still work.
_READ_ROOTS = []
for _p in [sys.prefix, sys.base_prefix, sys.exec_prefix] + list(sys.path):
    if not _p or not isinstance(_p, str):
        continue
    try:
        _rp = os.path.realpath(_p)
    except Exception:
        continue
    if _rp and _rp not in _READ_ROOTS:
        _READ_ROOTS.append(_rp)


def _inside(path):
    try:
        resolved = os.path.realpath(os.fsdecode(path))
    except Exception:
        return False
    return resolved == ROOT or resolved.startswith(ROOT + os.sep)


def _read_allowed(path):
    if _inside(path):
        return True
    try:
        resolved = os.path.realpath(os.fsdecode(path))
    except Exception:
        return False
    for root in _READ_ROOTS:
        if resolved == root or resolved.startswith(root + os.sep):
            return True
    return False


_BLOCKED_MODULES = frozenset({
    "ctypes", "_ctypes", "cffi", "_cffi_backend", "mmap",
    "socket", "_socket", "ssl", "_ssl", "http", "urllib",
    "ftplib", "smtplib", "telnetlib", "multiprocessing", "_multiprocessing",
    "subprocess", "_posixsubprocess", "pty",
    # Windows native file APIs: copying straight through _winapi fires no audit
    # events, so the open() hook cannot see the write (see the harness prelude).
    "_winapi",
})


class _BlockedImportFinder:
    """sys.meta_path finder that refuses the blocklisted module roots."""

    @staticmethod
    def find_spec(fullname, path=None, target=None):
        if fullname.split(".")[0] in _BLOCKED_MODULES:
            raise ImportError(
                "import of %r is blocked in the code sandbox" % (fullname,)
            )
        return None


# Metadata-only writes: arg0 is the path being changed.
_PATH_WRITE_EVENTS = {
    "os.remove", "os.unlink", "os.rmdir", "os.mkdir", "os.chmod", "os.chown",
    "os.truncate", "os.utime", "shutil.chown",
}
# Rename/link/symlink/move carry (src, dst, ...): both sides must stay inside
# because the operation either destroys or publishes the source.
_BOTH_SIDES_EVENTS = {
    "os.rename", "os.replace", "os.link", "os.symlink", "shutil.move",
}
# Copy variants: the source is only read, so an outside source is fine as
# long as the destination lands inside the tempdir.
_COPY_EVENTS = {
    "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree",
}
_EXEC_EVENTS = {
    "os.system", "subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "pty.spawn",
}
_NET_EVENTS = {
    "socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo",
    "socket.gethostbyname", "socket.sethostname",
}


def _audit(event, args):
    if event == "open":
        path, mode, flags = args
        if isinstance(path, int):
            return
        writing = False
        if mode is not None and any(c in str(mode) for c in "wax+"):
            writing = True
        if mode is None:
            try:
                writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT |
                                        os.O_APPEND | os.O_TRUNC))
            except Exception:
                writing = False
        if writing:
            if not _inside(path):
                raise PermissionError("write outside the sandbox tempdir blocked")
        elif not _read_allowed(path):
            raise PermissionError("read outside the sandbox tempdir blocked")
    elif event == "import":
        name = str(args[0]).split(".")[0] if args else ""
        if name in _BLOCKED_MODULES:
            raise ImportError("import of %r is blocked in the code sandbox" % (name,))
    elif event in _PATH_WRITE_EVENTS:
        target = args[0] if args else None
        if target is not None and not isinstance(target, int) and not _inside(target):
            raise PermissionError("filesystem change outside the sandbox tempdir blocked")
    elif event in _BOTH_SIDES_EVENTS:
        for target in (args[0] if args else None, args[1] if len(args) > 1 else None):
            if target is None or isinstance(target, int):
                continue
            if not _inside(target):
                raise PermissionError(
                    "rename/link/symlink outside the sandbox tempdir blocked"
                )
    elif event in _COPY_EVENTS:
        dst = args[1] if len(args) > 1 else None
        if dst is not None and not isinstance(dst, int) and not _inside(dst):
            raise PermissionError("copy destination outside the sandbox tempdir blocked")
    elif event in _EXEC_EVENTS:
        raise PermissionError("process execution blocked in the sandbox")
    elif event in _NET_EVENTS:
        raise PermissionError("network access blocked in the sandbox")



def _eq(got, want, tol=0.0):
    if isinstance(want, bool) or isinstance(got, bool):
        return type(got) is type(want) and got == want
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return abs(float(got) - float(want)) <= tol + 1e-12
    if isinstance(want, list):
        if isinstance(got, tuple):
            got = list(got)
        if not isinstance(got, list) or len(got) != len(want):
            return False
        return all(_eq(a, b, tol) for a, b in zip(got, want))
    if isinstance(want, dict):
        if not isinstance(got, dict) or set(got) != set(want):
            return False
        return all(_eq(got[k], want[k], tol) for k in want)
    if isinstance(want, str):
        return isinstance(got, str) and got == want
    return type(got) is type(want) and got == want


def _raises_ok(exc, name):
    if type(exc).__name__ == name:
        return True
    expected = getattr(builtins, name, None)
    return isinstance(expected, type) and isinstance(exc, expected)


# --- harness body (case runner) ---------------------------------------------
def _run(report):
    try:
        spec = json.load(open("spec.json", encoding="utf-8"))
        cases = json.load(open("cases.json", encoding="utf-8"))
        source = open("candidate.py", encoding="utf-8").read()
    except Exception as exc:
        report["fatal"] = "harness could not load inputs: %s" % (exc,)
        return

    namespace = {"__name__": "candidate", "__builtins__": builtins}
    try:
        exec(compile(source, "candidate.py", "exec"), namespace)
    except BaseException as exc:
        report["fatal"] = "candidate failed to load: %s: %s" % (type(exc).__name__, exc)
        return

    fn = namespace.get(spec.get("entrypoint"))
    if not callable(fn):
        report["fatal"] = "entrypoint %r not found or not callable" % (spec.get("entrypoint"),)
        return

    results = []
    for index, case in enumerate(cases):
        entry = {"i": index, "passed": False, "detail": ""}
        if not isinstance(case, dict):
            entry["detail"] = "malformed case"
            results.append(entry)
            continue
        args = copy.deepcopy(case.get("args", []))
        if not isinstance(args, list):
            entry["detail"] = "case args must be a list"
            results.append(entry)
            continue
        try:
            got = fn(*args)
        except BaseException as exc:
            if "raises" in case:
                name = str(case.get("raises"))
                if _raises_ok(exc, name):
                    entry["passed"] = True
                    entry["detail"] = "raised %s as expected" % (name,)
                else:
                    entry["detail"] = "raised %s, expected %s" % (type(exc).__name__, name)
            else:
                entry["detail"] = "raised %s: %s" % (type(exc).__name__, exc)
            results.append(entry)
            continue
        if "raises" in case:
            entry["detail"] = "did not raise %s" % (case.get("raises"),)
        elif "expect" in case:
            want = case["expect"]
            tol = float(case.get("tol", 0.0) or 0.0)
            if _eq(got, want, tol):
                entry["passed"] = True
                entry["detail"] = "ok"
            else:
                entry["detail"] = "got %r, want %r" % (got, want)
        else:
            entry["detail"] = "case has neither 'expect' nor 'raises'"
        entry["detail"] = str(entry["detail"])[:200]
        results.append(entry)

    report["cases"] = results


def main():
    # The parent passes env={}; CPython may still inject LC_CTYPE for locale
    # coercion. Drop it so candidate code genuinely sees an empty environment.
    os.environ.clear()
    report = {"cases": [], "fatal": None, "env": dict(os.environ), "cwd": os.getcwd()}
    sys.meta_path.insert(0, _BlockedImportFinder())
    sys.addaudithook(_audit)
    _run(report)
    try:
        with open(os.path.join(ROOT, RESULT), "w", encoding="utf-8") as fh:
            json.dump(report, fh)
    except BaseException:
        pass


main()
'''

_HARNESS_SPLIT = "# --- harness body (case runner) ---"
# Everything above the split is the hardening prelude: tempdir-only IO, an
# empty environment, the import blocker and the audit hook. The legacy suite
# harness below reuses it verbatim so T2/T6/T7/T8 get the same hardening as
# code_exec instead of a bare subprocess.
_HARNESS_PRELUDE = _HARNESS.split(_HARNESS_SPLIT)[0]

_LEGACY_HARNESS = _HARNESS_PRELUDE + r'''
import contextlib
import io


def _legacy_report():
    report = {"passed": False, "output": "", "fatal": None}
    try:
        source = open("candidate.py", encoding="utf-8").read()
        checks = open("checks.py", encoding="utf-8").read()
    except Exception as exc:
        report["fatal"] = "harness could not load inputs: %s" % (exc,)
        return report

    namespace = {"__name__": "candidate", "__builtins__": builtins}
    try:
        exec(compile(source, "candidate.py", "exec"), namespace)
    except BaseException as exc:
        report["fatal"] = "candidate failed to load: %s: %s" % (type(exc).__name__, exc)
        return report

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            exec(compile(checks, "checks.py", "exec"), namespace)
    except SystemExit as exc:
        report["output"] = buffer.getvalue()
        report["fatal"] = "checks exited with %s" % (exc.code,)
        return report
    except BaseException as exc:
        report["output"] = buffer.getvalue()
        report["fatal"] = "checks raised %s: %s" % (type(exc).__name__, exc)
        return report

    report["output"] = buffer.getvalue()
    report["passed"] = "ALL CHECKS PASSED" in report["output"]
    return report


def _legacy_main():
    os.environ.clear()
    sys.meta_path.insert(0, _BlockedImportFinder())
    sys.addaudithook(_audit)
    report = _legacy_report()
    try:
        with open(os.path.join(ROOT, RESULT), "w", encoding="utf-8") as fh:
            json.dump(report, fh)
    except BaseException:
        pass


_legacy_main()
'''


def _limit_resources() -> None:
    """preexec_fn: hard limits applied inside the child before exec."""
    try:
        import resource
    except ImportError:  # pragma: no cover - non-POSIX
        return
    limits = [
        (resource.RLIMIT_CPU, (EXEC_CPU_LIMIT_S, EXEC_CPU_LIMIT_S + 2)),
        (resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024)),
        (resource.RLIMIT_CORE, (0, 0)),
        (resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024)),
        (resource.RLIMIT_NPROC, (32, 32)),
    ]
    for which, value in limits:
        try:
            resource.setrlimit(which, value)
        except (ValueError, OSError):
            continue


def _run_sandboxed(code: str, harness_source: str, files: dict | None, timeout_s: float) -> dict:
    """Run candidate code plus a harness in the hardened subprocess.

    The single execution path for every scorer that runs model-written code:
    an empty environment, ``python -I``, rlimits and the audit hook / import
    blocker installed by the harness prelude.
    """
    wall = max(1, min(int(round(timeout_s or EXEC_CPU_LIMIT_S)), EXEC_WALL_CAP_S))
    with tempfile.TemporaryDirectory(prefix="kombat-run-") as td:
        root = Path(td)
        (root / "candidate.py").write_text(code, encoding="utf-8")
        (root / "harness.py").write_text(harness_source, encoding="utf-8")
        for name, text in (files or {}).items():
            (root / name).write_text(text, encoding="utf-8")
        log_path = root / "output.log"
        timed_out = False
        try:
            with log_path.open("wb") as log:
                subprocess.run(
                    [sys.executable, "-I", str(root / "harness.py")],
                    cwd=str(root),
                    env={},                       # stripped: no PATH, no HOME, no secrets
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=wall,
                    preexec_fn=_limit_resources if sys.platform != "win32" else None,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception as exc:  # runner machinery failure, not the model's fault
            return {"internal_error": f"{type(exc).__name__}: {exc}"}

        output = ""
        try:
            with log_path.open("rb") as fh:
                output = fh.read(MAX_STDOUT).decode("utf-8", "replace")
        except OSError:
            pass

        report = None
        result_path = root / "result.json"
        if result_path.exists():
            try:
                report = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                report = None
        return {"report": report, "timed_out": timed_out, "output": output, "wall_s": wall}


def _run_candidate(code: str, entrypoint: str, cases: list[dict], timeout_s: float) -> dict:
    """Run candidate code against the JSON case harness; return its report."""
    return _run_sandboxed(
        code,
        _HARNESS,
        {
            "spec.json": json.dumps({"entrypoint": entrypoint}),
            "cases.json": json.dumps(cases),
        },
        timeout_s,
    )


def run_legacy_harness(code: str, checks: str, timeout_s: float = 10) -> dict:
    """Run a legacy-suite candidate (T2/T6/T7/T8) in the hardened runner.

    ``checks`` is the legacy harness source, which prints its own FAIL lines
    and ends with ``ALL CHECKS PASSED``. It runs inside the same sandbox as
    code_exec — empty env, ``python -I``, rlimits, audit hook, import blocker —
    so no task in the suite has a softer execution path.

    Returns ``{"passed": bool, "details": [str, ...]}``.
    """
    raw = _run_sandboxed(code, _LEGACY_HARNESS, {"checks.py": checks}, timeout_s)
    if raw.get("internal_error"):
        return {"passed": False, "details": ["runner error: %s" % (raw["internal_error"],)]}
    report = raw.get("report") or {}
    if raw.get("timed_out"):
        return {"passed": False, "details": ["timeout after %ss" % (raw.get("wall_s"),)]}
    lines = [ln for ln in str(report.get("output") or "").splitlines() if ln.strip()]
    if report.get("fatal"):
        # The harness's own FAIL line is more useful than the exit code.
        return {"passed": False, "details": lines[-3:] if lines else [str(report["fatal"])]}
    return {
        "passed": bool(report.get("passed")),
        "details": lines[-3:] if lines else ["no output"],
    }


def _check_code_exec(check: dict, answer: str, allow_exec: bool) -> dict:
    entrypoint = check.get("entrypoint")
    cases = check.get("cases")
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        return _errored("code_exec: missing string 'entrypoint'")
    if not isinstance(cases, list) or not cases:
        return _errored("code_exec: 'cases' must be a non-empty list")
    if len(cases) > MAX_CASES:
        return _errored(f"code_exec: too many cases ({len(cases)} > {MAX_CASES})")
    for case in cases:
        if not isinstance(case, dict) or ("expect" not in case and "raises" not in case):
            return _errored("code_exec: every case needs 'expect' or 'raises'")
    try:
        timeout_s = float(check.get("timeout_s", 10) or 10)
    except (TypeError, ValueError):
        return _errored("code_exec: 'timeout_s' must be a number")
    if timeout_s <= 0:
        return _errored("code_exec: 'timeout_s' must be > 0")

    if not allow_exec:
        return _skipped("code_exec: execution disabled (allow_code_execution=false) — not scored")

    code = _extract_python(answer)
    if not code:
        return _scored(0.0, "code_exec: no python code block or function found in answer")

    run = _run_candidate(code, entrypoint.strip(), cases, timeout_s)
    if "internal_error" in run:
        return _errored(f"code_exec: runner failed: {run['internal_error']}")
    if run["timed_out"]:
        return _scored(0.0, f"code_exec: timed out after {run['wall_s']}s (0/{len(cases)} cases)")
    report = run["report"]
    if not isinstance(report, dict):
        tail = " | ".join(run["output"].strip().splitlines()[-2:])[:300]
        return _scored(0.0, f"code_exec: harness produced no report (0/{len(cases)} cases)"
                             + (f" — {tail}" if tail else ""))
    if report.get("fatal"):
        return _scored(0.0, f"code_exec: {report['fatal']} (0/{len(cases)} cases)")

    results = report.get("cases") or []
    passed = sum(1 for r in results if isinstance(r, dict) and r.get("passed"))
    total = len(cases)
    failed = [r for r in results if isinstance(r, dict) and not r.get("passed")]
    detail = f"code_exec: {passed}/{total} case(s) passed"
    if failed:
        bits = [f"#{r.get('i')} {r.get('detail', '')[:80]}" for r in failed[:3]]
        detail += "; failed " + ", ".join(bits)
    return _scored(passed / total, detail)


# ------------------------------------------------------------------ sql_exec

_SQL_FENCE_RE = re.compile(r"```sql[ \t]*\r?\n(.*?)```", re.S | re.I)
_SQL_ALLOWED = {"SELECT", "WITH"}
# setup snippets come from task specs, and .kombat/tasks.json is whatever repo
# you opened — untrusted input. ATTACH/DETACH would let a snippet mount any
# file on the host as a database, so they are refused outright.
_SQL_SETUP_FORBIDDEN = re.compile(r"\b(?:ATTACH|DETACH)\b", re.I)
_SQL_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _extract_sql(answer: str) -> str | None:
    block = _SQL_FENCE_RE.search(answer)
    if block:
        sql = block.group(1).strip()
        return sql or None
    text = answer.strip()
    # Bare SQL is accepted when it obviously starts with a read-only statement.
    if re.match(r"(?is)^\s*(select|with)\b", text):
        return text
    return None


def _strip_leading_comments(sql: str) -> str:
    text = sql
    while True:
        stripped = text.lstrip()
        if stripped.startswith("--"):
            newline = stripped.find("\n")
            if newline == -1:
                return ""
            text = stripped[newline + 1:]
            continue
        if stripped.startswith("/*"):
            end = stripped.find("*/")
            if end == -1:
                return ""
            text = stripped[end + 2:]
            continue
        return stripped


def _rows_equal(got: list, expected: list, ordered: bool) -> bool:
    def val_key(value):
        if isinstance(value, bool):
            return ("bool", value)
        if isinstance(value, (int, float)):
            return ("num", round(float(value), 9))
        if value is None:
            return ("none",)
        if isinstance(value, bytes):
            return ("bytes", value)
        return ("str", str(value))

    def norm(rows):
        return [[val_key(v) for v in row] for row in rows]

    if len(got) != len(expected):
        return False
    g, e = norm(got), norm(expected)
    if ordered:
        return g == e
    return sorted(g, key=repr) == sorted(e, key=repr)


def _check_sql_exec(check: dict, answer: str) -> dict:
    setup = check.get("setup", [])
    expect_rows = check.get("expect_rows")
    if not isinstance(setup, list) or not all(isinstance(s, str) for s in setup):
        return _errored("sql_exec: 'setup' must be a list of SQL strings")
    if not isinstance(expect_rows, list):
        return _errored("sql_exec: 'expect_rows' must be a list of rows")
    ordered = check.get("ordered", False)
    if not isinstance(ordered, bool):
        return _errored("sql_exec: 'ordered' must be a boolean")

    expected_norm = []
    for row in expect_rows:
        if not isinstance(row, (list, tuple)):
            return _errored("sql_exec: every expected row must be a list")
        expected_norm.append(list(row))

    sql = _extract_sql(str(answer))
    if not sql:
        return _scored(0.0, "sql_exec: no ```sql block (or bare SELECT/WITH) found")
    if len(sql) > MAX_SQL_BYTES:
        return _scored(0.0, "sql_exec: answer SQL is too large")

    stmt = _strip_leading_comments(sql).rstrip().rstrip(";").strip()
    if not stmt:
        return _scored(0.0, "sql_exec: empty SQL statement")
    match = re.match(r"([A-Za-z]+)", stmt)
    keyword = (match.group(1) if match else "").upper()
    if keyword not in _SQL_ALLOWED:
        return _scored(0.0, f"sql_exec: rejected statement type {keyword or '?'} — only SELECT/WITH allowed")

    for snippet in setup:
        if _SQL_SETUP_FORBIDDEN.search(_SQL_COMMENT_RE.sub(" ", snippet)):
            return _errored(
                "sql_exec: setup may not ATTACH/DETACH — setup runs against an in-memory database only"
            )

    conn = sqlite3.connect(":memory:")
    try:
        for snippet in setup:
            try:
                conn.executescript(snippet) if snippet.count(";") > 1 else conn.execute(snippet)
            except sqlite3.Error as exc:
                return _errored(f"sql_exec: setup failed: {exc}")
        # Belt and braces beyond the SELECT/WITH prefix check: even a
        # `WITH ... DELETE` cannot write after this.
        conn.execute("PRAGMA query_only = ON")
        try:
            cur = conn.execute(stmt)
            got = [list(row) for row in cur.fetchall()]
        except sqlite3.Error as exc:
            return _scored(0.0, f"sql_exec: query failed: {exc}")
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    if _rows_equal(got, expected_norm, ordered):
        return _scored(1.0, f"sql_exec: {len(got)} row(s) match ({'ordered' if ordered else 'unordered'})")
    return _scored(
        0.0,
        f"sql_exec: rows differ — got {got[:5]!r}, expected {expected_norm[:5]!r} "
        f"({'ordered' if ordered else 'unordered'})",
    )


# ------------------------------------------------------------------ dispatch

_SCORERS = {
    "exact": lambda check, answer, allow: _check_exact(check, answer),
    "numeric": lambda check, answer, allow: _check_numeric(check, answer),
    "phrases": lambda check, answer, allow: _check_phrases(check, answer),
    "regex": lambda check, answer, allow: _check_regex(check, answer),
    "constraints": lambda check, answer, allow: _check_constraints(check, answer),
    "json_fields": lambda check, answer, allow: _check_json_fields(check, answer),
    "code_exec": lambda check, answer, allow: _check_code_exec(check, answer, allow),
    "sql_exec": lambda check, answer, allow: _check_sql_exec(check, answer),
}


def score_check(check: dict, answer: str, allow_exec: bool = False) -> dict:
    """Score one answer against one mechanical check spec (SPEC §3).

    Never raises: malformed specs come back as status "errored", disabled
    execution as "skipped", and wrong answers as "scored" with score 0.0.
    """
    if not isinstance(check, dict):
        return _errored("check spec must be an object")
    check_type = check.get("type")
    scorer = _SCORERS.get(check_type) if isinstance(check_type, str) else None
    if scorer is None:
        return _errored(f"unknown check type {check_type!r}")
    if answer is None:
        answer = ""
    if not isinstance(answer, str):
        answer = str(answer)
    try:
        return scorer(check, answer, bool(allow_exec))
    except Exception as exc:  # noqa: BLE001 — scoring must never crash a fight
        return _errored(f"{check_type} scorer crashed: {type(exc).__name__}: {exc}")
