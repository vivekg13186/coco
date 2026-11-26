<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useConfirm } from 'primevue/useconfirm'
import type { EditorTab } from '~/composables/useTabs'
import type { Health } from '~/types/api'

const tabs = useTabs()
const dark = useDark()
const envs = useEnvs()
const confirm = useConfirm()
const health = ref<Health | null>(null)
const offline = ref(false)
let timer: ReturnType<typeof setInterval> | null = null
const { signals } = useSignals()
watch(() => signals.runs, () => setTimeout(ping, 300))

// closing a tab with unsaved changes asks first
tabs.setCloseGuard(tab => new Promise<boolean>((resolve) => {
  confirm.require({
    group: 'close-tab',
    header: `Close ${tab.title}?`,
    message: 'It has unsaved changes that will be lost.',
    icon: 'pi pi-exclamation-triangle',
    acceptProps: { label: 'Discard changes', severity: 'danger' },
    rejectProps: { label: 'Keep editing', severity: 'secondary', text: true },
    accept: () => resolve(true),
    reject: () => resolve(false),
    onHide: () => resolve(false),
  })
}))

async function ping() {
  try {
    health.value = await api.health()
    offline.value = false
  } catch {
    offline.value = true
  }
}

function onKey(e: KeyboardEvent) {
  // Alt+W closes the active editor tab (Ctrl/Cmd+W belongs to the browser); fixed tabs never close
  if (e.altKey && (e.key === 'w' || e.key === 'W' || e.code === 'KeyW')) {
    const t = tabs.state.tabs.find(x => x.key === tabs.state.active)
    if (t) {
      e.preventDefault()
      tabs.close(t.key)
    }
  }
}

onMounted(() => {
  ping()
  timer = setInterval(ping, 10_000)
  window.addEventListener('keydown', onKey)
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('beforeunload', beforeUnload)
})

function beforeUnload(e: BeforeUnloadEvent) {
  if (tabs.state.tabs.some(t => t.dirty)) {
    e.preventDefault()
    e.returnValue = ''
  }
}

function icon(t: EditorTab) {
  return t.kind === 'wf' ? 'pi pi-file-edit' : t.kind === 'run' ? 'pi pi-history' : 'pi pi-calendar'
}

// open editors and run viewers live in a dropdown instead of tabs
const openMenu = ref<any>(null)
const activeItem = computed(() => tabs.state.tabs.find(t => t.key === tabs.state.active) ?? null)
const anyDirty = computed(() => tabs.state.tabs.some(t => t.dirty))

function toggleOpen(e: Event) {
  openMenu.value?.toggle(e)
}

function activate(t: EditorTab) {
  tabs.state.active = t.key
  openMenu.value?.hide()
}

function closeItem(t: EditorTab) {
  tabs.close(t.key)
}

function closeAllItems() {
  openMenu.value?.hide()
  tabs.closeAll()
}
</script>

<template>
  <div class="shell">
    <header class="topbar row">
      <span class="logo"><span class="dot" />coco</span>
      <span class="muted small">workflow engine</span>
      <span class="grow" />
      <label class="row small muted env-pick" for="env-select">
        <i class="pi pi-sliders-h" /> Environment
        <Select v-model="envs.state.selected" input-id="env-select" :options="envs.options.value" option-label="label"
                option-value="value" size="small" class="env-select" />
      </label>
      <span v-if="offline" class="row small engine down"><i class="pi pi-circle-fill" /> engine offline</span>
      <span v-else-if="health" class="row small engine up" :title="`coco-server ${health.version} · core ${health.coco_core}`">
        <i class="pi pi-circle-fill" /> {{ health.active_runs }}/{{ health.max_runs }} running
      </span>
      <Button v-tooltip.bottom="dark ? 'Light mode' : 'Dark mode'" :icon="dark ? 'pi pi-sun' : 'pi pi-moon'" text rounded
              size="small" @click="dark = !dark" />
    </header>

    <div class="panel-wrap">
      <Tabs v-model:value="tabs.state.active" class="workpanel">
        <TabList>
          <Tab value="workflows"><span class="tab-label"><i class="pi pi-sitemap" /> Workflows</span></Tab>
          <Tab value="runs"><span class="tab-label"><i class="pi pi-list" /> Run History</span></Tab>
          <Tab value="schedulers"><span class="tab-label"><i class="pi pi-calendar-clock" /> Schedulers</span></Tab>
          <Tab value="environments"><span class="tab-label"><i class="pi pi-sliders-h" /> Environments</span></Tab>
          <Tab value="notes"><span class="tab-label"><i class="pi pi-book" /> Notes</span></Tab>
        </TabList>
      <TabPanels>
        <TabPanel value="workflows"><WorkflowsTab /></TabPanel>
        <TabPanel value="runs"><RunHistoryTab /></TabPanel>
        <TabPanel value="schedulers"><SchedulersTab /></TabPanel>
        <TabPanel value="environments"><EnvironmentsTab /></TabPanel>
        <TabPanel value="notes"><NotesTab /></TabPanel>
        <TabPanel v-for="t in tabs.state.tabs" :key="t.key" :value="t.key">
          <WorkflowEditor v-if="t.kind === 'wf'" :key="t.id" :tab="t" />
          <RunViewer v-else-if="t.kind === 'run'" :key="t.id" :tab="t" />
          <div v-else class="panel-pad muted">Schedule editors arrive with the scheduler (milestone 4).</div>
        </TabPanel>
      </TabPanels>
      </Tabs>

      <!-- open items: a dropdown at the end of the tab strip -->
      <div class="open-items">
        <button type="button" class="open-items-btn" :class="{ active: !!activeItem }" :aria-haspopup="true"
                :aria-label="`Open items (${tabs.state.tabs.length})`" @click="toggleOpen">
          <i :class="activeItem ? icon(activeItem) : 'pi pi-folder-open'" />
          <span class="oi-label">{{ activeItem ? activeItem.title : 'Open items' }}</span>
          <span v-if="activeItem?.dirty" class="dirty" title="Unsaved changes">●</span>
          <Badge v-if="tabs.state.tabs.length" :value="tabs.state.tabs.length" :severity="anyDirty ? 'warn' : 'secondary'" size="small" />
          <i class="pi pi-chevron-down chev" />
        </button>
        <Popover ref="openMenu" class="open-items-pop">
          <div class="oi-pop">
            <div class="oi-head row">
              <b class="grow">Open items</b>
              <Button v-if="tabs.state.tabs.length > 1" label="Close all" text size="small" severity="secondary" @click="closeAllItems" />
            </div>
            <div v-if="!tabs.state.tabs.length" class="muted oi-empty">
              Nothing open. Edit a workflow or open a run and it appears here.
            </div>
            <ul v-else class="oi-list">
              <li v-for="t in tabs.state.tabs" :key="t.key" class="open-item" :class="{ sel: t.key === tabs.state.active }"
                  @click="activate(t)" @mousedown.middle.prevent="closeItem(t)">
                <i :class="icon(t)" class="oi-icon" />
                <span class="grow ellipsis">{{ t.title }}</span>
                <span v-if="t.dirty" class="dirty" title="Unsaved changes">●</span>
                <button type="button" class="oi-close" :aria-label="`Close ${t.title}`" title="Close (Alt+W when active)"
                        @click.stop="closeItem(t)"><i class="pi pi-times" /></button>
              </li>
            </ul>
          </div>
        </Popover>
      </div>
    </div>

    <Toast position="bottom-right" />
    <ConfirmDialog />
    <ConfirmDialog group="close-tab" />
  </div>
</template>

<style scoped>
.shell {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.topbar {
  padding: 0.35rem 1rem;
  border-bottom: 1px solid var(--p-content-border-color);
  background: var(--p-content-background);
}
.logo {
  font-weight: 800;
  font-size: 16px;
  letter-spacing: -0.02em;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--p-primary-color);
}
.small { font-size: 12px; }
.engine i { font-size: 8px; }
.env-pick { margin-right: 0.75rem; }
.env-select { min-width: 11rem; }
.engine.up i { color: var(--p-green-500); }
.engine.down { color: var(--p-red-500); }
.panel-wrap {
  flex: 1 1 auto;
  min-height: 0;
  position: relative;
}
.workpanel {
  height: 100%;
}
.open-items {
  position: absolute;
  top: 5px;
  right: 12px;
  z-index: 2;
}
.open-items-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  max-width: 26rem;
  height: 34px;
  padding: 0 0.75rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 8px;
  background: var(--p-content-background);
  color: var(--p-text-muted-color);
  font: inherit;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
}
.open-items-btn:hover {
  background: var(--p-content-hover-background);
}
.open-items-btn.active {
  color: var(--p-primary-color);
  border-color: var(--p-primary-color);
  background: color-mix(in srgb, var(--p-primary-color) 8%, transparent);
}
.oi-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chev { font-size: 10px; }
.oi-pop { width: 24rem; }
.oi-head { padding: 0 0 0.4rem 0.25rem; }
.oi-empty { padding: 0.5rem 0.25rem 0.25rem; font-size: 13px; }
.oi-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 60vh;
  overflow: auto;
}
.open-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.45rem 0.5rem;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13.5px;
}
.open-item:hover { background: var(--p-content-hover-background); }
.open-item.sel { background: var(--p-highlight-background); color: var(--p-highlight-color); font-weight: 600; }
.oi-icon { color: var(--p-text-muted-color); }
.oi-close {
  display: inline-grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--p-text-muted-color);
  cursor: pointer;
}
.oi-close:hover { background: var(--p-content-hover-background); color: var(--p-text-color); }
.oi-close i { font-size: 10px; }
.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  white-space: nowrap;
}
.dirty {
  color: var(--p-primary-color);
  font-size: 10px;
}
:deep(.workpanel > .p-tablist .p-tab) {
  padding: 0.7rem 1rem;
  font-size: 13.5px;
}
</style>
