/**
 * 督战官子 Agent 配置 API。
 * 后端契约见 backend/app/duzhan_admin/router.py。
 */
import { apiGet, apiPost, apiPut, apiRequest } from '@/api/client'
import type { Block, BlockTypeSpec, BlocksPayload } from '@/api/duzhanBlocks'

export type { Block, BlockTypeSpec, BlockFieldSpec, BlocksPayload } from '@/api/duzhanBlocks'
export {
  addBlock, agentPayload, blockSummary, defaultBlock, fieldVisible,
  fromJson, moveBlock, removeBlock, toJson, updateBlockField,
} from '@/api/duzhanBlocks'

export interface DuzhanAgentItem {
  id: number
  name: string
  enabled: boolean
  timezone: string
  note: string
  blocks: BlocksPayload
  errors: string[]
  renderer: string
  updated_at: string
}

export interface PreviewResult {
  agent: { id: number | null; name: string; enabled: boolean }
  day: string | null
  hour: number | null
  errors: string[]
  spec: Record<string, unknown>
  summary: string[]
  note: string
}

export interface AgentInput {
  name: string
  timezone: string
  note: string
  enabled?: boolean
  blocks: Block[]
}

export function listAgents(): Promise<{ items: DuzhanAgentItem[] }> {
  return apiGet('/api/duzhan-agents')
}

export function blockSchema(): Promise<{ blocks: BlockTypeSpec[] }> {
  return apiGet('/api/duzhan-agents/block-schema')
}

export function createAgent(input: AgentInput): Promise<DuzhanAgentItem> {
  return apiPost('/api/duzhan-agents', input)
}

export function updateAgent(id: number, input: AgentInput): Promise<DuzhanAgentItem> {
  return apiPut('/api/duzhan-agents/' + id, input)
}

export function toggleAgent(id: number, enabled: boolean): Promise<DuzhanAgentItem> {
  return apiPost('/api/duzhan-agents/' + id + '/toggle', { enabled })
}

export function previewAgent(id: number, day?: string, hour?: number): Promise<PreviewResult> {
  return apiPost('/api/duzhan-agents/' + id + '/preview', { day, hour })
}

export function seedFromCode(): Promise<{ created: DuzhanAgentItem[]; skipped: string[] }> {
  return apiPost('/api/duzhan-agents/seed-from-code', {})
}

export async function deleteAgent(id: number): Promise<{ deleted: number }> {
  const response = await apiRequest('/api/duzhan-agents/' + id, { method: 'DELETE', credentials: 'include' })
  return (await response.json()) as { deleted: number }
}
