/**
 * 督战官积木：纯函数操作与类型（不依赖任何模块，便于 node 直接跑测试）。
 * 后端 schema 见 backend/app/duzhan_blocks.py。
 */

export interface Block {
  type: string
  [key: string]: unknown
}

export interface BlocksPayload {
  blocks: Block[]
}

export interface BlockFieldSpec {
  key: string
  label: string
  kind: 'text' | 'textarea' | 'uuid' | 'date' | 'select' | 'slots' | 'names' | 'bool' | string
  required?: boolean
  options?: string[]
  option_labels?: Record<string, string>
  default?: unknown
  /** 只在同块其它字段取到指定值时显示，例如 { mode: 'ai' } */
  show_when?: Record<string, unknown>
}

export interface BlockTypeSpec {
  type: string
  label: string
  hint?: string
  fields: BlockFieldSpec[]
}

export interface AgentErrors {
  message: string
  errors: string[]
}

/** 追加一块到末尾。 */
export function addBlock(blocks: Block[], block: Block): Block[] {
  return [...blocks, block]
}

/** 上下移动；越界原样返回。 */
export function moveBlock(blocks: Block[], from: number, to: number): Block[] {
  if (from === to || from < 0 || to < 0 || from >= blocks.length || to >= blocks.length) return [...blocks]
  const next = [...blocks]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

export function removeBlock(blocks: Block[], index: number): Block[] {
  return blocks.filter((_, position) => position !== index)
}

export function updateBlockField(blocks: Block[], index: number, key: string, value: unknown): Block[] {
  return blocks.map((block, position) => (position === index ? { ...block, [key]: value } : block))
}

export function toJson(blocks: Block[]): string {
  return JSON.stringify({ blocks }, null, 2)
}

/** JSON 文本 → 积木列表；坏 JSON 抛错，由界面提示。 */
export function fromJson(text: string): Block[] {
  const parsed: unknown = JSON.parse(text)
  const items = Array.isArray(parsed) ? parsed : (parsed as BlocksPayload | null)?.blocks
  if (!Array.isArray(items)) throw new Error('JSON 里没有 blocks 数组')
  return items
    .filter((item): item is Block => typeof item === 'object' && item !== null && !Array.isArray(item))
    .map((item) => ({ ...item, type: String(item.type || '') }))
}

/** 按面板定义生成一块新积木的默认值。 */
export function defaultBlock(spec: BlockTypeSpec): Block {
  const block: Block = { type: spec.type }
  for (const field of spec.fields) {
    if (field.default !== undefined) {
      block[field.key] = field.default
    } else if (field.kind === 'slots' || field.kind === 'names') {
      block[field.key] = []
    } else if (field.kind === 'bool') {
      block[field.key] = true
    } else if (field.required) {
      block[field.key] = ''
    }
  }
  return block
}

/** 字段是否该显示（show_when 全等匹配）。 */
export function fieldVisible(field: BlockFieldSpec, block: Block): boolean {
  if (!field.show_when) return true
  return Object.entries(field.show_when).every(([key, value]) => block[key] === value)
}

/** 列表/表单入参：后端 PUT 不带 enabled 表示保持原状态。 */
export function agentPayload(input: {
  name: string
  timezone: string
  note: string
  enabled?: boolean
  blocks: Block[]
}): { name: string; timezone: string; note: string; enabled?: boolean; blocks: Block[] } {
  const payload: { name: string; timezone: string; note: string; enabled?: boolean; blocks: Block[] } = {
    name: input.name.trim(),
    timezone: input.timezone,
    note: input.note,
    blocks: input.blocks,
  }
  if (input.enabled !== undefined) payload.enabled = input.enabled
  return payload
}

/** 卡片标题上的一行摘要。 */
export function blockSummary(block: Block): string {
  switch (block.type) {
    case 'group':
      return String(block.label || block.channel_id || '未填群')
    case 'times':
      return Array.isArray(block.slots) ? block.slots.join(' / ') : '未选档位'
    case 'people':
      return Array.isArray(block.names) && block.names.length ? block.names.join('、') : '该群全员'
    case 'source':
      return String(block.key || '未选来源')
    case 'rule':
      return block.mode === 'ai' ? 'AI 生成' : String(block.text || '未填文案')
    case 'strategy':
      return String(block.id || '未选策略')
    case 'recipient':
      return String(block.kind || '') + ' ' + String(block.id || '')
    case 'condition':
      return String(block.key || '') + ' = ' + (block.value ? '是' : '否')
    case 'style':
      return String(block.lang || 'zh') + (block.title ? ' · ' + String(block.title) : '')
    default:
      return block.type
  }
}
