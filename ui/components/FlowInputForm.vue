<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { ClassSymbol, Param } from '~/types/api'

// Input form generated from a flow signature. Scalars get native controls; classes,
// objects and arrays get a small JSON editor per field.
const props = defineProps<{ params: Param[]; modelValue: Record<string, any>; classes: ClassSymbol[] }>()
const emit = defineEmits<{ 'update:modelValue': [Record<string, any>]; 'update:valid': [boolean] }>()

const jsonText = reactive<Record<string, string>>({})
const jsonError = reactive<Record<string, string | null>>({})

function syncJson() {
  for (const p of props.params) {
    if (!isScalar(p.type)) {
      jsonText[p.name] = JSON.stringify(props.modelValue[p.name] ?? defaultFor(p.type, props.classes), null, 2)
      jsonError[p.name] = null
    }
  }
}
watch(() => props.params, syncJson, { immediate: true })
watch(() => props.modelValue, (v, old) => { if (v !== old) syncJson() })

function set(name: string, value: unknown) {
  emit('update:modelValue', { ...props.modelValue, [name]: value })
}

function onJson(name: string, text: string) {
  jsonText[name] = text
  try {
    const v = JSON.parse(text)
    jsonError[name] = null
    emit('update:modelValue', { ...props.modelValue, [name]: v })
  } catch (e: any) {
    jsonError[name] = e.message
  }
  emit('update:valid', Object.values(jsonError).every(x => !x))
}
</script>

<template>
  <div class="flow-form">
    <div v-if="!params.length" class="muted">This flow takes no input.</div>
    <div v-for="p in params" :key="p.name" class="field">
      <label :for="`in-${p.name}`" class="row">
        <span class="name">{{ p.name }}</span>
        <Tag :value="p.type" severity="secondary" class="type" />
      </label>
      <InputText v-if="p.type === 'string'" :id="`in-${p.name}`" :model-value="modelValue[p.name] ?? ''" fluid
                 @update:model-value="v => set(p.name, v)" />
      <InputNumber v-else-if="p.type === 'int'" :input-id="`in-${p.name}`" :model-value="modelValue[p.name] ?? 0"
                   :use-grouping="false" fluid @update:model-value="v => set(p.name, v ?? 0)" />
      <InputNumber v-else-if="p.type === 'float'" :input-id="`in-${p.name}`" :model-value="modelValue[p.name] ?? 0"
                   :use-grouping="false" :min-fraction-digits="0" :max-fraction-digits="10" fluid
                   @update:model-value="v => set(p.name, v ?? 0)" />
      <ToggleSwitch v-else-if="p.type === 'boolean'" :input-id="`in-${p.name}`" :model-value="!!modelValue[p.name]"
                    @update:model-value="v => set(p.name, v)" />
      <template v-else>
        <Textarea :id="`in-${p.name}`" :model-value="jsonText[p.name]" class="json-field" rows="5" auto-resize fluid
                  :invalid="!!jsonError[p.name]" @update:model-value="v => onJson(p.name, v)" />
        <small v-if="jsonError[p.name]" class="err">{{ jsonError[p.name] }}</small>
      </template>
    </div>
  </div>
</template>

<style scoped>
.flow-form {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}
.name {
  font-weight: 600;
}
.type {
  font-size: 11px;
  font-weight: 500;
}
.err {
  color: var(--p-red-500);
}
.json-field {
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12.5px;
}
</style>
