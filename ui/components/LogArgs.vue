<script setup lang="ts">
import type { LogArg } from '~/utils/events'

// The values of one log call, laid out like console.log: strings as text, the rest typed and expandable.
defineProps<{ args: LogArg[] }>()
</script>

<template>
  <span class="log-args">
    <template v-for="(a, i) in args" :key="i">
      <span v-if="a.type === 'string'" class="arg-str">{{ a.value }}</span>
      <ValueTree v-else :value="a.value" :class-name="a.class ?? null" />
      <template v-if="i < args.length - 1">{{ ' ' }}</template>
    </template>
  </span>
</template>

<style scoped>
.log-args {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
