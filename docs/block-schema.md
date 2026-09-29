# 积木字段与校验规则（block-schema）

> 本文**逐条对照** `backend/app/duzhan_blocks.py` 写成：每个字段、每条报错文案都取自该文件（含 `_check_one()`、`validate_blocks()` 与 `block_schema()`）；凡是代码里没写的约束，本文也不写。字段名大小写与 JSON 键名完全一致。

## 0. 总则

- 一份配置就是一个 JSON：`{"blocks": [{"type": "...", ...}, ...]}`；`blocks` 是**有序数组**，渲染按顺序拼装。
- 解析容错（`parse_blocks()`）：接受 JSON 字符串 / 字典 / 裸数组；JSON 坏掉时按**空配置**处理并记一条 warning；数组里非字典的元素直接跳过；`type` 会被 `str()` 后去空格。
- **未知字段不报错**：除 `type` 以外的键都会原样保留、落库、回显（所以可以自己加注释性字段，但不会生效）。
- 报错文案格式固定：「第 N 块 xxx：原因」，序号从 1 开始；`type` 缺失的块显示成 `(缺 type)`。
- 空配置（`blocks` 为空 / 解析不出来）只报一条：`至少需要一个积木块（至少要有 group + times）`。
- `type` 不在下方枚举里（含缺失、写空）只报一条：`第 N 块 xxx：不支持的积木类型`，**该块其他字段不再校验**。

枚举速查（都定义在 `duzhan_blocks.py` 顶部，前端面板直接读这些常量，不会两处漂移）：

| 常量 | 取值 | 用途 |
| --- | --- | --- |
| `BLOCK_TYPES` | group / times / people / source / rule / strategy / recipient / condition / style | 支持的块类型；顺序即面板展示顺序 |
| `SOURCE_KEYS` | msg_summary / quote_ocr / im_trace / daily_report / meeting_notes | `source` 块可选的数据源 |
| `CONDITION_KEYS` | workday / holiday / has_data | `condition` 块可选的条件 |
| `RULE_WHEN` | always / workday / holiday / missing_report | `rule` 块生效范围 |
| `RULE_MODES` | text / ai | `rule` 块生成方式 |
| `RECIPIENT_KINDS` | channel / user | `recipient` 块类型 |
| `LANGS` | zh / en | `style` 块语言 |
| `SLOT_HOURS` | 10 / 15 / 20 | `times` 块允许的整点档位 |

## 1. `group`：目标群

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `channel_id` | string | **是**（面板 `required`） | 群 UUID，形状 `8-4-4-4-12` 的十六进制 | 这份报告发到哪个群 |
| `label` | string | 否 | 任意文本，建议与名单里的群名一致 | 用来取该群名单；试跑里为空显示 `(未命名群)` |

```json
{"type": "group", "channel_id": "11111111-1111-4111-8111-111111111111", "label": "示例达标群 A"}
```

校验（`_check_one()` 的 `group` 分支 + `validate_blocks()` 全局段）：

| 触发条件 | 报错原文 |
| --- | --- |
| `channel_id` 缺失 / 空串 / 全空格 | `…：channel_id 不能为空` |
| `channel_id` 不是 UUID 形状 | `…：channel_id 必须是群 UUID，当前为 c1` |
| 整份配置一个 `group` 都没有 | `缺少 group 块：不知道发到哪个群` |
| 有 2 个及以上 `group` | `只能有一个 group 块（当前 2 个）：一个子 Agent 追一个群` |

补充（代码事实）：

- `label` 决定了 `people` 用哪份名单：`service.owners_for()` 拿 `label` 去 `roster_by_group()` 里查该群成员；**查不到就用全量名单** `roster_names()`。
- `label` **不**参与渲染器选择：`service.renderer_for()` 只看 `channel_id` 是否落在跟进群集合里（`group_brief.renderer` / `follow_brief.renderer`）。
- UUID 正则大小写不敏感，`A-F` 小写也可以。

## 2. `times`：推送档位

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `slots` | string[]（也接受逗号串 `"10:00,15:00"`） | **是** | `HH:MM`，且小时只能是 10 / 15 / 20 | 一天推哪几档；顺序即展示顺序 |

```json
{"type": "times", "slots": ["10:00", "15:00", "20:00"]}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| `slots` 缺失 / 空数组 / 空串 | `…：slots 不能为空` |
| 同一个档位出现两次 | `…：档位 10:00 重复`（每次重复出现都会报一条） |
| 形状不是 `HH:MM`（如 `9:00`、`9点`、`25:00`） | `…：时刻格式应为 HH:MM，当前为 9:00` |
| 形状合法但小时不在 10/15/20（如 `12:00`） | `…：当前引擎只支持 10:00/15:00/20:00，不支持 12:00` |
| 整份配置没有 `times` 块 | `缺少 times 块：不知道一天推哪几档` |

补充（代码事实）：

- 形状不合法时**跳过**小时校验（代码里是 `continue`），所以 `9:00` 只报一条、`12:00` 也只报一条；两者都不合法时会各报一条。
- 合法性只到"小时 ∈ SLOT_HOURS"这一层：`10:59` 形状合法、小时是 10，**会通过**；真要约束到整点，得在运行时按档位对齐。
- `service._slots_of()` 只取**第一个** `times` 块；校验却会检查每一个 `times` 块。放多块时只有第一块真正生效 —— 建议只放一块。
- 推送时区不在这块里，而是子 Agent 的 `timezone` 字段（接口入参，默认 `Asia/Shanghai`）。
## 3. `people`：覆盖人员

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `names` | string[]（也接受逗号串 `"示例成员A,示例成员B"`） | 块存在时**必须非空** | 姓名，必须在督战名单内 | 这份报告盯谁；不写这块 = 该群全员 |

```json
{"type": "people", "names": ["示例成员A", "示例成员B", "示例成员C"]}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| 名字不在名单里（可多个） | `…：不在督战名单内 示例成员Z`（多个名字用 `、` 连接） |

`names` 缺失 / 空数组 / 只有空白 **不算错**：空名单表示「该群全员」。

补充（代码事实）：

- 空名单 = 全群：想限定人员才填 `names`；想表达"该群全员"就留空（或者干脆不加这块，效果一样）。
- 名单由 `service.validate_agent()` 注入：`owners_for(blocks)` 先按 `group.label` 去 `roster_by_group()` 找该群成员，找不到则退回全量 `roster_names()`（达标群成员 + 跟进群主）。
- 测试/离线校验时可以传 `owners=None` 跳过人名校验；线上接口永远不会这么传。
- `people` 是**单例块**：放多块会被校验挡下（见 §10），运行时 `_people_of()` 也只用第一块。

## 4. `source`：数据源

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `key` | string | **是**（面板 `required`） | `msg_summary` / `quote_ocr` / `im_trace` / `daily_report` / `meeting_notes` | 这一节取什么数 |

5 个 key 的中文标签（`SOURCE_KEYS`，试跑里就是按这个显示）：

| key | 标签 |
| --- | --- |
| `msg_summary` | 消息汇总（触达 / 回复 / 新增） |
| `quote_ocr` | 报价图 OCR 识别 |
| `im_trace` | 内部 IM 留痕 |
| `daily_report` | 日报提交情况 |
| `meeting_notes` | 会议纪要 |

```json
{"type": "source", "key": "msg_summary"}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| `key` 缺失 / 空 | `…：未知数据源 (空)` |
| `key` 不在上面 5 个里 | `…：未知数据源 nope` |

补充：`source` 可以放多块，**顺序即取数顺序**，试跑里用 ` → ` 连接展示；同一个 key 重复放不会被拦（只是会取两遍），要不要去重由运行时决定。

## 5. `rule`：规则 / 动作

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `mode` | string | 否（默认 `text`） | `text` / `ai` | 固定文案，还是交给 AI 按当天数据生成 |
| `text` | string | `mode=text` 时必填 | 任意文本，去空格后非空 | 固定文案（面板上只在 text 模式显示） |
| `prompt` | string | `mode=ai` 时必填 | 任意文本，去空格后非空 | AI 提示词（面板上只在 ai 模式显示） |
| `when` | string | 否（默认 `always`） | `always` / `workday` / `holiday` / `missing_report` | 这块什么时候生效 |

```json
{"type": "rule", "mode": "text", "text": "本档动作：核验交付物与证据，未交的今天内补齐。", "when": "always"}
{"type": "rule", "mode": "ai", "prompt": "根据当天数据，为未交日报的成员各写一句不超过 40 字的催促，只讲事实、不评价人。", "when": "missing_report"}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| `mode` 不是 text/ai（如 `llm`） | `…：mode 只能是 text/ai，当前为 llm` |
| `mode` 缺省或 `text`，而 `text` 缺失/空白 | `…：规则文案不能为空` |
| `mode=ai` 而 `prompt` 缺失/空白 | `…：AI 规则必须填 prompt` |
| `when` 不在 4 个取值里 | `…：when 只能是 always/workday/holiday/missing_report，当前为 fullmoon` |

补充（代码事实）：

- `mode=ai` 时**不再校验 `text`**；`mode=text` 时也不校验 `prompt`。两个字段可以同时存在，只有生效的那个会被带进运行时结构。
- `resolve_spec()` 会做归一化：`text` 模式下把 `prompt` 清空，`ai` 模式下把 `text` 清空 —— 运行时拿到的永远只有该模式该用的那一个。
- `rule` 可以放多块，**全部保留且保序**（`spec["rules"]` 是数组，每块带自己的 `when`）。常见组合：一条 `always` 固定说明 + 一条 `missing_report` 的 AI 催促（见 `examples/03-ai-rule-missing-report.json`）。
- AI 块更慢、会产生调用费用，面板提示语原文就是 `AI 生成（慢·有费用）`。
## 6. `strategy`：策略核查

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `id` | string | **是**（面板 `required`） | 策略清单里的 id，如 `promo_a` | 挂一条触达策略检查 |
| `until` | string | 否 | `YYYY-MM-DD` | 策略到期日；早于今天就等于"永远不会命中" |

```json
{"type": "strategy", "id": "promo_a", "until": "2099-12-31"}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| `id` 缺失 / 空 | `…：策略 id 不能为空` |
| `id` 不在当前策略清单内 | `…：策略 ghost 不在当前策略清单内` |
| `until` 有值但不是 `YYYY-MM-DD` | `…：until 应为 YYYY-MM-DD，当前为 2099/12/31` |
| `until` 早于今天 | `…：策略 ghost 已于 2020-01-01 到期，这块永远不会命中，请改期或删掉` |

补充（代码事实）：

- "今天"取 `today_in_shanghai()`（Asia/Shanghai），比较是 `YYYY-MM-DD` 的字符串比较；**当天不算过期**（`until < today` 才报）。
- `until` 形状不合法时**只报格式错**，不再判过期（代码里是 `elif`）。
- 策略清单由 `service.strategy_ids()` 传入（`app/adapters/strategies.py` 的 `STRATEGIES`）。**如果策略清单读取失败，它会返回空列表 `[]`**，此时每一个 `strategy` 块都会报"不在当前策略清单内"——这是"清单坏了不许启用"的保守设计，不是块写错了。
- 测试/离线校验可传 `strategies=None` 跳过清单校验。

## 7. `recipient`：额外收件人

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `kind` | string | 否（默认 `channel`） | `channel`（群）/ `user`（个人） | 抄送给群还是个人 |
| `id` | string | **是**（面板 `required`） | UUID | 群 ID 或用户 ID |

```json
{"type": "recipient", "kind": "user", "id": "55555555-5555-4555-8555-555555555555"}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| `kind` 空或不在 channel/user 内 | `…：kind 只能是 channel/user，当前为 sms`（空值显示 `(空)`） |
| `id` 缺失 / 空 | `…：id 不能为空` |
| `id` 不是 UUID 形状 | `…：id 必须是 UUID，当前为 abc` |

补充：`recipient` 可以放多块（多抄送）；`kind` 只校验取值，不校验"这个 UUID 到底是不是群"——那属于运行时。

## 8. `condition`：发送条件

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `key` | string | **是**（面板 `required`） | `workday` / `holiday` / `has_data` | 满足才发 |
| `value` | boolean | 否（面板默认 `true`） | JSON `true` / `false` | 期望的取值；`false` 表示"反着来" |

3 个条件的标签（`CONDITION_KEYS`）：`workday` 仅工作日、`holiday` 仅节假日、`has_data` 仅当日有数据。

```json
{"type": "condition", "key": "workday", "value": true}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| `key` 缺失 / 空 | `…：未知条件 (空)` |
| `key` 不在 3 个里 | `…：未知条件 nope` |
| `value` 不是 JSON 布尔 | `…：value 必须是 true/false` |

补充（代码事实，字符串是常见坑）：

- `"yes"`、`"true"`、`1`、`null` 都**不是**布尔，一律报错；必须是 JSON 的 `true` / `false`。
- `value` 缺失时会被判错（`None` 不是 `bool`）；面板新增块时默认给 `true`。
- 运行时归一化用 `block.get("value") is True`：非 `true` 一律当 `false`，所以"漏填 value"在结构里表现为"条件取否"，但它在校验阶段就已经被拦下了。

## 9. `style`：样式

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `lang` | string | 否（默认 `zh`） | `zh` / `en` | 报告语言 |
| `title` | string | 否 | 非空文本 | 报告标题；不需要就别写这个键 |
| `footer` | string | 否 | 任意文本 | 落款（代码不校验） |

```json
{"type": "style", "lang": "zh", "title": "每日三追进度表", "footer": "示例落款：督战官自动播报"}
```

校验：

| 触发条件 | 报错原文 |
| --- | --- |
| `lang` 不是 zh/en | `…：lang 只能是 zh/en，当前为 fr` |
| `title` 存在但去空格后为空 | `…：title 不能为空字符串（不需要就删掉该字段）` |

补充（代码事实）：

- `title` 的判断是 `block.get("title") is not None and not _text(...)`：**不写 `title` 键完全没问题**，写了但给空串才报错。
- `footer` 只出现在面板定义与运行时结构里，**校验阶段没有任何约束**。
- 与 `times` / `people` 一样，运行时只取**第一个** `style` 块（`_style_of()`）；缺省时语言回落 `zh`、标题与落款回落空串。
## 10. 全局规则（不属于任何单块）

| 规则 | 报错原文 |
| --- | --- |
| `blocks` 为空 / 解析不出数组 | `至少需要一个积木块（至少要有 group + times）` |
| 没有 `group` 块 | `缺少 group 块：不知道发到哪个群` |
| 没有 `times` 块 | `缺少 times 块：不知道一天推哪几档` |
| 单例块放了多个 | `只能有一个 <kind> 块（当前 N 个）：…`，`kind` ∈ `group` / `times` / `people` / `style` |
| `type` 不认识 / 缺失 | `第 N 块 xxx：不支持的积木类型`（该块其他字段不再校验） |

**单例 vs 可重复**（`SINGLETON_BLOCKS` / `REPEATABLE_BLOCKS`，也就是 `GET /api/duzhan-agents/block-schema` 里每个块的 `multiple` 字段）：

- 单例（`multiple: false`）：`group`、`times`、`people`、`style` —— 只能一块，多出来的会被校验挡下（运行时 `_slots_of()` / `_people_of()` / `_style_of()` 也只读第一块）。
- 可重复（`multiple: true`）：`source`、`rule`、`strategy`、`recipient`、`condition` —— 按顺序生效，可以放多块。

## 11. 校验在什么时候发生

| 时机 | 行为 | 代码位置 |
| --- | --- | --- |
| 新建 `POST /api/duzhan-agents`（不带 `enabled` 或 `enabled=false`） | **不校验，直接存草稿**，响应里带 `errors[]` | `router.create_agent()` |
| 新建时带 `enabled=true` | 先校验；有错 → **400**，配置根本不会入库 | `router._guard_enable()` |
| 更新 `PUT /api/duzhan-agents/{id}` | 先落库；只有 `enabled` 最终为真时才校验（有错 → 400，且这次更新不会保存） | `router.update_agent()` |
| 更新时不传 `enabled` | 保持原状态，不会把在跑的配置悄悄停掉 | 同上（`enable = row.enabled if payload.enabled is None else payload.enabled`） |
| 启用 `POST /{id}/toggle {"enabled": true}` | 校验；有错 → **400**，`detail` 是 `{"message": "配置有误，不能启用", "errors": [...]}` | `router.toggle_agent()` |
| 停用 `{"enabled": false}` | 不校验，随时可停 | 同上 |
| 列表 / 详情 | 每条都带 `errors[]`（可能非空）与 `renderer` | `service.agent_out()` / `list_agents()` |
| 结构试跑 `POST /{id}/preview` | **不校验拦截、不取数、不发消息**：返回 `errors[]` + `spec` + `summary` + `note` | `service.preview_agent()` |

前端据此把「启用」按钮置灰：`:disabled="busy || (!enabled && savedErrors.length > 0)"`（`DuzhanAgentsPage.vue`）。

## 12. 排错速查

| 你看到的报错 | 多半是什么 | 怎么修 |
| --- | --- | --- |
| `channel_id 必须是群 UUID` | 填了群名或短 ID（`c1`） | 换成 `8-4-4-4-12` 形状的群 UUID |
| `只能有一个 group 块` | 一个子 Agent 里塞了两个群 | 拆成两个子 Agent（一个群一份配置） |
| `当前引擎只支持 10:00/15:00/20:00` | 选了引擎不认的档位 | 改成 10/15/20 三档之一 |
| `时刻格式应为 HH:MM` | 写成 `9:00` / `9点` / `25:00` | 补零写成 `09:00`（但它仍会因小时不在 10/15/20 被拦） |
| `档位 10:00 重复` | 同一档位勾了两次（JSON 里重复） | 去重 |
| `不在督战名单内 XXX` | 名字与名单不一致（空格/别名/离职） | 对齐 `roster` 里的 `display`；或先确认 `group.label` 能匹配上该群名 |
| `未知数据源 XXX` | key 拼错（如 `msg_summary` 少写一个字母） | 用第 4 节的 5 个 key |
| `规则文案不能为空` | `mode` 缺省（=text）但只填了 `prompt` | 补 `"mode": "ai"` 或填 `text` |
| `AI 规则必须填 prompt` | `mode=ai` 但提示词空 | 填 `prompt` |
| `策略 X 不在当前策略清单内` | 策略 id 拼错，或策略清单读取失败（返回空列表） | 对齐 `app/adapters/strategies.py` 的 `STRATEGIES`；检查适配器是否抛异常 |
| `策略 X 已于 … 到期` | `until` 早于今天 | 改期或删掉这块 |
| `value 必须是 true/false` | 写了 `"true"` / `"yes"` / `1` | 用 JSON 布尔 `true` |
| `title 不能为空字符串` | 标题留了空串 | 删掉 `title` 键，或填内容 |
| 启用返回 400 `配置有误，不能启用` | 配置里有上面某条错 | 看 `detail.errors` 逐条改，改完再启用 |

## 13. 与前端面板的关系

`block_schema()` 是**面板定义与校验的同源**：前端 `GET /api/duzhan-agents/block-schema` 拿到它渲染积木面板，字段的 `kind` 决定控件（`text` / `textarea` / `uuid` / `date` / `select` / `slots` / `names` / `bool`），`required` 显示"必填"角标，`options` / `option_labels` 给出下拉选项与中文标签，`show_when` 控制联动（如 `{"mode": "ai"}` 只在 AI 模式显示 `prompt`），`default` 是新增块时的初值（前端 `defaultBlock()` 会用它，`slots` / `names` 初值是空数组，`bool` 初值是 `true`）。

两个要点：

- 面板的 `required` 只是界面提示，**真正的判定只看 `validate_blocks()`**。二者目前一致（`group.channel_id`、`times.slots`、`source.key`、`strategy.id`、`recipient.id`、`condition.key` 在两边都是必填；`people.names` 面板不标必填但校验要求非空，本文第 3 节已单独说明）。
- 面板会**丢掉你手写但在 schema 里没定义的字段**的编辑入口（JSON 面板仍然保留它们）；校验不会因此报错。

## 14. 改积木类型时要动哪些地方

顺序固定（改完跑 `backend/tests`）：

1. `duzhan_blocks.py`：`BLOCK_TYPES`（或对应枚举）→ `_check_one()` 加分支 → `block_schema()` 加面板定义；
2. `app/duzhan_admin/service.py`：`resolve_spec()` 里把新块翻译进运行时结构（需要的话同时改 `_summary_lines()`，让试跑能看到它）；
3. `backend/tests/test_duzhan_blocks.py`：补正例 + 至少一条反例（断言报错文案）；
4. `frontend/src/api/duzhanBlocks.ts`：只有需要新的控件类型时才动 `kind`；
5. 本文档（`docs/block-schema.md`）与 `examples/`。

## 15. 怎么自己核对"文档 = 代码"

在仓库根目录跑（只读，不改任何文件）：

```python
import sys
sys.path.insert(0, "backend")
from app.duzhan_blocks import (BLOCK_TYPES, CONDITION_KEYS, LANGS, RECIPIENT_KINDS,
                               RULE_MODES, RULE_WHEN, SLOT_HOURS, SOURCE_KEYS,
                               block_schema, parse_blocks, validate_blocks)

print("类型顺序:", BLOCK_TYPES)
print("数据源  :", list(SOURCE_KEYS))
print("条件    :", list(CONDITION_KEYS))
print("when    :", RULE_WHEN, "mode:", RULE_MODES, "kind:", RECIPIENT_KINDS)
print("语言    :", LANGS, "档位:", SLOT_HOURS)
for spec in block_schema():
    print(spec["type"], "->", [(f["key"], f.get("required", False)) for f in spec["fields"]])
```

输出应当与本文第 0 节的枚举表、各节字段表的"必填"列逐格对上；`validate_blocks(parse_blocks(json.load(open(path, encoding="utf-8"))), owners=None, strategies=None)` 返回空列表，就说明该 JSON 除了名单/策略清单外没有别的问题（`examples/README.md` 有完整写法）。



