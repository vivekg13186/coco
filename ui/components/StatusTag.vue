<script setup lang="ts">
defineProps<{ status: string }>()
</script>

<template>
  <Tag :value="status" :severity="statusSeverity(status)" :icon="statusIcon(status)" class="status-tag" />
</template>

<style scoped>
.status-tag {
  text-transform: capitalize;
  font-size: 12px;
}
</style>
