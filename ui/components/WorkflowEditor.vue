<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useToast } from 'primevue/usetoast'
import type { EditorTab } from '~/composables/useTabs'
import type { Diagnostic, Symbols, Version, Workflow } from '~/types/api'

const props = defineProps<{ tab: EditorTab }>()
const toast = useToast()
const { setDirty, setTitle, rekey } = useTabs()
const { signals } = useSignals()

const TEMPLATE = `package my_workflow

step hello(string name)(string message):
    self.log("saying hello to " + name)
    return "Hello, " + name + "!"

flow main(string name)(string message):
    string m
    m = hello(name)
    return m
`

const isNew = computed(() => props.tab.id.startsWith('new-'))
const wf = ref<Workflow | null>(null)
const source = ref(isNew.value ? TEMPLATE : '')
const savedSource = ref(isNew.value ? '' : '')
const description = ref('')
const savedDescription = ref('')
const diagnostics = ref<Diagnostic[]>([])
const symbols = ref<Symbols | null>(null)
const pkg = ref<string | null>(null)
const versions = ref<Version[]>([])
const loading = ref(!isNew.value)
const saving = ref(false)
const checking = ref(false)
const sideTab = ref('flows')
const editor = ref<{ revealLine: (l: number, c?: number) => void } | null>(null)
const runVisible = ref(false)
const runFlow = ref<string | null>(null)

const dirty = computed(() => source.value !== savedSource.value || description.value !== savedDescription.value)
const errors = computed(() => diagnostics.value.filter(d => d.severity === 'error'))
const warnings = computed(() => diagnostics.value.filter(d => d.severity === 'warning'))
const flows = computed(() => symbols.value?.flows ?? [])
const current = computed(() => wf.value?.current_version ?? null)
const canRun = computed(() => !!wf.value && !dirty.value && !!current.value?.is_valid)

watch(dirty, d => setDirty(props.tab.key, d), { immediate: true })

async function load() {
  if (isNew.value) return
  loading.value = true
  try {
    const w = await api.getWorkflow(props.tab.id)
    wf.value = w
    source.value = w.current_version?.source ?? ''
    savedSource.value = source.value
    description.value = w.description
    savedDescription.value = w.description
    diagnostics.value = w.current_version?.diagnostics ?? []
    symbols.value = w.current_version?.symbols ?? null
    pkg.value = w.name
    setTitle(props.tab.key, w.name)
    loadVersions()
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not open workflow', detail: e.message, life: 6000 })
  } finally {
    loading.value = false
  }
}

async function loadVersions() {
  if (!wf.value) return
  try { versions.value = (await api.versions(wf.value.id)).items } catch { /* non-critical */ }
}

onMounted(() => {
  load()
  if (isNew.value) validate()
})
watch(() => signals.workflows, () => { if (!dirty.value && wf.value) load() })

// live check: /validate 500 ms after the last keystroke
let timer: ReturnType<typeof setTimeout> | null = null
let seq = 0
watch(source, () => {
  if (timer) clearTimeout(timer)
  timer = setTimeout(validate, 500)
})

async function validate() {
  const mine = ++seq
  checking.value = true
  try {
    const r = await api.validate(source.value, wf.value?.name)
    if (mine !== seq) return
    diagnostics.value = r.diagnostics
    symbols.value = r.symbols
    pkg.value = r.package
    if (isNew.value && r.package) setTitle(props.tab.key, `${r.package} (new)`)
  } catch { /* engine offline: keep last results */ } finally {
    if (mine === seq) checking.value = false
  }
}

async function save() {
  if (saving.value) return
  if (!dirty.value && !isNew.value) return
  saving.value = true
  try {
    let w: Workflow
    if (isNew.value) {
      w = await api.createWorkflow(source.value, description.value)
      rekey(props.tab.key, 'wf', w.id, w.name)
    } else {
      w = await api.saveWorkflow(wf.value!.id, { source: source.value, description: description.value })
    }
    wf.value = w
    savedSource.value = source.value
    savedDescription.value = description.value
    diagnostics.value = w.current_version?.diagnostics ?? []
    symbols.value = w.current_version?.symbols ?? symbols.value
    setTitle(props.tab.key, w.name)
    const v = w.current_version
    toast.add({
      severity: v?.is_valid ? 'success' : 'warn',
      summary: `Saved ${w.name} v${v?.version}`,
      detail: v?.is_valid ? undefined : `${errors.value.length} error(s) — this version cannot run yet.`,
      life: 2500,
    })
    bump('workflows')
    loadVersions()
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Save failed', detail: e.message, life: 6000 })
  } finally {
    saving.value = false
  }
}

const JSON_FIX: Record<string, string> = { true: 'True', false: 'False', null: 'None' }
const jsonProblems = computed(() => diagnostics.value.filter(d => d.code === 'E235'))

/** Replace JSON `true`/`false`/`null` with Python spellings at the reported positions (right to left per line). */
function fixJsonLiterals(only?: Diagnostic) {
  const targets = (only ? [only] : jsonProblems.value).filter(d => d.end_col)
  const lines = source.value.split('\n')
  const byLine = new Map<number, Diagnostic[]>()
  for (const d of targets) byLine.set(d.line, [...(byLine.get(d.line) ?? []), d])
  for (const [line, ds] of byLine) {
    let text = lines[line - 1] ?? ''
    for (const d of ds.sort((a, b) => b.col - a.col)) {
      const word = text.slice(d.col - 1, d.end_col! - 1)
      if (word in JSON_FIX) text = text.slice(0, d.col - 1) + JSON_FIX[word] + text.slice(d.end_col! - 1)
    }
    lines[line - 1] = text
  }
  source.value = lines.join('\n')
}

function jump(d: { line: number; col?: number }) {
  editor.value?.revealLine(d.line, d.col ?? 1)
}

async function loadVersion(v: Version) {
  if (!wf.value) return
  try {
    const full = await api.version(wf.value.id, v.version)
    source.value = full.source ?? ''
    toast.add({ severity: 'info', summary: `Loaded v${v.version} into the editor`, detail: 'Save to make it the current version.', life: 3500 })
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not load version', detail: e.message, life: 5000 })
  }
}

function startRun(flow?: string) {
  runFlow.value = flow ?? null
  runVisible.value = true
}
</script>

<template>
  <div class="wf-editor">
    <div class="wf-head row">
      <i class="pi pi-file-edit muted" />
      <span class="title">{{ wf?.name ?? pkg ?? 'New workflow' }}</span>
      <Tag v-if="current" :value="`v${current.version}`" severity="secondary" />
      <Tag v-if="checking" value="checking…" severity="secondary" icon="pi pi-spin pi-spinner" />
      <Tag v-else-if="errors.length" :value="`${errors.length} error${errors.length > 1 ? 's' : ''}`" severity="danger"
           icon="pi pi-times-circle" class="clickable" @click="sideTab = 'problems'" />
      <Tag v-else value="valid" severity="success" icon="pi pi-check" />
      <InputText v-model="description" placeholder="Description" size="small" class="grow desc" />
      <Button label="Save" icon="pi pi-save" size="small" :disabled="!dirty && !isNew" :loading="saving"
              v-tooltip.bottom="'Ctrl/Cmd+S'" @click="save" />
      <span v-tooltip.bottom="canRun ? 'Run a flow' : (dirty ? 'Save before running' : 'Fix errors before running')">
        <Button label="Run" icon="pi pi-play" size="small" severity="success" :disabled="!canRun" @click="startRun()" />
      </span>
    </div>

    <Splitter class="wf-body" :gutter-size="4">
      <SplitterPanel :size="74" :min-size="40" class="editor-panel">
        <div v-if="loading" class="center"><ProgressSpinner style="width: 2rem; height: 2rem" /></div>
        <CocoEditor v-else ref="editor" v-model="source" :diagnostics="diagnostics" :symbols="symbols" @save="save" />
      </SplitterPanel>
      <SplitterPanel :size="26" :min-size="18" class="side-panel">
        <Tabs v-model:value="sideTab" class="side-tabs">
          <TabList>
            <Tab value="flows">Flows</Tab>
            <Tab value="problems">
              Problems
              <Badge v-if="diagnostics.length" :value="diagnostics.length" :severity="errors.length ? 'danger' : 'warn'" size="small" />
            </Tab>
            <Tab value="versions">Versions</Tab>
          </TabList>
          <TabPanels class="side-panels">
            <TabPanel value="flows"><div class="side-scroll">
              <div v-if="!flows.length" class="muted pad">No flows yet. Add <code>flow name(inputs)(outputs):</code></div>
              <div v-for="f in flows" :key="f.name" class="item" @click="jump(f)">
                <div class="row">
                  <span class="item-name grow">{{ f.name }}</span>
                  <Button v-tooltip.left="canRun ? `Run ${f.name}` : 'Save a valid version first'" icon="pi pi-play"
                          text rounded size="small" severity="success" :disabled="!canRun" @click.stop="startRun(f.name)" />
                </div>
                <div class="mono muted sig">{{ signature(f) }}</div>
              </div>
              <template v-if="symbols?.steps?.length">
                <div class="section">Steps</div>
                <div v-for="s in symbols.steps" :key="s.name" class="item" @click="jump(s)">
                  <div class="item-name">{{ s.name }}</div>
                  <div class="mono muted sig">{{ signature(s) }}</div>
                </div>
              </template>
              <template v-if="symbols?.classes?.length">
                <div class="section">Classes</div>
                <div v-for="c in symbols.classes" :key="c.name" class="item" @click="jump(c)">
                  <div class="item-name">{{ c.name }}</div>
                  <div class="mono muted sig">{{ c.fields.map(x => `${x.type} ${x.name}`).join(', ') }}</div>
                </div>
              </template>
            </div></TabPanel>
            <TabPanel value="problems"><div class="side-scroll">
              <div v-if="!diagnostics.length" class="muted pad"><i class="pi pi-check" /> No problems.</div>
              <div v-for="(d, i) in diagnostics" :key="i" class="item problem" @click="jump(d)">
                <div class="row">
                  <i :class="d.severity === 'error' ? 'pi pi-times-circle err' : 'pi pi-exclamation-triangle warn'" />
                  <span class="mono muted">{{ d.line }}:{{ d.col }}</span>
                  <span class="mono muted">{{ d.code }}</span>
                </div>
                <div class="msg">{{ d.message }}</div>
                <Button v-if="d.code === 'E235'" label="Fix" icon="pi pi-wrench" text size="small" class="fix-btn"
                        @click.stop="fixJsonLiterals(d)" />
              </div>
              <div v-if="jsonProblems.length > 1" class="pad-sm">
                <Button :label="`Fix all ${jsonProblems.length} JSON literals`" icon="pi pi-wrench" size="small" outlined
                        @click="fixJsonLiterals()" />
              </div>
              <div v-if="warnings.length && !errors.length" class="muted pad small">Warnings do not block running.</div>
            </div></TabPanel>
            <TabPanel value="versions"><div class="side-scroll">
              <div v-if="isNew" class="muted pad">Save to create version 1.</div>
              <div v-for="v in versions" :key="v.id" class="item version">
                <div class="row">
                  <b>v{{ v.version }}</b>
                  <Tag v-if="v.is_current" value="current" severity="info" class="small-tag" />
                  <Tag :value="v.is_valid ? 'valid' : 'errors'" :severity="v.is_valid ? 'success' : 'danger'" class="small-tag" />
                  <span class="grow" />
                  <Button v-if="!v.is_current" label="Load" text size="small" @click="loadVersion(v)" />
                </div>
                <div class="muted small">{{ dateTime(v.created_at) }}</div>
              </div>
            </div></TabPanel>
          </TabPanels>
        </Tabs>
      </SplitterPanel>
    </Splitter>

    <RunDialog v-if="wf" v-model:visible="runVisible" :workflow-id="wf.id" :workflow-name="wf.name"
               :flows="current?.flows ?? []" :classes="current?.symbols?.classes ?? symbols?.classes ?? []"
               :initial-flow="runFlow" />
  </div>
</template>

<style scoped>
.wf-editor {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.wf-head {
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--p-content-border-color);
}
.title {
  font-weight: 700;
  font-size: 15px;
}
.desc {
  max-width: 28rem;
}
.clickable {
  cursor: pointer;
}
.wf-body {
  flex: 1 1 auto;
  min-height: 0;
  border: 0;
  border-radius: 0;
}
.editor-panel {
  min-height: 0;
  overflow: hidden;
}
.side-panel {
  min-height: 0;
  overflow: hidden;
}
.side-tabs {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.side-panels {
  flex: 1 1 auto;
  min-height: 0;
  padding: 0;
}
.side-panels > :deep(.p-tabpanel) {
  height: 100%;
}
.side-scroll {
  height: 100%;
  overflow: auto;
}
.item {
  padding: 0.45rem 0.85rem;
  border-bottom: 1px solid var(--p-content-border-color);
  cursor: pointer;
}
.item:hover {
  background: var(--p-content-hover-background);
}
.item-name {
  font-weight: 600;
}
.sig {
  font-size: 11.5px;
  word-break: break-word;
}
.section {
  padding: 0.6rem 0.85rem 0.25rem;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--p-text-muted-color);
}
.pad {
  padding: 0.85rem;
}
.small {
  font-size: 12px;
}
.small-tag {
  font-size: 11px;
}
.msg {
  font-size: 13px;
  margin-top: 2px;
}
.fix-btn {
  margin-top: 2px;
  padding-left: 0;
}
.pad-sm {
  padding: 0.6rem 0.85rem;
}
.err {
  color: var(--p-red-500);
}
.warn {
  color: var(--p-amber-500);
}
.center {
  display: grid;
  place-items: center;
  height: 100%;
}
:deep(.p-tabpanels) {
  padding: 0;
}
:deep(.side-tabs .p-tab) {
  padding: 0.6rem 0.8rem;
  font-size: 13px;
}
</style>
