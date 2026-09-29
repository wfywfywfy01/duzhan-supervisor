# 快速开始：从零到看见界面

目标：5 分钟内起好后端 + 前端，打开配置页，导入一份示例配置、试跑一次、再用 curl 走一遍。全程只读本仓库，不需要任何外部系统。

## 0. 你需要什么

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.12+ | 代码用到标准库 `zoneinfo`；CI 用 3.12 |
| Node | 20+（CI 用 22） | 前端用 Vite 7 + Vue 3.5 |
| 端口 | 8000（后端）、5173（前端） | 都能改，见第 6 节 |
| 终端 | PowerShell 7 / bash / zsh 均可 | 下面命令按仓库根目录为起点写 |

## 1. 起后端（约 1 分钟）

```powershell
cd backend
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

看到 `Application startup complete.` 就成了（启动时会自动建表）。**必须在 `backend/` 目录下起**，因为应用路径是相对的 `app.main:app`。

另开一个终端冒烟：

```powershell
curl.exe http://127.0.0.1:8000/health
# {"ok":true}
```

数据库默认落在 `backend/duzhan.db`（SQLite）。想换一份干净的库：

```powershell
$env:DUZHAN_DB = "$env:TEMP\duzhan-demo.db"    # bash: export DUZHAN_DB=/tmp/duzhan-demo.db
```

## 2. 起前端（约 1 分钟）

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。前端用相对路径请求 `/api/…`，由 Vite 代理到 `http://127.0.0.1:8000`（`vite.config.ts` 的 `server.proxy`），所以**不需要配 baseURL，也不会有跨域问题**。

## 3. 界面 60 秒上手

1. 点右上角 **从代码导入** → 生成示例群的配置（全部**停用**），列表里能看到「停用」标签；
2. 点左边任意一条 → 中间是积木（可上移/下移/删除），右边是 JSON 与「结构试跑」；
3. 点 **试跑** → 输出一段"到点会怎么发"的清单：发送到哪个群、档位、试跑日期、覆盖人员、数据源、规则、触发条件、额外收件人、策略核查、渲染器；
4. 故意写坏一条：把 `slots` 里的时刻改成 `12:00` → 保存 → 顶部提示「已存草稿，但配置还有 1 处问题，暂时不能启用」，列表出现「1 处问题」标签，**启用按钮变灰**；
5. 改回 `10:00` 保存 → 启用按钮可用 → 点「启用」→ 提示「已启用，调度会按这份配置注册」。

> 说明：这个仓库没有调度器，所以"启用"只是把 `enabled` 置为真、让下游可以按库注册；本仓库不会真的发消息。

## 4. 用 curl 走一遍（不开界面也能验）

以下命令在**仓库根目录**执行。先拼一个请求体（把 `examples/01-zh-daily-brief.json` 的积木塞进 `blocks`）：

```powershell
# ① 生成请求体
$blocks  = (Get-Content -Raw -Encoding utf8 .\examples\01-zh-daily-brief.json | ConvertFrom-Json).blocks
$payload = @{ name = '示例达标群 A 日报'; timezone = 'Asia/Shanghai'; note = 'curl 建的'; blocks = $blocks } | ConvertTo-Json -Depth 12
Set-Content -Path .\payload.json -Value $payload -Encoding utf8NoBOM   # PS 5.1 请改用 Invoke-RestMethod（见文末）

# ② 创建：默认停用，返回体带 id / enabled=false / errors=[]
curl.exe -s -X POST http://127.0.0.1:8000/api/duzhan-agents ^
  -H "Content-Type: application/json" --data-binary "@payload.json"
```

预期返回（节选）：

```json
{
  "id": 1,
  "name": "示例达标群 A 日报",
  "enabled": false,
  "timezone": "Asia/Shanghai",
  "blocks": { "blocks": [ { "type": "group", "channel_id": "11111111-1111-4111-8111-111111111111", "label": "示例达标群 A" }, … ] },
  "errors": [],
  "renderer": "group_brief.renderer",
  "updated_at": "2026-09-29T09:20:00+00:00"
}
```

```powershell
# ③ 启用（id 用上一步返回的）
'{"enabled": true}' | Set-Content -Path .\toggle.json -Encoding utf8NoBOM
curl.exe -s -X POST http://127.0.0.1:8000/api/duzhan-agents/1/toggle ^
  -H "Content-Type: application/json" --data-binary "@toggle.json"
# {"id":1,…,"enabled":true,…}

# ④ 结构试跑（不取数、不发消息；day/hour 都可省）
'{"day": "2026-09-29", "hour": 20}' | Set-Content -Path .\preview.json -Encoding utf8NoBOM
curl.exe -s -X POST http://127.0.0.1:8000/api/duzhan-agents/1/preview ^
  -H "Content-Type: application/json" --data-binary "@preview.json"
```

试跑返回里的 `summary` 就是界面上那几行：

```text
发送到：示例达标群 A（11111111-1111-4111-8111-111111111111）
档位：20:00（Asia/Shanghai）
试跑日期：2026-09-29
覆盖人员：示例成员A、示例成员B、示例成员C
数据源：消息汇总（触达 / 回复 / 新增） → 报价图 OCR 识别 → 日报提交情况
规则（always）：本档动作：核验交付物与证据，未交的今天内补齐。
触发条件：仅工作日 = 是
渲染器：group_brief.renderer
```

```powershell
# ⑤ 列表：每条都带 errors 与 renderer
curl.exe -s http://127.0.0.1:8000/api/duzhan-agents

# ⑥ 反例：坏配置能存草稿，但启用会被 400 挡下
$bad = @{ name = '反例'; blocks = @{ blocks = @(
  @{ type = 'group'; channel_id = 'not-a-uuid' },
  @{ type = 'times'; slots = @('12:00') }
) } } | ConvertTo-Json -Depth 12
Set-Content -Path .\bad.json -Value $bad -Encoding utf8NoBOM
curl.exe -s -X POST http://127.0.0.1:8000/api/duzhan-agents -H "Content-Type: application/json" --data-binary "@bad.json"
# 200 + errors: ["第 1 块 group：channel_id 必须是群 UUID，当前为 not-a-uuid", "第 2 块 times：当前引擎只支持 10:00/15:00/20:00，不支持 12:00"]

'{"enabled": true}' | Set-Content -Path .\toggle.json -Encoding utf8NoBOM
curl.exe -s -X POST http://127.0.0.1:8000/api/duzhan-agents/2/toggle -H "Content-Type: application/json" --data-binary "@toggle.json"
# 400 {"detail": {"message": "配置有误，不能启用", "errors": [ … ]}}
```

> Windows PowerShell 5.1 用户：`-Encoding utf8NoBOM` 不存在，`curl.exe` 的 JSON 引号也容易被吃掉。直接用 `Invoke-RestMethod` 更省事：
>
> ```powershell
> $payload = @{ name='示例达标群 A 日报'; timezone='Asia/Shanghai'; blocks=(Get-Content -Raw -Encoding utf8 .\examples\01-zh-daily-brief.json | ConvertFrom-Json).blocks } | ConvertTo-Json -Depth 12
> Invoke-RestMethod -Uri http://127.0.0.1:8000/api/duzhan-agents -Method Post -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload))
## 5. 跑测试

```powershell
# 后端：39 条单测（积木解析/校验 + API CRUD/闸门/试跑/导入 + 模型）
cd backend
$env:PYTHONPATH='.'                                  # bash: export PYTHONPATH=.
python -m unittest discover -s tests -p "test_*.py" -v

# 前端：7 条积木纯函数单测 + 类型检查 + 构建
cd ..\frontend
npm test
npm run build
```

测试都是离线的：不连网、不推送、不读真实数据。

## 6. 常见问题

**Q1. 端口被占（`Address already in use`）**
换端口即可，两边要对应：

```powershell
uvicorn app.main:app --reload --port 8010
# 前端：改 vite.config.ts 的 server.port 与 proxy['/api'].target，或临时
# npm run dev -- --port 5174
```

**Q2. 页面打得开但列表一直报错 / 404（跨域、CORS）**
前端请求的是相对路径 `/api/…`，只有两种情况会出问题：
1) 没走 Vite 而是直接开 `dist/index.html`（`file://`）——没有代理，必然失败；请用 `npm run dev`，或把 `dist` 交给后端同源托管。
2) 改了前端端口（比如 5174），但后端 CORS 白名单只有 `http://127.0.0.1:5173` 与 `http://localhost:5173`（`app/main.py`）。往 `allow_origins` 里加上你的地址即可。注意 `allow_credentials=True` 时**不能用** `*`。

**Q3. 试跑为什么没有真实数据、也不发消息？**
结构试跑（`POST /{id}/preview`）按设计**只做两件事**：把配置翻译成运行时结构（`resolve_spec()`）、跑一遍校验；返回的 `summary` 是"到点会怎么发"的说明，不是报告正文。返回体里 `note` 明写：`结构试跑：只解析配置与校验，不取数、不发消息。` 想看到真实报告，得由运行时（不在本仓库）按这份结构去取数、渲染、推送。

**Q4. 保存成功但「启用」是灰的 / 启用返回 400**
说明配置有错误：看列表项上的「N 处问题」或响应里的 `errors[]` / `detail.errors[]`，逐条对照 `docs/block-schema.md` 第 12 节的排错速查。

**Q5. 报「不在督战名单内 XXX」**
`people.names` 必须与名单里的名字完全一致（`app/adapters/roster.py` 的 `display`）。另外名字是按 **群** 取的：先拿 `group.label` 去 `roster_by_group()` 找该群成员，找不到才退回全量名单 `roster_names()`。所以 `label` 写错也可能导致"明明在名单里却报不在"。

**Q6. 报「策略 X 不在当前策略清单内」**
策略 id 必须来自 `app/adapters/strategies.py` 的 `STRATEGIES`（示例是 `promo_a` / `promo_b`）。另一种情况：策略清单读取失败时 `service.strategy_ids()` 会返回空列表，于是**所有**策略块都会报这一条——这是"清单不可信就不许启用"的保守设计。

**Q7. 报「策略 X 已于 … 到期」**
`until` 早于今天（Asia/Shanghai）就会命中；当天不算过期。改期或删掉这块。

**Q8. 从代码导入的配置为什么都是停用的？**
刻意的：`seed_from_code()` 一律 `enabled=False`，"避免导入即双跑"。核对（试跑 + 逐字等价性验收）之后再逐条启用；同名配置会被跳过并在 `skipped` 里返回，不会覆盖你手改过的配置。

**Q9. 改了代码不生效**
后端开了 `--reload` 会自动重载；前端 Vite 会热更新。库里已有数据不受影响（改 schema 要重建库或写迁移）。浏览器缓存可以强刷一次。

**Q10. 返回 401 未登录**
有人打开了鉴权开关：`DUZHAN_REQUIRE_AUTH=1` 时必须带 `Authorization: Bearer <DUZHAN_ADMIN_TOKEN>`，且 token 不能为空。单机 demo 不设这两个变量就是默认放行。

**Q11. 数据存在哪？怎么重置？**
默认 `backend/duzhan.db`（SQLite，`app/db.py`）。停掉服务、删掉这个文件、重启即可回到空白库；也可用 `DUZHAN_DB` 指到别处。生产请用 `migrations/versions/001_duzhan_agents.py` 这份 alembic 版本文件建表。

**Q12. 中文在 Windows 控制台显示乱码**
那是终端编码，不是数据问题（库与接口都是 UTF-8）。`chcp 65001` 或改 PowerShell 的输出编码即可；写文件时用 `-Encoding utf8NoBOM`，避免 BOM 让 JSON 解析失败。

## 7. 清理

```powershell
# 停掉 uvicorn / vite（Ctrl+C），然后：
Remove-Item .\payload.json, .\toggle.json, .\preview.json, .\bad.json -ErrorAction SilentlyContinue
Remove-Item .\backend\duzhan.db -ErrorAction SilentlyContinue   # 想留配置就别删
```

> ```
