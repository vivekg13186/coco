import type { ClassSymbol, Param } from '~/types/api'

/** Type strings come from Python hints: str, int, float, bool, dict, Any, list[T], T | None, or a class. */
export function baseType(type: string): string {
  return type.replace(/\s*\|\s*None$/, '').trim()
}

export function isList(type: string): boolean {
  return /^list\[.*\]$/.test(baseType(type))
}

const PRIMITIVE_DEFAULTS: Record<string, unknown> = {
  str: '', bool: false, int: 0, float: 0, dict: {}, Any: null,
}

/** A starting value for a flow input, filled with class fields where the type is a class. */
export function defaultFor(type: string, classes: ClassSymbol[], depth = 0): unknown {
  const t = baseType(type)
  if (type !== t) return null
  if (isList(t)) return []
  if (t in PRIMITIVE_DEFAULTS) return structuredClone(PRIMITIVE_DEFAULTS[t])
  const cls = classes.find(c => c.name === t || t.endsWith(`.${c.name}`))
  if (!cls || depth > 3) return {}
  const out: Record<string, unknown> = {}
  for (const f of cls.fields) out[f.name] = defaultFor(f.type, classes, depth + 1)
  return out
}

export function defaultInput(params: Param[], classes: ClassSymbol[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of params) {
    out[p.name] = p.optional ? structuredClone(p.default ?? null) : defaultFor(p.type, classes)
  }
  return out
}

export function isScalar(type: string) {
  return ['str', 'bool', 'int', 'float'].includes(baseType(type))
}
