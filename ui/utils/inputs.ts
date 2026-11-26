import type { ClassSymbol, Param } from '~/types/api'

const PRIMITIVE_DEFAULTS: Record<string, unknown> = {
  string: '', boolean: false, int: 0, float: 0, object: {}, array: [],
}

/** A starting value for a flow input, filled with class fields where the type is a class. */
export function defaultFor(type: string, classes: ClassSymbol[], depth = 0): unknown {
  if (type.endsWith('[]')) return []
  if (type in PRIMITIVE_DEFAULTS) return structuredClone(PRIMITIVE_DEFAULTS[type])
  const cls = classes.find(c => c.name === type || type.endsWith(`.${c.name}`))
  if (!cls || depth > 3) return {}
  const out: Record<string, unknown> = {}
  for (const f of cls.fields) out[f.name] = defaultFor(f.type, classes, depth + 1)
  return out
}

export function defaultInput(params: Param[], classes: ClassSymbol[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of params) out[p.name] = defaultFor(p.type, classes)
  return out
}

export function isScalar(type: string) {
  return ['string', 'boolean', 'int', 'float'].includes(type)
}
