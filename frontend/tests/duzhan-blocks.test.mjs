import assert from 'node:assert/strict'
import test from 'node:test'
import {
  addBlock, agentPayload, blockSummary, defaultBlock, fieldVisible,
  fromJson, moveBlock, removeBlock, toJson, updateBlockField,
} from '../src/api/duzhanBlocks.ts'

const SPECS = [
  { type: 'group', label: '目标群', fields: [{ key: 'channel_id', label: '群 ID', kind: 'uuid', required: true }, { key: 'label', label: '群名称', kind: 'text' }] },
  { type: 'times', label: '推送档位', fields: [{ key: 'slots', label: '档位', kind: 'slots', options: ['10:00', '15:00', '20:00'] }] },
  { type: 'rule', label: '规则', fields: [
    { key: 'mode', label: '生成方式', kind: 'select', options: ['text', 'ai'], default: 'text' },
    { key: 'text', label: '文案', kind: 'textarea', show_when: { mode: 'text' } },
    { key: 'prompt', label: 'AI 提示词', kind: 'textarea', show_when: { mode: 'ai' } },
  ] },
]

test('积木增删改序保持顺序', () => {
  let blocks = []
  blocks = addBlock(blocks, { type: 'times', slots: ['10:00'] })
  blocks = addBlock(blocks, { type: 'group', channel_id: 'c1', label: 'g' })
  assert.deepEqual(blocks.map((item) => item.type), ['times', 'group'])
  assert.deepEqual(moveBlock(blocks, 1, 0).map((item) => item.type), ['group', 'times'])
  assert.deepEqual(moveBlock(blocks, 0, 5).map((item) => item.type), ['times', 'group'])
  assert.deepEqual(removeBlock(blocks, 0).map((item) => item.type), ['group'])
  assert.deepEqual(removeBlock(blocks, 9).map((item) => item.type), ['times', 'group'])
})

test('改字段只动目标块', () => {
  const blocks = [{ type: 'group', channel_id: 'a' }, { type: 'group', channel_id: 'b' }]
  const next = updateBlockField(blocks, 1, 'channel_id', 'c')
  assert.equal(next[0].channel_id, 'a')
  assert.equal(next[1].channel_id, 'c')
  assert.equal(blocks[1].channel_id, 'b')
})

test('JSON 往返一致，坏 JSON 抛错', () => {
  const blocks = [{ type: 'group', channel_id: 'c1', label: '群' }, { type: 'times', slots: ['10:00'] }]
  assert.deepEqual(fromJson(toJson(blocks)), blocks)
  assert.deepEqual(fromJson(JSON.stringify(blocks)), blocks)
  assert.throws(() => fromJson('{'), /JSON/)
  assert.throws(() => fromJson('{"foo": 1}'), /blocks/)
})

test('默认值按面板定义生成', () => {
  assert.deepEqual(defaultBlock(SPECS[0]), { type: 'group', channel_id: '' })
  assert.deepEqual(defaultBlock(SPECS[1]), { type: 'times', slots: [] })
  assert.deepEqual(defaultBlock(SPECS[2]), { type: 'rule', mode: 'text' })
})

test('AI 规则按 mode 显示对应字段', () => {
  const rule = SPECS[2]
  assert.equal(fieldVisible(rule.fields[1], { type: 'rule', mode: 'text' }), true)
  assert.equal(fieldVisible(rule.fields[2], { type: 'rule', mode: 'text' }), false)
  assert.equal(fieldVisible(rule.fields[2], { type: 'rule', mode: 'ai' }), true)
})

test('保存入参：不带 enabled 时不下发该字段', () => {
  const base = { name: '  新人组官 ', timezone: 'Asia/Shanghai', note: '备注', blocks: [{ type: 'group' }] }
  assert.deepEqual(agentPayload(base), {
    name: '新人组官', timezone: 'Asia/Shanghai', note: '备注', blocks: [{ type: 'group' }],
  })
  assert.equal(agentPayload({ ...base, enabled: true }).enabled, true)
})

test('卡片摘要读取关键字段', () => {
  assert.equal(blockSummary({ type: 'group', label: '新人组' }), '新人组')
  assert.equal(blockSummary({ type: 'times', slots: ['10:00', '20:00'] }), '10:00 / 20:00')
  assert.equal(blockSummary({ type: 'people', names: [] }), '该群全员')
  assert.equal(blockSummary({ type: 'rule', mode: 'ai', prompt: 'x' }), 'AI 生成')
})
