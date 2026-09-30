# 督战官（pdca-workbench）

> 把"群督战"变成配置：在积木式配置页里配好一个子 Agent（群 + 档位 + 名单 + 取数 + 规则 + 收件人 + 渲染器），校验通过、启用之后，调度按这份配置注册采集与推送。
>
> 本目录是督战官的服务端完整项目（FastAPI + SQLModel + APScheduler），MIT 许可，已完成脱敏。

## 1. 模块定位

督战官解决的是"同一件事，一天追三遍"：把**配置**与**执行**分开，配置页说了算，执行仍走原有链路。

配置层做四件事（都在本目录）：

| 职责 | 在哪 | 说明 |
| --- | --- | --- |
| 定义积木 | `app/duzhan_blocks.py` | 积木 schema 是唯一事实来源：类型、字段、枚举、报错文案、面板定义同源 |
| 校验 | `app/duzhan_admin/service.py` | **保存永远允许（草稿）**，**启用必须过校验**；错误逐条返回 `errors[]` |
| 翻译 | `resolve_spec()` | 把积木数组翻译成运行时结构：群 / 档位 / 名单 / 取数 / 规则 / 条件 / 收件人 / 渲染器 |
| 注册 | `app/duzhan_admin/runtime.py` | `PDCA_DUZHAN_CONFIG_SOURCE=db` 时，按启用的子 Agent 注册采集与推送，台账按子 Agent 分档 |

执行层复用本项目现有链路：

- 达标群三追：`app/duzhan.py`（`render_brief` / `run_duzhan`）
- C转B 跟进群：`app/ctob.py`
- 小黑屋（直营门店）开单祝贺：`app/heiwu.py`
- 策略核查（WhatsApp 口径抽查）：`app/strategy_wa_brief.py` + `app/wa_strategies.json`
- 调度与防重复发：`app/scheduler/`（`run_ledger.py` 按 `(agent_id, 日期, 档位)` 占位）

对外只有两个出口：**取数 / 发消息走命令行 IM CLI**（`VERTU_COMMAND`），**数据服务走 MCP**（`PDCA_AISALES_MCP_URL`）。本项目不直连任何 IM 数据库。

## 2. 目录结构

| 路径 | 作用 |
| --- | --- |
| `run.py` | 生产启动入口：用 uvicorn 起 `app.main:app`，host / port 读配置 |
| `app/main.py` | FastAPI 应用：`/healthz` 探针 + `/api/duzhan-agents`（配置台在仓库根 `frontend/`） |
| `app/config.py` | 全部环境变量与默认值（`Settings`），启动时读本目录的 `.env` |
| `app/database.py` | PostgreSQL / SQLite 引擎与会话 |
| `app/duzhan.py` | 达标群三追：群清单 `GROUPS`、组表、渲染、推送 |
| `app/duzhan_ledger.py` | 达标群名单 `OWNERS`（`display` / 群名）与台账 |
| `app/duzhan_blocks.py` | 积木 schema / 解析 / 校验（唯一事实来源） |
| `app/duzhan_admin/` | 配置 API 与服务层：`router.py` / `service.py` / `runtime.py` / `ai_rules.py` |
| `app/ctob.py` | C转B 跟进群：群主清单 `OWNERS`、渲染、推送 |
| `app/heiwu.py` | 小黑屋开单祝贺轮询 |
| `app/strategy_wa_brief.py` | 策略核查；策略清单读 `app/wa_strategies.json` |
| `app/workday_calendar.py` | 工作日 / 节假日判定（配 `app/holidays_cn.json`） |
| `app/meeting_todos.py` | 早会待办抓取与落库 |
| `app/mto_ocr.py` | MTO 报价图 OCR（走视觉模型网关） |
| `app/im_files.py` | IM 附件落盘与清理 |
| `app/vps_im_push.py` | 群消息推送封装 |
| `app/agents/` | 子 Agent 运行时：流程控制、群上下文、LLM 客户端 |
| `app/auth/` | 身份与权限：JWT / 反代 Header / 角色校验（`require_role`） |
| `app/scheduler/` | 定时任务注册与 run 台账 |
| `app/models/` | SQLModel 表定义（含 `duzhan_agent.py`） |
| `app/alerting.py` | 告警出口 |
| `tests/` | 督战官单测（14 个 `test_*.py` 文件） |
| `migrations/versions/` | alembic 版本文件（含 `018_duzhan_agents.py`） |
| `requirements.txt` | 后端依赖 |
| `.env.example` | 环境变量示例，复制成 `.env` 后按第 6 节填 |

> 仓库根的 `frontend/` 是**另一份**独立的 Vue 3 + Vite 配置台，见第 7 节。

## 3. 依赖与安装

- Python 3.12+（代码用到标准库 `zoneinfo`）
- 数据库：PostgreSQL（生产）或 SQLite（本地）

```powershell
cd pdca-workbench
python -m pip install -r requirements.txt
Copy-Item .env.example .env      # 然后按第 6 节填
```

`.env` 在 `app/config.py` 导入时读取（`load_dotenv(APP_ROOT / ".env")`），改完要重启进程。

数据库连接串 `PDCA_DATABASE_URL`：

- 本地最省事：`sqlite:///./data/duzhan.db`（相对 `pdca-workbench/`，文件落在 `data/`）
- 生产：`postgresql+psycopg2://<user>:<password>@<host>:5432/<db>`
- 不填会走代码里的默认本机 PostgreSQL 连接串；连不上时引擎会退回本地 SQLite（`data/pdca_local.sqlite`）

## 4. 跑测试

```powershell
cd pdca-workbench
python -m unittest discover -s tests -p "test_*.py"
```

- 覆盖：积木解析 / 校验、配置 API 的 CRUD 与启用闸门、结构试跑、从代码导入、调度注册与 run 台账，以及三追 / C转B / 小黑屋 / 策略核查的现有行为。
- 全部离线：不连网、不推送、不读真实数据，也不需要额外的数据库服务。
- 仓库根 `frontend/` 还有前端单测（积木纯函数）：`cd frontend && npm test`。

## 5. 启动

```powershell
cd pdca-workbench && python run.py
```

- 服务入口是 `app.main:app`；`run.py` 读 `.env` 里的 `PDCA_HOST` / `PDCA_WORKBENCH_PORT`（默认 `0.0.0.0:8767`），单进程（`workers=1`、`reload=False`）。
- **必须在 `pdca-workbench/` 目录下起**：应用路径 `app.main:app` 是相对的，`app/` 与 `frontend/` 都按这个工作目录解析。
- 存活探针：`curl.exe http://127.0.0.1:8767/healthz` → `{"status":"ok","duzhan_enabled":…,"ctob_enabled":…,"heiwu_enabled":…}`（只报开关，不泄露密钥）。
- 启动时会**注册调度**（`app/scheduler/`）。调度器起不来不影响 API，只在日志与告警里体现。
- 启动时会调 `app/database.py` 的 `bootstrap_database()`（内部 `SQLModel.metadata.create_all`）把表建好；起库失败只告警、不拦住 API。生产库仍建议用 `migrations/versions/018_duzhan_agents.py` 走 alembic 迁移。
- 想在改代码时自动重载，可临时用：`uvicorn app.main:app --reload --port 8767`。

## 6. `.env` 主要开关

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `PDCA_DUZHAN_ENABLED` | `0` | 达标群三追总开关：`0` 只采集不推群，`1` 按档推群 |
| `PDCA_DUZHAN_TIMES` | `10:00,15:00,20:00` | 推送档位（逗号分隔）。引擎只认 `10:00/15:00/20:00`，写别的会被积木校验挡下 |
| `PDCA_DUZHAN_LEAD_MINUTES` | `15` | 每档提前多少分钟采集组表（代码里夹在 5–60）；推送时只读快照 |
| `PDCA_DUZHAN_CONFIG_SOURCE` | `code` | 群清单从哪来：`code` 用 `app/duzhan.py` 的 `GROUPS`（现状）；`db` 用配置页里**启用**的子 Agent |
| `PDCA_CTOB_ENABLED` | 跟随 `PDCA_DUZHAN_ENABLED` | C转B 跟进群三追开关；默认值跟督战开关走（没设时等于督战开关的值） |
| `PDCA_HEIWU_ENABLED` | `0` | 小黑屋（直营门店）开单祝贺机器人；机器人凭证没配时整条链路安静跳过 |
| `PDCA_DUZHAN_STRICT_HOLIDAY_CHANNELS` | 空 | 节假日也照常催的群（逗号分隔的群 ID，读自 `app/duzhan.py`）；留空 = 长假期间默认免罚 |
| `PDCA_SCHEDULER_ENABLED` | `1` | 调度器总开关：`0` 时不注册任何定时任务，只留 API 与配置页 |

其余常改的：`PDCA_HOST` / `PDCA_WORKBENCH_PORT`（默认 `0.0.0.0:8767`）、`PDCA_SECRET_KEY`（生产必填）、`PDCA_CORS_ORIGINS`、`PDCA_DATABASE_URL`、`VERTU_COMMAND`（IM CLI 命令名）、`PDCA_DATA_DIR`（默认 `./data`）、`PDCA_LOG_LEVEL`。完整清单见 `.env.example` 与 `app/config.py`。

> 切到 `PDCA_DUZHAN_CONFIG_SOURCE=db` 之前，先确认库里该启用的子 Agent 都已启用——否则会出现"一条都不发"，日志里会有明确告警。

## 7. 积木配置入口

配置台在仓库根的 `frontend/`（独立 Vue 3 + Vite 应用，调的就是本目录这一套接口）：

```powershell
cd frontend
npm install
npm run dev        # http://127.0.0.1:5173，/api 由 vite 代理到 127.0.0.1:8767
```

后端只提供 API 与 `/healthz`；配置台可以独立部署，也可以自己照着 `app/duzhan_admin/router.py` 的契约写一个。

配置接口（前缀 `/api/duzhan-agents`，定义在 `app/duzhan_admin/router.py`）：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/block-schema` | 积木类型定义（面板与校验同源） |
| GET | `/api/duzhan-agents` | 子 Agent 列表（每条带 `errors` 与 `renderer`） |
| POST | `/api/duzhan-agents` | 新建（默认停用） |
| PUT | `/{agent_id}` | 更新 |
| POST | `/{agent_id}/toggle` | 启用 / 停用（启用要过校验） |
| POST | `/{agent_id}/preview` | 试跑：结构预览 / 用快照渲染的真实内容（都不发消息） |
| DELETE | `/{agent_id}` | 删除 |
| POST | `/seed-from-code` | 从代码里现有的群清单导入配置 |
| GET | `/ai-status` | AI 规则今日用量 |

两个容易踩的点：

- **端口要对齐**：`run.py` 默认 `8767`，`frontend/vite.config.ts` 的代理默认也指向 `8767`；改了一边就要改另一边（或用 `DUZHAN_API_TARGET` 覆盖代理目标）。
- **写操作要 admin**：`require_admin = require_role("admin")`（`app/auth/deps.py`）。单测里用 `dependency_overrides` 覆盖它，真实调用要带登录态。

字段、枚举与报错文案逐条对照见 [`../docs/block-schema.md`](../docs/block-schema.md)；可粘贴的现成配置见 [`../examples/`](../examples/)。

## 8. 从代码导入与灰度

`code` 与 `db` 两个配置源会在一段时间内并存，切换步骤如下（每一步都可停）：

1. 配置页点「从代码导入」（`POST /api/duzhan-agents/seed-from-code`）→ 得到一批**停用**的配置（同名会跳过，不覆盖你手改过的）；
2. 逐条看列表里的 `errors`（应为空）→ 用「试跑」核对群 / 档位 / 人员 / 取数 / 渲染器；
3. 做等价性验收：同一时间、同一份数据，配置生成的报告与硬编码生成的报告应逐字一致；
4. 把 `PDCA_DUZHAN_CONFIG_SOURCE` 切成 `db`，同时关掉代码里那条群的硬编码发送（避免双跑），再把对应配置启用，观察一个完整档位；
5. 全部群切换完，`code` 源就可以退休了——`GROUPS` / `ctob.OWNERS` 与「从代码导入」都是过渡工具，不是长期依赖。

回滚随时可做：`POST /api/duzhan-agents/{id}/toggle {"enabled": false}`（停用不校验）。分层边界与验收清单见 [`../docs/architecture.md`](../docs/architecture.md)。

## 9. 脱敏说明

本仓库是脱敏后的开源版（MIT）。人名、群 ID、内部域名、工号等已全部替换为占位值：

| 类别 | 现在的样子 | 在哪 |
| --- | --- | --- |
| 群 ID | 形如 `11111111-1111-4111-8111-111111111111` 的占位 UUID | `app/duzhan.py` 的 `GROUPS`、`app/ctob.py` 的 `OWNERS`、`../examples/*.json` |
| 人名 | `成员A`、`跟进人01` 这类占位名 | `app/duzhan_ledger.py` 的 `OWNERS`、`app/ctob.py` 的 `OWNERS` |
| 策略 id | `sample-*` / `demo-*` / `quota` 这类占位 id | `app/wa_strategies.json` |
| 域名 | 一律 `example.com` | `app/config.py`、`.env.example` |
| 工号 / 内部用户 ID | 占位整数 | `app/duzhan_ledger.py`、`app/ctob.py` |

**真实接入需要自备**：

1. **IM CLI**：`VERTU_COMMAND` 指向你自己的命令行工具（取群消息、发群消息）。本项目不直连任何 IM 数据库。
2. **MCP 数据服务**：`PDCA_AISALES_MCP_URL` / `PDCA_AISALES_MCP_TOKEN` 等指向你的数据服务；没配的取数节会被跳过。
3. **数据库**：`PDCA_DATABASE_URL` 指到你自己的 PostgreSQL（本地可用 SQLite 起步）。
4. **大模型（可选）**：子 Agent 的 AI 规则块走 `PDCA_SUPERVISOR_PROVIDER` / `PDCA_SUPERVISOR_MODEL` / `PDCA_SUPERVISOR_API_KEY`；MTO 报价图 OCR 走 `PDCA_QWEN_BASE_URL` / `PDCA_QWEN_API_KEY` / `PDCA_QWEN_MODEL`。留空则 AI 规则块不调用模型。
5. **机器人凭证**：`PDCA_DUZHAN_BOT_APP_ID` / `PDCA_HEIWU_BOT_APP_ID` 等，用你自己 IM 平台的机器人；没配就整条链路安静跳过。

约定：

- 仓库里不含生产数据、导出文件、浏览器验收产物或密钥；`.env` 与 `data/` 已在 `.gitignore` 里忽略。
- 提交前自查：别把你的真实群 ID、真实名单、内部域名写回常量或示例 JSON。
- 示例数据只是"能跑通结构"的最小集合，与你自己的业务口径无关。

## 10. 相关文档

| 文档 | 讲什么 |
| --- | --- |
| [`../docs/architecture.md`](../docs/architecture.md) | 分层、一次配置改动的数据流、code / db 双源灰度、台账与逐字等价性验收 |
| [`../docs/block-schema.md`](../docs/block-schema.md) | 积木字段与报错文案（逐条对照 `app/duzhan_blocks.py`） |
| [`../docs/quickstart.md`](../docs/quickstart.md) | 从零起服务、打开界面、导入示例配置、用 curl 走一遍 |
| [`../examples/README.md`](../examples/README.md) | 三份可直接粘贴的示例配置与占位符换算 |

License: MIT（见仓库根 `LICENSE`）。