import { computed, reactive, watch } from 'vue'
import type { Environment } from '~/types/api'

// Environments and the one selected for new runs.
// `selected`: DEFAULT_ENV = the engine's default environment, NO_ENV = none, otherwise an environment name.
// (Environment names cannot contain `@`, so the two markers never clash with a real name.)
export const DEFAULT_ENV = '@default'
export const NO_ENV = '@none'

const state = reactive<{ items: Environment[]; selected: string; loaded: boolean }>({
  items: [], selected: DEFAULT_ENV, loaded: false,
})

/** The `env` field for POST .../runs: omitted for the default, "" for none, else the name. */
export function envForRun(choice: string): { env?: string } {
  if (choice === DEFAULT_ENV) return {}
  if (choice === NO_ENV) return { env: '' }
  return { env: choice }
}
let started = false

async function load() {
  try {
    state.items = (await api.listEnvs()).items
    if (state.selected !== DEFAULT_ENV && state.selected !== NO_ENV && !state.items.some(e => e.name === state.selected)) {
      state.selected = DEFAULT_ENV
    }
  } catch { /* engine offline */ } finally {
    state.loaded = true
  }
}

const defaultEnv = computed(() => state.items.find(e => e.is_default) ?? null)

/** The environment a new run gets with the current selection (null = none). */
const effective = computed<Environment | null>(() => {
  if (state.selected === NO_ENV) return null
  if (state.selected === DEFAULT_ENV) return defaultEnv.value
  return state.items.find(e => e.name === state.selected) ?? null
})

export interface EnvOption { label: string; value: string }

const options = computed<EnvOption[]>(() => [
  { label: defaultEnv.value ? `Default (${defaultEnv.value.name})` : 'Default (none set)', value: DEFAULT_ENV },
  ...state.items.map(e => ({ label: e.name, value: e.name })),
  { label: 'No environment', value: NO_ENV },
])

export function useEnvs() {
  if (!started && typeof window !== 'undefined') {
    started = true
    try {
      const saved = localStorage.getItem('coco.env')
      if (saved) state.selected = saved
    } catch { /* ignore */ }
    watch(() => state.selected, (v) => {
      try { localStorage.setItem('coco.env', v) } catch { /* ignore */ }
    })
    const { signals } = useSignals()
    watch(() => signals.environments, load)
    load()
  }
  return { state, load, defaultEnv, effective, options }
}
