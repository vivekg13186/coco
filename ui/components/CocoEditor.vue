<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type * as Monaco from 'monaco-editor'
import type { Diagnostic, Symbols } from '~/types/api'

const props = defineProps<{
  modelValue: string
  diagnostics?: Diagnostic[]
  symbols?: Symbols | null
  readOnly?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [string]; save: [] }>()

const el = ref<HTMLDivElement | null>(null)
const dark = useDark()
const envs = useEnvs()
let monaco: typeof Monaco | null = null
let lang: typeof import('~/monaco/coco-language') | null = null
const editor = shallowRef<Monaco.editor.IStandaloneCodeEditor | null>(null)

onMounted(async () => {
  const mod = await import('~/monaco/setup')
  lang = await import('~/monaco/coco-language')
  monaco = mod.monaco
  if (!el.value) return
  const ed = monaco.editor.create(el.value, {
    value: props.modelValue,
    language: 'coco',
    theme: mod.themeName(dark.value),
    automaticLayout: true,
    fontSize: 13,
    tabSize: 4,
    insertSpaces: true,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    renderWhitespace: 'boundary',
    readOnly: props.readOnly,
    fixedOverflowWidgets: true,
  })
  editor.value = ed
  ed.onDidChangeModelContent(() => {
    const v = ed.getValue()
    if (v !== props.modelValue) emit('update:modelValue', v)
  })
  ed.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => emit('save'))
  applyExtras()
  watch(envs.effective, e => lang?.setEnvData((e?.data as Record<string, unknown>) ?? null, e?.name ?? null), { immediate: true })
})

function applyExtras() {
  const ed = editor.value
  const model = ed?.getModel()
  if (!monaco || !lang || !model) return
  lang.setSymbols(model, props.symbols ?? null)
  lang.setDiagnostics(monaco, model, props.diagnostics ?? [])
}

watch(() => props.modelValue, (v) => {
  const ed = editor.value
  if (ed && v !== ed.getValue()) {
    ed.pushUndoStop()
    ed.executeEdits('external', [{ range: ed.getModel()!.getFullModelRange(), text: v }])
    ed.pushUndoStop()
  }
})
watch(() => [props.diagnostics, props.symbols], applyExtras)
watch(dark, async (d) => {
  if (monaco) monaco.editor.setTheme((await import('~/monaco/setup')).themeName(d))
})
watch(() => props.readOnly, ro => editor.value?.updateOptions({ readOnly: ro }))

onBeforeUnmount(() => {
  const model = editor.value?.getModel()
  if (model && lang) lang.setSymbols(model, null)
  editor.value?.dispose()
  model?.dispose()
})

function revealLine(line: number, col = 1) {
  const ed = editor.value
  if (!ed) return
  ed.revealLineInCenter(line)
  ed.setPosition({ lineNumber: line, column: col })
  ed.focus()
}

defineExpose({ revealLine })
</script>

<template>
  <div ref="el" class="coco-editor" />
</template>

<style scoped>
.coco-editor {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
