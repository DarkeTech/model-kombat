import {
  PANES_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  PALETTE_AREA,
  KANBAN_CARD_AREAS,
  KANBAN_CARD_BADGE_HUES,
  host
} from '@hermes/plugin-sdk'
import { useEffect, useState, useSyncExternalStore } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

// ---------------------------------------------------------------------------
// Model Kombat — arcade-fighter live view (SPEC §7/§8).
// Original 16-bit look via theme vars only (var(--ui-*)); no JSX, no inline
// colors, no remote assets. Live updates arrive on ctx.onEvent
// ('plugin.model-kombat.fight.updated'; SDK-tracked, disposed on unload) with
// a 5 s REST polling fallback; the pane degrades to a plain status message
// when the backend is off.
// ---------------------------------------------------------------------------

const LIVE_EVENT = 'plugin.model-kombat.fight.updated'
const LIVE_POLL_MS = 5000
const RECS_POLL_MS = 30000
// Trends move only when the ladder moves (a scored fight), so a slow poll is
// plenty; fight-end pushes re-read it via LIVE_EVENT.
const TRENDS_POLL_MS = 60000
// Keep the pane section bounded — the page shows the same component.
const TRENDS_PANE_LIMIT = 8

// --- stores (module scope; reset on load, updated from register()) ----------

const liveListeners = new Set()
let liveState = { live: null, loading: true, error: null }

function setLive(patch) {
  liveState = { ...liveState, ...patch }
  for (const fn of liveListeners) fn()
}

function subscribeLive(fn) {
  liveListeners.add(fn)
  return () => liveListeners.delete(fn)
}

function getLiveSnapshot() {
  return liveState
}

function useLive() {
  return useSyncExternalStore(subscribeLive, getLiveSnapshot, getLiveSnapshot)
}

// Recommendations keyed by kanban task id — the source for card badges.
let recMap = {}

function setRecs(obj) {
  recMap = obj && typeof obj === 'object' ? obj : {}
}

const recListeners = new Set()
let recState = { recs: {}, loading: true, error: null }

function setRecState(patch) {
  recState = { ...recState, ...patch }
  for (const fn of recListeners) fn()
}

function subscribeRec(fn) {
  recListeners.add(fn)
  return () => recListeners.delete(fn)
}

function getRecSnapshot() {
  return recState
}

function useRecs() {
  return useSyncExternalStore(subscribeRec, getRecSnapshot, getRecSnapshot)
}

// Ladder trends (per model per lane Elo history) — pane sparklines. Same
// external-store shape as recs: module scope, fed from register()'s poll.
const trendListeners = new Set()
let trendState = { trends: [], loading: true, error: null }

function setTrendState(patch) {
  trendState = { ...trendState, ...patch }
  for (const fn of trendListeners) fn()
}

function subscribeTrend(fn) {
  trendListeners.add(fn)
  return () => trendListeners.delete(fn)
}

function getTrendSnapshot() {
  return trendState
}

function useTrends() {
  return useSyncExternalStore(subscribeTrend, getTrendSnapshot, getTrendSnapshot)
}

// --- small presentational helpers ------------------------------------------

function hpBar(label, hp) {
  const value = typeof hp === 'number' && Number.isFinite(hp) ? Math.max(0, Math.min(100, hp)) : 100
  return jsxs('div', {
    style: { marginBottom: '8px' },
    children: [
      jsx('div', {
        style: { fontSize: '0.8rem', fontWeight: 700, color: 'var(--ui-text)', wordBreak: 'break-word' },
        children: label
      }),
      jsx('div', {
        role: 'progressbar',
        'aria-label': `${label} HP`,
        'aria-valuenow': Math.round(value),
        'aria-valuemin': 0,
        'aria-valuemax': 100,
        style: {
          height: '12px',
          marginTop: '4px',
          border: '2px solid var(--ui-text-tertiary)',
          borderRadius: '2px',
          overflow: 'hidden'
        },
        children: jsx('div', {
          style: {
            width: `${value}%`,
            height: '100%',
            background: value <= 25 ? 'var(--ui-danger)' : 'var(--ui-accent)',
            transition: 'none'
          }
        })
      }),
      jsx('div', {
        style: { fontSize: '0.7rem', color: 'var(--ui-text-tertiary)' },
        children: `HP ${Math.round(value)}/100`
      })
    ]
  })
}

function fighterOf(live, side) {
  const fighters = live && live.fighters
  const info = fighters && fighters[side]
  if (info && typeof info === 'object') {
    return { name: info.short || info.id || `Fighter ${side.toUpperCase()}`, id: info.id || '' }
  }
  if (typeof info === 'string') return { name: info, id: info }
  return { name: `Fighter ${side.toUpperCase()}`, id: '' }
}

function taskCell(task, side, name) {
  const sideData = task && task[side]
  const state = (sideData && sideData.state) || 'waiting'
  const score = sideData && sideData.score
  const scoreText = state === 'skipped' ? 'SKIPPED' : state === 'error' ? 'ERROR' : typeof score === 'number' ? score.toFixed(2) : '—'
  const detail = (sideData && sideData.detail) || ''
  const other = task && task[side === 'a' ? 'b' : 'a']
  const won =
    state === 'done' &&
    typeof score === 'number' &&
    other && other.state === 'done' &&
    typeof other.score === 'number' &&
    score > other.score
  return jsxs('td', {
    style: {
      padding: '6px 8px',
      border: '1px solid var(--ui-border)',
      verticalAlign: 'top',
      fontSize: '0.8rem',
      color: 'var(--ui-text)',
      background: won ? 'var(--ui-surface-hover)' : undefined,
      fontWeight: won ? 700 : undefined
    },
    children: [
      `${name} `,
      jsx('b', { children: scoreText }),
      ' ',
      jsx('span', {
        style: {
          fontSize: '0.65rem',
          textTransform: 'uppercase',
          letterSpacing: '0.08em',
          color: 'var(--ui-text-tertiary)'
        },
        children: state
      }),
      won ? jsx('span', { style: { color: 'var(--ui-accent)', fontWeight: 700 }, children: ' HIT!' }) : null,
      detail != null && detail !== '' ? jsx('small', {
        style: { display: 'block', color: 'var(--ui-text-tertiary)', fontSize: '0.7rem', wordBreak: 'break-word' },
        children: String(detail)
      }) : null
    ]
  })
}

function resultCell(task, nameA, nameB) {
  const ta = (task && task.a) || {}
  const tb = (task && task.b) || {}
  let text = '—'
  if (task && task.ko) text = '★ K.O. ★'
  else if (ta.state === 'error' || tb.state === 'error') text = 'ERROR'
  else if (ta.state === 'skipped' || tb.state === 'skipped') text = 'SKIPPED'
  else if (ta.state === 'done' && tb.state === 'done' && typeof ta.score === 'number' && typeof tb.score === 'number') {
    if (ta.score === tb.score) text = 'DRAW'
    else if (ta.score > tb.score) text = `${nameA} takes the task`
    else text = `${nameB} takes the task`
  }
  return jsx('td', {
    style: { padding: '6px 8px', border: '1px solid var(--ui-border)', verticalAlign: 'top', fontSize: '0.8rem', color: 'var(--ui-text)' },
    children: text
  })
}

function usageCostText(u) {
  if (!u || typeof u.cost_usd !== 'number' || !Number.isFinite(u.cost_usd)) return 'n/a'
  const text = `$${u.cost_usd.toFixed(4)}`
  return u.cost_source === 'estimated' ? `${text} est.` : text
}

function usageInt(value) {
  return typeof value === 'number' && Number.isFinite(value) ? String(Math.trunc(value)) : '—'
}

function usageSection(live, nameA, nameB) {
  const usage = (live && live.usage) || {}
  const rows = [['a', nameA], ['b', nameB]].map(([side, name]) => {
    const u = usage[side] && typeof usage[side] === 'object' ? usage[side] : {}
    const cell = { padding: '4px 8px', fontSize: '0.8rem', color: 'var(--ui-text)' }
    return jsxs('tr', { key: side, children: [
      jsx('td', { style: cell, children: name }),
      jsx('td', { style: cell, children: `${(typeof u.latency_s === 'number' ? u.latency_s : 0).toFixed(1)}s` }),
      jsx('td', { style: cell, children: usageInt(u.tokens) }),
      jsx('td', { style: cell, children: usageInt(u.input_tokens) }),
      jsx('td', { style: cell, children: usageInt(u.output_tokens) }),
      jsx('td', { style: cell, children: usageCostText(u) })
    ] })
  })
  const th = { textAlign: 'left', fontSize: '0.7rem', color: 'var(--ui-text-tertiary)', padding: '4px 8px' }
  return jsxs('div', { style: { marginTop: '12px' } }, [
    jsx('div', { style: { fontSize: '0.8rem', fontWeight: 700, letterSpacing: '0.12em', color: 'var(--ui-text)', textTransform: 'uppercase' }, children: 'Usage' }),
    jsx('table', {
      style: { width: '100%', borderCollapse: 'collapse', marginTop: '6px', fontSize: '0.8rem' },
      children: [
        jsx('thead', {}, jsx('tr', {}, ['Fighter', 'Latency', 'Tokens', 'Input', 'Output', 'Est. cost'].map((h) => jsx('th', { key: h, style: th, children: h })))),
        jsx('tbody', {}, rows)
      ]
    })
  ])
}

function shortForRef(ref, a, b) {
  if (!ref) return ''
  if (ref === 'a' || ref === a.name || ref === a.id) return a.name
  if (ref === 'b' || ref === b.name || ref === b.id) return b.name
  return String(ref)
}

function sdUsed(sd) {
  if (!sd || typeof sd !== 'object') return false
  if (sd.used || sd.fought) return true
  if (['fighting', 'done', 'scoring', 'error'].includes(sd.state)) return true
  return (sd.tasks || []).some((task) => ['a', 'b'].some((side) => task && task[side] && task[side].state && task[side].state !== 'waiting'))
}

function taskTable(tasks, nameA, nameB) {
  const th = { textAlign: 'left', fontSize: '0.7rem', color: 'var(--ui-text-tertiary)', padding: '4px 8px' }
  return jsx('table', {
    style: { width: '100%', borderCollapse: 'collapse', marginTop: '6px' },
    children: [
      jsx('thead', {}, jsx('tr', {}, [
        jsx('th', { style: th, children: 'Task' }),
        jsx('th', { style: th, children: nameA }),
        jsx('th', { style: th, children: nameB }),
        jsx('th', { style: th, children: 'Result' })
      ])),
      jsx('tbody', {}, (tasks || []).map((task) => jsxs('tr', {
        key: task.id || (task.title || 'task'),
        children: [
          jsx('td', {
            style: { padding: '6px 8px', border: '1px solid var(--ui-border)', verticalAlign: 'top', fontSize: '0.8rem', color: 'var(--ui-text)' },
            children: [
              jsx('b', { children: String(task.id || '?') }),
              jsx('small', { style: { display: 'block', color: 'var(--ui-text-tertiary)', fontSize: '0.7rem' }, children: String(task.title || '') })
            ]
          }),
          taskCell(task, 'a', nameA),
          taskCell(task, 'b', nameB),
          resultCell(task, nameA, nameB)
        ]
      })))
    ]
  })
}

function roundsList(live, nameA, nameB) {
  const rounds = (live && live.rounds) || []
  if (!rounds.length) {
    return jsx('div', { style: { color: 'var(--ui-text-tertiary)', fontSize: '0.8rem' }, children: 'No rounds yet.' })
  }
  const incompleteIds = new Set((live && live.incomplete_rounds) || [])
  for (const rnd of rounds) {
    if (rnd && rnd.incomplete) incompleteIds.add(rnd.id)
  }
  return jsxs('div', {}, rounds.map((rnd) => {
    const tasks = (rnd && rnd.tasks) || []
    const rname = rnd.name || 'Fight'
    const starred = incompleteIds.has(rnd.id) && !String(rname).endsWith('*') ? `${rname} *` : rname
    return jsxs('div', {
      key: rnd.id || rnd.name,
      style: { marginTop: '12px' }
    }, [
      jsx('div', {
        style: { fontSize: '0.8rem', fontWeight: 700, letterSpacing: '0.12em', color: 'var(--ui-text)', textTransform: 'uppercase' },
        children: `${rnd.id || ''} — ${starred}  ${'★'.repeat(rnd.points || 0)}`
      }),
      taskTable(tasks, nameA, nameB)
    ])
  }))
}

function arenaView(live) {
  const a = fighterOf(live, 'a')
  const b = fighterOf(live, 'b')
  const hp = (live && live.hp) || {}
  const status = (live && live.status) || 'idle'
  const points = (live && live.points) || {}
  const bannerByStatus = {
    idle: 'STANDBY — NO BOUT ON DECK',
    fighting: 'FIGHT ON',
    scoring: 'SCORING…',
    done: 'FIGHT OVER',
    error: 'BOUT ERROR — CHECK BELOW'
  }
  const children = [
    jsx('div', {
      role: 'status',
      style: {
        textAlign: 'center',
        border: '2px dashed var(--ui-accent)',
        padding: '6px 10px',
        marginBottom: '10px',
        letterSpacing: '0.2em',
        fontWeight: 700,
        fontSize: '0.9rem',
        color: 'var(--ui-text)',
        textTransform: 'uppercase'
      },
      children: bannerByStatus[status] || bannerByStatus.idle
    }),
    hpBar(a.name, hp.a),
    jsx('div', { style: { textAlign: 'center', fontWeight: 700, color: 'var(--ui-accent)', margin: '2px 0 8px' }, children: 'VS' }),
    hpBar(b.name, hp.b),
    jsx('div', {
      style: { fontSize: '0.75rem', color: 'var(--ui-text-tertiary)', textAlign: 'center', marginBottom: '4px' },
      children: `POINTS ${a.name} ${points.a ?? 0} — ${points.b ?? 0} ${b.name}`
    })
  ]
  const isDraw = live && live.winner === 'draw'
  const budget = live && live.budget && typeof live.budget === 'object' ? live.budget : null
  const budgetHit = Boolean((budget && budget.exceeded) || (live && live.budget_exceeded))
  if (budgetHit) {
    const reason = budget && budget.reason ? ` (${budget.reason})` : ''
    children.push(jsx('div', {
      role: 'status',
      style: {
        textAlign: 'center', border: '2px solid var(--ui-warning, var(--ui-accent))',
        padding: '6px 10px', margin: '8px 0', letterSpacing: '0.12em', fontWeight: 700,
        fontSize: '0.8rem', color: 'var(--ui-text)', textTransform: 'uppercase'
      },
      children: `BUDGET CAP HIT — remaining tasks skipped${reason}`
    }))
  }
  if (isDraw) {
    const edge = live.edge && typeof live.edge === 'object' ? live.edge : null
    const drawChildren = [
      jsx('div', {
        role: 'status',
        style: {
          textAlign: 'center', border: '2px solid var(--ui-accent)', padding: '6px 10px',
          margin: '8px 0', letterSpacing: '0.2em', fontWeight: 700, fontSize: '0.9rem',
          color: 'var(--ui-text)', textTransform: 'uppercase'
        },
        children: 'DRAW'
      })
    ]
    if (edge) {
      const short = edge.fighter ? shortForRef(edge.fighter, a, b) : ''
      const reason = edge.reason || edge.label || ''
      drawChildren.push(jsx('div', {
        style: { textAlign: 'center', fontSize: '0.75rem', color: 'var(--ui-text-tertiary)', marginTop: '4px' },
        children: short ? `efficiency edge: ${short} — ${reason}` : `efficiency edge: ${reason}`
      }))
    }
    children.push(jsxs('div', {}, drawChildren))
  }
  if (live && live.ko && !isDraw) {
    children.push(jsx('div', {
      role: 'status',
      style: {
        textAlign: 'center',
        fontSize: '1.9rem',
        fontWeight: 800,
        letterSpacing: '0.14em',
        color: 'var(--ui-danger)',
        border: '4px double var(--ui-danger)',
        borderRadius: '6px',
        padding: '4px 18px',
        margin: '10px auto',
        transform: 'rotate(-4deg)',
        maxWidth: '220px'
      },
      children: 'K.O.'
    }))
  }
  if (live && live.winner && !isDraw) {
    children.push(jsx('div', {
      style: {
        textAlign: 'center',
        border: '2px solid var(--ui-accent)',
        borderRadius: '6px',
        padding: '8px',
        margin: '10px 0'
      },
      children: [
        jsx('div', { style: { fontWeight: 800, color: 'var(--ui-text)', wordBreak: 'break-word' }, children: `WINNER: ${live.winner}` }),
        jsx('div', { style: { fontSize: '0.75rem', color: 'var(--ui-text-tertiary)' }, children: live.ko ? 'by K.O.' : 'wins the fight' })
      ]
    }))
  }
  children.push(roundsList(live, a.name, b.name))
  if (sdUsed(live && live.sudden_death)) {
    const sd = live.sudden_death
    const sdChildren = [
      jsx('div', {
        role: 'status',
        style: {
          textAlign: 'center', border: '2px solid var(--ui-warning, var(--ui-accent))',
          padding: '6px 10px', margin: '12px 0 4px', letterSpacing: '0.2em', fontWeight: 700,
          fontSize: '0.9rem', color: 'var(--ui-text)', textTransform: 'uppercase'
        },
        children: 'SUDDEN DEATH'
      })
    ]
    if (live.sudden_death_winner) {
      sdChildren.push(jsx('div', {
        style: { textAlign: 'center', fontSize: '0.8rem', color: 'var(--ui-text)', marginTop: '4px' },
        children: `SUDDEN DEATH winner: ${shortForRef(live.sudden_death_winner, a, b)}`
      }))
    }
    sdChildren.push(taskTable(sd.tasks || [], a.name, b.name))
    children.push(jsxs('div', {}, sdChildren))
  }
  children.push(usageSection(live, a.name, b.name))
  const incompleteIds = new Set((live && live.incomplete_rounds) || [])
  const hasIncomplete = Boolean(live && live.incomplete) || incompleteIds.size > 0 ||
    ((live && live.rounds) || []).some((rnd) => rnd && rnd.incomplete)
  if (hasIncomplete) {
    children.push(jsx('div', {
      style: { fontSize: '0.75rem', color: 'var(--ui-text-tertiary)', textAlign: 'center', marginTop: '8px' },
      children: '* round excluded from points (errored/skipped)'
    }))
  }
  return jsxs('div', { children })
}

function backendNote(error) {
  return jsx('div', {
    role: 'status',
    style: { fontSize: '0.8rem', color: 'var(--ui-text-tertiary)', marginTop: '8px' },
    children: `Backend unreachable (${String((error && error.message) || error || 'unknown error')}) — showing last known state.`
  })
}

// --- wordmark (heading typography only) -------------------------------------
// Original treatment: thin-outlined monospace caps, letter-spaced, single
// glow, flanked by a >+< crossed-bars emblem (inline SVG). No drop-shadow
// stack, no jagged/stencil lettering, no third-party marks.

function wordmarkEmblem(mirror) {
  return jsx('svg', {
    width: 36,
    height: 18,
    viewBox: '0 0 72 36',
    'aria-hidden': true,
    focusable: 'false',
    style: { flexShrink: 0, transform: mirror ? 'scaleX(-1)' : undefined },
    children: [
      jsx('path', { d: 'M4 6 L20 18 L4 30', fill: 'none', stroke: 'var(--ui-accent)', strokeWidth: 4, strokeLinecap: 'square' }),
      jsx('path', { d: 'M68 6 L52 18 L68 30', fill: 'none', stroke: 'var(--ui-danger)', strokeWidth: 4, strokeLinecap: 'square' }),
      jsx('path', { d: 'M36 9 V27 M28 18 H44', fill: 'none', stroke: 'var(--ui-text)', strokeWidth: 4, strokeLinecap: 'square' })
    ]
  })
}

function wordmarkText() {
  return jsxs('span', {
    style: {
      fontWeight: 400,
      letterSpacing: '0.32em',
      textTransform: 'uppercase',
      color: 'var(--ui-text)',
      WebkitTextStroke: '1px var(--ui-accent)',
      textShadow: '0 0 10px var(--ui-accent)'
    },
    children: ['Model ', 'Kombat']
  })
}

function wordmarkChildren() {
  return [wordmarkEmblem(false), wordmarkText(), wordmarkEmblem(true)]
}

// --- ladder trends (pane + page section) --------------------------------------

const TREND_GLYPH = { up: '▲', down: '▼', flat: '◆' }
const TREND_COLOR = {
  up: 'var(--ui-accent)',
  down: 'var(--ui-danger)',
  flat: 'var(--ui-text-tertiary)'
}

function fmtDelta(value) {
  const n = Number(value) || 0
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}`
}

/** Inline SVG sparkline of a trend row's Elo series (oldest -> newest). */
function sparkline(series, direction) {
  const w = 84
  const h = 22
  const pad = 2
  const points = (Array.isArray(series) ? series : [])
    .map(pt => Number(pt && pt.elo))
    .filter(v => Number.isFinite(v))
  const color = TREND_COLOR[direction] || TREND_COLOR.flat
  if (points.length === 0) {
    return jsx('span', { style: { width: `${w}px`, flexShrink: 0 }, 'aria-hidden': true })
  }
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min
  const coords = points.map((v, i) => {
    const x = points.length === 1 ? w / 2 : pad + (i / (points.length - 1)) * (w - 2 * pad)
    const y = span === 0 ? h / 2 : h - pad - ((v - min) / span) * (h - 2 * pad)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const last = coords[coords.length - 1].split(',')
  return jsx('svg', {
    width: w,
    height: h,
    viewBox: `0 0 ${w} ${h}`,
    'aria-hidden': true,
    focusable: 'false',
    style: { flexShrink: 0 },
    children: [
      jsx('polyline', {
        points: coords.join(' '),
        fill: 'none',
        stroke: color,
        strokeWidth: 1.5,
        strokeLinejoin: 'round',
        strokeLinecap: 'round'
      }),
      jsx('circle', { cx: last[0], cy: last[1], r: 2, fill: color })
    ]
  })
}

function trendRow(t) {
  const glyph = TREND_GLYPH[t.direction] || TREND_GLYPH.flat
  return jsxs('div', {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '3px 0',
      borderBottom: '1px solid var(--ui-border)'
    },
    children: [
      jsx('span', {
        title: `net ${fmtDelta(t.recent_delta)} over the last bouts`,
        style: { color: TREND_COLOR[t.direction] || TREND_COLOR.flat, width: '14px', textAlign: 'center', flexShrink: 0 },
        children: glyph
      }),
      jsx('span', {
        style: { color: 'var(--ui-text)', fontSize: '0.78rem', width: '30%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
        children: t.short || t.model || 'Model'
      }),
      sparkline(t.series, t.direction),
      jsx('span', {
        style: { color: 'var(--ui-text-tertiary)', fontSize: '0.72rem', marginLeft: 'auto', whiteSpace: 'nowrap' },
        children: `${Math.round(t.elo)} (${fmtDelta(t.delta)}) · ${t.bouts}`
      })
    ]
  })
}

function TrendsSection({ limit }) {
  const { trends, loading, error } = useTrends()
  const rows = (trends || []).slice(0, limit || trends.length)
  return jsxs('div', { style: { marginTop: '16px' }, children: [
    jsx('h2', { style: { fontSize: '1rem', letterSpacing: '0.12em', marginTop: '4px' }, children: 'LADDER TRENDS' }),
    error && !trends.length
      ? backendNote(error)
      : rows.length
        ? jsx('div', {}, rows.map((t, i) => jsx('div', { key: `${t.lane}:${t.model}`, children: [
            (i === 0 || rows[i - 1].lane !== t.lane)
              ? jsx('div', { style: { color: 'var(--ui-text-tertiary)', fontSize: '0.68rem', letterSpacing: '0.08em', textTransform: 'uppercase', marginTop: '6px' }, children: t.lane })
              : null,
            trendRow(t)
          ] })))
        : jsx('div', { style: { color: 'var(--ui-text-tertiary)', fontSize: '0.8rem' }, children: loading ? 'Loading trends…' : 'No trend history yet — trends start with the first scored fight.' })
  ] })
}

// --- pane -------------------------------------------------------------------

function KombatPane() {
  const { live, loading, error } = useLive()
  if (loading && !live) {
    return jsx('div', {
      style: { padding: '12px', color: 'var(--ui-text-tertiary)', fontSize: '0.8rem' },
      children: 'Warming up the arena…'
    })
  }
  if (!live) {
    return jsxs('div', { style: { padding: '12px' }, children: [
      jsx('div', { style: { fontWeight: 400, color: 'var(--ui-text)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px' }, children: wordmarkChildren() }),
      backendNote(error || 'no data yet'),
      jsx('div', { style: { fontSize: '0.75rem', color: 'var(--ui-text-tertiary)', marginTop: '4px' }, children: 'Run a fight from the Model Kombat page.' }),
      TrendsSection({ limit: TRENDS_PANE_LIMIT })
    ] })
  }
  return jsxs('div', {
    style: {
      height: '100%',
      overflowY: 'auto',
      padding: '12px',
      fontFamily: '"Courier New", ui-monospace, Menlo, Consolas, monospace'
    },
    children: [
      jsx('div', {
        style: { textAlign: 'center', fontWeight: 400, color: 'var(--ui-text)', marginBottom: '8px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px' },
        children: wordmarkChildren()
      }),
      arenaView(live),
      error ? backendNote(error) : null,
      TrendsSection({ limit: TRENDS_PANE_LIMIT })
    ]
  })
}

// --- full page ----------------------------------------------------------------

function KombatPage({ ctx }) {
  const { live, loading, error } = useLive()
  const { recs } = useRecs()

  const [ladder, setLadder] = useState(null)
  const [history, setHistory] = useState(null)
  const [loadError, setLoadError] = useState(null)

  useEffect(() => {
    if (!ctx) return
    let cancelled = false
    Promise.all([ctx.rest('/ladder'), ctx.rest('/history?limit=20')])
      .then(([ladderData, historyData]) => {
        if (cancelled) return
        setLadder(ladderData)
        setHistory(historyData)
        setLoadError(null)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err)
      })
    return () => { cancelled = true }
  }, [ctx])

  const recEntries = Object.entries(recs || {})
  const ladderLanes = (ladder && ladder.lanes) || {}
  const ladderEntries = Object.entries(ladderLanes)

  const ladderRows = []
  for (const [lane, laneData] of ladderEntries) {
    const models = (laneData && laneData.models) || {}
    for (const [model, stats] of Object.entries(models)) {
      ladderRows.push(
        jsx('tr', { key: `${lane}:${model}` }, [
          jsx('td', { style: { padding: '4px 8px' }, children: lane }),
          jsx('td', { style: { padding: '4px 8px', wordBreak: 'break-word' }, children: model }),
          jsx('td', { style: { padding: '4px 8px' }, children: typeof stats?.elo === 'number' ? Math.round(stats.elo) : '—' }),
          jsx('td', { style: { padding: '4px 8px' }, children: `W ${stats?.wins ?? 0} / L ${stats?.losses ?? 0} / D ${stats?.draws ?? 0}` })
        ])
      )
    }
  }

  return jsxs('div', {
    style: {
      height: '100%',
      overflowY: 'auto',
      padding: '16px',
      fontFamily: '"Courier New", ui-monospace, Menlo, Consolas, monospace',
      color: 'var(--ui-text)'
    },
    children: [
      jsx('h1', {
        style: { margin: '0 0 4px', textAlign: 'center', fontSize: '1.6rem', fontWeight: 400, color: 'var(--ui-text)', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px' },
        children: wordmarkChildren()
      }),
      jsx('p', {
        style: { textAlign: 'center', color: 'var(--ui-text-tertiary)', fontSize: '0.75rem', letterSpacing: '0.3em', textTransform: 'uppercase', marginTop: 0 },
        children: 'Head-to-Head Benchmark Arena'
      }),
      loading && !live
        ? jsx('div', { style: { padding: '12px', color: 'var(--ui-text-tertiary)' }, children: 'Warming up the arena…' })
        : live
          ? arenaView(live)
          : jsxs('div', {}, [
              jsx('div', { style: { fontWeight: 700 }, children: 'No fight on deck.' }),
              backendNote(error)
            ]),
      jsx('h2', { style: { fontSize: '1rem', letterSpacing: '0.12em', marginTop: '20px' }, children: 'RECOMMENDATIONS' }),
      recEntries.length
        ? jsx('table', {
            style: { width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' },
            children: [
              jsx('thead', {}, jsx('tr', {}, [
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Card' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Lane' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Pick' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Confidence' })
              ])),
              jsx('tbody', {}, recEntries.map(([cardId, rec]) => jsxs('tr', { key: cardId, children: [
                jsx('td', { style: { padding: '4px 8px', wordBreak: 'break-word' }, children: cardId }),
                jsx('td', { style: { padding: '4px 8px' }, children: (rec && rec.lane) || '—' }),
                jsx('td', { style: { padding: '4px 8px', wordBreak: 'break-word' }, children: (rec && (rec.short || rec.pick)) || '—' }),
                jsx('td', { style: { padding: '4px 8px' }, children: (rec && rec.confidence) || '—' })
              ] })))
            ]
          })
        : jsx('div', { style: { color: 'var(--ui-text-tertiary)', fontSize: '0.8rem' }, children: 'No recommendations yet.' }),
      jsx('h2', { style: { fontSize: '1rem', letterSpacing: '0.12em', marginTop: '20px' }, children: 'LADDER' }),
      ladderRows.length
        ? jsx('table', {
            style: { width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' },
            children: [
              jsx('thead', {}, jsx('tr', {}, [
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Lane' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Model' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Elo' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Record' })
              ])),
              jsx('tbody', {}, ladderRows)
            ]
          })
        : jsx('div', { style: { color: 'var(--ui-text-tertiary)', fontSize: '0.8rem' }, children: ladder ? 'No ladder entries yet.' : 'Loading ladder…' }),
      jsx('h2', { style: { fontSize: '1rem', letterSpacing: '0.12em', marginTop: '20px' }, children: 'FIGHT HISTORY' }),
      history && history.length
        ? jsx('table', {
            style: { width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' },
            children: [
              jsx('thead', {}, jsx('tr', {}, [
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Fight' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Winner' }),
                jsx('th', { style: { textAlign: 'left', padding: '4px 8px', color: 'var(--ui-text-tertiary)' }, children: 'Result' })
              ])),
              jsx('tbody', {}, history.map((rec, i) => jsxs('tr', { key: rec.fight_id || String(i), children: [
                jsx('td', { style: { padding: '4px 8px' }, children: rec.fight_id || '—' }),
                jsx('td', { style: { padding: '4px 8px', wordBreak: 'break-word' }, children: rec.winner || '—' }),
                jsx('td', { style: { padding: '4px 8px' }, children: rec.ko ? 'K.O.' : 'Decision' })
              ] })))
            ]
          })
        : jsx('div', { style: { color: 'var(--ui-text-tertiary)', fontSize: '0.8rem' }, children: history ? 'No fights recorded.' : 'Loading history…' }),
      TrendsSection({}),
      loadError && live
        ? backendNote(loadError)
        : null
    ]
  })
}

// --- status chip ---------------------------------------------------------------

function KombatChip() {
  const { live } = useLive()
  const status = (live && live.status) || null
  const label = status ? `KOMB ${status.toUpperCase().slice(0, 8)}` : 'KOMB OFF'
  return jsx('button', {
    type: 'button',
    title: 'Open Model Kombat',
    onClick: () => host.navigate('/kombat'),
    style: {
      padding: '2px 8px',
      fontSize: '0.7rem',
      fontWeight: 700,
      letterSpacing: '0.08em',
      color: 'var(--ui-text)',
      background: 'transparent',
      border: '1px solid var(--ui-border)',
      borderRadius: '3px',
      cursor: 'pointer',
      fontFamily: '"Courier New", ui-monospace, Menlo, Consolas, monospace'
    },
    children: label
  })
}

// --- kanban badge ------------------------------------------------------------

function badgeFor(cardId) {
  const rec = recMap[cardId]
  if (!rec) return null
  // Board limits (KANBAN_CARD_BADGE_LIMITS): label <=24 chars, tooltip <=160;
  // we return exactly one badge per card (<=2 allowed), tone from HUES.
  const label = String(rec.short || rec.pick || 'picked').slice(0, 24)
  if (!label.trim()) return null
  const tooltip = [
    `Kombat pick: ${rec.pick || label}`,
    rec.lane ? `lane: ${rec.lane}` : '',
    rec.confidence ? `confidence: ${rec.confidence}` : '',
    typeof rec.elo === 'number' ? `elo: ${Math.round(rec.elo)}` : ''
  ].filter(Boolean).join(' · ').slice(0, 160)
  return { label, tone: KANBAN_CARD_BADGE_HUES[2], tooltip }
}

// --- plugin -------------------------------------------------------------------

export default {
  id: 'model-kombat',
  name: 'Model Kombat',
  register(ctx) {
    // Keep the latest badge disposer: re-registering the same id replaces the
    // contribution (the board then repaints), so dispose only the newest.
    let badgeDisposer = null
    const reRegisterBadges = () => {
      if (badgeDisposer) { try { badgeDisposer() } catch (err) { /* host already replaced it */ } badgeDisposer = null }
      badgeDisposer = ctx.register({
        id: 'badges',
        area: KANBAN_CARD_AREAS.badges,
        data: {
          decorate: (card) => badgeFor(card && card.id)
        }
      })
    }

    const loadLive = async () => {
      try {
        const data = await ctx.rest('/live')
        setLive({ live: data, loading: false, error: null })
      } catch (err) {
        setLive({ loading: false, error: err })
      }
    }

    const loadRecs = async () => {
      try {
        const data = await ctx.rest('/recommendations')
        setRecs(data)
        setRecState({ recs: recMap, loading: false, error: null })
        reRegisterBadges() // repaint kanban badges with fresh picks
      } catch (err) {
        setRecState({ loading: false, error: err })
      }
    }

    const loadTrends = async () => {
      try {
        const data = await ctx.rest('/trends')
        setTrendState({ trends: (data && data.trends) || [], loading: false, error: null })
      } catch (err) {
        setTrendState({ loading: false, error: err })
      }
    }

    loadLive()
    loadRecs()
    loadTrends()

    // Polling fallback: ctx.setInterval is tracked by the host and disposed
    // with the plugin (bare window.setInterval would survive disable).
    ctx.setInterval(loadLive, LIVE_POLL_MS)
    ctx.setInterval(loadRecs, RECS_POLL_MS)
    ctx.setInterval(loadTrends, TRENDS_POLL_MS)

    // Live push: the engine broadcasts fight updates over the global stream.
    // ctx.onEvent is SDK-tracked (PluginContext.onEvent: removed on
    // unload/reload/disable), so no manual disposer is needed — the previous
    // host.onEvent + ctx.onDispose wiring is removed.
    ctx.onEvent(LIVE_EVENT, (event) => {
      const payload = event && event.payload
      if (payload && typeof payload === 'object') {
        setLive({ live: payload, loading: false, error: null })
        // A scored fight moves the ladder — refresh trend sparklines.
        loadTrends()
      }
    })

    ctx.register({
      id: 'arena',
      area: PANES_AREA,
      title: 'Model Kombat',
      data: { placement: 'right', width: '320px' },
      render: () => jsx(KombatPane, {})
    })

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/kombat' },
        render: () => jsx(KombatPage, { ctx })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/kombat', label: 'Model Kombat', codicon: 'zap' }
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 120,
        render: () => jsx(KombatChip, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'model-kombat.open',
          label: 'Open Model Kombat',
          keywords: ['kombat', 'model', 'fight', 'arena'],
          run: () => host.navigate('/kombat')
        }
      }
    ])

    reRegisterBadges()
    ctx.onDispose(() => {
      if (badgeDisposer) badgeDisposer()
    })
  }
}
