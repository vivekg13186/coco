<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import type { Workflow } from '~/types/api'

const toast = useToast()
const confirm = useConfirm()
const { open, openNewWorkflow } = useTabs()
const { signals } = useSignals()

const rows = ref<Workflow[]>([])
const loading = ref(false)
const search = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const runTarget = ref<Workflow | null>(null)
const runVisible = ref(false)

const filtered = computed(() => {
  const s = search.value.trim().toLowerCase()
  if (!s) return rows.value
  return rows.value.filter(w => w.name.toLowerCase().includes(s) || w.description.toLowerCase().includes(s)
    || (w.current_version?.flows ?? []).some(f => f.name.toLowerCase().includes(s)))
})

async function load() {
  loading.value = true
  try {
    rows.value = (await api.listWorkflows()).items
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not load workflows', detail: e.message, life: 5000 })
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(() => signals.workflows, load)

function edit(w: Workflow) {
  open('wf', w.id, w.name)
}

async function runWorkflow(w: Workflow) {
  if (!w.current_version?.is_valid) {
    toast.add({ severity: 'warn', summary: 'Fix errors first', detail: `${w.name} has errors and cannot run.`, life: 4000 })
    return
  }
  try {
    runTarget.value = await api.getWorkflow(w.id)  // full version incl. symbols for input templates
    runVisible.value = true
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not open run dialog', detail: e.message, life: 5000 })
  }
}

async function duplicate(w: Workflow) {
  try {
    const full = await api.getWorkflow(w.id)
    const src = full.current_version?.source ?? ''
    let name = `${w.name}_copy`
    let n = 2
    while (rows.value.some(r => r.name === name)) name = `${w.name}_copy${n++}`
    const copy = await api.createWorkflow(name, src, w.description)
    bump('workflows')
    open('wf', copy.id, copy.name)
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Duplicate failed', detail: e.message, life: 5000 })
  }
}

function remove(w: Workflow) {
  confirm.require({
    header: `Delete ${w.name}?`,
    message: 'This deletes the workflow, all its versions and its run history. It cannot be undone.',
    icon: 'pi pi-trash',
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', severity: 'secondary', text: true },
    accept: async () => {
      try {
        await api.deleteWorkflow(w.id)
        toast.add({ severity: 'success', summary: `Deleted ${w.name}`, life: 2500 })
        bump('workflows')
        bump('runs')
      } catch (e: any) {
        toast.add({ severity: 'error', summary: 'Delete failed', detail: e.message, life: 5000 })
      }
    },
  })
}

async function onImport(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    const source = await file.text()
    let name = file.name.replace(/\.(coco|py|txt)$/i, '').replace(/\W/g, '_') || 'workflow'
    if (/^\d/.test(name)) name = `w_${name}`
    const base = name
    let n = 2
    while (rows.value.some(r => r.name === name)) name = `${base}_${n++}`
    const w = await api.createWorkflow(name, source)
    toast.add({ severity: 'success', summary: `Imported ${w.name}`, detail: w.current_version?.is_valid ? undefined : 'It has errors to fix.', life: 3000 })
    bump('workflows')
    open('wf', w.id, w.name)
  } catch (e: any) {
    toast.add({ severity: 'error', summary: `Import of ${file.name} failed`, detail: e.message, life: 6000 })
  }
}
</script>

<template>
  <div class="tab-root">
    <Toolbar class="tab-toolbar">
      <template #start>
        <div class="row">
          <Button label="New" icon="pi pi-plus" size="small" @click="openNewWorkflow()" />
          <Button label="Import file" icon="pi pi-upload" size="small" severity="secondary" outlined @click="fileInput?.click()" />
          <input ref="fileInput" type="file" accept=".coco,.py,text/plain,text/x-python" hidden @change="onImport">
          <Button v-tooltip.bottom="'Refresh'" icon="pi pi-refresh" size="small" text :loading="loading" @click="load" />
        </div>
      </template>
      <template #end>
        <IconField>
          <InputIcon class="pi pi-search" />
          <InputText v-model="search" placeholder="Search workflows" size="small" />
        </IconField>
      </template>
    </Toolbar>

    <DataTable :value="filtered" data-key="id" size="small" striped-rows scrollable scroll-height="flex"
               :loading="loading" class="grow-table" @row-dblclick="e => edit(e.data)">
      <template #empty>
        <div class="empty">
          No workflows yet. Click <b>New</b> to write one, or <b>Import .coco</b> to load a file.
        </div>
      </template>
      <Column field="name" header="Name" sortable style="min-width: 12rem">
        <template #body="{ data }">
          <a class="link" @click="edit(data)">{{ data.name }}</a>
          <div v-if="data.description" class="muted small ellipsis">{{ data.description }}</div>
        </template>
      </Column>
      <Column header="Flows" style="min-width: 12rem">
        <template #body="{ data }">
          <div class="chips">
            <Tag v-for="f in data.current_version?.flows ?? []" :key="f.name" :value="f.name" severity="secondary" class="chip" />
          </div>
        </template>
      </Column>
      <Column header="Version" style="width: 6rem">
        <template #body="{ data }">v{{ data.current_version?.version ?? '—' }}</template>
      </Column>
      <Column header="Valid" style="width: 7rem">
        <template #body="{ data }">
          <Tag v-if="data.current_version?.is_valid" value="valid" severity="success" icon="pi pi-check" />
          <Tag v-else value="errors" severity="danger" icon="pi pi-exclamation-triangle" />
        </template>
      </Column>
      <Column header="Last run" style="min-width: 11rem">
        <template #body="{ data }">
          <div v-if="data.last_run" class="row">
            <StatusTag :status="data.last_run.status" />
            <span class="muted small">{{ ago(data.last_run.queued_at) }}</span>
          </div>
          <span v-else class="muted">never</span>
        </template>
      </Column>
      <Column field="updated_at" header="Updated" sortable style="width: 11rem">
        <template #body="{ data }"><span class="muted small">{{ dateTime(data.updated_at) }}</span></template>
      </Column>
      <Column header="" style="width: 11rem" body-class="actions">
        <template #body="{ data }">
          <Button v-tooltip.bottom="'Edit'" icon="pi pi-pencil" text rounded size="small" @click.stop="edit(data)" />
          <Button v-tooltip.bottom="'Run'" icon="pi pi-play" text rounded size="small" severity="success" @click.stop="runWorkflow(data)" />
          <Button v-tooltip.bottom="'Duplicate'" icon="pi pi-clone" text rounded size="small" severity="secondary" @click.stop="duplicate(data)" />
          <Button v-tooltip.bottom="'Delete'" icon="pi pi-trash" text rounded size="small" severity="danger" @click.stop="remove(data)" />
        </template>
      </Column>
    </DataTable>

    <RunDialog v-if="runTarget" v-model:visible="runVisible" :workflow-id="runTarget.id" :workflow-name="runTarget.name"
               :flows="runTarget.current_version?.flows ?? []" :classes="runTarget.current_version?.symbols?.classes ?? []" />
  </div>
</template>

<style scoped>
.tab-root {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.tab-toolbar {
  border: 0;
  border-bottom: 1px solid var(--p-content-border-color);
  border-radius: 0;
  padding: 0.5rem 1rem;
}
.grow-table {
  flex: 1 1 auto;
  min-height: 0;
}
.link {
  font-weight: 600;
  cursor: pointer;
  color: var(--p-primary-color);
}
.small {
  font-size: 12px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.chip {
  font-weight: 500;
  font-size: 11.5px;
}
.empty {
  padding: 2rem;
  text-align: center;
  color: var(--p-text-muted-color);
}
:deep(.actions) {
  text-align: right;
  white-space: nowrap;
}
</style>
