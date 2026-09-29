<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { HttpError } from '@/api/client'
import BlockCard from '@/components/BlockCard.vue'
import BlockPalette from '@/components/BlockPalette.vue'
import {
  addBlock as appendBlock, agentPayload, blockSchema, createAgent, defaultBlock, deleteAgent,
  fromJson, listAgents, moveBlock, previewAgent, removeBlock, seedFromCode, toJson, toggleAgent,
  updateAgent, updateBlockField,
  type Block,
  type BlockTypeSpec,
  type DuzhanAgentItem,
  type PreviewResult,
} from '@/api/duzhanAgents'

const specs = ref<BlockTypeSpec[]>([])
const agents = ref<DuzhanAgentItem[]>([])
const selectedId = ref<number | null>(null)
const blocks = ref<Block[]>([])
const name = ref('')
const timezone = ref('Asia/Shanghai')
const note = ref('')
const enabled = ref(false)
const jsonText = ref('{"blocks": []}')
const jsonError = ref('')
const preview = ref<PreviewResult | null>(null)
const day = ref(new Date().toISOString().slice(0, 10))
const hour = ref('')
const busy = ref(false)
const message = ref('')
const error = ref('')
const pendingDelete = ref(false)

const current = computed(() => agents.value.find((item) => item.id === selectedId.value) || null)
const savedErrors = computed(() => current.value?.errors || [])
const isDraft = computed(() => selectedId.value === null)

watch(blocks, () => { jsonText.value = toJson(blocks.value) }, { deep: true })

function describe(err: unknown): string {
  if (err instanceof HttpError) {
    const detail = (err as HttpError & { detail?: string }).detail
    return detail || err.message
  }
  return err instanceof Error ? err.message : String(err)
}

function fill(item: DuzhanAgentItem) {
  selectedId.value = item.id
  name.value = item.name
  timezone.value = item.timezone
  note.value = item.note
  enabled.value = item.enabled
  blocks.value = item.blocks.blocks.map((block) => ({ ...block }))
  preview.value = null
  pendingDelete.value = false
  jsonError.value = ''
}

function startDraft() {
  selectedId.value = null
  name.value = '新子 Agent'
  timezone.value = 'Asia/Shanghai'
  note.value = ''
  enabled.value = false
  blocks.value = []
  preview.value = null
  message.value = ''
  error.value = ''
  pendingDelete.value = false
}

async function reload(keepId: number | null) {
  agents.value = (await listAgents()).items
  const next = agents.value.find((item) => item.id === keepId)
  if (next) fill(next)
}

async function load() {
  busy.value = true
  error.value = ''
  try {
    specs.value = (await blockSchema()).blocks
    agents.value = (await listAgents()).items
    if (agents.value.length && selectedId.value === null) fill(agents.value[0])
  } catch (err) {
    error.value = describe(err)
  } finally {
    busy.value = false
  }
}

async function save() {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const payload = agentPayload({ name: name.value, timezone: timezone.value, note: note.value, blocks: blocks.value })
    const saved = selectedId.value === null
      ? await createAgent(payload)
      : await updateAgent(selectedId.value, payload)
    await reload(saved.id)
    message.value = saved.errors.length
      ? '已存草稿，但配置还有 ' + saved.errors.length + ' 处问题，暂时不能启用。'
      : '已保存。'
  } catch (err) {
    error.value = describe(err)
  } finally {
    busy.value = false
  }
}

async function setEnabled(next: boolean) {
  if (selectedId.value === null) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const saved = await toggleAgent(selectedId.value, next)
    await reload(saved.id)
    message.value = next ? '已启用，调度会按这份配置注册。' : '已停用。'
  } catch (err) {
    error.value = describe(err)
  } finally {
    busy.value = false
  }
}

async function removeCurrent() {
  if (selectedId.value === null) return
  if (!pendingDelete.value) {
    pendingDelete.value = true
    return
  }
  busy.value = true
  error.value = ''
  try {
    await deleteAgent(selectedId.value)
    selectedId.value = null
    agents.value = (await listAgents()).items
    if (agents.value.length) fill(agents.value[0])
    else startDraft()
    message.value = '已删除。'
  } catch (err) {
    error.value = describe(err)
  } finally {
    busy.value = false
    pendingDelete.value = false
  }
}

async function runPreview() {
  if (selectedId.value === null) {
    error.value = '先保存，再试跑。'
    return
  }
  busy.value = true
  error.value = ''
  try {
    preview.value = await previewAgent(selectedId.value, day.value, hour.value === '' ? undefined : Number(hour.value))
  } catch (err) {
    error.value = describe(err)
  } finally {
    busy.value = false
  }
}

async function importFromCode() {
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await seedFromCode()
    agents.value = (await listAgents()).items
    message.value = '导入完成：新增 ' + result.created.length + ' 条（默认停用），跳过 ' + result.skipped.length + ' 条同名。'
  } catch (err) {
    error.value = describe(err)
  } finally {
    busy.value = false
  }
}

function onAddBlock(spec: BlockTypeSpec) {
  blocks.value = appendBlock(blocks.value, defaultBlock(spec))
}

function onFieldChange(index: number, key: string, value: unknown) {
  blocks.value = updateBlockField(blocks.value, index, key, value)
}

function onMove(index: number, offset: number) {
  blocks.value = moveBlock(blocks.value, index, index + offset)
}

function onRemove(index: number) {
  blocks.value = removeBlock(blocks.value, index)
}

function applyJson() {
  jsonError.value = ''
  try {
    blocks.value = fromJson(jsonText.value)
    message.value = '已按 JSON 覆盖中间列表。'
  } catch (err) {
    jsonError.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(load)
</script>

<template>
  <main class="page">
    <header class="head">
      <div>
        <h1>督战官配置</h1>
        <p class="sub">一个子 Agent = 追一个群 + 一组规则。用积木拼，保存后启用才生效。</p>
      </div>
      <div class="actions">
        <button class="btn" type="button" :disabled="busy" @click="load">刷新</button>
        <button class="btn" type="button" :disabled="busy" @click="importFromCode">从代码导入</button>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="startDraft">新建子 Agent</button>
      </div>
    </header>

    <p v-if="message" class="toast" role="status">{{ message }}</p>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>

    <div class="layout">
      <aside class="col list">
        <h3>子 Agent（{{ agents.length }}）</h3>
        <button
          v-for="item in agents"
          :key="item.id"
          class="row"
          type="button"
          :class="{ active: item.id === selectedId }"
          @click="fill(item)"
        >
          <span class="row-name">{{ item.name }}</span>
          <span class="tags">
            <em v-if="item.enabled" class="on">启用</em>
            <em v-else class="off">停用</em>
            <em v-if="item.errors.length" class="bad">{{ item.errors.length }} 处问题</em>
          </span>
        </button>
        <p v-if="!agents.length" class="hint">还没有配置。点「从代码导入」把现有群导进来看看。</p>
      </aside>

      <section class="col editor">
        <div class="meta">
          <label class="field">
            <span>名称</span>
            <input v-model="name" type="text" />
          </label>
          <label class="field">
            <span>时区</span>
            <input v-model="timezone" type="text" />
          </label>
          <label class="field">
            <span>备注</span>
            <input v-model="note" type="text" />
          </label>
        </div>

        <BlockPalette :specs="specs" :disabled="busy" @add="onAddBlock" />

        <div class="blocks">
          <BlockCard
            v-for="(block, index) in blocks"
            :key="index + '-' + block.type"
            :block="block"
            :spec="specs.find((item) => item.type === block.type) || { type: block.type, label: block.type, fields: [] }"
            :index="index"
            :total="blocks.length"
            @change="(key, value) => onFieldChange(index, key, value)"
            @move="(offset) => onMove(index, offset)"
            @remove="() => onRemove(index)"
          />
          <p v-if="!blocks.length" class="hint">左边点一块加进来。</p>
        </div>

        <div class="save-row">
          <button class="btn btn-primary" type="button" :disabled="busy" @click="save">
            {{ isDraft ? '创建' : '保存' }}
          </button>
          <button
            v-if="!isDraft"
            class="btn"
            type="button"
            :disabled="busy || (!enabled && savedErrors.length > 0)"
            @click="setEnabled(!enabled)"
          >
            {{ enabled ? '停用' : '启用' }}
          </button>
          <button v-if="!isDraft" class="btn" type="button" :disabled="busy" @click="removeCurrent">
            {{ pendingDelete ? '再点一次确认删除' : '删除' }}
          </button>
          <span v-if="!isDraft" class="renderer">渲染器：{{ current?.renderer }}</span>
        </div>

        <ul v-if="savedErrors.length" class="errors">
          <li v-for="(item, index) in savedErrors" :key="index">{{ item }}</li>
        </ul>
      </section>

      <aside class="col side">
        <h3>JSON</h3>
        <textarea v-model="jsonText" rows="10" spellcheck="false"></textarea>
        <p v-if="jsonError" class="alert">{{ jsonError }}</p>
        <button class="btn btn-sm" type="button" @click="applyJson">按 JSON 覆盖</button>

        <h3>结构试跑</h3>
        <div class="preview-row">
          <input v-model="day" type="date" />
          <select v-model="hour">
            <option value="">全部档位</option>
            <option value="10">10:00</option>
            <option value="15">15:00</option>
            <option value="20">20:00</option>
          </select>
          <button class="btn btn-sm" type="button" :disabled="busy || isDraft" @click="runPreview">试跑</button>
        </div>
        <template v-if="preview">
          <p class="hint">{{ preview.note }}</p>
          <ul class="summary">
            <li v-for="(line, index) in preview.summary" :key="index">{{ line }}</li>
          </ul>
          <ul v-if="preview.errors.length" class="errors">
            <li v-for="(item, index) in preview.errors" :key="index">{{ item }}</li>
          </ul>
        </template>
      </aside>
    </div>
  </main>
</template>

<style scoped>
.page { padding: 16px 20px 40px; }
.head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--border, #e4e7ec); }
.head h1 { margin: 0 0 4px; font-size: 20px; }
.sub { margin: 0; color: var(--text-muted, #667085); font-size: 13px; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; }
.layout { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(320px, 2fr) minmax(240px, 1.2fr); gap: 16px; margin-top: 16px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 8px; }
.col h3 { margin: 0; font-size: 14px; }
.list .row { display: flex; flex-direction: column; gap: 4px; padding: 8px 10px; border: 1px solid var(--border, #e4e7ec); border-radius: 8px; background: #fff; cursor: pointer; text-align: left; }
.list .row.active { border-color: var(--brand, #2563eb); box-shadow: 0 0 0 1px var(--brand, #2563eb) inset; }
.row-name { font-size: 13px; }
.tags { display: flex; gap: 6px; font-size: 11px; font-style: normal; }
.tags em { font-style: normal; padding: 1px 6px; border-radius: 999px; }
.tags .on { background: #ecfdf3; color: #027a48; }
.tags .off { background: #f2f4f7; color: #475467; }
.tags .bad { background: #fef3f2; color: #b42318; }
.meta { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; }
.field { display: grid; gap: 4px; font-size: 12px; color: var(--text-muted, #475467); }
.field input { padding: 6px 8px; border: 1px solid var(--border, #d0d5dd); border-radius: 6px; font-size: 13px; }
.blocks { display: flex; flex-direction: column; gap: 8px; }
.save-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.renderer { color: var(--text-muted, #667085); font-size: 12px; }
.errors { margin: 0; padding-left: 18px; color: #b42318; font-size: 12px; line-height: 1.6; }
.side textarea { width: 100%; font-family: ui-monospace, monospace; font-size: 12px; padding: 8px; border: 1px solid var(--border, #d0d5dd); border-radius: 6px; }
.preview-row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.preview-row input, .preview-row select { padding: 5px 6px; border: 1px solid var(--border, #d0d5dd); border-radius: 6px; font-size: 12px; }
.summary { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.7; }
.hint { color: var(--text-muted, #667085); font-size: 12px; line-height: 1.6; margin: 0; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
</style>
