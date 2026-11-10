"""Model Kombat HTML renderers (arcade-artist owned; SPEC §7/§8).

Originality note: this UI is an original neon-cyan/magenta benchmark arena
with angled panels and a stamp-style K.O. mark; it is not a copy of any
specific fighting game's layout, palette, or presentation.

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
:root{color-scheme:dark;--cyan:#00e5ff;--magenta:#ff2ea6;--ink:#e8fbff;--amber:#ffb84d}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:#07070f;color:var(--ink);
  font-family:"Courier New",ui-monospace,Menlo,Consolas,monospace;
  line-height:1.35;-webkit-text-size-adjust:100%}
.stage{position:relative;max-width:1120px;margin:0 auto;padding:4px 10px 4px}
.crt{pointer-events:none;position:fixed;inset:0;z-index:50;
  background:repeating-linear-gradient(to bottom,
    rgba(0,229,255,.03) 0 1px,rgba(0,0,0,0) 1px 3px)}
.frame{position:relative;border:3px solid var(--cyan);border-radius:4px;background:#0b0b1a;
  box-shadow:0 0 0 2px #050508,0 0 22px rgba(0,229,255,.28),inset 0 0 40px rgba(255,46,166,.08);
  padding:8px 12px;clip-path:polygon(0 0,100% 0,100% calc(100% - 14px),calc(100% - 14px) 100%,0 100%)}
.wordmark{display:flex;align-items:center;justify-content:center;gap:10px;margin:0 0 2px}
.wordmark svg{flex:0 0 auto}
.wordmark .wm-text{font-size:1.55rem;font-weight:400;letter-spacing:.30em;text-transform:uppercase;
 color:var(--ink);-webkit-text-stroke:1px var(--cyan);
 text-shadow:0 0 10px rgba(0,229,255,.35)}
.tagline{text-align:center;margin:0 0 8px;color:var(--magenta);letter-spacing:.28em;
  text-transform:uppercase;font-size:.65rem}
.hp-row{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:start;margin:2px 0}
.fighter{min-width:0}
.fighter-name{font-weight:700;font-size:.88rem;color:#fff;word-break:break-word}
.fighter-id{color:var(--cyan);font-size:.66rem;word-break:break-word}
.hp-track{height:13px;border:2px solid var(--cyan);border-radius:2px;background:#12060f;
  box-shadow:inset 0 0 0 2px #000;margin-top:3px;overflow:hidden;transform:skewX(-12deg)}
.hp-fill{height:100%;background:linear-gradient(to bottom,var(--cyan),#0088aa);
  box-shadow:inset 0 -3px 0 rgba(0,0,0,.35)}
.hp-fill.low{background:linear-gradient(to bottom,var(--magenta),#a1116b)}
.hp-num{font-size:.65rem;color:#cfd2e8;margin-top:2px}
.vs{align-self:center;font-size:1.25rem;font-weight:700;color:var(--magenta);
  text-shadow:2px 2px 0 #000;transform:rotate(-3deg)}
.banner{text-align:center;border:2px dashed var(--magenta);color:var(--magenta);
  padding:3px 8px;margin:5px 0 2px;letter-spacing:.18em;font-weight:700;
  text-transform:uppercase;font-size:.82rem;background:rgba(255,46,166,.07);
  transform:skewX(-4deg)}
.banner.error{border-color:#ff8a7a;color:#ff8a7a;background:rgba(255,82,64,.08)}
.banner.standby{border-color:var(--cyan);color:var(--cyan);background:rgba(0,229,255,.06)}
.banner.draw{border-style:solid;border-color:var(--cyan);color:var(--cyan);background:rgba(0,229,255,.06)}
.banner.sudden{border-color:var(--amber);color:var(--amber);background:rgba(255,184,77,.08)}
.edge-line{text-align:center;color:#cfd2e8;font-size:.72rem;margin:2px 0 4px}
.budget-strip{border:2px solid var(--amber);color:var(--amber);background:rgba(255,184,77,.08);
  text-align:center;padding:4px 8px;margin:5px 0;letter-spacing:.12em;font-weight:700;
  text-transform:uppercase;font-size:.78rem}
.sd-outcome{text-align:center;color:#fff;font-size:.78rem;margin:2px 0 4px}
.stars{text-align:center;color:var(--cyan);font-size:.9rem;letter-spacing:.35em;margin:1px 0 4px}
.meta{text-align:center;color:#cfd2e8;font-size:.72rem;margin:0 0 4px;word-break:break-word}
.meta b{color:#fff}
h2{font-size:.8rem;margin:5px 0 2px;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;margin:1px 0 5px;font-size:.78rem}
th,td{border:1px solid #2a4a66;padding:3px 6px;vertical-align:top;text-align:left}
th{background:#12122a;color:var(--cyan);letter-spacing:.06em}
td small{display:block;color:#b9bcce;font-size:.68rem;margin-top:1px;word-break:break-word}
.hit{animation:hitflash .5s steps(2) 2}
@keyframes hitflash{0%{box-shadow:inset 0 0 0 2px #ffffff}50%{box-shadow:inset 0 0 0 2px transparent}}
td.won-a{background:rgba(0,229,255,.10)}
td.won-a::before{content:"HIT! ";color:var(--cyan);font-weight:700}
td.won-b{background:rgba(255,46,166,.10)}
td.won-b::before{content:"HIT! ";color:var(--magenta);font-weight:700}
td.result-draw{color:#cfd2e8;font-weight:700;text-transform:uppercase}
td.result-error{color:#ff8a7a;font-weight:700}
td.result-skipped{color:#9db4ff;font-weight:700}
.state{display:inline-block;border:1px solid currentColor;padding:0 4px;
  font-size:.60rem;letter-spacing:.08em;text-transform:uppercase}
.state.done{color:#7dff8a}.state.fighting{color:var(--cyan)}
.state.error{color:#ff8a7a}.state.waiting,.state.skipped{color:#9a9cb2}
.ko-wrap{position:relative;text-align:center;margin:4px 0}
.ko{position:relative;display:inline-block;padding:3px 22px;font-size:1.9rem;font-weight:800;
  letter-spacing:.14em;color:#fff;border:4px double var(--magenta);border-radius:2px;
  background:rgba(255,46,166,.18);transform:rotate(-5deg);
  box-shadow:0 0 18px rgba(255,46,166,.5),inset 0 0 12px rgba(0,229,255,.25);animation:kopop .45s steps(3) both}
@keyframes kopop{from{transform:rotate(-5deg) scale(2.4);opacity:0}
  to{transform:rotate(-5deg) scale(1);opacity:1}}
.winner-card{border:3px solid var(--cyan);background:#08121a;border-radius:4px;
  padding:5px;text-align:center;margin:5px 0;clip-path:polygon(0 0,100% 0,100% calc(100% - 10px),calc(100% - 10px) 100%,0 100%)}
.winner-card .who{font-size:1.05rem;font-weight:800;color:var(--cyan);word-break:break-word}
.winner-card .how{color:#cfd2e8;font-size:.78rem;margin-top:1px}
footer{margin-top:4px;color:#9a9cb2;font-size:.68rem;text-align:center}
:focus-visible{outline:3px solid var(--cyan);outline-offset:2px}
@media (max-height:860px){
  .stage{padding-top:2px;padding-bottom:2px}
  .wordmark .wm-text{font-size:1.3rem}.tagline{margin-bottom:4px}
  .hp-track{height:10px}.ko{font-size:1.45rem;padding:2px 16px}
  .winner-card .who{font-size:.9rem}table{font-size:.72rem}
  th,td{padding:2px 5px}
  .banner{font-size:.75rem}.stars{font-size:.75rem}
}
@media (max-width:700px){
  .hp-row{grid-template-columns:1fr;gap:4px}.vs{justify-self:center}
  .stage{padding:6px 6px 8px}.frame{padding:8px 9px}.wordmark .wm-text{font-size:1.25rem}
}
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
}
"""

# Tiny script: stamp the K.O. (no-op without JS; CSS alone shows it).
_ARCADE_JS = """
(function(){var k=document.querySelector('.ko');if(k){k.setAttribute('role','status');}})();
"""

# Original wordmark: thin-outlined monospace caps with a single cyan glow,
# flanked by a >+< crossed-bars emblem (inline SVG, no third-party marks).
_WORDMARK_HTML = (
    '<h1 class="wordmark">'
    '<svg width="30" height="15" viewBox="0 0 72 36" aria-hidden="true" focusable="false">'
    '<path d="M4 6 L20 18 L4 30" fill="none" stroke="#00e5ff" stroke-width="3" stroke-linecap="square"/>'
    '<path d="M68 6 L52 18 L68 30" fill="none" stroke="#ff2ea6" stroke-width="3" stroke-linecap="square"/>'
    '<path d="M36 9 V27 M28 18 H44" fill="none" stroke="#e8fbff" stroke-width="3" stroke-linecap="square"/></svg>'
    '<span class="wm-text">Model Kombat</span>'
    '<svg width="30" height="15" viewBox="0 0 72 36" aria-hidden="true" focusable="false" style="transform:scaleX(-1)">'
    '<path d="M4 6 L20 18 L4 30" fill="none" stroke="#00e5ff" stroke-width="3" stroke-linecap="square"/>'
    '<path d="M68 6 L52 18 L68 30" fill="none" stroke="#ff2ea6" stroke-width="3" stroke-linecap="square"/>'
    '<path d="M36 9 V27 M28 18 H44" fill="none" stroke="#e8fbff" stroke-width="3" stroke-linecap="square"/></svg>'
    '</h1>'
)


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


def _cost_text(u: Mapping[str, Any]) -> str:
    """Cost display: null -> 'n/a'; estimated -> ' est.' suffix; else $x.xxxx."""
    cost = u.get("cost_usd")
    if cost is None or isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return "n/a"
    text = f"${float(cost):.4f}"
    if str(u.get("cost_source") or "") == "estimated":
        text += " est."
    return text


def _int_or_dash(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return "—"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "—"


def _usage_table(usage: Mapping[str, Any] | None) -> str:
    rows = ""
    for fighter, u in (usage or {}).items():
        if not isinstance(u, Mapping):
            u = {}
        rows += (
            f"<tr><td>{_esc(fighter)}</td>"
            f"<td>{float(u.get('latency_s', 0) or 0):.1f}s</td>"
            f"<td>{_int_or_dash(u.get('tokens'))}</td>"
            f"<td>{_int_or_dash(u.get('input_tokens'))}</td>"
            f"<td>{_int_or_dash(u.get('output_tokens'))}</td>"
            f"<td>{_esc(_cost_text(u))}</td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="6">No usage recorded.</td></tr>'
    return (
        "<h2>Usage</h2><table><tr><th>Fighter</th><th>Latency</th>"
        f"<th>Tokens</th><th>Input tokens</th><th>Output tokens</th><th>Est. cost</th></tr>{rows}</table>"
    )


def _short_for(fighter: Any, name_a: str, id_a: str, name_b: str, id_b: str) -> str:
    """Map an engine fighter reference (id / short / 'a'/'b') to a short name."""
    ref = str(fighter or "").strip()
    if ref in ("a", name_a, id_a) and ref:
        return name_a
    if ref in ("b", name_b, id_b) and ref:
        return name_b
    return ref


def _edge_line(edge: Any, name_a: str, id_a: str, name_b: str, id_b: str) -> str:
    if not isinstance(edge, Mapping):
        return ""
    reason = str(edge.get("reason") or edge.get("label") or "").strip()
    fighter = edge.get("fighter")
    if fighter:
        short = _short_for(fighter, name_a, id_a, name_b, id_b)
        text = f"efficiency edge: {short} — {reason}" if reason else f"efficiency edge: {short}"
    else:
        text = f"efficiency edge: {reason}" if reason else "efficiency edge"
    value = str(edge.get("value_summary") or "").strip()
    if value:
        text = f"{text} · {value}"
    if edge.get("provisional"):
        text = f"{text} (provisional — fight had errored calls)"
    return f'<p class="edge-line">{_esc(text)}</p>'


def _budget_strip(budget: Any, budget_exceeded: Any = None) -> str:
    exceeded = False
    reason = ""
    if isinstance(budget, Mapping):
        exceeded = bool(budget.get("exceeded"))
        reason = str(budget.get("reason") or "").strip()
    if budget_exceeded is not None:
        exceeded = bool(budget_exceeded) or exceeded
    if not exceeded:
        return ""
    suffix = f" ({reason})" if reason else ""
    return (
        '<div class="budget-strip">BUDGET CAP HIT — remaining tasks skipped'
        f"{_esc(suffix)}</div>"
    )


def _sd_used(sd: Any) -> bool:
    """True only when sudden death was actually fought, not just carded/up-front."""
    if not isinstance(sd, Mapping):
        return False
    if sd.get("used") or sd.get("fought"):
        return True
    if str(sd.get("state") or "") in ("fighting", "done", "scoring", "error"):
        return True
    for task in sd.get("tasks") or []:
        if not isinstance(task, Mapping):
            continue
        for side in ("a", "b"):
            slot = task.get(side)
            if isinstance(slot, Mapping) and str(slot.get("state") or "waiting") != "waiting":
                return True
    return False


def _incomplete_round_ids(live: Mapping[str, Any]) -> set:
    ids: set = set()
    raw = live.get("incomplete_rounds")
    if isinstance(raw, (list, tuple)):
        ids.update(str(x) for x in raw)
    for rnd in live.get("rounds") or []:
        if isinstance(rnd, Mapping) and rnd.get("incomplete"):
            ids.add(str(rnd.get("id", "")))
    return ids


_INCOMPLETE_NOTE = "* round excluded from points (errored/skipped)"


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
    "idle": ("STANDBY — NO BOUT ON DECK", "standby"),
    "fighting": ("FIGHT ON", ""),
    "scoring": ("SCORING…", "standby"),
    "done": ("FIGHT OVER", ""),
    "error": ("BOUT ERROR — CHECK THE TABLE BELOW", "error"),
}


def _arena_task_row(task: Mapping[str, Any], name_a: str, name_b: str) -> str:
    def cell(side: str, name: str) -> str:
        t: Any = task.get(side)
        t = t if isinstance(t, Mapping) else {}
        state = str(t.get("state") or "waiting")
        score = t.get("score")
        if state == "skipped":
            score_txt = "SKIPPED"
        elif state == "error":
            score_txt = "ERROR"
        else:
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
        cls = f' class="won-{side} hit"' if won else ""
        return (
            f"<td{cls}>{_esc(name)} <b>{_esc(score_txt)}</b> "
            f'<span class="state {state}">{_esc(state)}</span>'
            f"<small>{_esc(' · '.join(extra))}"
            f"{' · ' if extra and meta else ''}{_esc(meta)}</small></td>"
        )

    ko_note = ""
    if task.get("ko"):
        ko_note = ' <b style="color:#ff2ea6">★ K.O. ★</b>'
    # Result column: DRAW only when both scored and equal; SKIPPED/ERROR distinct.
    ta = _as_mapping(task.get("a"))
    tb = _as_mapping(task.get("b"))
    sa_state, sb_state = str(ta.get("state") or ""), str(tb.get("state") or "")
    sa_score, sb_score = ta.get("score"), tb.get("score")
    result_txt, result_cls = "—", ""
    if ko_note:
        result_txt, result_cls = ko_note, ""
    elif "error" in (sa_state, sb_state):
        result_txt, result_cls = "ERROR", " result-error"
    elif "skipped" in (sa_state, sb_state):
        result_txt, result_cls = "SKIPPED", " result-skipped"
    elif sa_state == "done" and sb_state == "done" and sa_score is not None and sb_score is not None:
        if float(sa_score) == float(sb_score):
            result_txt, result_cls = "DRAW", " result-draw"
        elif float(sa_score) > float(sb_score):
            result_txt = f"{_esc(name_a)} takes the task"
        else:
            result_txt = f"{_esc(name_b)} takes the task"
    lane = task.get("lane")
    return (
        f"<tr><td><b>{_esc(task.get('id', '?'))}</b>"
        f"<br><small>{_esc(task.get('title', ''))}"
        f"{' · ' + _esc(lane) if lane else ''}</small></td>"
        f"{cell('a', name_a)}{cell('b', name_b)}"
        f'<td class="result{result_cls}">{result_txt}</td></tr>'
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
        f"BOUT <b>{_esc(live.get('fight_id') or '???')}</b> · "
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
        cname = str(current.get("name") or "BOUT")
        idx = f"ROUND {cid} · {cname}" if cid.isdigit() else f"{cid} · {cname}" if cid else cname
        round_banner = (
            f'<div class="banner">{_esc(idx)}'
            f'<div class="stars" aria-label="{current.get("points", 0)} star round">'
            f"{_stars(current.get('points', 0) or 0)}</div></div>"
        )

    sections: list[str] = []
    incomplete_ids = _incomplete_round_ids(live)
    for rnd in rounds:
        if not isinstance(rnd, Mapping):
            continue
        rows = "".join(
            _arena_task_row(t, name_a, name_b)
            for t in (rnd.get("tasks") or [])
            if isinstance(t, Mapping)
        )
        rname = str(rnd.get("name", ""))
        if str(rnd.get("id", "")) in incomplete_ids and not rname.endswith("*"):
            rname = f"{rname} *" if rname else "*"
        sections.append(
            f"<h2>{_esc(rnd.get('id', ''))} — {_esc(rname)} "
            f"({_esc(rnd.get('points', 0))} pt/task) "
            f'<span class="state {_esc(rnd.get("state", "waiting"))}">'
            f"{_esc(rnd.get('state', 'waiting'))}</span></h2>"
            f"<table><tr><th>Task</th><th>{_esc(name_a)}</th><th>{_esc(name_b)}</th><th>Result</th></tr>"
            f"{rows}</table>"
        )

    sd = live.get("sudden_death")
    if _sd_used(sd) and isinstance(sd, Mapping):
        rows = "".join(
            _arena_task_row(t, name_a, name_b)
            for t in (sd.get("tasks") or [])
            if isinstance(t, Mapping)
        )
        if rows:
            sd_winner = live.get("sudden_death_winner")
            sd_outcome = ""
            if sd_winner:
                sd_outcome = (
                    '<p class="sd-outcome">SUDDEN DEATH winner: '
                    f"{_esc(_short_for(sd_winner, name_a, id_a, name_b, id_b))}</p>"
                )
            sections.append(
                '<div class="banner sudden">SUDDEN DEATH</div>'
                f"{sd_outcome}"
                f"<table><tr><th>Task</th><th>{_esc(name_a)}</th><th>{_esc(name_b)}</th><th>Result</th></tr>{rows}</table>"
            )

    ko_html = (
        '<div class="ko-wrap"><span class="ko">K.O.</span></div>'
        if live.get("ko")
        else ""
    )

    # Draw: distinct banner + optional efficiency edge; never a winner card / K.O.
    is_draw = str(live.get("winner") or "") == "draw"
    draw_html = ""
    if is_draw:
        draw_html = (
            '<div class="banner draw">DRAW</div>'
            + _edge_line(live.get("edge"), name_a, id_a, name_b, id_b)
        )
        ko_html = ""

    winner_html = ""
    if live.get("winner") and not is_draw:
        winner = _esc(live.get("winner"))
        how = "by K.O." if live.get("ko") else "wins the fight"
        winner_html = (
            '<div class="winner-card"><div class="who">WINNER: '
            f"{winner}</div><div class=\"how\">{_esc(how)}</div></div>"
        )

    budget_html = _budget_strip(live.get("budget"), live.get("budget_exceeded"))

    report_note = ""
    if live.get("report_path"):
        report_note = f'<p class="meta">Full report: {_esc(live["report_path"])}</p>'

    # Usage may be keyed by side (live) or by fighter name (score result).
    usage = live.get("usage") if isinstance(live.get("usage"), Mapping) else {}
    usage_rows: dict = {}
    for side, key in (("a", name_a), ("b", name_b)):
        u = usage.get(side) if isinstance(usage, Mapping) else None
        if isinstance(u, Mapping):
            usage_rows[key] = u
    if not usage_rows:
        for key in (id_a, name_a, id_b, name_b):
            u = usage.get(key) if key else None
            if isinstance(u, Mapping):
                usage_rows[key] = u
    incomplete_note = f"<p>{_esc(_INCOMPLETE_NOTE)}</p>" if incomplete_ids else ""
    body = (
        f"{_WORDMARK_HTML}<p class=\"tagline\">Head-to-Head Benchmark Arena</p>"
        '<div class="hp-row">'
        f"{_hp_block('a', name_a, id_a, hp_a)}"
        '<div class="vs">VS</div>'
        f"{_hp_block('b', name_b, id_b, hp_b)}</div>"
        f'<div class="banner {banner_cls}">{_esc(banner_txt)}</div>'
        f"{round_banner}"
        f'<p class="meta">{meta}</p>'
        f"{budget_html}{draw_html}{ko_html}{winner_html}"
        + "".join(sections)
        + _usage_table(usage_rows)
        + report_note
        + '<footer>Scored mechanically by Model Kombat. Errored/skipped rounds are '
          "excluded from points. No models were judged by another model."
          f"{incomplete_note}</footer>"
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
    edge: dict | None = None,
    sudden_death_winner: Any = None,
    budget: Any = None,
    budget_exceeded: Any = None,
    incomplete: Any = None,
    incomplete_rounds: Any = None,
) -> str:
    """Render the final scorecard (legacy ``tools._render_report`` coverage).

    Optional engine fields: ``edge`` (draw-only efficiency edge), 
    ``sudden_death_winner`` (score result), ``budget``/``budget_exceeded``,
    and ``incomplete``/``incomplete_rounds``.
    """
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

    # Incomplete rounds: explicit per-round flags + score-result lists.
    incomplete_ids: set = set()
    if isinstance(incomplete_rounds, (list, tuple)):
        incomplete_ids.update(str(x) for x in incomplete_rounds)
    for rnd in rounds_out:
        if isinstance(rnd, Mapping) and rnd.get("incomplete"):
            incomplete_ids.add(str(rnd.get("id", "")))

    sections: list[str] = []
    sd_seen = False
    for rnd in rounds_out:
        is_sd_round = str(rnd.get("id", "")) == "SD" or "sudden" in str(rnd.get("name", "")).lower()
        rows = ""
        for r in rnd.get("tasks") or []:
            sa, sb = r.get("a") or {}, r.get("b") or {}
            fa, fb = _fraction_of(sa), _fraction_of(sb)
            if r.get("excluded"):
                verdict = f"excluded ({r['excluded']})"
            elif sa.get("errored") or sb.get("errored") or sa.get("state") == "error" or sb.get("state") == "error":
                verdict = "ERROR"
            elif sa.get("skipped") or sb.get("skipped") or sa.get("state") == "skipped" or sb.get("state") == "skipped":
                verdict = "SKIPPED"
            elif fa is not None and fb is not None and fa > fb:
                verdict = f"{_esc(fighter_a)} takes the task"
            elif fa is not None and fb is not None and fb > fa:
                verdict = f"{_esc(fighter_b)} takes the task"
            elif fa is not None and fb is not None and fa == fb:
                verdict = "DRAW"
            else:
                verdict = "—"
            ko_note = ' — <b style="color:#ff2ea6">K.O.</b>' if r.get("ko") else ""
            attr_a = ' class="won-a hit"' if (fa is not None and fb is not None and fa > fb) else ""
            attr_b = ' class="won-b hit"' if (fa is not None and fb is not None and fb > fa) else ""

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
        if is_sd_round:
            # Sudden death was actually used to break a tie -> banner + task.
            sd_seen = True
            sd_outcome = ""
            if sudden_death_winner:
                sd_outcome = (
                    '<p class="sd-outcome">SUDDEN DEATH winner: '
                    f"{_esc(sudden_death_winner)}</p>"
                )
            else:
                # Derive outcome from the single task's verdict text if present.
                sd_outcome = ""
            sections.append(
                '<div class="banner sudden">SUDDEN DEATH</div>'
                f"{sd_outcome}"
                "<table><tr><th>Task</th>"
                f"<th>{_esc(fighter_a)}</th><th>{_esc(fighter_b)}</th><th>Verdict</th></tr>"
                f"{rows}</table>"
            )
            continue
        rname = str(rnd.get("name", ""))
        if str(rnd.get("id", "")) in incomplete_ids and not rname.endswith("*"):
            rname = f"{rname} *" if rname else "*"
        sections.append(
            f"<h2>{_esc(rnd.get('id', ''))} — {_esc(rname)} "
            f"({_esc(rnd.get('points', 0))} pt/task)</h2>"
            f'<div class="stars">{_stars(rnd.get("points", 0) or 0)}</div>'
            "<table><tr><th>Task</th>"
            f"<th>{_esc(fighter_a)}</th><th>{_esc(fighter_b)}</th><th>Verdict</th></tr>"
            f"{rows}</table>"
        )

    # Sudden death decided on the score result but its round is not in
    # rounds_out (caller passed only sudden_death_winner): still banner it.
    if sudden_death_winner and not sd_seen:
        sections.append(
            '<div class="banner sudden">SUDDEN DEATH</div>'
            '<p class="sd-outcome">SUDDEN DEATH winner: '
            f"{_esc(sudden_death_winner)}</p>"
        )

    is_draw = str(winner or "") == "draw"
    budget_html = _budget_strip(budget, budget_exceeded)
    if is_draw:
        ko_html = ""
        result_html = (
            '<div class="banner draw">DRAW</div>'
            + _edge_line(edge, fighter_a, fighter_a, fighter_b, fighter_b)
        )
    else:
        ko_html = '<div class="ko-wrap"><span class="ko">K.O.</span></div>' if ko else ""
        winner_line = f"{_esc(winner)} wins by K.O." if ko else f"{_esc(winner)} wins the fight"
        result_html = (
            f'<div class="winner-card"><div class="who">{winner_line}</div>'
            '<div class="how">mechanically scored — no model judged a model</div></div>'
        )
    incomplete_note = (
        f"<p>{_esc(_INCOMPLETE_NOTE)}</p>" if (incomplete_ids or incomplete) else ""
    )
    body = (
        f"{_WORDMARK_HTML}<p class=\"tagline\">Head-to-Head Benchmark Arena · Final Scorecard</p>"
        '<div class="hp-row">'
        f"{_hp_block('a', fighter_a, fighter_a, hp_a)}"
        '<div class="vs">VS</div>'
        f"{_hp_block('b', fighter_b, fighter_b, hp_b)}</div>"
        f'<p class="meta">POINTS <b>{_esc(fighter_a)} {points_a:g} — '
        f"{_esc(fighter_b)} {points_b:g}</b></p>"
        f"{budget_html}{ko_html}{result_html}"
        + "".join(sections)
        + _usage_table(usage or {})
        + "<footer>Scored mechanically by Model Kombat. Code rounds execute only "
          "with allow_code_execution=true (subprocess, timeout, not a security "
          "sandbox). Errored/skipped rounds are excluded from points."
          f"{incomplete_note}</footer>"
    )
    return _page(f"Model Kombat — {fighter_a} vs {fighter_b}", body)
