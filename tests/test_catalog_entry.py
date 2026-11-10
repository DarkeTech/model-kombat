"""Catalog-entry sanity checks (what the submission PR pins).

Parses `catalog/model-kombat.yaml` and asserts it is still consistent with
`plugin.yaml`. Runs with or without PyYAML: the structural checks that need a
parser are skipped when `yaml` is unavailable (system python usually has none,
`uv run --with pyyaml` does).

Run: python3 tests/test_catalog_entry.py
"""

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY = ROOT / "catalog" / "model-kombat.yaml"
PLACEHOLDER = "0" * 40

try:  # optional dependency
    import yaml  # type: ignore
except Exception:  # noqa: BLE001
    yaml = None


def _raw():
    return ENTRY.read_text(encoding="utf-8")


def _grab(pattern, text):
    found = re.search(pattern, text, re.M)
    assert found is not None, f"pattern not found: {pattern}"
    return found.group(1)


def _manifest_tools():
    lines = (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines()
    start = next(n for n, line in enumerate(lines) if line.strip().startswith("provides_tools:"))
    out = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
        elif stripped and not stripped.startswith("#"):
            break
    return out


def _entry_tools(raw):
    lines = raw.splitlines()
    start = next(n for n, line in enumerate(lines) if line.strip().startswith("provides_tools:"))
    out = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
        elif stripped and not stripped.startswith("#"):
            break
    return out


def test_entry_parses():
    if yaml is None:
        print("  (skipped: PyYAML not installed)")
        return
    data = yaml.safe_load(_raw())
    assert isinstance(data, dict), type(data)
    for key in ("name", "repo", "sha", "maintainer", "tier", "category",
                "requires_hermes", "version", "description", "capabilities"):
        assert key in data, f"missing key {key}"


def test_name_and_repo():
    raw = _raw()
    assert re.search(r"^name: model-kombat$", raw, re.M)
    assert re.search(r"^repo: https://github\.com/DarkeTech/model-kombat$", raw, re.M)
    assert re.search(r"^tier: community$", raw, re.M)
    assert re.search(r"^category: models$", raw, re.M)


def test_version_matches_manifest():
    raw = _raw()
    entry_version = _grab(r'^version: "(.+)"$', raw)
    plugin_version = _grab(r"^version: (.+)$", (ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    assert entry_version == plugin_version, (entry_version, plugin_version)


def test_requires_hermes_matches_manifest():
    raw = _raw()
    entry_req = _grab(r'^requires_hermes: "(.+)"$', raw)
    plugin_req = _grab(r"^requires_hermes: \"?([^\"]+)\"?$",
                       (ROOT / "plugin.yaml").read_text(encoding="utf-8")).strip()
    assert entry_req == plugin_req, (entry_req, plugin_req)


def test_declared_tools_match_manifest():
    assert _entry_tools(_raw()) == _manifest_tools()


def test_sha_placeholder_and_pinned_urls():
    raw = _raw()
    # The in-repo draft keeps every pin at 40 zeros so one `sed` swaps them at PR time.
    assert raw.count(PLACEHOLDER) == 7, raw.count(PLACEHOLDER)
    # Only the image/screenshot URLs are pinned assets; `docs_url`/`repo` are plain links.
    assets = raw.split("image:", 1)[1].split("capabilities:", 1)[0]
    for url in re.findall(r"https?://\S+", assets):
        assert url.startswith("https://"), url
        assert "githubusercontent.com" in url, url
        assert PLACEHOLDER in url, f"unpinned asset url: {url}"
    sha_line = _grab(r'^sha: "(.+)"$', raw)
    assert re.fullmatch(r"[0-9a-f]{40}", sha_line), sha_line


def test_description_is_one_line_and_discloses():
    if yaml is None:
        print("  (skipped: PyYAML not installed)")
        return
    data = yaml.safe_load(_raw())
    desc = data["description"]
    assert "\n" not in desc, "description must be one line"
    assert "Disclosure" in desc, "description must carry the Disclosure line"
    # Quoted scalars: a stray " would break the YAML — test_entry_parses covers it,
    # this one keeps the prose from creeping past a readable length.
    assert len(desc) <= 1800, len(desc)


def test_screenshots_at_most_six():
    raw = _raw()
    block = raw.split("screenshots:", 1)[1].split("capabilities:", 1)[0]
    count = len(re.findall(r"^\s+-\s", block, re.M))
    assert 0 < count <= 6, count


def main():
    tests = [v for n, v in sorted(globals().items()) if n.startswith("test_") and callable(v)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - test harness
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} catalog-entry tests passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
