#!/usr/bin/env python3
"""Render HTML previews from tests/fixtures/live_sample_*.json and screenshot them.

Writes HTML into docs/previews/ and PNG screenshots (1280x800) next to them
using headless chromium. stdlib only.

Run: python3 scripts/render_previews.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from render import render_arena_preview  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
PREVIEWS = ROOT / "docs" / "previews"
CHROMIUM_CANDIDATES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]


def find_chromium() -> str | None:
    for name in CHROMIUM_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def screenshot(chromium: str, html_path: Path, png_path: Path) -> None:
    cmd = [
        chromium,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=1280,800",
        f"--screenshot={png_path}",
        html_path.as_uri(),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not png_path.is_file() or png_path.stat().st_size == 0:
        raise RuntimeError(
            f"screenshot failed for {html_path.name}: rc={result.returncode}\n"
            + (result.stderr or "")[-2000:]
        )


def main() -> int:
    PREVIEWS.mkdir(parents=True, exist_ok=True)
    chromium = find_chromium()
    if not chromium:
        print("ERROR: no chromium binary found (tried: %s)" % ", ".join(CHROMIUM_CANDIDATES),
              file=sys.stderr)
        return 1
    print(f"chromium: {chromium}")

    fixtures = sorted(FIXTURES.glob("live_sample_*.json"))
    if not fixtures:
        print(f"ERROR: no fixtures in {FIXTURES}", file=sys.stderr)
        return 1

    for fixture in fixtures:
        live = json.loads(fixture.read_text(encoding="utf-8"))
        html_name = f"arena_{fixture.stem.replace('live_sample_', '')}.html"
        png_name = html_name.replace(".html", ".png")
        html_path = PREVIEWS / html_name
        png_path = PREVIEWS / png_name
        html_path.write_text(render_arena_preview(live), encoding="utf-8")
        screenshot(chromium, html_path, png_path)
        print(f"{html_path} ({html_path.stat().st_size} bytes)")
        print(f"{png_path} ({png_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
