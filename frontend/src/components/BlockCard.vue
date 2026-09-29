<script setup lang="ts">
import { computed } from 'vue'
import { blockSummary, fieldVisible, type Block, type BlockFieldSpec, type BlockTypeSpec } from '@/api/duzhanBlocks'

const props = defineProps<{ block: Block; spec: BlockTypeSpec; index: number; total: number }>()
const emit = defineEmits<{
  (event: 'change', key: string, value: unknown): void
  (event: 'move', offset: number): void
  (event: 'remove'): void
}>()

const summary = computed(() => blockSummary(props.block))
const fields = computed(() => props.spec.fields.filter((field) => fieldVisible(field, props.block)))

function rawText(field: BlockFieldSpec): string {
  const value = props.block[field.key]
  if (Array.isArray(value)) return value.join(', ')
  return value === undefined || value === null ? '' : String(value)
}

function slotChecked(field: BlockFieldSpec, option: string): boolean {
  const value = props.block[field.key]
  return Array.isArray(value) && value.includes(option)
}

function onText(field: BlockFieldSpec, event: Event) {
  const value = (event.target as HTMLInputElement | HTMLTextAreaElement).value
  if (field.kind === 'names') {
    emit('change', field.key, value.split(',').map((item) => item.trim()).filter(Boolean))
    return
  }
  emit('change', field.key, value)
}

function onSelect(field: BlockFieldSpec, event: Event) {
  emit('change', field.key, (event.target as HTMLSelectElement).value)
}

function onBool(field: BlockFieldSpec, event: Event) {
  emit('change', field.key, (event.target as HTMLInputElement).checked)
}

function onSlot(field: BlockFieldSpec, option: string, event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const current = Array.isArray(props.block[field.key]) ? [...(props.block[field.key] as string[])] : []
  const next = checked ? [...current, option] : current.filter((item) => item !== option)
  emit('change', field.key, next.sort())
}

function optionLabel(field: BlockFieldSpec, option: string): string {
  return (field.option_labels && field.option_labels[option]) || option
}
</script>

<template>
  <article class="block-card">
    <header>
      <span class="order">{{ index + 1 }}</span>
      <div class="titles">
        <strong>{{ spec.label }}</strong>
        <small>{{ summary }}</small>
      </div>
      <div class="ops">
        <button class="btn btn-sm" type="button" :disabled="index === 0" title="上移" @click="emit('move', -1)">↑</button>
        <button class="btn btn-sm" type="button" :disabled="index >= total - 1" title="下移" @click="emit('move', 1)">↓</button>
        <button class="btn btn-sm" type="button" title="删除这块" @click="emit('remove')">✕</button>
      </div>
    </header>
    <div class="fields">
      <label v-for="field in fields" :key="field.key" class="field">
        <span class="label">{{ field.label }}<em v-if="field.required">必填</em></span>

        <select v-if="field.kind === 'select'" :value="rawText(field)" @change="onSelect(field, $event)">
          <option v-for="option in field.options || []" :key="option" :value="option">{{ optionLabel(field, option) }}</option>
        </select>

        <span v-else-if="field.kind === 'bool'" class="inline">
          <input type="checkbox" :checked="block[field.key] === true" @change="onBool(field, $event)" />
          <span>{{ block[field.key] === true ? '是' : '否' }}</span>
        </span>

        <span v-else-if="field.kind === 'slots'" class="inline">
          <label v-for="option in field.options || []" :key="option" class="slot">
            <input type="checkbox" :checked="slotChecked(field, option)" @change="onSlot(field, option, $event)" />
            <span>{{ option }}</span>
          </label>
        </span>

        <textarea
          v-else-if="field.kind === 'textarea'"
          rows="3"
          :value="rawText(field)"
          @input="onText(field, $event)"
        ></textarea>

        <input
          v-else
          :type="field.kind === 'date' ? 'date' : 'text'"
          :value="rawText(field)"
          @input="onText(field, $event)"
        />
      </label>
    </div>
  </article>
</template>

<style scoped>
.block-card { border: 1px solid var(--border, #e4e7ec); border-radius: 10px; background: #fff; }
.block-card > header { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-bottom: 1px solid var(--border, #eef0f4); }
.order { width: 20px; height: 20px; border-radius: 50%; background: var(--brand, #2563eb); color: #fff; font-size: 11px; display: flex; align-items: center; justify-content: center; }
.titles { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.titles small { color: var(--text-muted, #667085); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ops { display: flex; gap: 4px; }
.fields { display: grid; gap: 8px; padding: 10px; }
.field { display: grid; gap: 4px; font-size: 12px; }
.label { color: var(--text-muted, #475467); }
.label em { margin-left: 6px; color: #d92d20; font-style: normal; font-size: 10px; }
.field input[type='text'], .field input[type='date'], .field select, .field textarea {
  width: 100%; padding: 6px 8px; border: 1px solid var(--border, #d0d5dd); border-radius: 6px; font-size: 13px; font-family: inherit;
}
.inline { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.slot { display: flex; align-items: center; gap: 4px; font-size: 12px; }
</style>
