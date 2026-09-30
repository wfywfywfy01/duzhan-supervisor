# 示例配置

三份可以直接用的配置，覆盖"中文达标群日报 / 英文群 / 带 AI 规则块"三种典型场景。**全部是占位数据**：群 ID、成员名、策略 id 都是占位值，和 `pdca-workbench/app/` 里的演示常量（`duzhan.py` 的 `GROUPS`、`duzhan_ledger.py` / `ctob.py` 的 `OWNERS`、`wa_strategies.json` 的策略清单）同属一套"示例数据"，换成你自己的系统时记得一起换掉。

## 1. 三份示例一览

| 文件 | 场景 | 积木顺序 | 渲染器 | 看点 |
| --- | --- | --- | --- | --- |
| `01-zh-daily-brief.json` | 中文达标群日报（示例达标群 A） | group → times → people → source ×3 → rule → condition → style | `duzhan.render_brief` | 最典型的"一天三追"：三档、限定 3 人、三种取数、工作日才发 |
| `02-en-group.json` | 英文群（示例达标群 B） | group → times → people → source ×2 → condition → style | `duzhan.render_brief` | 英文文案（`lang: en`）+ 两档 + "仅当日有数据"才发 |
| `03-ai-rule-missing-report.json` | 跟进群 + AI 规则块（示例跟进群 1） | group → times → people → source ×2 → rule ×2 → strategy → recipient → condition → style | `ctob.render_brief` | 固定文案 + `missing_report` 的 AI 催促、策略核查、额外收件人 |

三份共同点：都是 `{"blocks": [ … ]}` 结构，`blocks` 有序；每份都只有一个 `group` 块、一个 `times` 块、一个 `style` 块。

## 2. 怎么用

**方式 A：界面粘贴（最快）**
1. 起好后端与前端（见 `docs/quickstart.md`）；
2. 配置页 → **新建子 Agent** → 填写名称；
3. 把某个示例 JSON 全文粘到右侧 **JSON** 文本框 → 点 **按 JSON 覆盖** → 中间积木列表会同步出现；
4. **保存**（此时是草稿）→ 看右侧「结构试跑」→ 没问题再点**启用**。

**方式 B：curl / Invoke-RestMethod 建**（写接口要 admin 身份，见 `docs/quickstart.md` 第 4 节）

```powershell
# 仓库根目录执行：把示例积木塞进请求体再 POST
$blocks  = (Get-Content -Raw -Encoding utf8 .\examples\01-zh-daily-brief.json | ConvertFrom-Json).blocks
$payload = @{ name = '示例达标群 A 日报'; timezone = 'Asia/Shanghai'; note = 'examples 导入'; blocks = $blocks } | ConvertTo-Json -Depth 12
Set-Content .\payload.json -Value $payload -Encoding utf8NoBOM
curl.exe -s -X POST http://127.0.0.1:8767/api/duzhan-agents -H "Content-Type: application/json" --data-binary "@payload.json"
```

**方式 C：先导后改**

点「从代码导入」生成示例群配置，再照着这三个示例往上加块（AI 规则、策略核查、额外收件人），比从空白开始快。

## 3. 前置换算：示例里的占位符对应哪份演示数据

| 示例里的值 | pdca-workbench 里的同类演示数据 | 换成你的系统时 |
| --- | --- | --- |
| `11111111-1111-4111-8111-111111111111`（示例达标群 A） | `app/duzhan.py` 的 `GROUPS`（同款占位群 UUID） | 换成你的群 UUID；`group.label` 建议与该群在名单里的群名一致 |
| `22222222-2222-4222-8222-222222222222`（示例达标群 B，`Europe/Paris` + `en`） | 同上；语言 / 时区由配置自己写，不必和 `GROUPS` 里的值一致 | 同上 |
| `33333333-3333-4333-8333-333333333333`（示例跟进群 1） | 占位 UUID；跟进群集合来自 `app/ctob.py` 的 `OWNERS`（`channel_id`） | 换成你的跟进群 UUID（落在跟进群集合里才会走 `ctob.render_brief`） |
| `示例成员A/B/C、示例成员D/E、示例跟进人1` | 达标群名单在 `app/duzhan_ledger.py` 的 `OWNERS`，跟进群主在 `app/ctob.py` 的 `OWNERS`（字段都叫 `display`） | 换成真实名单里的 `display`；名字必须完全一致 |
| `promo_a` | 占位策略 id；真实清单在 `app/wa_strategies.json`（由 `app/strategy_wa_brief.py` 的 `load_strategies()` 读取） | 换成你当前的策略 id |
| `55555555-5555-4555-8555-555555555555`（额外收件人） | 纯占位 UUID | 换成真实收件人 ID（`kind` 为 `user` 或 `channel`） |

> 换名字之后如果报「不在督战名单内」，说明还没同步改名单常量（`app/duzhan_ledger.py` / `app/ctob.py`）——这是设计使然：名单只有一份，配置里不另存。

## 4. 怎么核对这三份示例是合法的

方式一：直接跑仓库自己的测试（会覆盖校验规则）：

```powershell
cd pdca-workbench
python -m unittest discover -s tests -p "test_*.py"
```

方式二：拿下面这段对着示例文件跑一遍（只读；`owners=None` 跳过名单、`strategies=None` 跳过策略清单，再各跑一次带名单/清单的）：

```python
import json, pathlib, sys
sys.path.insert(0, "pdca-workbench")
from app.duzhan_blocks import parse_blocks, validate_blocks
from app.duzhan_admin import service
from app.models.duzhan_agent import DuzhanAgent

for path in sorted(pathlib.Path("examples").glob("*.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    blocks = parse_blocks(data)
    offline = validate_blocks(blocks, owners=None, strategies=None)      # 只校验结构与枚举
    agent = DuzhanAgent(name=path.stem, blocks_json=json.dumps(data, ensure_ascii=False))
    online = service.validate_agent(agent)                               # 再叠上名单 + 策略清单
    print(f"{path.name}: 结构 {offline or 'OK'} | 接名单/策略 {online or 'OK'}")
# 预期：三行全是 OK
```

方式三（最接近真实链路）：用 FastAPI 的 `TestClient` 起内存库，POST 建配置 → `toggle` 启用 → `preview` 试跑，断言 `errors == []`。仓库单测 `pdca-workbench/tests/test_duzhan_agents_api.py` 就是这套写法，可以直接照抄。

## 5. 实测结果（写文档时跑过）

三份示例在随仓库提供的演示数据上都是：创建 200 且 `errors: []` → 启用 200 → 试跑 200，试跑摘要分别为：

```text
01：发送到：示例达标群 A（11111111-…） / 档位：10:00、15:00、20:00 / 覆盖人员：示例成员A、示例成员B、示例成员C
    数据源：消息汇总（触达 / 回复 / 新增） → 报价图 OCR 识别 → 日报提交情况 / 触发条件：仅工作日 = 是
    渲染器：duzhan.render_brief
02：发送到：示例达标群 B（22222222-…） / 档位：15:00、20:00 / 覆盖人员：示例成员D、示例成员E
    数据源：消息汇总（触达 / 回复 / 新增） → 内部 IM 留痕 / 触发条件：仅当日有数据 = 是
    渲染器：duzhan.render_brief
03：发送到：示例跟进群 1（33333333-…） / 档位：20:00 / 覆盖人员：示例跟进人1
    数据源：日报提交情况 → 会议纪要
    规则（always）：上表是今日日报提交情况，未提交的以日报为准补交。
    规则（missing_report）：AI 生成：根据当天数据，为未交日报的成员各写一句不超过 40 字的催促，只讲事实、不评价人。
    额外收件人：user 55555555-… / 策略核查：promo_a（到 2099-12-31） / 渲染器：ctob.render_brief
```

反例（同一台机器上验过）：把 `channel_id` 写成 `not-a-uuid`、`slots` 写成 `12:00` 的配置，**能存草稿**（200，返回 `errors` 两条），但启用返回 **400** `{"detail": {"message": "配置有误，不能启用", "errors": [ … ]}}`。

> 上面这段摘要是脱敏前的示例数据上跑出来的记录。换成你的群 / 名单 / 策略之后，请按方式一、方式二重跑一遍——`promo_a` 这类占位策略 id 要先换成 `app/wa_strategies.json` 里的 id。
