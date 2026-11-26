import type { CocoEvent } from '~/types/api'

// Builds the Steps tree of a run from its events: step and sub-flow calls become nodes,
// loop iterations and if-arms become grouping nodes. Node keys are event paths; a path that
// repeats (the same step called twice in one block) gets a `#2`, `#3` suffix.

export interface StepNodeData {
  name: string
  kind: 'flow' | 'subflow' | 'step' | 'loop' | 'branch'
  status: 'running' | 'succeeded' | 'failed' | 'cancelled' | ''
  duration_ms: number | null
  seq: number | null
}

export interface StepNode {
  key: string
  data: StepNodeData
  children: StepNode[]
}

function segmentKind(seg: string): StepNodeData['kind'] {
  if (seg.startsWith('for#')) return 'loop'
  if (seg.startsWith('if#')) return 'branch'
  return 'step'
}

export function buildRunTree(events: CocoEvent[]): StepNode[] {
  const roots: StepNode[] = []
  const byKey = new Map<string, StepNode>()
  const latest = new Map<string, string>() // path -> key of the newest node for that path
  const counts = new Map<string, number>()

  function ensure(path: string, kind?: StepNodeData['kind']): StepNode {
    const key = latest.get(path) ?? path
    let node = byKey.get(key)
    if (node) return node
    const segs = path.split('/')
    const name = segs[segs.length - 1]
    node = {
      key, children: [],
      data: { name, kind: kind ?? (segs.length === 1 ? 'flow' : segmentKind(name)), status: '', duration_ms: null, seq: null },
    }
    byKey.set(key, node)
    latest.set(path, key)
    if (segs.length === 1) roots.push(node)
    else ensure(segs.slice(0, -1).join('/')).children.push(node)
    return node
  }

  function fresh(path: string, kind: StepNodeData['kind']): StepNode {
    if (byKey.has(latest.get(path) ?? path)) {
      const n = (counts.get(path) ?? 1) + 1
      counts.set(path, n)
      latest.set(path, `${path}#${n}`)
    }
    return ensure(path, kind)
  }

  for (const ev of events) {
    const path = ev.path
    if (!path) continue
    const p = ev.payload ?? {}
    switch (ev.type) {
      case 'flow.started': {
        const n = ensure(path, 'flow')
        n.data.status = 'running'
        n.data.seq = ev.seq
        break
      }
      case 'flow.completed':
      case 'flow.failed':
      case 'flow.cancelled': {
        const root = roots[0]
        if (root) {
          root.data.status = ev.type === 'flow.completed' ? 'succeeded' : ev.type === 'flow.failed' ? 'failed' : 'cancelled'
          root.data.duration_ms = p.duration_ms ?? null
        }
        break
      }
      case 'step.started':
      case 'subflow.started': {
        const n = fresh(path, ev.type === 'step.started' ? 'step' : 'subflow')
        n.data.status = 'running'
        n.data.seq = ev.seq
        break
      }
      case 'step.completed':
      case 'subflow.completed':
      case 'step.failed': {
        const n = ensure(path)
        n.data.status = ev.type === 'step.failed' ? 'failed' : 'succeeded'
        n.data.duration_ms = p.duration_ms ?? null
        break
      }
      case 'loop.iteration':
        ensure(`${path}[${p.index}]`, 'loop')
        break
      case 'branch.taken':
        ensure(`${path}.${p.arm}`, 'branch')
        break
      default:
        break
    }
  }
  // a run that stopped leaves running nodes behind: mark them with the run's outcome
  const final = roots[0]?.data.status
  if (final === 'failed' || final === 'cancelled') {
    for (const n of byKey.values()) if (n.data.status === 'running') n.data.status = final
  }
  return roots
}

export function allKeys(nodes: StepNode[], out: Record<string, boolean> = {}): Record<string, boolean> {
  for (const n of nodes) {
    out[n.key] = true
    allKeys(n.children, out)
  }
  return out
}
