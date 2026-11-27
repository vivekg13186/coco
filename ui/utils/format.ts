export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  if (m < 60) return `${m} m ${s} s`
  return `${Math.floor(m / 60)} h ${m % 60} m`
}

function pad(n: number, w = 2) {
  return String(n).padStart(w, '0')
}

/** Local date and time, `2026-11-26 20:14:02`. */
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** Local time with milliseconds, `20:14:02.101`. */
export function clockMs(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}

/** Short local time, `20:14`. */
export function clock(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return '—'
  const s = Math.round((now - new Date(iso).getTime()) / 1000)
  if (s < 5) return 'just now'
  if (s < 60) return `${s} s ago`
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

type SigParam = { name: string; type: string; optional?: boolean; default?: unknown }

/** Python-style signature: `(name: str, n: int = 2) -> {"message": str}`. */
export function signature(sig: { inputs: SigParam[]; outputs: SigParam[] | null }): string {
  const ins = sig.inputs.map(x => `${x.name}: ${x.type}${x.optional ? ` = ${pyValue(x.default)}` : ''}`).join(', ')
  const outs = sig.outputs
  let ret = ''
  if (outs === null) ret = ''
  else if (outs.length === 0) ret = ' -> None'
  else if (outs.length === 1 && outs[0]!.name === 'result') ret = ` -> ${outs[0]!.type}`
  else if (outs.every((o, i) => o.name === `result${i + 1}`)) ret = ` -> tuple[${outs.map(o => o.type).join(', ')}]`
  else ret = ` -> {${outs.map(o => `"${o.name}": ${o.type}`).join(', ')}}`
  return `(${ins})${ret}`
}

export function pyValue(v: unknown): string {
  if (v === null || v === undefined) return 'None'
  if (v === true) return 'True'
  if (v === false) return 'False'
  if (typeof v === 'string') return JSON.stringify(v)
  return JSON.stringify(v).replace(/\btrue\b/g, 'True').replace(/\bfalse\b/g, 'False').replace(/\bnull\b/g, 'None')
}
