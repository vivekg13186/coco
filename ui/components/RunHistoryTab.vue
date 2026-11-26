<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import type { Run, Workflow } from '~/types/api'
import { TERMINAL } from '~/types/api'

const toast = useToast()
const confirm = useConfirm()
const { open } = useTabs()
const { signals } = useSignals()

const rows = ref<Run[]>([])
const total = ref(0)
const loading = ref(false)
const first = ref(0)
const pageSize = ref(25)
const workflows = ref<Workflow[]>([])
const filters = ref<{ workflow: string | null; status: string[]; trigger: string | null; env: string | null; dates: Date[] | null; q: string }>({
  workflow: null, status: [], trigger: null, env: null, dates: null, q: '',
})
const envs = useEnvs()
const autoRefresh = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const STATUSES = ['queued', 'running', 'succeeded', 'failed', 'cancelled']
const TRIGGERS = ['manual', 'api', 'schedule', 'rerun']

function dayIso(d: Date, endOfDay = false) {
  const x = new Date(d)
  if (endOfDay) x.setHours(24, 0, 0, 0)
  else x.setHours(0, 0, 0, 0)
  return x.toISOString()
}

async function load(quiet = false) {
  if (!quiet) loading.value = true
  try {
    const f = filters.value
    const [from, to] = f.dates ?? []
    const page = await api.listRuns({
      workflow_id: f.workflow, status: f.status.join(','), trigger: f.trigger, env: f.env, q: f.q,
      since: from ? dayIso(from) : undefined, until: (to ?? from) ? dayIso((to ?? from)!, true) : undefined,
      page: Math.floor(first.value / pageSize.value) + 1, size: pageSize.value,
    })
    rows.value = page.items
    total.value = page.total
  } catch (e: any) {
    if (!quiet) toast.add({ severity: 'error', summary: 'Could not load runs', detail: e.message, life: 5000 })
  } finally {
    loading.value = false
  }
}

async function loadWorkflows() {
  try { workflows.value = (await api.listWorkflows()).items } catch { /* shown elsewhere */ }
}

onMounted(() => {
  load()
  loadWorkflows()
  timer = setInterval(() => {
    if (autoRefresh.value && rows.value.some(r => !TERMINAL.includes(r.status))) load(true)
  }, 2000)
})
onBeforeUnmount(() => timer && clearInterval(timer))
watch(() => signals.runs, () => load(true))
watch(() => signals.workflows, loadWorkflows)
watch(filters, () => { first.value = 0; load() }, { deep: true })

function onPage(e: { first: number; rows: number }) {
  first.value = e.first
  pageSize.value = e.rows
  load()
}

function openRun(r: Run) {
  open('run', r.id, `Run · ${r.workflow_name}.${r.flow_name} · ${clock(r.queued_at)}`)
}

async function rerun(r: Run) {
  try {
    const n = await api.rerun(r.id)
    bump('runs')
    openRun({ ...n, workflow_name: r.workflow_name })
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Rerun failed', detail: e.message, life: 5000 })
  }
}

async function cancel(r: Run) {
  try {
    await api.cancelRun(r.id)
    toast.add({ severity: 'info', summary: 'Cancel requested', detail: 'The run stops at its next statement.', life: 3000 })
    setTimeout(() => load(true), 400)
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Cancel failed', detail: e.message, life: 5000 })
  }
}

function remove(r: Run) {
  confirm.require({
    header: 'Delete this run?',
    message: `${r.workflow_name}.${r.flow_name} from ${dateTime(r.queued_at)} and all its events will be deleted.`,
    icon: 'pi pi-trash',
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', severity: 'secondary', text: true },
    accept: async () => {
      try {
        await api.deleteRun(r.id)
        load(true)
      } catch (e: any) {
        toast.add({ severity: 'error', summary: 'Delete failed', detail: e.message, life: 5000 })
      }
    },
  })
}
</script>

<template>
  <div class="tab-root">
    <Toolbar class="tab-toolbar">
      <template #start>
        <div class="row filters">
          <Select v-model="filters.workflow" :options="workflows" option-label="name" option-value="id" placeholder="All workflows"
                  show-clear size="small" class="f-wf" />
          <MultiSelect v-model="filters.status" :options="STATUSES" placeholder="Any status" size="small" class="f-status"
                       :max-selected-labels="2" />
          <Select v-model="filters.trigger" :options="TRIGGERS" placeholder="Any trigger" show-clear size="small" class="f-trigger" />
          <Select v-model="filters.env" :options="envs.state.items" option-label="name" option-value="name" placeholder="Any env"
                  show-clear size="small" class="f-trigger" />
          <DatePicker v-model="filters.dates" selection-mode="range" placeholder="Date range" show-button-bar size="small"
                      date-format="yy-mm-dd" class="f-dates" :manual-input="false" />
          <IconField>
            <InputIcon class="pi pi-search" />
            <InputText v-model.lazy="filters.q" placeholder="Search" size="small" class="f-q" />
          </IconField>
        </div>
      </template>
      <template #end>
        <div class="row">
          <label class="row muted small" for="autorefresh">
            <ToggleSwitch v-model="autoRefresh" input-id="autorefresh" /> Auto-refresh
          </label>
          <Button v-tooltip.bottom="'Refresh'" icon="pi pi-refresh" text size="small" :loading="loading" @click="load()" />
        </div>
      </template>
    </Toolbar>

    <DataTable :value="rows" data-key="id" lazy paginator :rows="pageSize" :first="first" :total-records="total"
               :rows-per-page-options="[25, 50, 100]" size="small" striped-rows scrollable scroll-height="flex"
               :loading="loading" class="grow-table" @page="onPage" @row-click="e => openRun(e.data)">
      <template #empty>
        <div class="empty">No runs match. Start one from the Workflows tab or a workflow editor.</div>
      </template>
      <Column header="Started" style="width: 11rem">
        <template #body="{ data }"><span class="small">{{ dateTime(data.queued_at) }}</span></template>
      </Column>
      <Column header="Workflow" style="min-width: 9rem">
        <template #body="{ data }"><b>{{ data.workflow_name }}</b> <span class="muted small">v{{ data.version }}</span></template>
      </Column>
      <Column field="flow_name" header="Flow" style="min-width: 8rem" />
      <Column header="Env" style="width: 7rem">
        <template #body="{ data }"><span v-if="data.env_name" class="small">{{ data.env_name }}</span><span v-else class="muted small">—</span></template>
      </Column>
      <Column header="Trigger" style="width: 7rem">
        <template #body="{ data }"><Tag :value="data.trigger" severity="secondary" class="small-tag" /></template>
      </Column>
      <Column header="Status" style="width: 9rem">
        <template #body="{ data }"><StatusTag :status="data.status" /></template>
      </Column>
      <Column header="Duration" style="width: 7rem">
        <template #body="{ data }"><span class="small">{{ duration(data.duration_ms) }}</span></template>
      </Column>
      <Column header="Events" style="width: 5rem">
        <template #body="{ data }"><span class="small muted">{{ data.event_count || '—' }}</span></template>
      </Column>
      <Column header="" style="width: 9rem" body-class="actions">
        <template #body="{ data }">
          <Button v-tooltip.bottom="'Open'" icon="pi pi-external-link" text rounded size="small" @click.stop="openRun(data)" />
          <Button v-tooltip.bottom="'Rerun'" icon="pi pi-replay" text rounded size="small" severity="secondary" @click.stop="rerun(data)" />
          <Button v-if="!TERMINAL.includes(data.status)" v-tooltip.bottom="'Cancel'" icon="pi pi-stop-circle" text rounded
                  size="small" severity="warn" @click.stop="cancel(data)" />
          <Button v-else v-tooltip.bottom="'Delete'" icon="pi pi-trash" text rounded size="small" severity="danger" @click.stop="remove(data)" />
        </template>
      </Column>
    </DataTable>
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
.filters {
  flex-wrap: wrap;
}
.f-wf { width: 11rem; }
.f-status { width: 11rem; }
.f-trigger { width: 9rem; }
.f-dates { width: 13rem; }
.f-q { width: 10rem; }
.grow-table {
  flex: 1 1 auto;
  min-height: 0;
}
.small { font-size: 12px; }
.small-tag { font-size: 11.5px; font-weight: 500; }
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
