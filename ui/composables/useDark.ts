import { ref, watch } from 'vue'

const dark = ref(false)
let ready = false

function apply(on: boolean) {
  document.documentElement.classList.toggle('app-dark', on)
}

export function useDark() {
  if (!ready && typeof window !== 'undefined') {
    ready = true
    let saved: string | null = null
    try { saved = localStorage.getItem('coco.dark') } catch { /* ignore */ }
    dark.value = saved ? saved === '1' : window.matchMedia?.('(prefers-color-scheme: dark)').matches
    apply(dark.value)
    watch(dark, (on) => {
      apply(on)
      try { localStorage.setItem('coco.dark', on ? '1' : '0') } catch { /* ignore */ }
    })
  }
  return dark
}
