<script setup lang="ts">
import { computed, ref } from 'vue'

// log_table output: sortable columns and a text filter across all cells, like console.table with extras.
const props = defineProps<{ columns: string[]; rows: unknown[][]; rowCount?: number; truncated?: boolean }>()

const filter = ref('')
const data = computed(() => props.rows.map((r, i) => {
  const o: Record<string, unknown> = { __i: i }
  props.columns.forEach((_, c) => { o[`c${c}`] = r[c] })
  return o
}))
const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return data.value
  return data.value.filter(o => props.columns.some((_, c) => cellText(o[`c${c}`]).toLowerCase().includes(q)))
})
const isObj = (v: unknown) => v !== null && typeof v === 'object'
</script>

<template>
  <div class="log-table">
    <div class="lt-bar row">
      <IconField>
        <InputIcon class="pi pi-filter" />
        <InputText v-model="filter" placeholder="Filter rows" size="small" class="lt-filter" />
      </IconField>
      <span class="muted small">
        {{ filter ? `${filtered.length} of ${rows.length}` : rows.length }} row{{ rows.length === 1 ? '' : 's' }}
        <template v-if="truncated"> · first {{ rows.length }} of {{ rowCount }}</template>
        · click a header to sort
      </span>
    </div>
    <DataTable :value="filtered" data-key="__i" size="small" striped-rows removable-sort scrollable scroll-height="22rem"
               :paginator="filtered.length > 50" :rows="50" class="lt-table">
      <template #empty><span class="muted small">No rows match.</span></template>
      <Column v-for="(col, c) in columns" :key="col" :field="`c${c}`" :header="col" sortable
              :class="{ 'idx-col': c === 0 }">
        <template #body="{ data: row }">
          <ValueTree v-if="isObj(row[`c${c}`])" :value="row[`c${c}`]" />
          <span v-else-if="row[`c${c}`] === null || row[`c${c}`] === undefined" class="muted"></span>
          <span v-else :class="['cell', typeof row[`c${c}`]]">{{ cellText(row[`c${c}`]) }}</span>
        </template>
      </Column>
    </DataTable>
  </div>
</template>

<style scoped>
.log-table {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  max-width: 100%;
}
.lt-filter { width: 14rem; }
.small { font-size: 12px; }
.lt-table :deep(th), .lt-table :deep(td) {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
}
.lt-table :deep(.idx-col) {
  color: var(--p-text-muted-color);
  width: 1%;
  white-space: nowrap;
}
.cell.number { color: #1c55c4; }
.cell.boolean { color: #7b39b8; }
:global(.app-dark) .cell.number { color: #8db5ff; }
:global(.app-dark) .cell.boolean { color: #c8a2f0; }
</style>
