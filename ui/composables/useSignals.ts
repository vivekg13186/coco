import { reactive } from 'vue'

// Tiny cross-tab change signals: a save, run start or run finish bumps a counter and the
// overview tables that watch it reload. Everything lives in one page, so no BroadcastChannel.
const signals = reactive({ workflows: 0, runs: 0, schedules: 0, environments: 0, notes: 0 })

export function bump(kind: keyof typeof signals) {
  signals[kind] += 1
}

export function useSignals() {
  return { signals, bump }
}
