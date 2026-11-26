<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import type { Environment } from '~/types/api'

const toast = useToast()
const confirm = useConfirm()
const envs = useEnvs()
const { signals } = useSignals()

const selectedId = ref<string | null>(null) // env id, or 'new'
const name = ref('')
const description = ref('')
const isDefault = ref(false)
const jsonText = ref('{}')
const jsonError = ref<string | null>(null)
const saved = ref({ name: '', description: '', isDefault: false, jsonText: '{}' })
const saving = ref(false)

const items = computed(() => envs.state.items)
const current = computed<Environment | null>(() => items.value.find(e => e.id === selectedId.value) ?? null)
const isNew = computed(() => selectedId.value === 'new')
const dirty = computed(() => name.value !== saved.value.name || description.value !== saved.value.description
  || isDefault.value !== saved.value.isDefault || jsonText.value !== saved.value.jsonText)
const nameValid = computed(() => /^[A-Za-z0-9_.-]{1,64}$/.test(name.value))
const canSave = computed(() => (dirty.value || isNew.value) && nameValid.value && !jsonError.value && !saving.value)
const usedForRuns = computed(() => envs.effective.value?.id === current.value?.id && !!current.value)

function fill(e: Environment | null) {
  name.value = e?.name ?? ''
  description.value = e?.description ?? ''
  isDefault.value = e?.is_default ?? false
  jsonText.value = JSON.stringify(e?.data ?? {}, null, 2)
  jsonError.value = null
  saved.value = { name: name.value, description: description.value, isDefault: isDefault.value, jsonText: jsonText.value }
}

function guard(next: () => void) {
  if (!dirty.value) return next()
  confirm.require({
    header: 'Discard changes?',
    message: `Your edits to ${name.value || 'this environment'} are not saved.`,
    icon: 'pi pi-exclamation-triangle',
    acceptProps: { label: 'Discard', severity: 'danger' },
    rejectProps: { label: 'Keep editing', severity: 'secondary', text: true },
    accept: next,
  })
}

function select(e: Environment) {
  if (e.id === selectedId.value) return
  guard(() => {
    selectedId.value = e.id
    fill(e)
  })
}

function startNew() {
  guard(() => {
    selectedId.value = 'new'
    fill(null)
    jsonText.value = '{\n  "api_url": "https://example.com"\n}'
    name.value = items.value.length ? '' : 'dev'
    isDefault.value = !items.value.length
  })
}

onMounted(async () => {
  await envs.load()
  if (!selectedId.value && items.value.length) {
    selectedId.value = items.value[0].id
    fill(items.value[0])
  }
})
watch(() => signals.environments, () => {
  if (current.value && !dirty.value) fill(current.value)
})

function onJson(t: string) {
  jsonText.value = t
  try {
    const v = JSON.parse(t)
    jsonError.value = (typeof v !== 'object' || v === null || Array.isArray(v)) ? 'An environment must be a JSON object { … }' : null
  } catch (e: any) {
    jsonError.value = e.message
  }
}

async function save() {
  if (!canSave.value) return
  saving.value = true
  try {
    const body = { name: name.value, description: description.value, data: JSON.parse(jsonText.value), is_default: isDefault.value }
    const e = isNew.value ? await api.createEnv(body) : await api.updateEnv(selectedId.value!, body)
    if (!isNew.value && saved.value.name !== e.name && envs.state.selected === saved.value.name) envs.state.selected = e.name
    await envs.load()
    selectedId.value = e.id
    fill(e)
    bump('environments')
    toast.add({ severity: 'success', summary: `Saved ${e.name}`, life: 2000 })
  } catch (err: any) {
    toast.add({ severity: 'error', summary: 'Save failed', detail: err.message, life: 6000 })
  } finally {
    saving.value = false
  }
}

function remove() {
  const e = current.value
  if (!e) return
  confirm.require({
    header: `Delete ${e.name}?`,
    message: 'Runs that used it keep its name in their history, but they can no longer be rerun with it.',
    icon: 'pi pi-trash',
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', severity: 'secondary', text: true },
    accept: async () => {
      try {
        await api.deleteEnv(e.id)
        if (envs.state.selected === e.name) envs.state.selected = DEFAULT_ENV
        await envs.load()
        bump('environments')
        selectedId.value = items.value[0]?.id ?? null
        fill(items.value[0] ?? null)
      } catch (err: any) {
        toast.add({ severity: 'error', summary: 'Delete failed', detail: err.message, life: 5000 })
      }
    },
  })
}

function useForRuns() {
  if (current.value) envs.state.selected = current.value.name
}

function cancelNew() {
  selectedId.value = items.value[0]?.id ?? null
  fill(items.value[0] ?? null)
}
</script>

<template>
  <div class="tab-root">
    <Splitter class="env-split" :gutter-size="4">
      <SplitterPanel :size="28" :min-size="18" class="env-list">
        <div class="list-head row">
          <b class="grow">Environments</b>
          <Button label="New" icon="pi pi-plus" size="small" @click="startNew" />
        </div>
        <div v-if="!items.length && !isNew" class="muted pad">
          No environments yet. An environment is a JSON object of settings (URLs, keys, flags) that a run can use.
        </div>
        <div v-for="e in items" :key="e.id" class="env-item" :class="{ sel: e.id === selectedId }" @click="select(e)">
          <div class="row">
            <span class="env-name grow ellipsis">{{ e.name }}</span>
            <Tag v-if="e.is_default" value="default" severity="info" class="small-tag" />
            <i v-if="envs.effective.value?.id === e.id" v-tooltip.left="'Used for new runs'" class="pi pi-play-circle used" />
          </div>
          <div class="muted small ellipsis">{{ e.description || `${e.keys.length} key${e.keys.length === 1 ? '' : 's'}: ${e.keys.join(', ')}` }}</div>
        </div>
        <div v-if="isNew" class="env-item sel"><span class="env-name">{{ name || 'New environment' }}</span></div>
      </SplitterPanel>

      <SplitterPanel :size="72" :min-size="40" class="env-edit">
        <div v-if="!selectedId" class="muted pad">Select an environment or create one with <b>New</b>.</div>
        <template v-else>
          <div class="edit-head row">
            <div class="field">
              <label for="env-name" class="small muted">Name</label>
              <InputText id="env-name" v-model="name" size="small" :invalid="!!name && !nameValid" placeholder="dev" />
            </div>
            <div class="field grow">
              <label for="env-desc" class="small muted">Description</label>
              <InputText id="env-desc" v-model="description" size="small" fluid placeholder="What this environment is for" />
            </div>
            <label class="row small default-toggle" for="env-default">
              <ToggleSwitch v-model="isDefault" input-id="env-default" /> Default
            </label>
          </div>
          <div class="edit-actions row">
            <Button label="Save" icon="pi pi-save" size="small" :disabled="!canSave" :loading="saving" @click="save" />
            <Button v-if="isNew" label="Cancel" size="small" severity="secondary" text @click="cancelNew" />
            <template v-else>
              <Button :label="usedForRuns ? 'Used for new runs' : 'Use for new runs'" :icon="usedForRuns ? 'pi pi-check' : 'pi pi-play-circle'"
                      size="small" severity="secondary" outlined :disabled="usedForRuns" @click="useForRuns" />
              <Button label="Delete" icon="pi pi-trash" size="small" severity="danger" text @click="remove" />
            </template>
            <span class="grow" />
            <small v-if="name && !nameValid" class="err">Letters, digits, _ - . only</small>
            <small v-else-if="jsonError" class="err">{{ jsonError }}</small>
            <small v-else-if="dirty" class="muted">Unsaved changes</small>
          </div>
          <div class="json-area">
            <JsonView :key="selectedId" editable :text="jsonText" title="Values (JSON object)" @update:text="onJson" />
          </div>
          <div class="help small muted">
            In a flow: <code>env.api_url</code>, <code>env["api_url"]</code> or <code>env.get("retries", 3)</code>.
            In a step: <code>self.env.api_url</code>. Values are read-only during a run. Run events record only the
            environment's name, but values are stored unencrypted in the engine database, so keep real secrets elsewhere.
          </div>
        </template>
      </SplitterPanel>
    </Splitter>
  </div>
</template>

<style scoped>
.tab-root {
  height: 100%;
}
.env-split {
  height: 100%;
  border: 0;
  border-radius: 0;
}
.env-list {
  overflow: auto;
  border-right: 1px solid var(--p-content-border-color);
}
.list-head {
  padding: 0.6rem 0.85rem;
  border-bottom: 1px solid var(--p-content-border-color);
}
.env-item {
  padding: 0.55rem 0.85rem;
  border-bottom: 1px solid var(--p-content-border-color);
  cursor: pointer;
}
.env-item:hover { background: var(--p-content-hover-background); }
.env-item.sel { background: var(--p-highlight-background); color: var(--p-highlight-color); }
.env-name { font-weight: 600; }
.used { color: var(--p-primary-color); }
.env-edit {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0.75rem 1rem;
  gap: 0.6rem;
}
.edit-head { align-items: flex-end; gap: 0.75rem; }
.field { display: flex; flex-direction: column; gap: 0.25rem; }
.default-toggle { padding-bottom: 0.4rem; }
.json-area {
  flex: 1 1 auto;
  min-height: 0;
}
.help { line-height: 1.5; }
.small { font-size: 12px; }
.small-tag { font-size: 11px; }
.pad { padding: 1rem; }
.err { color: var(--p-red-500); }
</style>
