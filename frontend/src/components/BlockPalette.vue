<script setup lang="ts">
import type { BlockTypeSpec } from '@/api/duzhanBlocks'

defineProps<{ specs: BlockTypeSpec[]; disabled?: boolean }>()
const emit = defineEmits<{ (event: 'add', spec: BlockTypeSpec): void }>()
</script>

<template>
  <section class="palette">
    <h3>积木面板</h3>
    <p class="hint">点一下加到中间列表，按顺序拼出这个子 Agent 的动作。</p>
    <button
      v-for="spec in specs"
      :key="spec.type"
      class="chip"
      type="button"
      :disabled="disabled"
      :title="spec.hint || ''"
      @click="emit('add', spec)"
    >
      <strong>{{ spec.label }}</strong>
      <span class="code">{{ spec.type }}</span>
    </button>
  </section>
</template>

<style scoped>
.palette { display: flex; flex-direction: column; gap: 6px; }
.palette h3 { margin: 0; font-size: 14px; }
.hint { margin: 0 0 4px; color: var(--text-muted, #667085); font-size: 12px; line-height: 1.5; }
.chip {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  padding: 7px 10px; border: 1px solid var(--border, #e4e7ec); border-radius: 8px;
  background: #fff; cursor: pointer; text-align: left; font-size: 13px;
}
.chip:hover { border-color: var(--brand, #2563eb); }
.chip:disabled { opacity: .5; cursor: not-allowed; }
.code { color: var(--text-muted, #98a2b3); font-size: 11px; font-family: ui-monospace, monospace; }
</style>
