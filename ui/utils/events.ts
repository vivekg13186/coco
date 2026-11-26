import type { CocoEvent, RunStatus } from '~/types/api'

export type EventCategory = 'flow' | 'step' | 'log' | 'control' | 'error'

export const CATEGORIES: { label: string; value: EventCategory }[] = [
  { label: 'Flow', value: 'flow' },
  { label: 'Steps', value: 'step' },
  { label: 'Logs', value: 'log' },
  { label: 'Branches & loops', value: 'control' },
  { label: 'Errors', value: 'error' },
]

export function category(type: string): EventCategory {
  if (type.endsWith('.failed') || type === 'flow.cancelled') return 'error'
  if (type.startsWith('log')) return 'log'
  if (type.startsWith('step.') || type.startsWith('subflow.')) return 'step'
  if (type === 'branch.taken' || type === 'loop.iteration') return 'control'
  return 'flow'
}

type TagSeverity = 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast'

export function eventSeverity(type: string): TagSeverity {
  if (type.endsWith('.failed')) return 'danger'
  if (type === 'flow.cancelled') return 'warn'
  if (type.endsWith('.completed')) return 'success'
  if (type.startsWith('log')) return 'contrast'
  if (type.endsWith('.started')) return 'info'
  return 'secondary'
}

export function statusSeverity(status: RunStatus | string): TagSeverity {
  return ({ queued: 'secondary', running: 'info', succeeded: 'success', failed: 'danger', cancelled: 'warn' } as Record<string, TagSeverity>)[status] ?? 'secondary'
}

export function statusIcon(status: RunStatus | string): string {
  return ({
    queued: 'pi pi-clock', running: 'pi pi-spin pi-spinner', succeeded: 'pi pi-check',
    failed: 'pi pi-times', cancelled: 'pi pi-ban',
  } as Record<string, string>)[status] ?? 'pi pi-circle'
}

function short(v: unknown, max = 80): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  if (s === undefined) return ''
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

/** One line describing an event, for the event list. */
export function detail(ev: CocoEvent): string {
  const p = ev.payload ?? {}
  switch (ev.type) {
    case 'flow.started': return `${p.flow}`
    case 'flow.completed': return `output ${short(p.output)}`
    case 'flow.failed': return p.error?.message ?? 'failed'
    case 'flow.cancelled': return `cancelled at ${p.at_path ?? ev.path}`
    case 'step.started': return `${ev.step}`
    case 'step.completed': return `${ev.step} · ${p.duration_ms} ms`
    case 'step.failed': return `${ev.step}: ${p.error?.message ?? 'failed'}`
    case 'subflow.started': return `${p.flow}`
    case 'subflow.completed': return `${p.flow} · ${p.duration_ms} ms`
    case 'branch.taken': return `${lastSegment(ev.path)} → ${p.arm}`
    case 'loop.iteration': return `${lastSegment(ev.path)} [${p.index}] ${p.value !== undefined ? short(p.value, 40) : ''}`.trim()
    case 'log': return `${p.level && p.level !== 'info' ? `[${p.level}] ` : ''}${p.message}`
    case 'log.table': return p.message ?? `table: ${p.row_count} row(s)`
    case 'log.value': return `${p.name} = ${short(p.value)}`
    case 'log.object': return `${p.name}: ${short(p.value)}`
    default: return ev.path ?? ''
  }
}

export function lastSegment(path: string | null): string {
  if (!path) return ''
  const i = path.lastIndexOf('/')
  return i < 0 ? path : path.slice(i + 1)
}

export function matches(ev: CocoEvent, cats: EventCategory[], search: string): boolean {
  if (!cats.includes(category(ev.type))) return false
  if (!search) return true
  const s = search.toLowerCase()
  return ev.type.includes(s) || (ev.path ?? '').toLowerCase().includes(s) || detail(ev).toLowerCase().includes(s)
}

export interface LogArg {
  type: 'string' | 'number' | 'boolean' | 'null' | 'array' | 'object'
  value: unknown
  class?: string
}

function kindOf(v: unknown): LogArg['type'] {
  if (v === null || v === undefined) return 'null'
  if (Array.isArray(v)) return 'array'
  const t = typeof v
  if (t === 'string' || t === 'number' || t === 'boolean') return t
  return 'object'
}

/** The values of a console-style log event; older log.value / log.object events are mapped too. */
export function logArgs(ev: CocoEvent): LogArg[] {
  const p = ev.payload ?? {}
  if (ev.type === 'log') {
    if (Array.isArray(p.args)) return p.args as LogArg[]
    return [{ type: 'string', value: String(p.message ?? '') }]
  }
  if (ev.type === 'log.value' || ev.type === 'log.object') {
    return [{ type: 'string', value: `${p.name}${ev.type === 'log.value' ? ' =' : ':'}` },
      { type: kindOf(p.value), value: p.value, class: p.class_name && p.class_name !== 'dict' ? p.class_name : undefined }]
  }
  return []
}

export function logLevel(ev: CocoEvent): 'debug' | 'info' | 'warning' | 'error' {
  const l = ev.payload?.level
  return l === 'debug' || l === 'warning' || l === 'error' ? l : 'info'
}

export function isLog(ev: CocoEvent) {
  return ev.type === 'log' || ev.type === 'log.table' || ev.type === 'log.value' || ev.type === 'log.object'
}

/** Short preview of a JSON value, like the browser console: {name: "Ada", age: 33} or Array(3) [1, 2, 3]. */
export function preview(v: unknown, max = 70): string {
  const one = (x: unknown, depth: number): string => {
    if (x === null || x === undefined) return 'None'
    if (typeof x === 'string') return JSON.stringify(x)
    if (typeof x === 'boolean') return x ? 'True' : 'False'
    if (typeof x !== 'object') return String(x)
    if (depth > 0) return Array.isArray(x) ? `Array(${x.length})` : '{…}'
    if (Array.isArray(x)) return `[${x.map(i => one(i, depth + 1)).join(', ')}]`
    return `{${Object.entries(x as Record<string, unknown>).map(([k, i]) => `${k}: ${one(i, depth + 1)}`).join(', ')}}`
  }
  const s = Array.isArray(v) ? `Array(${v.length}) ${one(v, 0)}` : one(v, 0)
  return s.length > max ? `${s.slice(0, max - 1)}…` : s
}

/** Text of a table cell, for display, filtering and sorting. */
export function cellText(v: unknown): string {
  if (v === null || v === undefined) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'number') return String(v)
  return JSON.stringify(v)
}
