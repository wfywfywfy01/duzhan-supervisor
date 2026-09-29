# 督战官（Duzhan Supervisor）

**一句话定位**：把硬编码在代码里的「群督战报告」——几点发、发到哪个群、覆盖谁、取哪些数据、走什么规则、查哪条策略、什么条件下发——拆成 **JSON 积木**：落库、可视化配置、按配置注册调度；**配置有错只能存草稿、不能启用**，从根上避免"到点推不出去"。

**本仓库是配置层，不是发送器**：它不取数、不渲染、不推送。它只做四件事——定义积木、校验积木、把积木翻译成"运行时打算怎么做"、把这套东西做成能点的界面与能调的 API。真正的取数与发送由运行时按配置接管（见 `docs/architecture.md`）。

> **这一份是脱敏分享版**：可以对外公开，示例数据全是占位符（示例群 / 示例成员 / 示例策略）。
> 真实的群映射、成员名单、策略内容，以及运行时（取数 / 渲染 / 推送 / 台账）留在使用方自己的私有仓库里，
> 通过 `backend/app/adapters/` 下三个函数接入。两边**配置层的规则完全一致**，可以互相对照。

---

## 1. 它解决什么问题

原来的群督战报告写死在代码里：群 ID、推送档位、覆盖名单、取数节、规则文案、策略核查全是一个个大常量。于是：

| 硬编码时代 | 积木化之后 |
| --- | --- |
| 改一档时间、加一个群、换一批人，都要改代码走发版 | 在配置页改积木，保存入库；启用后调度按库注册 |
| "线上到底几点发给谁"只能翻代码 | `GET /api/duzhan-agents` 一眼看全，每条还带校验状态 |
| 配置写错要等到点才发现推不出去 | 保存即给出人类可读的错误；**有错直接不给启用** |
| 新人不敢碰调度代码 | 点积木拼装，产物是纯 JSON：可复制、可评审、可 diff |
| 换群 / 换人 / 换策略得动常量 | 「从代码导入」一键把现有群导成配置，默认停用，核对后再切 |

一句话：**把"发什么"从代码里搬到数据里，并且给这份数据配一道闸门。**

## 2. 架构一览

```
   ① 配置页（Vue 3 + Vite，frontend/）
   ┌──────────────────────────────────────────────────────────┐
   │ 积木面板 ──点一下──▶ 中间列表（有序）──▶ JSON 面板（可直改）│
   │ 结构试跑：选日期 + 档位 → 看"到点会怎么发"                │
   └───────────────────────────┬──────────────────────────────┘
                               │ HTTP（fetch，/api 由 vite 代理到 8000）
   ② API 层（FastAPI，backend/app/duzhan_admin/router.py）
   ┌───────────────────────────▼──────────────────────────────┐
   │ GET  /api/duzhan-agents            列表（含 errors/renderer）│
   │ POST /api/duzhan-agents            新建（默认停用）          │
   │ PUT  /api/duzhan-agents/{id}       更新（不带 enabled 保持原状）│
   │ POST /api/duzhan-agents/{id}/toggle 启用 / 停用（启用前校验） │
   │ POST /api/duzhan-agents/{id}/preview 结构试跑（不取数不发消息）│
   │ POST /api/duzhan-agents/seed-from-code 从代码导入（默认停用） │
   │ GET  /api/duzhan-agents/block-schema  积木面板定义（与校验同源）│
   └───────┬───────────────────────────────┬──────────────────┘
           │                               │
   ③ 服务层 │                       ④ 积木 schema
   ┌───────▼─────────────────┐   ┌─────────▼────────────────────┐
   │ duzhan_admin/service.py │   │ duzhan_blocks.py             │
   │ 解析 / 校验 / 结构试跑   │◀──│ 9 种块定义 + 校验规则 + 面板  │
   │ 从代码导入              │   │ （唯一事实来源）              │
   └───────┬─────────────────┘   └──────────────────────────────┘
           │ SQLModel
   ⑤ 库   ┌▼──────────────────────────────────────────────────┐
          │ duzhan_agents：name / enabled / timezone /          │
          │ blocks_json / note / created_at / updated_at        │
          └─────────┬───────────────────────────────────────────┘
                    │ enabled=true 才注册（按配置注册调度）
   ⑥ 运行时（不在本仓库）
   ┌────────────────▼──────────────────────────────────────────┐
   │ 调度（按 timezone + slots 到点触发）                       │
   │   → 取数（数据源适配器：消息汇总 / 报价图 OCR / IM 留痕 /   │
   │      日报 / 会议纪要）                                     │
   │   → 渲染（复用现有渲染器 group_brief.renderer /            │
   │      follow_brief.renderer，不重写）                       │
   │   → 推送（群 + 额外收件人）→ 记 run 台账（按子 Agent 分档） │
   └───────────────────────────────────────────────────────────┘
```

## 3. 目录结构

```
duzhan-supervisor/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                  FastAPI 入口 + CORS + /health
│  │  ├─ db.py                    SQLite 引擎与会话（DUZHAN_DB 可覆盖）
│  │  ├─ security.py              鉴权占位（默认放行，可开关）
│  │  ├─ duzhan_blocks.py         ★ 9 种积木 + 校验 + 面板定义（唯一事实来源）
│  │  ├─ duzhan_admin/
│  │  │  ├─ router.py             HTTP 接口：CRUD / 启停 / 试跑 / 导入 / 面板
│  │  │  └─ service.py            配置 → 运行时结构、结构试跑、从代码导入
│  │  ├─ adapters/                接你自己系统的三个点（示例数据）
│  │  │  ├─ roster.py             名单：roster_by_group() / roster_names()
│  │  │  ├─ strategies.py         策略清单：strategy_ids()
│  │  │  └─ groups.py             现有硬编码群：GROUPS / CTOB_GROUPS（导入用）
│  │  └─ models/duzhan_agent.py   表模型 duzhan_agents
│  ├─ migrations/versions/001_duzhan_agents.py  生产风格 alembic 版本文件
│  ├─ tests/                      39 个积木/接口单测（见第 4 节）
│  └─ requirements.txt
├─ frontend/
│  ├─ src/pages/DuzhanAgentsPage.vue  配置页：列表 + 积木编辑器 + JSON + 试跑
│  ├─ src/components/BlockPalette.vue 积木面板；BlockCard.vue 单块编辑
│  ├─ src/api/duzhanBlocks.ts         积木纯函数（可 node 直测）
│  ├─ src/api/duzhanAgents.ts         接口封装；client.ts fetch 封装
│  ├─ tests/duzhan-blocks.test.mjs    7 个纯函数单测
│  └─ vite.config.ts                  dev 5173，/api 代理到 127.0.0.1:8000
├─ docs/
│  ├─ architecture.md             分层与数据流、双源灰度、台账、等价性验收
│  ├─ block-schema.md             9 种块逐字段表 + 校验规则清单（以代码为准）
│  └─ quickstart.md               从零到看到界面（含 curl 与常见问题）
├─ examples/                      3 个可直接导入的配置 + 说明
└─ .github/workflows/ci.yml       后端单测 + 前端测试/类型检查/构建
```

## 4. 五分钟跑起来

前置：Python 3.12+（代码用到 `zoneinfo`）、Node 20+（CI 用 22）、能连内网 pip / npm 源。以下命令在仓库根目录开始执行。

```powershell
# ① 后端：装依赖 + 起服务（必须在 backend/ 目录下起，app.main:app 是相对路径）
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

```powershell
# ② 冒烟：另开一个终端
curl.exe http://127.0.0.1:8000/health
# {"ok":true}
```

```powershell
# ③ 前端：装依赖 + 起 dev server（/api 已代理到 127.0.0.1:8000，无需改 baseURL）
cd frontend
npm install
npm run dev
# ➜ http://127.0.0.1:5173
```

打开 `http://127.0.0.1:5173`，点右上角 **从代码导入**：会把示例群（`app/adapters/groups.py` 里那几条）导成配置，**默认停用**；左边选中一条，右边就能看到积木、JSON 和「结构试跑」。

![积木式配置页：左列子 Agent、中间积木、右列 JSON 与结构试跑](docs/screenshots/block-editor.png)

```powershell
# ④ 跑测试（提交前建议全跑一遍）
cd backend
$env:PYTHONPATH='.'                                  # Linux/macOS：export PYTHONPATH=.
python -m unittest discover -s tests -p "test_*.py"  # 39 passed

cd ../frontend
npm test          # 7 个积木纯函数单测
npm run build     # vue-tsc 类型检查 + vite 构建
```

常用环境变量（都有默认值，单机不用设）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `DUZHAN_DB` | `backend/duzhan.db` | SQLite 文件路径；换个路径就换一份配置库 |
| `DUZHAN_REQUIRE_AUTH` | 未设置（放行） | 设为 `1` 后要求 `Authorization: Bearer <token>` |
| `DUZHAN_ADMIN_TOKEN` | 空 | 上面的 token；为空时开了鉴权也一律 401 |

更多细节（curl 三步走、端口冲突、CORS、试跑为什么没有真实数据）见 `docs/quickstart.md`。

## 5. 九种积木一览

积木是**有序数组**，渲染按顺序拼装；一个子 Agent 的配置就是 `{"blocks": [ ... ]}`。

| # | `type` | 名称 | 干什么 | 关键字段 | 硬校验（不满足不给启用） |
| --- | --- | --- | --- | --- | --- |
| 1 | `group` | 目标群 | 这份报告发到哪个群 | `channel_id`(UUID)、`label` | **有且只有一个**；`channel_id` 必须是 UUID |
| 2 | `times` | 推送档位 | 一天推哪几档 | `slots` | 非空、不重复、`HH:MM` 形状、小时只能是 10/15/20；**至少要有一个 times 块** |
| 3 | `people` | 覆盖人员 | 这份报告盯谁 | `names` | 非空；名字必须在督战名单内（不限定就别加这块） |
| 4 | `source` | 数据源 | 这一节取什么数 | `key` | 只能是 `msg_summary` / `quote_ocr` / `im_trace` / `daily_report` / `meeting_notes` |
| 5 | `rule` | 规则 / 动作 | 固定文案，或交给 AI 生成 | `mode`、`text`、`prompt`、`when` | `text` 模式文案非空；`ai` 模式 prompt 必填；`when` 只能是 always/workday/holiday/missing_report |
| 6 | `strategy` | 策略核查 | 挂一条触达策略检查 | `id`、`until` | `id` 必须在策略清单内；`until` 是 `YYYY-MM-DD` 且不能早于今天 |
| 7 | `recipient` | 额外收件人 | 除群之外再抄送给谁 | `kind`、`id` | `kind` 只能是 channel/user；`id` 必须是 UUID |
| 8 | `condition` | 发送条件 | 满足才发 | `key`、`value` | `key` 只能是 workday/holiday/has_data；`value` 必须是 JSON 布尔 |
| 9 | `style` | 样式 | 语言、标题、落款 | `lang`、`title`、`footer` | `lang` 只能是 zh/en；`title` 不能是空字符串（不要就删掉该字段） |

逐字段说明、报错原文与反例见 `docs/block-schema.md`；能直接导入的成品见 `examples/`。

## 6. 安全设计

| 设计 | 落地位置 | 为什么 |
| --- | --- | --- |
| **草稿可存，启用需校验** | `router._guard_enable()`：新建/更新时 `enabled` 为真、或调 `/toggle` 启用时，先跑 `service.validate_agent()`，有错返回 400 `{"detail": {"message": "配置有误，不能启用", "errors": [...]}}` | 坏配置一旦启用，到点推不出去 —— 比不推更糟 |
| **错误随列表返回** | `GET /api/duzhan-agents` 每条带 `errors[]`；前端据此把「启用」按钮置灰 | 让人在点之前就知道哪里不对 |
| **结构试跑不取数、不发消息** | `service.preview_agent()` 只调 `resolve_spec()` + 校验，返回 `summary` 文案；`note` 明写"不取数、不发消息" | 试错成本为零，不需要造数据、更不会误发 |
| **试跑不改配置** | 单测断言试跑前后 `updated_at` 不变（`test_preview_returns_structure_without_sending`） | 试跑是只读操作 |
| **鉴权默认放行、可一键收紧** | `app/security.py`：`DUZHAN_REQUIRE_AUTH=1` + `DUZHAN_ADMIN_TOKEN` 才要求 Bearer；配置接口统一走 `require_admin` 依赖 | demo 开箱能跑，接生产只换这一层 |
| **密钥与库不进 Git** | `.gitignore` 忽略 `*.db` / `.env*` / `node_modules` / `dist` | 配置里可能含群 ID 与名单 |
| **仓库内无真实业务数据** | `app/adapters/` 与 `examples/` 全是占位符（示例群、示例成员、`promo_a` 之类） | 可以公开；真实数据留在你自己的系统里 |

## 7. 与原生产系统的关系

这套东西是从一个真实的群督战系统里"剥"出来的配置层。剥的时候刻意留下三条缝，方便你接自己的系统——**真实数据一行都没进这个仓库**：

| 能力 | 本仓库有什么 | 不在本仓库 | 怎么接 |
| --- | --- | --- | --- |
| 数据源适配器 | 5 个取数 key 的定义与校验（`duzhan_blocks.SOURCE_KEYS`）；试跑只显示"要取哪几节" | 真正取数：消息汇总、报价图 OCR、IM 留痕、日报、会议纪要 | 运行时按 `spec["sources"]` 决定调哪几个取数函数；key 与你的取数函数一一对应 |
| 策略清单 | `app/adapters/strategies.py` 的 `STRATEGIES` / `strategy_ids()`（示例两条） | 真实策略定义、有效期、核查逻辑 | 让 `strategy_ids()` 返回你当下在查的策略 id 即可，`strategy` 块的"在不在清单内"校验自动生效 |
| 名单 | `app/adapters/roster.py` 的 `ROSTER` / `FOLLOW_OWNERS`（示例成员） | 真实员工姓名与组织架构 | 接 HR / 组织架构 / 群成员表，保持 `roster_by_group()`、`roster_names()` 两个函数签名 |
| 真实群映射 | `app/adapters/groups.py` 的 `GROUPS` / `CTOB_GROUPS`（示例群，导入用） | 真实群 ID、群名、语言、时区 | 把 `GROUPS` 换成你的常量表；导入完再把这张表删掉（见 `docs/architecture.md` 的灰度步骤） |
| 渲染器 | 只有渲染器**标识**与选择规则：`group_brief.renderer`（达标群）/ `follow_brief.renderer`（跟进群） | 真实的渲染模板、AI 生成、消息卡片样式 | 沿用你现有的渲染函数，把 `resolve_spec()` 的输出喂进去；不重写渲染（理由见架构文档） |
| 调度与 run 台账 | `enabled` 字段与"启用才注册"的契约 | 定时器、并发锁、按子 Agent 分档的去重台账 | 调度启动时 `SELECT ... WHERE enabled = true` 注册；台账唯一键建议 `(agent_id, 日期, 档位)` |
| 权限 | `app/security.py` 桩：默认放行，`DUZHAN_REQUIRE_AUTH=1` 时校验 Bearer | 你们自己的登录体系 / SSO / 网关注入身份 | 替换 `require_admin` 依赖即可，路由层不用动 |

**已知边界**：本仓库不取数、不渲染、不推送；SQLite 只为演示（换 `DUZHAN_DB` 或直接换 `app/db.py` 里的引擎）；`migrations/versions/001_duzhan_agents.py` 是生产风格的 alembic 版本文件，单机跑服务时并不需要它（启动时 `create_all` 建表）；没有并发锁与多实例协调，那些属于运行时。

License：`MIT License /  / Copyright (c) 2026 Duzhan Supervisor contributors…`（详见 `LICENSE`）。

## 8. 文档与示例

| 文件 | 讲什么 | 什么时候看 |
| --- | --- | --- |
| `docs/quickstart.md` | 从零到看见界面：装依赖、起服务、curl 创建/启用/试跑、常见问题 | 第一次跑 |
| `docs/architecture.md` | 分层与数据流、为什么积木 JSON 是唯一事实来源、code/db 双源灰度、run 台账、等价性验收 | 要接运行时 / 要评审设计 |
| `docs/block-schema.md` | 9 种块逐字段表 + 全部校验规则与报错原文（以 `duzhan_blocks.py` 为准） | 写配置 / 加新积木类型 |
| `examples/README.md`、`examples/*.json` | 3 个开箱可导入的配置（中文达标群日报 / 英文群 / 带 AI 规则块） | 想抄一份能用的 |
| `backend/tests/`、`frontend/tests/`、`.github/workflows/ci.yml` | 39 + 7 条单测；CI 跑后端单测、前端测试、类型检查与构建 | 改代码前 |


