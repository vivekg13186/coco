import { reactive, watch } from 'vue'

// WorkPanel tab state: three fixed overview tabs plus closable editor tabs.
// Editor tabs are keyed `wf:<id>`, `run:<id>`, `sch:<id>` so opening an item twice focuses it.

export type TabKind = 'wf' | 'run' | 'sch'
export const FIXED_TABS = ['workflows', 'runs', 'schedulers', 'environments', 'notes'] as const

export interface EditorTab {
  key: string
  kind: TabKind
  id: string
  title: string
  dirty: boolean
  params?: Record<string, unknown>
}

interface TabsState {
  tabs: EditorTab[]
  active: string
}

const STORAGE_KEY = 'coco.tabs'
const state = reactive<TabsState>({ tabs: [], active: 'workflows' })
let initialised = false
let closeGuard: ((tab: EditorTab) => Promise<boolean>) | null = null

function persist() {
  try {
    const tabs = state.tabs.filter(t => !t.id.startsWith('new-')).map(t => ({ ...t, dirty: false }))
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ tabs, active: state.active }))
  } catch { /* storage unavailable */ }
  try {
    const url = new URL(window.location.href)
    if (state.active === 'workflows') url.searchParams.delete('tab')
    else url.searchParams.set('tab', state.active)
    window.history.replaceState(null, '', url.toString())
  } catch { /* not in a browser */ }
}

function restore() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (saved && Array.isArray(saved.tabs)) {
      state.tabs = saved.tabs
      state.active = saved.active || 'workflows'
    }
  } catch { /* ignore broken storage */ }
  try {
    const want = new URL(window.location.href).searchParams.get('tab')
    if (want) {
      const [kind, ...rest] = want.split(':')
      const id = rest.join(':')
      if ((FIXED_TABS as readonly string[]).includes(want)) state.active = want
      else if ((kind === 'wf' || kind === 'run' || kind === 'sch') && id) open(kind, id, id)
    }
  } catch { /* ignore */ }
  if (!isOpen(state.active)) state.active = 'workflows'
}

function isOpen(key: string) {
  return (FIXED_TABS as readonly string[]).includes(key) || state.tabs.some(t => t.key === key)
}

function find(key: string) {
  return state.tabs.find(t => t.key === key)
}

export function open(kind: TabKind, id: string, title: string, params?: Record<string, unknown>) {
  const key = `${kind}:${id}`
  const existing = find(key)
  if (existing) {
    if (params) existing.params = { ...existing.params, ...params }
  } else {
    state.tabs.push({ key, kind, id, title, dirty: false, params })
  }
  state.active = key
  return key
}

let newCounter = 0
export function openNewWorkflow(params?: Record<string, unknown>) {
  newCounter += 1
  return open('wf', `new-${Date.now()}-${newCounter}`, 'New workflow', params)
}

export async function close(key: string, force = false) {
  const tab = find(key)
  if (!tab) return
  if (tab.dirty && !force && closeGuard && !(await closeGuard(tab))) return
  const i = state.tabs.indexOf(tab)
  state.tabs.splice(i, 1)
  if (state.active === key) {
    const neighbour = state.tabs[i] ?? state.tabs[i - 1]
    state.active = neighbour ? neighbour.key : 'workflows'
  }
}

export function setTitle(key: string, title: string) {
  const t = find(key)
  if (t) t.title = title
}

export function setDirty(key: string, dirty: boolean) {
  const t = find(key)
  if (t && t.dirty !== dirty) t.dirty = dirty
}

/** A new workflow got its id on first save: keep the tab, change its key. */
export function rekey(oldKey: string, kind: TabKind, id: string, title: string) {
  const t = find(oldKey)
  if (!t) return
  const key = `${kind}:${id}`
  const clash = find(key)
  if (clash && clash !== t) state.tabs.splice(state.tabs.indexOf(clash), 1)
  const wasActive = state.active === oldKey
  t.key = key
  t.id = id
  t.title = title
  if (wasActive) state.active = key
}

/** Close every open item; items with unsaved changes ask first (one by one). */
export async function closeAll() {
  for (const t of [...state.tabs]) await close(t.key)
}

export function setCloseGuard(fn: (tab: EditorTab) => Promise<boolean>) {
  closeGuard = fn
}

export function useTabs() {
  if (!initialised && typeof window !== 'undefined') {
    initialised = true
    restore()
    watch(() => [state.active, state.tabs.map(t => t.key + t.title).join('|')], persist)
  }
  return { state, open, openNewWorkflow, close, closeAll, setTitle, setDirty, rekey, setCloseGuard, isOpen }
}
