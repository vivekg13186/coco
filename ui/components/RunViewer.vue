<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useToast } from 'primevue/usetoast'
import type { EditorTab } from '~/composables/useTabs'
import type { CocoEvent, Run } from '~/types/api'
import { TERMINAL } from '~/types/api'
import type { EventCategory } from '~/utils/events'
import type { StepNode } from '~/utils/runTree'

const props = defineProps<{ tab: EditorTab }>()
const toast = useToast()
const { setTitle, open } = useTabs()

let wasTerminal: boolean | null = null
function onRun(r: Run) {
  setTitle(props.tab.key, `Run · ${r.workflow_name}.${r.flow_name} · ${clock(r.queued_at)}`)
  const term = TERMINAL.includes(r.status)
  if (wasTerminal === false && term) {
    bump('runs')
    bump('workflows')
  }
  wasTerminal = term
}

const { run, events, live, error, load, refreshRun } = useRunStream(props.tab.id, onRun)
onMounted(load)

const sub = ref('events')
const logView = ref<'Log' | 'JSON'>('Log')
const LEVELS = [
  { label: 'Debug', value: 'debug' }, { label: 'Info', value: 'info' },
  { label: 'Warnings', value: 'warning' }, { label: 'Errors', value: 'error' },
]
const levels = ref<string[]>(['debug', 'info', 'warning', 'error'])
const logSearch = ref('')
const cats = ref<EventCategory[]>(CATEGORIES.map(c => c.value))
const search = ref('')
const autoScroll = ref(true)
const selectedSeq = ref<number | null>(null)
const showTrace = ref(false)
const listEl = ref<HTMLElement | null>(null)
const scroller = ref<any>(null)
const following = ref(false) // keep the newest event selected and in view, until the user picks one
watch(live, (v) => { if (v) following.value = autoScroll.value })
watch(autoScroll, (v) => { following.value = v && (live.value || following.value) })

const active = computed(() => !!run.value && !TERMINAL.includes(run.value.status))
const filtered = computed(() => events.value.filter(e => matches(e, cats.value, search.value)))
const selected = computed<CocoEvent | null>(() =>
  events.value.find(e => e.seq === selectedSeq.value) ?? null)
const tree = computed(() => buildRunTree(events.value))
const logs = computed(() => events.value.filter(isLog))
const logErrors = computed(() => logs.value.some(e => logLevel(e) === 'error'))
const logWarnings = computed(() => logs.value.some(e => logLevel(e) === 'warning'))
const shownLogs = computed(() => {
  const q = logSearch.value.trim().toLowerCase()
  return logs.value.filter(e => levels.value.includes(logLevel(e))
    && (!q || detail(e).toLowerCase().includes(q) || (e.path ?? '').toLowerCase().includes(q)
      || (e.type === 'log.table' && JSON.stringify(e.payload.rows ?? []).toLowerCase().includes(q))))
})

function locate(ev: CocoEvent) {
  selectedSeq.value = ev.seq
  following.value = false
  autoScroll.value = false
  sub.value = 'events'
  const i = filtered.value.findIndex(x => x.seq === ev.seq)
  nextTick(() => requestAnimationFrame(() => scrollTo(Math.max(0, i - 3))))
}
const expanded = ref<Record<string, boolean>>({})
const selKeys = ref<Record<string, boolean>>({})
watch(tree, (t) => { expanded.value = { ...allKeys(t), ...expanded.value } }, { immediate: true })

// select something useful once loaded: the first failure, else the last event
watch(() => events.value.length, (n, old) => {
  if (!n) return
  if (selectedSeq.value === null) {
    const fail = events.value.find(e => e.type.endsWith('.failed'))
    selectedSeq.value = (fail ?? events.value[n - 1]).seq
  } else if (following.value && old !== undefined) {
    selectedSeq.value = events.value[n - 1].seq
  }
  // wait a frame so the virtual list has grown before scrolling to its end
  if (following.value) {
    nextTick(() => requestAnimationFrame(scrollToEnd))
    // the virtual list resizes its spacer a moment later; scroll again once it has
    setTimeout(scrollToEnd, 80)
    setTimeout(scrollToEnd, 300)
  }
})

// the list can shrink after the last event (the status banner animates in): keep the end in view
let resizeObs: ResizeObserver | null = null
watch(scroller, (vs) => {
  resizeObs?.disconnect()
  const el = vs?.$el as HTMLElement | undefined
  if (!el || typeof ResizeObserver === 'undefined') return
  resizeObs = new ResizeObserver(() => { if (following.value) scrollToEnd() })
  resizeObs.observe(el)
})
onBeforeUnmount(() => resizeObs?.disconnect())

function scrollToEnd() {
  const el = scroller.value?.$el as HTMLElement | undefined
  if (el) el.scrollTop = el.scrollHeight
}

function scrollTo(index: number) {
  if (index < 0) return
  scroller.value?.scrollToIndex?.(index, 'auto')
}

function select(ev: CocoEvent) {
  selectedSeq.value = ev.seq
  following.value = false
  if (live.value) autoScroll.value = false
}

function onKey(e: KeyboardEvent) {
  const list = filtered.value
  if (!list.length) return
  let i = list.findIndex(x => x.seq === selectedSeq.value)
  if (e.key === 'ArrowDown') i = Math.min(list.length - 1, i + 1)
  else if (e.key === 'ArrowUp') i = Math.max(0, i - 1)
  else if (e.key === 'Home') i = 0
  else if (e.key === 'End') i = list.length - 1
  else if (e.key === 'Enter') {
    if (selected.value) showInTree(selected.value)
    return
  } else return
  e.preventDefault()
  selectedSeq.value = list[i].seq
  following.value = false
  autoScroll.value = false
  const vs = scroller.value
  if (vs?.getRenderedRange) {
    const r = vs.getRenderedRange()
    const firstVisible = r?.viewport?.first ?? 0
    const lastVisible = r?.viewport?.last ?? 0
    if (i <= firstVisible || i >= lastVisible - 1) scrollTo(Math.max(0, i - 3))
  }
}

function nodeFor(ev: CocoEvent): StepNode | null {
  if (!ev.path) return null
  let best: StepNode | null = null
  const walk = (ns: StepNode[]) => {
    for (const n of ns) {
      const base = n.key.replace(/#\d+$/, '')
      if (base === ev.path && (n.data.seq ?? 0) <= ev.seq && (!best || (n.data.seq ?? 0) > (best.data.seq ?? 0))) best = n
      walk(n.children)
    }
  }
  walk(tree.value)
  return best
}

function showInTree(ev: CocoEvent) {
  const n = nodeFor(ev)
  if (!n) return
  selKeys.value = { [n.key]: true }
  sub.value = 'steps'
}

function onNodeSelect(node: any) {
  const seq = node?.data?.seq
  if (seq) {
    selectedSeq.value = seq
    autoScroll.value = false
  }
}

async function cancel() {
  try {
    await api.cancelRun(props.tab.id)
    toast.add({ severity: 'info', summary: 'Cancel requested', detail: 'The run stops at its next statement.', life: 3000 })
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Cancel failed', detail: e.message, life: 5000 })
    refreshRun().catch(() => {})
  }
}

async function rerun() {
  try {
    const r = await api.rerun(props.tab.id)
    bump('runs')
    open('run', r.id, `Run · ${run.value?.workflow_name}.${r.flow_name} · ${clock(r.queued_at)}`)
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Rerun failed', detail: e.message, life: 5000 })
  }
}

function openWorkflow() {
  if (run.value) open('wf', run.value.workflow_id, run.value.workflow_name)
}

const kindLabel: Record<string, string> = { flow: 'flow', subflow: 'sub-flow', step: 'step', loop: 'loop', branch: 'branch' }
</script>

<template>
  <div class="run-viewer">
    <div class="rv-head row">
      <i class="pi pi-history muted" />
      <span class="title">{{ run ? `${run.workflow_name}.${run.flow_name}` : 'Run' }}</span>
      <StatusTag v-if="run" :status="run.status" />
      <Tag v-if="live" value="live" severity="info" icon="pi pi-circle-fill" class="live" />
      <Tag v-if="run?.env_name" v-tooltip.bottom="'Environment'" :value="run.env_name" icon="pi pi-sliders-h" severity="secondary" />
      <span v-if="run" class="muted small">
        {{ duration(run.duration_ms) }} · {{ run.trigger }} ·
        <a class="link" @click="openWorkflow">v{{ run.version }}</a> · {{ dateTime(run.queued_at) }} ·
        <span class="mono">{{ run.id }}</span>
      </span>
      <span class="grow" />
      <Button v-if="active" label="Cancel" icon="pi pi-stop-circle" size="small" severity="warn" outlined @click="cancel" />
      <Button label="Rerun" icon="pi pi-replay" size="small" severity="secondary" outlined :disabled="!run" @click="rerun" />
    </div>

    <Message v-if="error" severity="error" class="banner">{{ error }}</Message>
    <Message v-if="run?.error && run.status !== 'succeeded'" :severity="run.status === 'cancelled' ? 'warn' : 'error'" class="banner">
      <div class="row">
        <b>{{ run.error.code }}</b><span class="grow">{{ run.error.message }}</span>
        <Button v-if="run.error.traceback" :label="showTrace ? 'Hide traceback' : 'Show traceback'" text size="small" @click="showTrace = !showTrace" />
      </div>
      <pre v-if="showTrace && run.error.traceback" class="trace mono">{{ run.error.traceback }}</pre>
    </Message>

    <Tabs v-model:value="sub" class="rv-tabs">
      <TabList>
        <Tab value="events">Events <Badge :value="events.length" severity="secondary" size="small" /></Tab>
        <Tab value="console">Console <Badge v-if="logs.length" :value="logs.length" :severity="logErrors ? 'danger' : (logWarnings ? 'warn' : 'secondary')" size="small" /></Tab>
        <Tab value="steps">Steps</Tab>
        <Tab value="io">Input / Output</Tab>
      </TabList>
      <TabPanels class="rv-panels">
        <TabPanel value="events">
          <div class="pane">
          <div class="ev-toolbar row">
            <SelectButton v-model="cats" :options="CATEGORIES" option-label="label" option-value="value" multiple size="small" />
            <IconField>
              <InputIcon class="pi pi-search" />
              <InputText v-model="search" placeholder="Filter events" size="small" />
            </IconField>
            <span class="grow" />
            <label v-if="live" class="row small muted" for="autoscroll">
              <ToggleSwitch v-model="autoScroll" input-id="autoscroll" /> Follow
            </label>
            <span class="small muted">{{ filtered.length }} / {{ events.length }}</span>
          </div>
          <Splitter class="ev-split" :gutter-size="4">
            <SplitterPanel :size="55" :min-size="30" class="ev-left">
              <div class="ev-head row mono">
                <span class="c-seq">#</span><span class="c-time">time</span><span class="c-type">type</span><span class="grow">step / detail</span>
              </div>
              <div ref="listEl" class="ev-list-wrap" tabindex="0" @keydown="onKey">
                <VirtualScroller ref="scroller" :items="filtered" :item-size="30" scroll-height="100%" class="ev-list">
                  <template #item="{ item }">
                    <div class="ev-row row" :class="{ sel: item.seq === selectedSeq, bad: item.type.endsWith('.failed') }"
                         @click="select(item)" @dblclick="showInTree(item)">
                      <span class="c-seq mono muted">{{ item.seq }}</span>
                      <span class="c-time mono muted">{{ clockMs(item.ts) }}</span>
                      <span class="c-type"><Tag :value="item.type" :severity="eventSeverity(item.type)" class="ev-tag" /></span>
                      <span class="grow ellipsis ev-detail" :title="item.path ?? ''">{{ detail(item) }}</span>
                    </div>
                  </template>
                </VirtualScroller>
                <div v-if="!events.length" class="muted pad">{{ run?.status === 'queued' ? 'Waiting for a free worker…' : 'No events yet.' }}</div>
              </div>
            </SplitterPanel>
            <SplitterPanel :size="45" :min-size="25" class="ev-right">
              <template v-if="selected && isLog(selected)">
                <div class="view-switch row">
                  <span class="small strong">Event {{ selected.seq }} · {{ selected.type }}</span>
                  <span class="grow" />
                  <SelectButton v-model="logView" :options="['Log', 'JSON']" :allow-empty="false" size="small" />
                </div>
                <div v-if="logView === 'Log'" class="log-view">
                  <LogEntry :event="selected" :meta="false" />
                </div>
                <JsonView v-else :value="selected" :title="`Event ${selected.seq} · ${selected.type}`" />
              </template>
              <JsonView v-else-if="selected" :value="selected"
                        :title="`Event ${selected.seq} · ${selected.type}${selected.payload?.name ? ' · ' + selected.payload.name : ''}`" />
              <div v-else class="muted pad">Select an event to see it as JSON.</div>
            </SplitterPanel>
          </Splitter>
          </div>
        </TabPanel>

        <TabPanel value="console">
          <div class="pane">
            <div class="ev-toolbar row">
              <SelectButton v-model="levels" :options="LEVELS" option-label="label" option-value="value" multiple size="small" />
              <IconField>
                <InputIcon class="pi pi-search" />
                <InputText v-model="logSearch" placeholder="Filter logs" size="small" />
              </IconField>
              <span class="grow" />
              <span class="small muted">{{ shownLogs.length }} / {{ logs.length }}</span>
            </div>
            <div class="console-list">
              <LogEntry v-for="ev in shownLogs" :key="ev.seq" :event="ev" @locate="locate" />
              <div v-if="!logs.length" class="muted pad">
                No logs yet. In a step: <code>self.log("total", n, data)</code>, <code>self.warn(...)</code>,
                <code>self.error(...)</code> or <code>self.log_table(rows)</code>. <code>print()</code> output lands here too.
              </div>
            </div>
          </div>
        </TabPanel>

        <TabPanel value="steps">
          <div class="pane scroll">
          <TreeTable v-model:expanded-keys="expanded" v-model:selection-keys="selKeys" :value="tree" selection-mode="single"
                     size="small" class="tree" @node-select="onNodeSelect">
            <template #empty><div class="muted pad">No steps have run yet.</div></template>
            <Column field="name" header="Call" expander>
              <template #body="{ node }"><span :class="{ strong: node.data.kind === 'step' || node.data.kind === 'subflow' }">{{ node.data.name }}</span></template>
            </Column>
            <Column header="Kind" style="width: 8rem">
              <template #body="{ node }"><span class="muted small">{{ kindLabel[node.data.kind] }}</span></template>
            </Column>
            <Column header="Status" style="width: 9rem">
              <template #body="{ node }"><StatusTag v-if="node.data.status" :status="node.data.status" /></template>
            </Column>
            <Column header="Duration" style="width: 7rem">
              <template #body="{ node }"><span class="small">{{ node.data.duration_ms !== null ? duration(node.data.duration_ms) : '' }}</span></template>
            </Column>
          </TreeTable>
          <div class="muted small pad">Click a call to select its first event; double-click an event in the Events list to find its call here.</div>
          </div>
        </TabPanel>

        <TabPanel value="io">
          <div class="pane">
          <Splitter v-if="run" class="io-split" :gutter-size="4">
            <SplitterPanel :size="50" class="io-panel"><JsonView :value="run.input ?? {}" title="Input" /></SplitterPanel>
            <SplitterPanel :size="50" class="io-panel">
              <JsonView v-if="run.status === 'succeeded'" :value="run.output ?? {}" title="Output" />
              <JsonView v-else-if="run.error" :value="run.error" title="Error" wrap />
              <div v-else class="muted pad">Output appears when the run finishes.</div>
            </SplitterPanel>
          </Splitter>
          </div>
        </TabPanel>
      </TabPanels>
    </Tabs>
  </div>
</template>

<style scoped>
.run-viewer {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.rv-head {
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--p-content-border-color);
  flex-wrap: wrap;
}
.title {
  font-weight: 700;
  font-size: 15px;
}
.live :deep(.p-tag-icon) {
  font-size: 8px;
  animation: blink 1.2s infinite;
}
@keyframes blink { 50% { opacity: 0.2; } }
.small { font-size: 12px; }
.link {
  cursor: pointer;
  color: var(--p-primary-color);
}
.banner {
  margin: 0.5rem 1rem 0;
}
.trace {
  white-space: pre-wrap;
  max-height: 14rem;
  overflow: auto;
  margin: 0.5rem 0 0;
  font-size: 12px;
}
.rv-tabs {
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.rv-panels {
  flex: 1 1 auto;
  min-height: 0;
  padding: 0;
}
.rv-panels > :deep(.p-tabpanel) {
  height: 100%;
}
.pane {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.scroll {
  overflow: auto;
}
.ev-toolbar {
  padding: 0.5rem 1rem;
  flex-wrap: wrap;
}
.ev-split {
  flex: 1 1 auto;
  min-height: 0;
  border: 0;
  border-top: 1px solid var(--p-content-border-color);
  border-radius: 0;
}
.ev-left, .ev-right {
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.ev-right {
  padding: 0.5rem;
}
.ev-head {
  padding: 4px 10px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--p-text-muted-color);
  border-bottom: 1px solid var(--p-content-border-color);
}
.ev-list-wrap {
  flex: 1 1 auto;
  min-height: 0;
  outline: none;
  position: relative;
}
.ev-list {
  position: absolute;
  inset: 0;
}
.ev-row {
  height: 30px;
  padding: 0 10px;
  cursor: pointer;
  border-bottom: 1px solid var(--p-content-border-color);
  box-sizing: border-box;
  gap: 0.6rem;
}
.ev-row:hover { background: var(--p-content-hover-background); }
.ev-row.sel { background: var(--p-highlight-background); color: var(--p-highlight-color); }
.ev-row.bad .ev-detail { color: var(--p-red-500); }
.c-seq { width: 2.6rem; text-align: right; flex: none; }
.c-time { width: 6.6rem; flex: none; }
.c-type { width: 8.6rem; flex: none; }
.ev-tag { font-size: 11px; font-weight: 500; padding: 1px 6px; }
.ev-detail { font-size: 13px; }
.pad { padding: 1rem; }
.strong { font-weight: 600; }
.view-switch {
  padding: 0 0 0.4rem;
}
.log-view {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  border: 1px solid var(--p-content-border-color);
  border-radius: 6px;
}
.console-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  border-top: 1px solid var(--p-content-border-color);
}
.tree { margin: 0.5rem 1rem 0; }
.strong { font-weight: 600; }
.io-split {
  flex: 1 1 auto;
  min-height: 0;
  border: 0;
  border-radius: 0;
}
.io-panel {
  padding: 0.5rem;
  min-height: 0;
}
:deep(.rv-tabs .p-tab) {
  padding: 0.6rem 1rem;
}
</style>
