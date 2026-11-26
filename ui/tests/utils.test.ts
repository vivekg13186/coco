import { describe, expect, it } from 'vitest'
import { buildRunTree } from '../utils/runTree'
import { category, detail, matches } from '../utils/events'
import { duration, signature } from '../utils/format'
import type { CocoEvent } from '../types/api'

let seq = 0
function ev(type: string, path: string, payload: Record<string, any> = {}, step: string | null = null): CocoEvent {
  seq += 1
  return { run_id: 'r', seq, type, ts: '2026-11-26T19:14:02.101Z', path, step, payload }
}

describe('format', () => {
  it('formats durations', () => {
    expect(duration(299)).toBe('299 ms')
    expect(duration(1420)).toBe('1.42 s')
    expect(duration(125_000)).toBe('2 m 5 s')
    expect(duration(null)).toBe('—')
  })
  it('formats signatures', () => {
    expect(signature({ inputs: [{ name: 't', type: 'Team' }], outputs: [{ name: 'n', type: 'int' }] }))
      .toBe('(Team t) → (int n)')
  })
})

describe('events', () => {
  it('categorises and describes events', () => {
    expect(category('step.failed')).toBe('error')
    expect(category('log.object')).toBe('log')
    expect(category('subflow.started')).toBe('step')
    expect(category('loop.iteration')).toBe('control')
    expect(detail(ev('log.value', 'f/s', { name: 'age', value: 36 }, 's'))).toBe('age = 36')
    expect(detail(ev('step.completed', 'f/s', { duration_ms: 12 }, 's'))).toBe('s · 12 ms')
    expect(detail(ev('branch.taken', 'f/if#0', { arm: 'then' }))).toBe('if#0 → then')
  })
  it('filters by category and text', () => {
    const e = ev('log', 'f/enrich', { level: 'info', message: 'fetching Ada' }, 'enrich')
    expect(matches(e, ['log'], 'ada')).toBe(true)
    expect(matches(e, ['step'], '')).toBe(false)
    expect(matches(e, ['log'], 'grace')).toBe(false)
  })
})

describe('run tree', () => {
  it('nests loops, branches, steps and sub-flows', () => {
    const events = [
      ev('flow.started', 'onboard', { flow: 'onboard' }),
      ev('loop.iteration', 'onboard/for#0', { index: 0 }),
      ev('step.started', 'onboard/for#0[0]/enrich', {}, 'enrich'),
      ev('step.completed', 'onboard/for#0[0]/enrich', { duration_ms: 5 }, 'enrich'),
      ev('branch.taken', 'onboard/for#0[0]/if#0', { arm: 'then' }),
      ev('subflow.started', 'onboard/for#0[0]/if#0.then/notify', { flow: 'notify' }),
      ev('step.started', 'onboard/for#0[0]/if#0.then/notify/mail', {}, 'mail'),
      ev('step.completed', 'onboard/for#0[0]/if#0.then/notify/mail', { duration_ms: 1 }, 'mail'),
      ev('subflow.completed', 'onboard/for#0[0]/if#0.then/notify', { duration_ms: 2 }),
      ev('flow.completed', 'onboard', { duration_ms: 9 }),
    ]
    const [root] = buildRunTree(events)
    expect(root.data).toMatchObject({ name: 'onboard', kind: 'flow', status: 'succeeded', duration_ms: 9 })
    const iter = root.children[0]
    expect(iter.data).toMatchObject({ name: 'for#0[0]', kind: 'loop' })
    expect(iter.children.map(c => c.data.name)).toEqual(['enrich', 'if#0.then'])
    const notify = iter.children[1].children[0]
    expect(notify.data).toMatchObject({ kind: 'subflow', status: 'succeeded' })
    expect(notify.children[0].data).toMatchObject({ name: 'mail', status: 'succeeded', duration_ms: 1 })
  })

  it('keeps repeated calls of the same step apart and marks failures', () => {
    const events = [
      ev('flow.started', 'f'),
      ev('step.started', 'f/s', {}, 's'),
      ev('step.completed', 'f/s', { duration_ms: 1 }, 's'),
      ev('step.started', 'f/s', {}, 's'),
      ev('step.failed', 'f/s', { duration_ms: 2, error: { message: 'boom' } }, 's'),
      ev('flow.failed', 'f/s', { error: { message: 'boom' } }),
    ]
    const [root] = buildRunTree(events)
    expect(root.children.map(c => [c.key, c.data.status])).toEqual([['f/s', 'succeeded'], ['f/s#2', 'failed']])
    expect(root.data.status).toBe('failed')
  })
})

describe('console logs', () => {
  it('reads log values, including older log.value / log.object events', async () => {
    const { logArgs, logLevel, preview, cellText, isLog } = await import('../utils/events')
    const e = ev('log', 'f/s', { level: 'warning', message: 'x 1', args: [{ type: 'string', value: 'x' }, { type: 'number', value: 1 }] }, 's')
    expect(logArgs(e).map(a => a.type)).toEqual(['string', 'number'])
    expect(logLevel(e)).toBe('warning')
    const old = ev('log.object', 'f/s', { name: 'resp', value: { a: 1 }, class_name: 'dict' }, 's')
    expect(logArgs(old)).toEqual([{ type: 'string', value: 'resp:' }, { type: 'object', value: { a: 1 }, class: undefined }])
    expect(isLog(old) && isLog(ev('log.table', 'f/s'))).toBe(true)
    expect(preview({ name: 'Ada', tags: ['x'], n: null })).toBe('{name: "Ada", tags: Array(1), n: None}')
    expect(preview([1, 2, 3])).toBe('Array(3) [1, 2, 3]')
    expect([cellText(null), cellText(true), cellText(2.5), cellText({ a: 1 })]).toEqual(['', 'True', '2.5', '{"a":1}'])
  })
})
