<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type * as Monaco from 'monaco-editor'

// JSON in a Monaco code view. Read-only by default (pass `value`); editable with v-model:text.
const props = withDefaults(defineProps<{
  value?: unknown
  text?: string
  editable?: boolean
  wrap?: boolean
  title?: string
  minHeight?: string
}>(), { editable: false, wrap: false, minHeight: '120px' })
const emit = defineEmits<{ 'update:text': [string] }>()

const el = ref<HTMLDivElement | null>(null)
const dark = useDark()
const editor = shallowRef<Monaco.editor.IStandaloneCodeEditor | null>(null)
const wrapOn = ref(props.wrap)
const copied = ref(false)

const content = computed(() => props.editable
  ? (props.text ?? '')
  : (props.value === undefined ? '' : JSON.stringify(props.value, null, 2)))

onMounted(async () => {
  const { monaco, themeName } = await import('~/monaco/setup')
  if (!el.value) return
  const ed = monaco.editor.create(el.value, {
    value: content.value,
    language: 'json',
    theme: themeName(dark.value),
    readOnly: !props.editable,
    automaticLayout: true,
    fontSize: 12.5,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    folding: true,
    lineNumbers: 'on',
    wordWrap: wrapOn.value ? 'on' : 'off',
    renderLineHighlight: props.editable ? 'line' : 'none',
    tabSize: 2,
    fixedOverflowWidgets: true,
  })
  editor.value = ed
  ed.onDidChangeModelContent(() => {
    if (props.editable) emit('update:text', ed.getValue())
  })
})

watch(content, (v) => {
  const ed = editor.value
  if (ed && ed.getValue() !== v) ed.setValue(v)
})
watch(wrapOn, w => editor.value?.updateOptions({ wordWrap: w ? 'on' : 'off' }))
watch(dark, async (d) => {
  const { monaco, themeName } = await import('~/monaco/setup')
  monaco.editor.setTheme(themeName(d))
})
onBeforeUnmount(() => {
  const m = editor.value?.getModel()
  editor.value?.dispose()
  m?.dispose()
})

async function copy() {
  try {
    await navigator.clipboard.writeText(editor.value?.getValue() ?? content.value)
    copied.value = true
    setTimeout(() => (copied.value = false), 1200)
  } catch { /* clipboard blocked */ }
}
</script>

<template>
  <div class="json-view">
    <div class="json-head row">
      <span class="grow ellipsis json-title">{{ title }}</span>
      <Button v-tooltip.bottom="'Wrap long lines'" :icon="wrapOn ? 'pi pi-align-justify' : 'pi pi-align-left'"
              text size="small" :aria-label="'Toggle wrap'" @click="wrapOn = !wrapOn" />
      <Button :icon="copied ? 'pi pi-check' : 'pi pi-copy'" :label="copied ? 'Copied' : 'Copy'" text size="small" @click="copy" />
    </div>
    <div ref="el" class="json-body" :style="{ minHeight }" />
  </div>
</template>

<style scoped>
.json-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  border: 1px solid var(--p-content-border-color);
  border-radius: 6px;
  overflow: hidden;
}
.json-head {
  padding: 2px 4px 2px 10px;
  border-bottom: 1px solid var(--p-content-border-color);
  background: var(--p-content-hover-background);
}
.json-title {
  font-weight: 600;
  font-size: 12.5px;
}
.json-body {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
