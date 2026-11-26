<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useToast } from 'primevue/usetoast'
import type { ClassSymbol, Signature } from '~/types/api'

// Pick a flow, enter its input as a form or as JSON, start the run and open its viewer tab.
const props = defineProps<{
  workflowId: string
  workflowName: string
  flows: Signature[]
  classes: ClassSymbol[]
  initialFlow?: string | null
}>()
const visible = defineModel<boolean>('visible', { default: false })

const toast = useToast()
const { open } = useTabs()
const envs = useEnvs()
const envChoice = ref<string>(DEFAULT_ENV)
const flowName = ref<string | null>(null)
const mode = ref<'Form' | 'JSON'>('Form')
const input = ref<Record<string, any>>({})
const jsonText = ref('{}')
const jsonError = ref<string | null>(null)
const formValid = ref(true)
const busy = ref(false)

const flow = computed(() => props.flows.find(f => f.name === flowName.value) ?? null)
const remembered = new Map<string, Record<string, any>>()

function storageKey(f: string) {
  return `coco.input.${props.workflowName}.${f}`
}

function loadInput(f: Signature) {
  let saved: Record<string, any> | null = null
  try { saved = JSON.parse(localStorage.getItem(storageKey(f.name)) || 'null') } catch { /* ignore */ }
  const base = defaultInput(f.inputs, props.classes) as Record<string, any>
  input.value = remembered.get(f.name) ?? (saved ? { ...base, ...saved } : base)
  jsonText.value = JSON.stringify(input.value, null, 2)
  jsonError.value = null
}

watch(visible, (v) => {
  if (!v) return
  envChoice.value = envs.state.selected
  flowName.value = props.initialFlow && props.flows.some(f => f.name === props.initialFlow)
    ? props.initialFlow
    : (props.flows.length === 1 ? props.flows[0].name : props.flows.at(-1)?.name ?? null)
})
watch(flow, f => f && loadInput(f), { immediate: true })

watch(mode, (m) => {
  if (m === 'JSON') jsonText.value = JSON.stringify(input.value, null, 2)
  else if (!jsonError.value) {
    try { input.value = JSON.parse(jsonText.value) } catch { /* keep form */ }
  }
})

function onJsonText(t: string) {
  jsonText.value = t
  try {
    const v = JSON.parse(t)
    if (typeof v !== 'object' || v === null || Array.isArray(v)) throw new Error('input must be a JSON object')
    input.value = v
    jsonError.value = null
  } catch (e: any) {
    jsonError.value = e.message
  }
}

const canRun = computed(() => !!flow.value && !busy.value && (mode.value === 'JSON' ? !jsonError.value : formValid.value))

async function run() {
  if (!flow.value) return
  busy.value = true
  try {
    const r = await api.startRun(props.workflowId, flow.value.name, input.value,
      envForRun(envChoice.value))
    remembered.set(flow.value.name, input.value)
    try { localStorage.setItem(storageKey(flow.value.name), JSON.stringify(input.value)) } catch { /* ignore */ }
    bump('runs')
    bump('workflows')
    visible.value = false
    open('run', r.id, `Run · ${props.workflowName}.${r.flow_name} · ${clock(r.queued_at)}`)
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Run not started', detail: e.message, life: 6000 })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog v-model:visible="visible" modal :header="`Run ${workflowName}`" :style="{ width: '40rem' }"
          :breakpoints="{ '720px': '95vw' }">
    <div class="run-dialog">
      <div class="row">
        <Select v-model="flowName" :options="flows" option-label="name" option-value="name" placeholder="Flow"
                class="grow" />
        <SelectButton v-model="mode" :options="['Form', 'JSON']" :allow-empty="false" />
      </div>
      <div v-if="flow" class="sig mono muted">{{ flow.name }}{{ signature(flow) }}</div>
      <div class="row env-row">
        <label for="run-env" class="small muted"><i class="pi pi-sliders-h" /> Environment</label>
        <Select v-model="envChoice" input-id="run-env" :options="envs.options.value" option-label="label" option-value="value"
                size="small" class="grow" />
      </div>
      <div class="body">
        <FlowInputForm v-if="flow && mode === 'Form'" v-model="input" :params="flow.inputs" :classes="classes"
                       @update:valid="v => formValid = v" />
        <div v-else-if="flow" class="json-wrap">
          <JsonView editable :text="jsonText" title="Input (JSON)" min-height="260px" @update:text="onJsonText" />
          <small v-if="jsonError" class="err">{{ jsonError }}</small>
        </div>
      </div>
    </div>
    <template #footer>
      <Button label="Cancel" text severity="secondary" @click="visible = false" />
      <Button label="Run" icon="pi pi-play" :loading="busy" :disabled="!canRun" @click="run" />
    </template>
  </Dialog>
</template>

<style scoped>
.run-dialog {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.sig {
  font-size: 12px;
}
.env-row label {
  white-space: nowrap;
}
.small {
  font-size: 12px;
}
.body {
  max-height: 60vh;
  overflow: auto;
}
.json-wrap {
  height: 300px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.err {
  color: var(--p-red-500);
}
</style>
