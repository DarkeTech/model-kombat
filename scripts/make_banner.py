#!/usr/bin/env python3
"""Render the Model Kombat catalog banner (1200x600) via headless chromium.

Original neon cyan/magenta design, no third-party marks/logos/sprites/audio:
title "MODEL KOMBAT — head-to-head model benchmark arena", two HP bars and a
K.O. stamp, all pure CSS/HTML. Writes docs/banner.png.

Run: python3 scripts/make_banner.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "banner.png"
WIDTH, HEIGHT = 1200, 600
CHROMIUM_CANDIDATES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]

_BANNER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Model Kombat</title>
<style>
:root{color-scheme:dark;--cyan:#00e5ff;--magenta:#ff2ea6;--ink:#e8fbff}
*{box-sizing:border-box}
html,body{margin:0;padding:0;width:1200px;height:600px;overflow:hidden}
body{background:#07070f;color:var(--ink);
 font-family:"Courier New",ui-monospace,Menlo,Consolas,monospace}
.banner{position:relative;width:1200px;height:600px;padding:36px 48px;
 border:3px solid var(--cyan);background:#0b0b1a;
 box-shadow:0 0 22px rgba(0,229,255,.28),inset 0 0 40px rgba(255,46,166,.08);
 clip-path:polygon(0 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%)}
.wordmark{display:flex;align-items:center;justify-content:center;gap:22px;margin:8px 0 4px}
.wordmark svg{flex:0 0 auto}
.wordmark .wm-text{font-size:58px;font-weight:400;letter-spacing:.32em;text-transform:uppercase;
 color:var(--ink);-webkit-text-stroke:1.5px var(--cyan);
 text-shadow:0 0 14px rgba(0,229,255,.45)}
.tagline{text-align:center;margin:0 0 34px;color:var(--magenta);
 letter-spacing:.22em;text-transform:uppercase;font-size:24px}
.hp-row{display:grid;grid-template-columns:1fr auto 1fr;gap:24px;align-items:start}
.fighter-name{font-weight:700;font-size:28px;color:#fff}
.fighter-id{color:#9a9cb2;font-size:18px}
.hp-track{height:26px;border:3px solid var(--cyan);background:#12060f;
 margin-top:8px;overflow:hidden;transform:skewX(-12deg)}
.hp-track.b{border-color:var(--magenta)}
.hp-fill{height:100%;background:linear-gradient(to bottom,var(--cyan),#0088aa)}
.hp-fill.b{background:linear-gradient(to bottom,var(--magenta),#a1116b)}
.hp-num{font-size:18px;color:#cfd2e8;margin-top:6px}
.vs{align-self:center;font-size:44px;font-weight:700;color:var(--magenta);
 text-shadow:3px 3px 0 #000;transform:rotate(-3deg)}
.below{display:flex;justify-content:center;margin-top:42px}
.ko{display:inline-block;padding:6px 44px;font-size:64px;font-weight:800;
 letter-spacing:.14em;color:#fff;border:6px double var(--magenta);border-radius:4px;
 background:rgba(255,46,166,.18);transform:rotate(-5deg);
 box-shadow:0 0 18px rgba(255,46,166,.5),inset 0 0 12px rgba(0,229,255,.25)}
footer{position:absolute;left:0;right:0;bottom:18px;text-align:center;
 color:#9a9cb2;font-size:16px;letter-spacing:.12em;text-transform:uppercase}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
</style></head><body>
<main class="banner">
<h1 class="wordmark"><svg width="64" height="32" viewBox="0 0 72 36" aria-hidden="true" focusable="false"><path d="M4 6 L20 18 L4 30" fill="none" stroke="#00e5ff" stroke-width="3" stroke-linecap="square"/><path d="M68 6 L52 18 L68 30" fill="none" stroke="#ff2ea6" stroke-width="3" stroke-linecap="square"/><path d="M36 9 V27 M28 18 H44" fill="none" stroke="#e8fbff" stroke-width="3" stroke-linecap="square"/></svg><span class="wm-text">Model Kombat</span><svg width="64" height="32" viewBox="0 0 72 36" aria-hidden="true" focusable="false" style="transform:scaleX(-1)"><path d="M4 6 L20 18 L4 30" fill="none" stroke="#00e5ff" stroke-width="3" stroke-linecap="square"/><path d="M68 6 L52 18 L68 30" fill="none" stroke="#ff2ea6" stroke-width="3" stroke-linecap="square"/><path d="M36 9 V27 M28 18 H44" fill="none" stroke="#e8fbff" stroke-width="3" stroke-linecap="square"/></svg></h1>
<p class="tagline">head-to-head model benchmark arena</p>
<div class="hp-row">
<div><div class="fighter-name">FIGHTER A</div><div class="fighter-id">challenger</div>
<div class="hp-track" role="progressbar" aria-label="Fighter A HP" aria-valuenow="82" aria-valuemin="0" aria-valuemax="100"><div class="hp-fill" style="width:82%"></div></div>
<div class="hp-num">HP 82/100</div></div>
<div class="vs">VS</div>
<div><div class="fighter-name">FIGHTER B</div><div class="fighter-id">challenger</div>
<div class="hp-track b" role="progressbar" aria-label="Fighter B HP" aria-valuenow="64" aria-valuemin="0" aria-valuemax="100"><div class="hp-fill b" style="width:64%"></div></div>
<div class="hp-num">HP 64/100</div></div>
</div>
<div class="below"><span class="ko">K.O.</span></div>
<footer>Mechanically scored &middot; no model judged a model</footer>
</main>
</body></html>
"""


def find_chromium() -> str | None:
    for name in CHROMIUM_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


def main() -> int:
    chromium = find_chromium()
    if not chromium:
        print("ERROR: no chromium binary found (tried: %s)" % ", ".join(CHROMIUM_CANDIDATES),
              file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as fh:
        fh.write(_BANNER_HTML)
        html_path = Path(fh.name)
    try:
        cmd = [
            chromium, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", f"--window-size={WIDTH},{HEIGHT}",
            f"--screenshot={OUT}", html_path.as_uri(),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not OUT.is_file() or OUT.stat().st_size == 0:
            print(f"ERROR: screenshot failed rc={result.returncode}\n{(result.stderr or '')[-2000:]}",
                  file=sys.stderr)
            return 1
    finally:
        html_path.unlink(missing_ok=True)
    print(f"{OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
