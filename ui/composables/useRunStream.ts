import { markRaw, onBeforeUnmount, ref, shallowRef } from 'vue'
import { api, streamUrl } from '~/composables/useApi'
import type { CocoEvent, Run } from '~/types/api'
import { TERMINAL } from '~/types/api'

const EVENT_TYPES = [
  'flow.started', 'flow.completed', 'flow.failed', 'flow.cancelled',
  'step.started', 'step.completed', 'step.failed', 'subflow.started', 'subflow.completed',
  'branch.taken', 'loop.iteration', 'log', 'log.table', 'log.value', 'log.object',
]

/**
 * A run and all its events: loads stored events page by page, then follows the SSE stream
 * (resuming with after_seq) until the run ends.
 */
export function useRunStream(runId: string, onChange?: (run: Run) => void) {
  const run = ref<Run | null>(null)
  const events = shallowRef<CocoEvent[]>([])
  const live = ref(false)
  const error = ref<string | null>(null)
  let lastSeq = 0
  let source: EventSource | null = null
  let stopped = false
  let retry: ReturnType<typeof setTimeout> | null = null
  let pending: CocoEvent[] = []
  let flushTimer: ReturnType<typeof setTimeout> | null = null

  function push(list: CocoEvent[]) {
    const fresh = list.filter(e => e.seq > lastSeq)
    if (!fresh.length) return
    lastSeq = fresh[fresh.length - 1].seq
    events.value = [...events.value, ...fresh.map(e => markRaw(e))]
  }

  // batch live events into one render every 100 ms
  function queue(ev: CocoEvent) {
    pending.push(ev)
    if (!flushTimer) {
      flushTimer = setTimeout(() => {
        flushTimer = null
        const batch = pending
        pending = []
        push(batch)
      }, 100)
    }
  }

  async function refreshRun() {
    run.value = await api.getRun(runId)
    onChange?.(run.value)
    return run.value
  }

  function closeSource() {
    source?.close()
    source = null
    live.value = false
  }

  function follow() {
    if (stopped) return
    closeSource()
    const es = new EventSource(streamUrl(runId, lastSeq))
    source = es
    live.value = true
    for (const t of EVENT_TYPES) {
      es.addEventListener(t, (m) => {
        const ev = JSON.parse((m as MessageEvent).data) as CocoEvent
        if (run.value && run.value.status === 'queued') run.value = { ...run.value, status: 'running' }
        queue(ev)
      })
    }
    es.addEventListener('end', async () => {
      closeSource()
      if (flushTimer) { clearTimeout(flushTimer); flushTimer = null }
      push(pending)
      pending = []
      await refreshRun().catch(() => {})
    })
    es.onerror = () => {
      // the browser would reconnect without after_seq; do it ourselves
      closeSource()
      if (!stopped && run.value && !TERMINAL.includes(run.value.status)) {
        retry = setTimeout(follow, 1500)
      }
    }
  }

  async function load() {
    stopped = false
    error.value = null
    try {
      await refreshRun()
      let more = true
      while (more) {
        const page = await api.events(runId, lastSeq, 2000)
        push(page.items)
        more = page.has_more
      }
      if (run.value && !TERMINAL.includes(run.value.status)) follow()
    } catch (e: any) {
      error.value = e?.message ?? String(e)
    }
  }

  function stop() {
    stopped = true
    if (retry) clearTimeout(retry)
    if (flushTimer) clearTimeout(flushTimer)
    closeSource()
  }

  onBeforeUnmount(stop)

  return { run, events, live, error, load, stop, refreshRun }
}
