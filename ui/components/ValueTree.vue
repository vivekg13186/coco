<script setup lang="ts">
import { computed, ref } from 'vue'

// A JSON value as an expandable tree, like objects in the browser console.
const props = withDefaults(defineProps<{
  value: unknown
  name?: string | number | null
  className?: string | null
  depth?: number
  open?: boolean
  quoteStrings?: boolean
}>(), { name: null, className: null, depth: 0, open: false, quoteStrings: true })

const expanded = ref(props.open)
const isObj = computed(() => props.value !== null && typeof props.value === 'object')
const isArr = computed(() => Array.isArray(props.value))
const entries = computed<[string, unknown][]>(() => {
  if (!isObj.value) return []
  return isArr.value
    ? (props.value as unknown[]).map((v, i) => [String(i), v])
    : Object.entries(props.value as Record<string, unknown>)
})
const summary = computed(() => {
  const p = preview(props.value, 90)
  return props.className && !isArr.value ? `${props.className} ${p}` : p
})
const kind = computed(() => {
  const v = props.value
  if (v === null || v === undefined) return 'null'
  return typeof v
})
const text = computed(() => {
  const v = props.value
  if (v === null || v === undefined) return 'None'
  if (typeof v === 'boolean') return v ? 'True' : 'False'
  if (typeof v === 'string') return props.quoteStrings ? JSON.stringify(v) : v
  return String(v)
})
</script>

<template>
  <span class="vt" :class="{ block: expanded && isObj }">
    <template v-if="isObj">
      <span class="vt-head" role="button" :aria-expanded="expanded" @click.stop="expanded = !expanded">
        <i :class="expanded ? 'pi pi-angle-down' : 'pi pi-angle-right'" class="caret" />
        <span v-if="name !== null" class="vt-key">{{ name }}: </span>
        <span class="vt-preview">{{ expanded ? (isArr ? `Array(${entries.length})` : (className ?? 'Object')) : summary }}</span>
      </span>
      <div v-if="expanded" class="vt-children">
        <div v-for="[k, v] in entries" :key="k" class="vt-row">
          <ValueTree :value="v" :name="k" :depth="depth + 1" />
        </div>
        <div v-if="!entries.length" class="vt-row vt-empty">{{ isArr ? 'empty array' : 'empty object' }}</div>
      </div>
    </template>
    <template v-else>
      <span v-if="name !== null" class="vt-key leaf">{{ name }}: </span>
      <span :class="`vt-${kind}`">{{ text }}</span>
    </template>
  </span>
</template>

<style scoped>
.vt {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.55;
}
.vt.block {
  display: inline-block;
  vertical-align: top;
}
.vt-head {
  cursor: pointer;
  user-select: none;
}
.vt-head:hover .vt-preview {
  text-decoration: underline dotted;
}
.caret {
  font-size: 10px;
  width: 12px;
  color: var(--p-text-muted-color);
}
.vt-children {
  padding-left: 16px;
  border-left: 1px solid var(--p-content-border-color);
  margin-left: 5px;
}
.vt-key {
  color: #a1489c;
}
.vt-key.leaf {
  margin-left: 12px;
}
.vt-preview {
  color: var(--p-text-color);
}
.vt-string { color: #b5432c; }
.vt-number { color: #1c55c4; }
.vt-boolean, .vt-null { color: #7b39b8; }
.vt-empty { color: var(--p-text-muted-color); font-style: italic; margin-left: 12px; }
:global(.app-dark) .vt-key { color: #d48fd0; }
:global(.app-dark) .vt-string { color: #f0a37c; }
:global(.app-dark) .vt-number { color: #8db5ff; }
:global(.app-dark) .vt-boolean, :global(.app-dark) .vt-null { color: #c8a2f0; }
</style>
