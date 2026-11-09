"""Model Kombat HTML renderers (arcade-artist owned; SPEC §7/§8).

Two entry points, both fully self-contained (inline CSS/JS, zero external
assets — no fonts, images, logos, sprites or audio; original 16-bit look
built with pure CSS):

- ``render_report(fighter_a, fighter_b, rounds_out, usage, winner, ko)`` —
  final scorecard. Same inputs and ``rounds_out`` shape as the legacy
  ``tools._render_report`` (per-task ``a``/``b`` dicts with ``score``/``max``/
  ``details``, ``excluded``, ``ko``), including its details and usage table.
- ``render_arena_preview(live)`` — standalone live arena page for a SPEC §6
  ``live.json`` payload (mid-fight, KO finish and error states included).

Both honour ``prefers-reduced-motion`` and keep high-contrast legible text.
"""

from __future__ import annotations

import html
from typing import Any, Mapping

__all__ = ["render_arena_preview", "render_report"]


# ---------------------------------------------------------------- stage css

_ARCADE_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:#0a0a12;color:#f2f2e8;
  font-family:"Courier New",ui-monospace,Menlo,Consolas,monospace;
  line-height:1.45;-webkit-text-size-adjust:100%}
.stage{position:relative;max-width:980px;margin:0 auto;padding:24px 18px 40px}
.crt{pointer-events:none;position:fixed;inset:0;z-index:50;
  background:repeating-linear-gradient(to bottom,
    rgba(255,255,255,.035) 0 1px,rgba(0,0,0,0) 1px 3px)}
.frame{border:4px solid #2e2e46;border-radius:8px;background:#10101c;
  box-shadow:0 0 0 2px #050508,0 0 28px rgba(80,80,180,.25),inset 0 0 60px rgba(0,0,0,.6);
  padding:18px 20px}
h1{margin:0 0 4px;font-size:1.9rem;letter-spacing:.18em;text-align:center;
  color:#ffe23d;text-shadow:3px 3px 0 #b3202e,6px 6px 0 rgba(0,0,0,.55);
  image-rendering:pixelated}
.tagline{text-align:center;margin:0 0 16px;color:#9db4ff;letter-spacing:.3em;
  text-transform:uppercase;font-size:.72rem}
.hp-row{display:grid;grid-template-columns:1fr auto 1fr;gap:10px;align-items:start;margin:8px 0 4px}
.fighter{min-width:0}
.fighter-name{font-weight:700;font-size:.95rem;color:#fff;word-break:break-word}
.fighter-id{color:#9db4ff;font-size:.72rem;word-break:break-word}
.hp-track{height:18px;border:3px solid #d8d8e8;border-radius:3px;background:#1a0606;
  box-shadow:inset 0 0 0 2px #000;margin-top:4px;overflow:hidden}
.hp-fill{height:100%;background:linear-gradient(to bottom,#ffe23d,#f5a400);
  box-shadow:inset 0 -3px 0 rgba(0,0,0,.35)}
.hp-fill.low{background:linear-gradient(to bottom,#ff8a7a,#d42a1d)}
.hp-num{font-size:.72rem;color:#cfd2e8;margin-top:3px}
.vs{align-self:center;font-size:1.5rem;font-weight:700;color:#ff5470;
  text-shadow:2px 2px 0 #000}
.banner{text-align:center;border:3px dashed #ffe23d;color:#ffe23d;
  padding:7px 10px;margin:14px 0 6px;letter-spacing:.22em;font-weight:700;
  text-transform:uppercase;font-size:.95rem;background:rgba(255,226,61,.06)}
.banner.error{border-color:#ff8a7a;color:#ff8a7a;background:rgba(255,82,64,.08)}
.banner.standby{border-color:#9db4ff;color:#9db4ff;background:rgba(80,110,255,.06)}
.stars{text-align:center;color:#ffe23d;font-size:1.05rem;letter-spacing:.4em;margin:2px 0 10px}
.meta{text-align:center;color:#cfd2e8;font-size:.8rem;margin:0 0 12px}
.meta b{color:#fff}
table{width:100%;border-collapse:collapse;margin:8px 0 18px;font-size:.85rem}
th,td{border:2px solid #3a3a56;padding:7px 8px;vertical-align:top;text-align:left}
th{background:#1d1d30;color:#ffe23d;letter-spacing:.08em}
td small{display:block;color:#b9bcce;font-size:.75rem;margin-top:2px;word-break:break-word}
.hit{animation:hitflash .5s steps(2) 2}
@keyframes hitflash{0%{box-shadow:inset 0 0 0 2px #ffffff}50%{box-shadow:inset 0 0 0 2px transparent}}
td.won{background:rgba(255,226,61,.12)}
td.won::before{content:"HIT! ";color:#ffe23d;font-weight:700}
.state{display:inline-block;border:2px solid currentColor;padding:0 5px;
  font-size:.68rem;letter-spacing:.1em;text-transform:uppercase}
.state.done{color:#7dff8a}.state.fighting{color:#ffe23d}
.state.error{color:#ff8a7a}.state.waiting,.state.skipped{color:#9a9cb2}
.ko-wrap{position:relative;text-align:center;margin:14px 0}
.ko{position:relative;display:inline-block;padding:6px 28px;font-size:2.6rem;font-weight:800;
  letter-spacing:.14em;color:#ffb9b0;border:6px double #ff2a1d;border-radius:6px;
  background:rgba(255,42,29,.12);transform:rotate(-6deg);
  box-shadow:0 0 22px rgba(255,42,29,.45);animation:kopop .45s steps(3) both}
@keyframes kopop{from{transform:rotate(-6deg) scale(2.4);opacity:0}
  to{transform:rotate(-6deg) scale(1);opacity:1}}
.winner-card{border:4px solid #ffe23d;background:#1a1a10;border-radius:6px;
  padding:12px;text-align:center;margin:12px 0}
.winner-card .who{font-size:1.25rem;font-weight:800;color:#ffe23d;word-break:break-word}
.winner-card .how{color:#cfd2e8;font-size:.85rem;margin-top:2px}
footer{margin-top:18px;color:#9a9cb2;font-size:.75rem;text-align:center}
:focus-visible{outline:3px solid #9db4ff;outline-offset:2px}
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
}
"""

# Tiny script: stamp the K.O. (no-op without JS; CSS alone shows it).
_ARCADE_JS = """
(function(){var k=document.querySelector('.ko');if(k){k.setAttribute('role','status');}})();
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        f"<style>{_ARCADE_CSS}</style>"
        "</head><body>"
        '<div class="crt" aria-hidden="true"></div>'
        f'<main class="stage"><div class="frame">{body}</div></main>'
        f"<script>{_ARCADE_JS}</script>"
        "</body></html>\n"
    )


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _as_mapping(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _stars(points: int) -> str:
    try:
        return "★" * max(0, min(6, int(points))) or "☆"
    except (TypeError, ValueError):
        return "☆"


def _hp_block(side: str, name: str, sub: str, hp: float) -> str:
    try:
        hp = max(0.0, min(100.0, float(hp)))
    except (TypeError, ValueError):
        hp = 100.0
    fill_cls = "hp-fill low" if hp <= 25 else "hp-fill"
    return (
        f'<div class="fighter"><div class="fighter-name">{_esc(name)}</div>'
        f'<div class="fighter-id">{_esc(sub)}</div>'
        f'<div class="hp-track" role="progressbar" aria-label="{_esc(name)} HP" '
        f'aria-valuenow="{hp:.0f}" aria-valuemin="0" aria-valuemax="100">'
        f'<div class="{fill_cls}" style="width:{hp:.1f}%"></div></div>'
        f'<div class="hp-num">HP {hp:.0f}/100</div></div>'
    )


def _usage_table(usage: Mapping[str, Any] | None) -> str:
    rows = ""
    for fighter, u in (usage or {}).items():
        if not isinstance(u, Mapping):
            u = {}
        rows += (
            f"<tr><td>{_esc(fighter)}</td>"
            f"<td>{float(u.get('latency_s', 0) or 0):.1f}s</td>"
            f"<td>{int(u.get('tokens', 0) or 0)}</td>"
            f"<td>${float(u.get('cost_usd', 0) or 0):.4f}</td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="4">No usage recorded.</td></tr>'
    return (
        "<h2>Usage</h2><table><tr><th>Fighter</th><th>Latency</th>"
        f"<th>Tokens</th><th>Est. cost</th></tr>{rows}</table>"
    )


def _fighter_label(fighters: Any, side: str) -> tuple[str, str]:
    info: Any = (fighters or {}).get(side) if isinstance(fighters, Mapping) else None
    if isinstance(info, Mapping):
        short = info.get("short") or info.get("id") or ("Fighter " + side.upper())
        fid = info.get("id") or ""
        return str(short), str(fid)
    if isinstance(info, str):
        return info, info
    return "Fighter " + side.upper(), ""


# -------------------------------------------------------------- arena preview

_STATUS_BANNERS = {
    "idle": ("STANDBY — NO FIGHT ON DECK", "standby"),
    "fighting": ("FIGHT!", ""),
    "scoring": ("SCORING…", "standby"),
    "done": ("FIGHT OVER", ""),
    "error": ("FIGHT ERROR — CHECK THE TABLE BELOW", "error"),
}


def _arena_task_row(task: Mapping[str, Any], name_a: str, name_b: str) -> str:
    def cell(side: str, name: str) -> str:
        t: Any = task.get(side)
        t = t if isinstance(t, Mapping) else {}
        state = str(t.get("state") or "waiting")
        score = t.get("score")
        score_txt = f"{float(score):.2f}" if score is not None else "—"
        meta = str(t.get("detail") or "")
        extra = []
        if t.get("latency_s") is not None:
            extra.append(f"{float(t['latency_s']):.1f}s")
        won = False
        other: Any = task.get("b" if side == "a" else "a")
        other = other if isinstance(other, Mapping) else {}
        if (
            state == "done"
            and score is not None
            and other.get("score") is not None
            and other.get("state") == "done"
            and float(score) > float(other.get("score") or 0)
        ):
            won = True
        cls = ' class="won hit"' if won else ""
        return (
            f"<td{cls}>{_esc(name)} <b>{_esc(score_txt)}</b> "
            f'<span class="state {state}">{_esc(state)}</span>'
            f"<small>{_esc(' · '.join(extra))}"
            f"{' · ' if extra and meta else ''}{_esc(meta)}</small></td>"
        )

    ko_note = ""
    if task.get("ko"):
        ko_note = ' <b style="color:#ff8a7a">★ K.O. ★</b>'
    lane = task.get("lane")
    return (
        f"<tr><td><b>{_esc(task.get('id', '?'))}</b>"
        f"<br><small>{_esc(task.get('title', ''))}"
        f"{' · ' + _esc(lane) if lane else ''}</small></td>"
        f"{cell('a', name_a)}{cell('b', name_b)}"
        f"<td>{ko_note or '—'}</td></tr>"
    )


def render_arena_preview(live: dict) -> str:
    """Render a standalone live arena page from a SPEC §6 live payload."""
    live = live if isinstance(live, Mapping) else {}
    fighters = live.get("fighters") or {}
    name_a, id_a = _fighter_label(fighters, "a")
    name_b, id_b = _fighter_label(fighters, "b")
    hp = _as_mapping(live.get("hp"))
    hp_a = float(hp.get("a", 100) if hp.get("a") is not None else 100)
    hp_b = float(hp.get("b", 100) if hp.get("b") is not None else 100)
    status = str(live.get("status") or "idle")
    banner_txt, banner_cls = _STATUS_BANNERS.get(status, (status.upper(), "standby"))

    points = _as_mapping(live.get("points"))
    card = _as_mapping(live.get("card"))
    lanes = ", ".join(str(x) for x in (card.get("lanes") or [])) or "—"
    meta = (
        f"FIGHT <b>{_esc(live.get('fight_id') or '???')}</b> · "
        f"CARD <b>{_esc(card.get('title') or 'Untitled')}</b> · "
        f"LANES <b>{_esc(lanes)}</b> · "
        f"POINTS <b>{_esc(name_a)} {float(points.get('a', 0) or 0):g} — "
        f"{_esc(name_b)} {float(points.get('b', 0) or 0):g}</b>"
    )

    # Current round banner: the fighting round, else the last done round.
    rounds = live.get("rounds") or []
    current = None
    for rnd in rounds:
        if isinstance(rnd, Mapping) and rnd.get("state") == "fighting":
            current = rnd
            break
    if current is None:
        for rnd in rounds:
            if isinstance(rnd, Mapping) and rnd.get("state") == "done":
                current = rnd
    round_banner = ""
    if current:
        cid = str(current.get("id", ""))
        idx = "ROUND " + cid if cid.isdigit() else cid
        round_banner = (
            f'<div class="banner">{_esc(idx)} — {_esc(current.get("name") or "FIGHT")}'
            f'<div class="stars" aria-label="{current.get("points", 0)} star round">'
            f"{_stars(current.get('points', 0) or 0)}</div></div>"
        )

    sections: list[str] = []
    for rnd in rounds:
        if not isinstance(rnd, Mapping):
            continue
        rows = "".join(
            _arena_task_row(t, name_a, name_b)
            for t in (rnd.get("tasks") or [])
            if isinstance(t, Mapping)
        )
        sections.append(
            f"<h2>{_esc(rnd.get('id', ''))} — {_esc(rnd.get('name', ''))} "
            f"({_esc(rnd.get('points', 0))} pt/task) "
            f'<span class="state {_esc(rnd.get("state", "waiting"))}">'
            f"{_esc(rnd.get('state', 'waiting'))}</span></h2>"
            "<table><tr><th>Task</th><th>Scorecard</th><th></th><th>Result</th></tr>"
            f"{rows}</table>"
        )

    sd = live.get("sudden_death")
    if isinstance(sd, Mapping):
        rows = "".join(
            _arena_task_row(t, name_a, name_b)
            for t in (sd.get("tasks") or [])
            if isinstance(t, Mapping)
        )
        if rows:
            sections.append(f"<h2>SD — Sudden Death</h2><table>{rows}</table>")

    ko_html = (
        '<div class="ko-wrap"><span class="ko">K.O.</span></div>'
        if live.get("ko")
        else ""
    )

    winner_html = ""
    if live.get("winner"):
        winner = _esc(live.get("winner"))
        how = "by K.O." if live.get("ko") else "wins the fight"
        winner_html = (
            '<div class="winner-card"><div class="who">WINNER: '
            f"{winner}</div><div class=\"how\">{_esc(how)}</div></div>"
        )

    report_note = ""
    if live.get("report_path"):
        report_note = f'<p class="meta">Full report: {_esc(live["report_path"])}</p>'

    usage = live.get("usage") if isinstance(live.get("usage"), Mapping) else {}
    usage_rows = {}
    for side, key in (("a", name_a), ("b", name_b)):
        u = usage.get(side)
        if isinstance(u, Mapping):
            usage_rows[key] = u
    body = (
        "<h1>MODEL KOMBAT</h1><p class=\"tagline\">insert coin · press start</p>"
        '<div class="hp-row">'
        f"{_hp_block('a', name_a, id_a, hp_a)}"
        '<div class="vs">VS</div>'
        f"{_hp_block('b', name_b, id_b, hp_b)}</div>"
        f'<div class="banner {banner_cls}">{_esc(banner_txt)}</div>'
        f"{round_banner}"
        f'<p class="meta">{meta}</p>'
        f"{ko_html}{winner_html}"
        + "".join(sections)
        + _usage_table(usage_rows)
        + report_note
        + '<footer>Scored mechanically by Model Kombat. Errored/skipped rounds are '
          "excluded from points. No models were judged by another model.</footer>"
    )
    return _page(f"Model Kombat — {name_a} vs {name_b}", body)


# ------------------------------------------------------------------- report

def _fraction_of(score: Mapping[str, Any]) -> float | None:
    if score.get("skipped") or score.get("errored") or not score.get("max"):
        return None
    try:
        return float(score["score"]) / float(score["max"])
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def render_report(
    fighter_a: str, fighter_b: str, rounds_out: list[dict], usage: dict,
    winner: str, ko: bool,
) -> str:
    """Render the final scorecard (legacy ``tools._render_report`` coverage)."""
    fighter_a = str(fighter_a or "Fighter A")
    fighter_b = str(fighter_b or "Fighter B")
    rounds_out = rounds_out or []

    # Points totals -> HP (damage = opponent's lead scaled to total, SPEC §6).
    points_a = sum(float(t.get("points_a", 0) or 0) for r in rounds_out for t in (r.get("tasks") or []))
    points_b = sum(float(t.get("points_b", 0) or 0) for r in rounds_out for t in (r.get("tasks") or []))
    total = points_a + points_b
    if total > 0:
        hp_a = 100.0 * (1.0 - points_b / total)
        hp_b = 100.0 * (1.0 - points_a / total)
    else:
        hp_a = hp_b = 100.0

    sections: list[str] = []
    for rnd in rounds_out:
        rows = ""
        for r in rnd.get("tasks") or []:
            sa, sb = r.get("a") or {}, r.get("b") or {}
            fa, fb = _fraction_of(sa), _fraction_of(sb)
            if r.get("excluded"):
                verdict = f"excluded ({r['excluded']})"
            elif fa is not None and fb is not None and fa > fb:
                verdict = f"{_esc(fighter_a)} takes the task"
            elif fa is not None and fb is not None and fb > fa:
                verdict = f"{_esc(fighter_b)} takes the task"
            else:
                verdict = "Task is a draw"
            ko_note = ' — <b style="color:#ff8a7a">K.O.</b>' if r.get("ko") else ""
            attr_a = ' class="won hit"' if (fa is not None and fb is not None and fa > fb) else ""
            attr_b = ' class="won hit"' if (fa is not None and fb is not None and fb > fa) else ""

            def _score_cell(score: Mapping[str, Any], attr: str) -> str:
                details = score.get("details") or []
                detail_txt = "; ".join(str(d) for d in details)
                return (
                    f"<td{attr}>{_esc(score.get('score', 0))}/{_esc(score.get('max', 0))}"
                    f"<br><small>{_esc(detail_txt)}</small></td>"
                )

            rows += (
                f"<tr><td><b>{_esc(r.get('id', '?'))}</b>"
                f"<br><small>{_esc(r.get('title', ''))}</small></td>"
                + _score_cell(sa, attr_a)
                + _score_cell(sb, attr_b)
                + f"<td>{verdict}{ko_note}</td></tr>"
            )
        sections.append(
            f"<h2>{_esc(rnd.get('id', ''))} — {_esc(rnd.get('name', ''))} "
            f"({_esc(rnd.get('points', 0))} pt/task)</h2>"
            f'<div class="stars">{_stars(rnd.get("points", 0) or 0)}</div>'
            "<table><tr><th>Task</th>"
            f"<th>{_esc(fighter_a)}</th><th>{_esc(fighter_b)}</th><th>Verdict</th></tr>"
            f"{rows}</table>"
        )

    ko_html = '<div class="ko-wrap"><span class="ko">K.O.</span></div>' if ko else ""
    winner_line = f"{_esc(winner)} wins by K.O." if ko else f"{_esc(winner)} wins the fight"
    body = (
        "<h1>MODEL KOMBAT</h1><p class=\"tagline\">final scorecard</p>"
        '<div class="hp-row">'
        f"{_hp_block('a', fighter_a, fighter_a, hp_a)}"
        '<div class="vs">VS</div>'
        f"{_hp_block('b', fighter_b, fighter_b, hp_b)}</div>"
        f'<p class="meta">POINTS <b>{_esc(fighter_a)} {points_a:g} — '
        f"{_esc(fighter_b)} {points_b:g}</b></p>"
        f"{ko_html}"
        f'<div class="winner-card"><div class="who">{winner_line}</div>'
        '<div class="how">mechanically scored — no model judged a model</div></div>'
        + "".join(sections)
        + _usage_table(usage or {})
        + "<footer>Scored mechanically by Model Kombat. Code rounds execute only "
          "with allow_code_execution=true (subprocess, timeout, not a security "
          "sandbox). Errored/skipped rounds are excluded from points.</footer>"
    )
    return _page(f"Model Kombat — {fighter_a} vs {fighter_b}", body)
