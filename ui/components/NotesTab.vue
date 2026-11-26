<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useConfirm } from 'primevue/useconfirm'
import { useToast } from 'primevue/usetoast'
import type { Note } from '~/types/api'

// Plain-text notes stored in the engine. Edits save on their own a moment after you stop typing.
const toast = useToast()
const confirm = useConfirm()
const { signals } = useSignals()

const notes = ref<Note[]>([])
const search = ref('')
const selectedId = ref<string | null>(null)
const title = ref('')
const body = ref('')
const saved = ref({ title: '', body: '' })
const status = ref<'idle' | 'saving' | 'saved' | 'error'>('idle')
const savedAt = ref<string | null>(null)
const loading = ref(false)
const titleInput = ref<any>(null)

const dirty = computed(() => title.value !== saved.value.title || body.value !== saved.value.body)
const words = computed(() => (body.value.trim() ? body.value.trim().split(/\s+/).length : 0))
const lines = computed(() => (body.value ? body.value.split('\n').length : 0))

async function loadList() {
  try {
    notes.value = (await api.listNotes(search.value.trim() || undefined)).items
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not load notes', detail: e.message, life: 5000 })
  }
}

let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(loadList, 250)
})

async function open(id: string) {
  await flush()
  loading.value = true
  try {
    const n = await api.getNote(id)
    selectedId.value = n.id
    title.value = n.title
    body.value = n.body ?? ''
    saved.value = { title: n.title, body: n.body ?? '' }
    savedAt.value = n.updated_at
    status.value = 'idle'
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not open note', detail: e.message, life: 5000 })
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadList()
  if (notes.value.length) open(notes.value[0].id)
})
watch(() => signals.notes, loadList)

// autosave 800 ms after the last change
let timer: ReturnType<typeof setTimeout> | null = null
watch([title, body], () => {
  if (!selectedId.value || !dirty.value) return
  status.value = 'idle'
  if (timer) clearTimeout(timer)
  timer = setTimeout(save, 800)
})

async function save() {
  if (timer) { clearTimeout(timer); timer = null }
  const id = selectedId.value
  if (!id || !dirty.value) return
  const snapshot = { title: title.value, body: body.value }
  status.value = 'saving'
  try {
    const n = await api.updateNote(id, snapshot)
    saved.value = snapshot
    savedAt.value = n.updated_at
    status.value = 'saved'
    const i = notes.value.findIndex(x => x.id === id)
    const summary = { ...n }
    delete summary.body
    if (i >= 0) notes.value.splice(i, 1)
    if (!search.value) notes.value.unshift(summary)
    else if (i >= 0) notes.value.splice(i, 0, summary)
  } catch (e: any) {
    status.value = 'error'
    toast.add({ severity: 'error', summary: 'Note not saved', detail: e.message, life: 6000 })
  }
}

async function flush() {
  if (dirty.value) await save()
}

async function createNote() {
  await flush()
  try {
    const n = await api.createNote({ title: '', body: '' })
    search.value = ''
    await loadList()
    await open(n.id)
    await nextTick()
    titleInput.value?.$el?.focus?.()
  } catch (e: any) {
    toast.add({ severity: 'error', summary: 'Could not create note', detail: e.message, life: 5000 })
  }
}

function remove() {
  const id = selectedId.value
  if (!id) return
  confirm.require({
    header: `Delete ${title.value || 'this note'}?`,
    message: 'The note is removed from the engine database. This cannot be undone.',
    icon: 'pi pi-trash',
    acceptProps: { label: 'Delete', severity: 'danger' },
    rejectProps: { label: 'Cancel', severity: 'secondary', text: true },
    accept: async () => {
      if (timer) { clearTimeout(timer); timer = null }
      try {
        await api.deleteNote(id)
        saved.value = { title: title.value, body: body.value }
        selectedId.value = null
        await loadList()
        if (notes.value.length) await open(notes.value[0].id)
        else { title.value = ''; body.value = ''; saved.value = { title: '', body: '' } }
      } catch (e: any) {
        toast.add({ severity: 'error', summary: 'Delete failed', detail: e.message, life: 5000 })
      }
    },
  })
}

function download() {
  const name = (title.value.trim() || 'note').replace(/[^\w.-]+/g, '_').slice(0, 60)
  const url = URL.createObjectURL(new Blob([body.value], { type: 'text/plain;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `${name}.txt`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
    e.preventDefault()
    save()
  }
}

function beforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value) {
    save()
    e.preventDefault()
    e.returnValue = ''
  }
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnload)
  flush()
})
</script>

<template>
  <div class="notes-root">
    <Splitter class="notes-split" :gutter-size="4">
      <SplitterPanel :size="26" :min-size="16" class="notes-list">
        <div class="list-head row">
          <IconField class="grow">
            <InputIcon class="pi pi-search" />
            <InputText v-model="search" placeholder="Search notes" size="small" fluid />
          </IconField>
          <Button v-tooltip.bottom="'New note'" icon="pi pi-plus" size="small" aria-label="New note" @click="createNote" />
        </div>
        <div v-if="!notes.length" class="muted pad">
          {{ search ? 'No notes match.' : 'No notes yet. Click + to write one.' }}
        </div>
        <div v-for="n in notes" :key="n.id" class="note-item" :class="{ sel: n.id === selectedId }" @click="open(n.id)">
          <div class="note-title ellipsis">{{ (n.id === selectedId ? title : n.title) || 'Untitled' }}</div>
          <div class="muted small ellipsis">{{ ago(n.updated_at) }} · {{ n.preview || 'empty' }}</div>
        </div>
      </SplitterPanel>

      <SplitterPanel :size="74" :min-size="40" class="note-edit">
        <div v-if="!selectedId" class="muted pad">
          Notes are plain text kept in the engine database: ideas, to-dos, snippets.
          <Button label="New note" icon="pi pi-plus" size="small" class="mt" @click="createNote" />
        </div>
        <template v-else>
          <div class="edit-head row">
            <InputText ref="titleInput" v-model="title" placeholder="Untitled" class="title-input grow" @keydown="onKey" />
            <span class="small save-state" :class="status">
              <template v-if="status === 'saving'"><i class="pi pi-spin pi-spinner" /> Saving…</template>
              <template v-else-if="status === 'error'"><i class="pi pi-exclamation-triangle" /> Not saved</template>
              <template v-else-if="dirty">Unsaved</template>
              <template v-else-if="savedAt"><i class="pi pi-check" /> Saved {{ clock(savedAt) }}</template>
            </span>
            <Button v-tooltip.bottom="'Download as .txt'" icon="pi pi-download" text size="small" aria-label="Download as .txt" @click="download" />
            <Button v-tooltip.bottom="'Delete note'" icon="pi pi-trash" text size="small" severity="danger" aria-label="Delete note" @click="remove" />
          </div>
          <textarea v-model="body" class="note-body" spellcheck="true" placeholder="Write here…" :disabled="loading"
                    @keydown="onKey" />
          <div class="note-foot small muted">{{ lines }} line{{ lines === 1 ? '' : 's' }} · {{ words }} word{{ words === 1 ? '' : 's' }} · {{ body.length }} characters · saves automatically</div>
        </template>
      </SplitterPanel>
    </Splitter>
  </div>
</template>

<style scoped>
.notes-root { height: 100%; }
.notes-split {
  height: 100%;
  border: 0;
  border-radius: 0;
}
.notes-list {
  overflow: auto;
  border-right: 1px solid var(--p-content-border-color);
}
.list-head {
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--p-content-border-color);
}
.note-item {
  padding: 0.55rem 0.85rem;
  border-bottom: 1px solid var(--p-content-border-color);
  cursor: pointer;
}
.note-item:hover { background: var(--p-content-hover-background); }
.note-item.sel { background: var(--p-highlight-background); color: var(--p-highlight-color); }
.note-title { font-weight: 600; }
.note-edit {
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0.75rem 1rem;
  gap: 0.5rem;
}
.title-input {
  font-size: 16px;
  font-weight: 600;
}
.save-state { white-space: nowrap; color: var(--p-text-muted-color); }
.save-state.error { color: var(--p-red-500); }
.note-body {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  box-sizing: border-box;
  resize: none;
  padding: 0.75rem 0.9rem;
  border: 1px solid var(--p-content-border-color);
  border-radius: 6px;
  background: var(--p-content-background);
  color: var(--p-text-color);
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 13.5px;
  line-height: 1.6;
  outline: none;
  tab-size: 4;
}
.note-body:focus { border-color: var(--p-primary-color); }
.small { font-size: 12px; }
.pad { padding: 1rem; line-height: 1.6; }
.mt { margin-top: 0.75rem; display: flex; }
</style>
