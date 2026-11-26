<script setup lang="ts">
import { computed } from 'vue'
import type { CocoEvent } from '~/types/api'

// One log event rendered like a console line: level marker, values (or a table), and where it came from.
const props = withDefaults(defineProps<{ event: CocoEvent; meta?: boolean }>(), { meta: true })
const emit = defineEmits<{ locate: [CocoEvent] }>()

const level = computed(() => logLevel(props.event))
const args = computed(() => logArgs(props.event))
const icon = computed(() => ({
  debug: 'pi pi-code', info: '', warning: 'pi pi-exclamation-triangle', error: 'pi pi-times-circle',
} as const)[level.value])
</script>

<template>
  <div class="log-entry" :class="`lvl-${level}`">
    <i v-if="icon" :class="icon" class="lvl-icon" />
    <div class="body">
      <LogTable v-if="event.type === 'log.table'" :columns="event.payload.columns ?? []" :rows="event.payload.rows ?? []"
                :row-count="event.payload.row_count" :truncated="event.payload.truncated" />
      <LogArgs v-else :args="args" />
    </div>
    <div v-if="meta" class="meta mono muted">
      <a class="loc" :title="event.path ?? ''" @click="emit('locate', event)">{{ event.step ?? lastSegment(event.path) }} · #{{ event.seq }}</a>
      <span>{{ clockMs(event.ts) }}</span>
    </div>
  </div>
</template>

<style scoped>
.log-entry {
  display: flex;
  gap: 0.6rem;
  align-items: flex-start;
  padding: 0.4rem 1rem;
  border-bottom: 1px solid var(--p-content-border-color);
}
.body {
  flex: 1 1 auto;
  min-width: 0;
}
.lvl-icon {
  margin-top: 3px;
  font-size: 12px;
}
.lvl-debug { color: var(--p-text-muted-color); }
.lvl-warning { background: color-mix(in srgb, var(--p-amber-400) 12%, transparent); }
.lvl-warning .lvl-icon { color: var(--p-amber-500); }
.lvl-error { background: color-mix(in srgb, var(--p-red-400) 12%, transparent); }
.lvl-error .lvl-icon, .lvl-error .body { color: var(--p-red-600); }
.meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  font-size: 11px;
  white-space: nowrap;
}
.loc {
  cursor: pointer;
  color: var(--p-primary-color);
}
</style>
