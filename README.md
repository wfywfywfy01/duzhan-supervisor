# 督战官（Duzhan Supervisor）

多群、多智能体的销售督战系统：按固定档位把"谁在动、谁没动、卡在哪"直接推到达标群和跟进群，
并把每档的群原话、MTO 报价图、日报、会议待办留成可复核的证据快照。

本仓库是内部生产项目 **整份脱敏开源版**：代码结构与线上一致（`pdca-workbench/` 即线上服务本体），
只替换了人员姓名、群 ID、内部域名、工号与业务策略数据。

## 它解决什么问题

- **达标群三追**：每天 10:00 / 15:00 / 20:00 三个档位，按"早追·定任务 → 午追·看进度 → 晚追·要结果"
  推送组表：每人 MTD 业绩、过程动作、卡点原话、MTO 报价、当日待办。群时区不同就按各自当地时间推。
- **C转B 跟进群**：15 个跟进群的负责人各有自己的群，同一套积木配置按群生成不同口径的跟进表。
- **小黑屋开单祝贺**：群里有人晒单，机器人自动回一条祝贺（同一条只回一次）。
- **策略核查**：每天 08:00 抽查群聊里前 24 小时的策略口径执行情况，出 HTML 报告并发管理群。
- **积木式配置台**：不改代码就能给每个群/每个子 Agent 配"追哪个群、什么时间、盯谁、看哪些数据源、
  用什么规则、发什么话术"，配置即数据，落库后由调度器注册任务。
- **证据链**：每个档位先采集落快照（群消息、MTO 原图与 OCR、日报、会议待办），推送时只读快照——
  群里发出去的每个数字都能回溯到原始证据。

## 目录结构

```
pdca-workbench/           督战官服务本体（FastAPI + SQLModel + APScheduler）
├─ app/
│  ├─ duzhan.py           达标群三追：取快照、渲染组表、推群
│  ├─ duzhan_ledger.py    台账与人员花名册：业绩/过程/卡点/MTO/日报的采集与口径
│  ├─ duzhan_blocks.py    积木式配置的 schema 与校验（9 类积木）
│  ├─ duzhan_admin/       配置 API、db 配置翻译、调度注册、AI 规则生成
│  ├─ ctob.py             C转B 跟进群
│  ├─ strategy_wa_brief.py 策略口径核查
│  ├─ heiwu.py            开单祝贺机器人
│  ├─ vps_im_push.py      IM 推送（机器人身份 / 用户身份两条通道）
│  ├─ workday_calendar.py 工作日/调休/长假判定
│  ├─ meeting_todos.py    早会待办
│  ├─ mto_ocr.py          MTO 报价图 OCR（视觉模型）
│  ├─ agents/             档位流程控制（采集 → 渲染 → 推送 → 落台账）
│  ├─ scheduler/          调度器与任务台账
│  └─ models/             数据表
├─ tests/                 督战官测试（unittest）
├─ migrations/            alembic 迁移
├─ run.py                 启动入口（uvicorn）
└─ .env.example           环境变量示例
frontend/                 积木式配置台（Vue 3 + Vite，独立部署）
docs/                     架构、积木 schema、快速开始
pdca-workbench/docs/      督战官实现说明、多智能体督战系统实施规格
examples/                 示例积木配置（JSON）
```

## 快速开始

```bash
cd pdca-workbench
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                              # 按自己环境填写
python -m unittest discover -s tests -p "test_*.py"   # 跑测试
python run.py                                     # 启动服务（默认 8767）
```

配置台：

```bash
cd frontend
npm install
npm run dev      # 本地开发；接口默认打到同源 /api/duzhan-agents
```

## 积木式配置

一个子 Agent = 一组积木（存在 `duzhan_agents` 表里，启用后由调度器注册任务）：

| 积木 | 说明 | 可重复 |
| --- | --- | --- |
| group | 追哪个群（唯一） | 否 |
| times | 每天哪几档（10:00 / 15:00 / 20:00） | 否 |
| people | 盯哪些人；留空=全群 | 否 |
| source | 看哪些数据源（群摘要 / 报价 OCR / IM 轨迹 / 日报 / 会议待办） | 是 |
| rule | 这一档要做什么（自然语言规则） | 是 |
| strategy | 引用策略核查里的哪条策略 | 是 |
| recipient | 额外收件人（私聊/管理群） | 是 |
| condition | 触发条件（工作日 / 有数据 / 有卡点） | 是 |
| style | 语言与标题（中文 / 英文） | 否 |

开关：`PDCA_DUZHAN_CONFIG_SOURCE=code` 用代码里写死的群，`=db` 用配置台里启用的子 Agent。

## 脱敏说明

- 人员姓名、群 ID、会话 ID、IM 用户号、工号、内部域名、机器人凭据、月目标与促销策略数据
  **全部替换为占位值**（示例：`张三` / `Alice` / `11111111-1111-4111-8111-111111111111` / `vps.example.com`）。
- `app/wa_strategies.json`、`app/meeting_todos.json`、`app/monthly_sales_targets.json` 都是**虚构示例数据**，
  只为跑通链路与测试；换成你自己的数据即可生效。
- 代码里通过命令行工具（`VERTU_COMMAND`）读取群消息、通过模型网关做 OCR/文案，
  这两处都需要你自备：开源版不含任何真实凭据，也没有可用的生产地址。

## 许可

MIT，见 `LICENSE`。
