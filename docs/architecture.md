# 架构：从积木 JSON 到一条群报告

本文讲清三件事：**这份配置是怎么流动的**、**为什么这么分层**、**接生产时要满足什么验收条件**。所有函数名都来自本仓库代码；标注「运行时」的部分不在本仓库（见 README 第 7 节）。

## 1. 分层总览

```
① 配置页（browser, frontend/）
   积木面板 → 有序积木数组 → JSON 文本（双向可改）
        │  fetch /api/duzhan-agents/*（vite 代理到 127.0.0.1:8767）
② API 层（FastAPI, pdca-workbench/app/duzhan_admin/router.py）
   pydantic 入参 → 权限依赖 require_admin → 调服务层 → 统一响应体
        │
③ 服务层（pdca-workbench/app/duzhan_admin/service.py）—— 纯逻辑，不取数不推送
   blocks_of / validate_agent / resolve_spec / preview_agent / seed_from_code
        │                                    ▲
④ 积木 schema（pdca-workbench/app/duzhan_blocks.py）—— 唯一事实来源
   BLOCK_TYPES / 枚举 / parse_blocks / dump_blocks / validate_blocks / block_schema
        │
⑤ 存储（SQLModel + SQLite，pdca-workbench/app/database.py + pdca-workbench/app/models/duzhan_agent.py）
   duzhan_agents(id, name, enabled, timezone, blocks_json, note, created_at, updated_at)
        │  enabled = true 才注册
⑥ 运行时（不在本仓库）
   调度（timezone + slots）→ 取数（数据源适配器）→ 渲染（复用 `app/duzhan.py` / `app/ctob.py` 的 `render_brief` 渲染器）
   → 推送（群 + recipient）→ 写 run 台账（按子 Agent 分档）
```

## 2. 一次配置改动的完整数据流

| 步 | 谁 | 做什么 | 入口 | 会不会碰外部世界 |
| --- | --- | --- | --- | --- |
| 1 | 配置页 | 点积木 → 数组；JSON 面板可反向覆盖数组 | `BlockPalette.vue` / `BlockCard.vue` / `fromJson()` | 否 |
| 2 | API | 收 `{name, timezone, enabled?, note, blocks}`，pydantic 校验（名称非空、时区存在、长度） | `AgentIn` | 否 |
| 3 | 服务/积木 | 规范化：解析成 Block 列表再序列化落库（紧凑 JSON、`ensure_ascii=False` 保留中文） | `parse_blocks()` → `dump_blocks()` | 写库 |
| 4 | 存储 | 一行 = 一个子 Agent；`enabled` 默认 false | `DuzhanAgent` | 写库 |
| 5 | 列表/详情 | 读回 → 翻译成运行时结构 → 附校验错误 → 返回 | `service.agent_out()` | 读库 |
| 6 | 启用闸门 | 校验全过才置 `enabled=true`，否则 400 带 `errors[]` | `router._guard_enable()` | 读库 |
| 7 | 结构试跑 | 只解析 + 校验 + 生成"到点会怎么发"的文字摘要 | `service.preview_agent()` | 否（明确不取数、不发消息） |
| 8 | 运行时 | `WHERE enabled = true` 注册；到点取数、渲染、推送、记台账 | 不在本仓库 | 是 |

一句话：**前 7 步全在本仓库，第 8 步是本仓库的"下游消费者"**。

## 3. 为什么积木 JSON 是唯一事实来源

- **库里只有一份**：模型字段就是 `blocks_json: str`（`app/models/duzhan_agent.py`），没有第二张"块表"、也没有把块拆成列。读的时候统一走 `parse_blocks()`，写的时候统一走 `dump_blocks()`——同一对函数，保证"存进去什么样、读出来什么样"。
- **前端不维护第二套结构**：面板字段来自 `GET /api/duzhan-agents/block-schema`（后端 `block_schema()`），下拉选项直接取 `BLOCK_TYPES` / `SOURCE_KEYS` / `CONDITION_KEYS` / `RULE_MODES` / `RULE_WHEN` / `RECIPIENT_KINDS` / `LANGS` / `SLOT_HOURS` 这些常量。**加一个数据源只改后端常量，面板自动多一个下拉项**，不会出现"面板能选、后端不认"的漂移。
- **坏数据降级而不是炸**：`parse_blocks()` 遇到非 JSON、非数组、非字典元素一律按空配置处理并记 warning。配置页不会因为一条脏数据打不开，运行时也不会抛异常。
- **未知字段原样保留**：解析只把 `type` 拎出来，其余键进 `data`；落库、回显都保留。老配置里多出来的字段不会丢，也不影响校验（校验只挑它认识的键）。
- **顺序即语义**：`blocks` 是有序数组（`README` 里的"渲染按顺序拼装"），前后调换块顺序会改变展示顺序；前端还有上移/下移按钮（`moveBlock()`）。

## 4. 为什么是"配置 → 运行时对象 → 复用现有渲染器"

替代方案是"配置直接驱动一套新渲染器"，我们没走这条路，原因是：

| 维度 | 重写渲染器 | 复用现有渲染器（本仓库做法） |
| --- | --- | --- |
| 上线风险 | 文本、口径、样式全部重新对齐一遍 | 渲染路径不变，只换"谁来触发、发给谁" |
| 回归成本 | 每加一个块都要回归所有群 | 配置层出问题，`enabled=false` 即回滚 |
| 验收 | "看起来差不多"很难判定 | 可以和硬编码输出**逐字 diff**（见第 7 节） |
| 代码量 | 两套渲染逻辑长期并存 | 渲染器只有一份，配置层只做翻译 |

具体做法：

- `resolve_spec(agent)` 是**唯一的翻译层**：把积木数组翻译成一个纯字典（`groups` / `slots` / `people` / `sources` / `rules` / `conditions` / `recipients` / `strategies` / `lang` / `title` / `footer` / `renderer`）。它是纯函数，不读网络、不读库、不取数，可以随便单测。
- `renderer_for(blocks)` 只做一件事：如果 `group.channel_id` 落在跟进群集合里 → `ctob.render_brief`，否则 → `duzhan.render_brief`。也就是说 **"发什么"由配置决定，"怎么渲染"仍然是原有的那两套渲染器**。
- 渲染器拿到的输入，就是 `resolve_spec()` 的输出——运行时要做的只有：按 `sources` 取数、把数据 + spec 一起交给渲染器、按 `groups` 与 `recipients` 推送。

## 5. code / db 双源与灰度

切换期间同时存在两个"真相"：

| 源 | 在哪 | 谁在读 |
| --- | --- | --- |
| 代码常量 | `app/duzhan.py` 的 `GROUPS` / `app/ctob.py` 的 `OWNERS` | 现有硬编码路径（生产）+ `seed_from_code()` |
| 库配置 | `duzhan_agents` 表 | 配置页 + 运行时（按 `enabled` 注册） |

`seed_from_code()` 的设计就是为了让这两个源能对上：

- 导入内容与硬编码同源：群名 / `channel_id` / 语言 / 时区直接取 `GROUPS`、`CTOB_GROUPS`；档位取 `PDCA_DUZHAN_TIMES`（默认 `10:00/15:00/20:00`）；取数节取 `DUZHAN_SEED_SOURCES`（达标群）与 `CTOB_SEED_SOURCES`（跟进群）。
- **一律默认停用**（`enabled=False`，注释原文"避免导入即双跑"），同名配置**跳过不覆盖**（响应里回 `skipped` 列表），导入完还会把每条配置的 `errors[]` 一起返回。
- 导入的备注固定写 `从代码导入（默认停用，核对后再启用）`，方便在列表里一眼认出来。

建议的灰度步骤（每一步都可停）：

1. 点「从代码导入」→ 拿到 N 条**停用**配置；
2. 逐条看列表里的 `errors`（应为空）→ 用「结构试跑」核对群、档位、人员、数据源、渲染器；
3. 按第 7 节做**逐字等价性验收**（配置生成的报告 vs 硬编码生成的报告）；
4. 关掉代码里那条群的硬编码发送（或加开关），把对应配置 `enabled=true`，观察一个完整档位；
5. 全部群切换完，删掉 `GROUPS` / `CTOB_GROUPS` 常量与 `seed_from_code()`（导入功能是过渡工具，不是长期依赖）。

回滚就是 `POST /{id}/toggle {"enabled": false}` —— 停用不校验、随时可停。
## 6. run 台账：按子 Agent 分档，防重复发

多实例、重试、进程重启、调度延迟补偿——任何一条都能让同一档推送跑两遍。台账（run ledger）是唯一能兜住它的东西。本仓库负责的与不负责的，边界如下：

**本仓库保证（配置层能给的契约）**

- 一个子 Agent **只能有一个** `group` 块（`validate_blocks()`：`只能有一个 group 块…`）——即"一个子 Agent = 一个群"，台账可以直接按 `agent_id` 分档；`times` / `people` / `style` 也是单例块（`SINGLETON_BLOCKS`）；
- `times.slots` **不允许重复**（`档位 10:00 重复`）——一个子 Agent 一天里每个档位最多一条，不会出现"同档两条配置"的歧义；
- 推送时区写在子 Agent 上（`timezone`，默认 `Asia/Shanghai`），"今天第几档"的判定基准唯一；
- `enabled=true` 才注册调度（模型注释：`是否启用（启用才注册调度）`）；
- 结构试跑能给出 `spec.slots` / `spec.groups` / `spec.timezone`，也就是台账键的全部输入。

**运行时需要实现（不在本仓库）**

| 项 | 建议 |
| --- | --- |
| 台账唯一键 | `(agent_id, 业务日期, 档位)`，例如 `(7, 2026-09-29, 20:00)` |
| 写入时机 | **先占位再发送**：拿到唯一键先 `INSERT`（唯一索引冲突即说明别处已在跑），发送成功再落状态；失败时把占位标记为失败并按策略重试 |
| 状态机 | `pending → sending → sent / failed / skipped（条件不满足）` |
| 幂等 | 唯一索引 + 状态判断，而不是"内存里记个集合" |
| 可观测 | 台账同时是审计：谁在什么时候、按哪份配置、发到哪个群 |

为什么强调"按子 Agent 分档"而不是"按群分档"：同一个群将来可能有多个子 Agent（比如一条日报、一条周报复盘），唯一键带 `agent_id` 才不会互相顶掉；而"一个子 Agent 只追一个群"的约束，又保证 `agent_id` 与群是一一对应的，排查问题时不需要额外映射表。

## 7. 等价性验收：配置跑出来的内容必须与硬编码逐字一致

替换生产路径的准入条件只有一条：**同一时间、同一份数据，配置生成的报告与硬编码生成的报告逐字相同**——包括标点、空格、换行、emoji、数字格式。差一个字都不算通过。

为什么这么严：报告是发到群里的，多一个错别字、少一个空行、数字格式从 `12.3%` 变 `12.30%` 都是可见的回归；"看起来差不多"会在群里被当成事故。

验收方法（五对齐）：

| 对齐项 | 怎么对 | 本仓库对应的东西 |
| --- | --- | --- |
| 数据源 | 配置里的 `sources` 与硬编码那一节取的数一一对应 | `SOURCE_KEYS`、`DUZHAN_SEED_SOURCES` / `CTOB_SEED_SOURCES` |
| 渲染器 | 试跑输出的 `渲染器：…` 与硬编码走的那支一致 | `renderer_for()` → `duzhan.render_brief` / `ctob.render_brief` |
| 档位与时区 | `spec.slots` 与 `spec.timezone` 与原配置一致 | `PDCA_DUZHAN_TIMES`（默认 `10:00/15:00/20:00`）、子 Agent 的 `timezone` |
| 名单 | `spec.people` 与原来 @ 的人一致（不限定就是全群） | `owners_for()`、`roster_by_group()` / `roster_names()` |
| 文本 | 取同一时刻的同一份数据，两份报告做 diff（含换行） | 渲染器输出（运行时）；本仓库只保证输入结构一致 |

本仓库已经替你保证的部分：

- 导入内容与硬编码同源（同常量、同档位常量）——`seed_from_code()`；
- 导入出来的配置**本身没有校验问题**（后端单测 `test_seed_from_code_imports_every_group_disabled` 断言导入结果每条 `errors` 为空，且全部 `enabled=False`）；
- 结构试跑把"这次会怎么发"打成人类可读的清单（`_summary_lines()`），可以先肉眼过一遍再去比文本。

验收清单（建议照抄进你的上线单）：

- [ ] 导入后逐条 `errors` 为空；
- [ ] 逐条试跑：群 / 档位 / 人员 / 数据源 / 规则 / 条件 / 收件人 / 策略 / 渲染器 全部与硬编码一致；
- [ ] 至少用**一个工作日 + 一个节假日 + 一个无数据日**三种情形各跑一次结构试跑（看 `condition` 与 `rule.when` 的表现）；
- [ ] 用同一份数据分别生成硬编码报告与配置报告，`diff` 为空（含尾部换行）；
- [ ] 灰度期间两台"发送方"不同时开：代码路径关掉后，才把配置 `enabled` 打开；
- [ ] run 台账里能看到 `(agent_id, 日期, 档位)` 各一条。

## 8. 边界与维护约定

**不在本仓库（运行时/你们的系统）**：取数实现、渲染模板与 AI 生成、推送通道、调度器、run 台账、权限体系。本仓库只到"配置解释清楚 + 校验干净 + 结构可预览"为止。

**维护约定**

- 加/改积木：改 `duzhan_blocks.py`（枚举 → 校验 → 面板）→ 改 `service.resolve_spec()`（翻译）→ 补 `pdca-workbench/tests` 正反例 → 改 `docs/block-schema.md` 与 `examples/`。步骤见 `docs/block-schema.md` 第 14 节。
- 闸门只有一道、但必须守住：**保存永远允许（草稿）**，**启用永远要过校验**。不要为了"先跑起来"绕过 `_guard_enable()`。
- 配置是数据，不是代码：改配置不需要发版；但也别把业务逻辑塞进 `rule.text` 之外的字段里（那些字段运行时并不解释）。

