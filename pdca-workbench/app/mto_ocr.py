# -*- coding: utf-8 -*-
"""服务器核对 MTO 报价图：读完立刻删磁盘文件，台账只留结构化字段。"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import httpx
from loguru import logger

from app.config import get_settings

RATE_CNY = 7.1
THRESHOLD_WAN = 30.0
PROMPT = (
    "这是 VERTU MTO 报价截图。只根据图上可见文字填 JSON，不要编造。"
    "字段：model, total_usd, delivery, sku, target_customer。"
    "model 必填且要完整：抄图上的机型/型号原文，连系列名和配置后缀一起抄"
    "（例：Vertu Signature S+、Vertu Alphafold、Vertu Quantum、Vertu Aster、"
    "Vertu Metavertu 2、机械表型号等）；中文机型名就原样抄中文。"
    "只写“VERTU”不算，必须写到具体型号；确实没有型号字样才留空字符串。"
    "total_usd 只填数字（ESTIMATED TOTAL / USD / $）。"
    "target_customer 是图上的客户名/经销商/国家；没有就空字符串。"
    "只输出一个 JSON 对象。"
)
# 型号读不出时的定向二次识别：只抄型号，避免被金额/客户名分散注意力。
MODEL_PROMPT = (
    "只做一件事：把这张报价图里的机型/型号原文逐字抄出来（含系列与配置后缀，"
    "如 Vertu Signature S+ / Vertu Alphafold / Vertu Agent Q / 机械表型号）。"
    "图里可能同时有多台机器或多个型号：能看清几个就抄几个，用「 / 」分隔。"
    "先看图片大标题，再看每台机器下方或参数栏的小字；型号通常以 Vertu 开头。"
    "只输出 JSON：{\"model\": \"...\"}；"
    "只有在整张图确实没有任何型号文字时才输出 {\"model\": \"\"}；"
    "能看清一部分就照抄那一部分，不要因为看不清全部而留空。"
)
# 不贪婪：逐个匹配「不含嵌套花括号」的片段，最后取能解析成 dict 的那个。
# 推理模型常见的输出是「先给示例 JSON，再给最终 JSON」，贪婪匹配会把两段一起吃进来 → json.loads 失败、字段全丢。
_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


_MD_MODEL_RE = re.compile(
    r"(?:机型|型号|model)\s*\*{0,2}\s*[：:]\s*\*{0,2}([^\n\r]{2,80})",
    re.I,
)
# 没有“机型：”前缀时，抓 Vertu 开头的型号串（含系列与后缀）
_VERTU_MODEL_RE = re.compile(
    r"((?:VERTU|Vertu|vertu)\s?[A-Za-z][A-Za-z0-9+\-]*(?:\s+[A-Za-z0-9+\-]{1,}){0,3})"
)
_MD_USD_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")
_MD_DELIVERY_RE = re.compile(r"(?:EST\.?\s*DELIVERY\s*DATE|\u4ea4\u4ed8\u65e5\u671f|\u9884\u8ba1\u4ea4\u4ed8)[^\d]{0,20}(\d{4}-\d{2}-\d{2})", re.I)


def _tls_verify():
    """Qwen 网关的 TLS 校验策略。

    2026-09-20 实测：llm.example.com:8443 的证书由公共 CA 签发，Python 默认
    信任库可直接校验通过，因此不再用 verify=False（那会失去中间人防护）。
    万一将来换成内网 CA，配置 PDCA_QWEN_CA_BUNDLE=/path/ca.pem 指向 CA 包，
    仍然保持校验，不要退回 verify=False。
    """
    bundle = (getattr(get_settings(), "qwen_ca_bundle", "") or "").strip()
    return bundle or True


def parse_quote_text(raw: str) -> dict:
    """从模型输出抠报价字段；JSON 优先，Markdown 输出做确定性兜底。读不到标待确认。"""
    text = (raw or "").strip()
    payload: dict = {}
    # 取「最后一个能解析成 dict 的 JSON 片段」：模型常先给示例再给答案，
    # 用第一个会把示例当成结果，用贪婪匹配又会把两段连起来解析失败。
    for match in _JSON_RE.finditer(text):
        try:
            loaded = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            payload = loaded
    model = _clean_model(payload.get("model"))
    sku = str(payload.get("sku") or "").strip()
    delivery = str(payload.get("delivery") or "").strip()
    target = str(payload.get("target_customer") or "").strip()
    usd = _usd(payload.get("total_usd"))
    # Markdown 兜底：推理模型常忽略“只输出 JSON”，输出机型/金额/交付日期的正文。
    if not model:
        model_match = _MD_MODEL_RE.search(text)
        if model_match:
            model = _clean_model(model_match.group(1))
    if not model:
        vertu_match = _VERTU_MODEL_RE.search(text)
        if vertu_match:
            model = _clean_model(vertu_match.group(1))
    if usd is None:
        usd_match = _MD_USD_RE.search(text)
        if usd_match:
            usd = _usd(usd_match.group(1))
    if not delivery:
        delivery_match = _MD_DELIVERY_RE.search(text)
        if delivery_match:
            delivery = delivery_match.group(1)
    wan = round(usd * RATE_CNY / 10000, 1) if usd is not None else None
    qualifies = wan is not None and wan >= THRESHOLD_WAN
    return {
        "model": model,
        "sku": sku,
        "delivery": delivery,
        "target_customer": target,
        "usd": usd,
        "wan": wan,
        "qualifies": qualifies,
        "raw_ok": bool(model or usd is not None),
        # 型号是硬要求（老板 2026-09-19）：读不出要单独标出来，便于二次追问。
        "model_missing": not bool(model),
    }


_MODEL_CUT_RE = re.compile(
    r"\s*[-—–]?\s*(?:金额|总价|合计|价格|售价|报价|\bEST\b|\bESTIMATED\b|USD|delivery|交付|客户|customer|sku).*$",
    re.I | re.S,
)


def _clean_model(value: object) -> str:
    """型号归一：砍掉字段尾巴与说明，只写“VERTU”视为无效。"""
    text = str(value or "")
    text = text.replace("*", " ").replace("\u3000", " ")
    # 去掉“机型：”后的整行尾巴（金额/交付/客户/分号后面的说明）
    text = _MODEL_CUT_RE.sub("", text)
    text = re.split(r"[；;，,。!！?？]", text)[0]
    text = re.sub(r"^[\s:：\-—•]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" \t\"'“”‘’|")
    # 去掉型号后面的括号说明（含未闭合的情况，如“Vertu AlphaFold（折叠屏”）
    text = re.split(r"[（(]", text)[0].strip()
    if len(text) > 80:
        text = text[:80].strip()
    if text.upper().replace(" ", "") in ("VERTU", "VERTU5G"):
        return ""
    return text


def _usd(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").replace("$", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def summarize_quotes(quotes: list[dict]) -> tuple[int, list[str]]:
    """≥30 万且有报价的张数；文案含金额、是否达标、目标客户。"""
    names: list[str] = []
    qualify_n = 0
    for item in quotes:
        if not item.get("raw_ok"):
            names.append("未读出报价")
            continue
        if item.get("qualifies"):
            qualify_n += 1
        usd = item.get("usd")
        wan = item.get("wan")
        money = f"${usd:g}" if usd is not None else "金额待确认"
        bar = "达标" if item.get("qualifies") else "未满30万"
        if wan is not None and usd is not None:
            money = f"${usd:g}≈{wan}万"
        target = str(item.get("target_customer") or "").strip() or "目标客户待确认"
        model = str(item.get("model") or "机型待确认")
        names.append(f"{model} {money}（{bar}）客户:{target}")
    return qualify_n, names[:6]


def ocr_image_bytes(content: bytes, mime: str = "image/jpeg") -> dict:
    """调本机/内网 Qwen 读图。密钥只从配置读。

    WebP 会先转 PNG：本地 Qwen 网关的视觉编码器对 webp 解码不稳定
    （实测 webp 直传读不出报价，转 PNG 后正常）。
    """
    import base64

    if "webp" in (mime or "").lower():
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(content)).convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            content = buffer.getvalue()
            mime = "image/png"
        except Exception as exc:  # noqa: BLE001 — 转码失败仍按原格式提交
            logger.warning("webp→png 转换失败，按原格式提交: {}", exc)

    settings = get_settings()
    url = settings.qwen_base_url.rstrip("/") + "/v1/chat/completions"
    key = settings.qwen_api_key
    model = settings.qwen_model
    if not key or not url:
        return parse_quote_text("")
    b64 = base64.b64encode(content).decode()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ],
        # 本地 Qwen 网关为推理模型：reasoning+正文会吃预算，400 曾导致末尾
        # JSON 被截断（finish_reason=length）而读不出报价，给足预算。
        "max_tokens": 1500,
        "temperature": 0.1,
    }
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
            verify=_tls_verify(),
        )
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        text = choice["message"]["content"] or ""
        row = parse_quote_text(text)
        if row.get("model_missing"):
            # 型号是硬要求：再定向问一次（只抄型号），仍读不出才留空并标记。
            row = _retry_model_only(payload, url, key, row)
        # 截断重试一次：追加“直接输出 JSON”引导，避免 reasoning 吃满预算。
        # 触发条件除了 raw_ok=False，还要覆盖「型号读出来了但金额丢了」——
        # 那正是 finish_reason=length 的典型后果，漏了它会把达标款写成「未满30万」。
        truncated = choice.get("finish_reason") == "length"
        if truncated and (not row["raw_ok"] or row.get("usd") is None):
            payload["messages"] = payload["messages"] + [{
                "role": "user",
                "content": "请直接输出结果 JSON，不要任何说明。",
            }]
            retry_resp = httpx.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
                verify=_tls_verify(),
            )
            retry_resp.raise_for_status()
            text = retry_resp.json()["choices"][0]["message"]["content"]
            return parse_quote_text(text)
        return row
    except Exception as exc:  # noqa: BLE001
        logger.warning("MTO OCR 失败: {}", exc)
        return parse_quote_text("")


def _retry_model_only(payload: dict, url: str, key: str, row: dict) -> dict:
    """型号读不出时的定向重试：**同一轮里保留原图**，只让它抄型号。

    2026-09-23 修复：原来是在「图片 + 完整提示词」之后再追加一条纯文字 user 指令，
    两条 user 指令互相干扰，且 max_tokens 被砍到 600（推理模型 reasoning 就吃完了），
    实测连续多张图回 {"model": ""}。现在改成「原图 + 只抄型号」单轮，并沿用原预算。
    """
    messages = list(payload.get("messages") or [])
    retry_messages: list[dict] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            images = [
                part
                for part in content
                if isinstance(part, dict) and part.get("type") == "image_url"
            ]
            retry_messages.append(
                {
                    "role": message.get("role") or "user",
                    "content": [{"type": "text", "text": MODEL_PROMPT}, *images],
                }
            )
        else:
            retry_messages.append(message)
    if not retry_messages:
        retry_messages = [{"role": "user", "content": MODEL_PROMPT}]
    retry_payload = dict(payload)
    retry_payload["messages"] = retry_messages
    try:
        budget = int(payload.get("max_tokens") or 0)
    except (TypeError, ValueError):
        budget = 0
    retry_payload["max_tokens"] = max(budget, 1500)
    try:
        resp = httpx.post(
            url,
            json=retry_payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=120.0,
            verify=_tls_verify(),
        )
        resp.raise_for_status()
        choice = (resp.json().get("choices") or [{}])[0]
        text = choice.get("message", {}).get("content") or ""
        found = parse_quote_text(text)
        if found.get("model"):
            logger.info("MTO 型号二次识别成功: {}", found["model"])
            row["model"] = found["model"]
            row["model_missing"] = False
            row["raw_ok"] = True
        else:
            # 带上 finish_reason：截断(length)与「模型真的说没有型号」是两种问题。
            logger.warning(
                "MTO 型号二次识别仍失败（finish={}）: {}",
                choice.get("finish_reason"),
                (text or "（空响应）")[:200],
            )
    except Exception as exc:  # noqa: BLE001 — 二次识别失败不影响其它字段
        logger.warning("MTO 型号二次识别异常: {}", exc)
    return row


def _vps_auth() -> tuple[str, dict]:
    """VPS 附件下载凭据。

    优先容器环境变量（部署时注入的最新 Agent 凭据，与拉群消息同一套身份）；
    缺省回退 ~/.vertu/vps-service.json 会话文件（历史会话可能过期，曾导致附件
    下载 401、MTO 全部读不出报价）。
    """
    import os

    env_key = os.environ.get("VERTU_APP_KEY", "").strip()
    env_id = os.environ.get("VERTU_APP_ID", "").strip()
    env_login = os.environ.get("VERTU_USER_LOGIN", "").strip()
    base = os.environ.get(
        "VERTU_VPS_SERVICE_URL", "https://vps.example.com"
    ).strip().rstrip("/")
    if env_key and env_login:
        return base, {
            "x-vertu-auth-channel": "vps-work",
            "user-agent": "vps-work",
            "Authorization": f"Bearer {env_key}",
            "x-vertu-agent-app-id": env_id,
            "x-vertu-user-login": env_login,
        }
    cfg_path = Path.home() / ".vps-work" / "vps-service.json"
    if not cfg_path.is_file():
        cfg_path = Path.home() / ".vertu" / "vps-service.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    base = str(cfg.get("baseUrl") or "https://vps.example.com").rstrip("/")
    headers = {
        "x-vertu-auth-channel": "vps-work",
        "user-agent": "vps-work",
        "Authorization": f"Bearer {cfg.get('agentAppKey')}",
        "x-vertu-agent-app-id": str(cfg.get("agentAppId") or ""),
        "x-vertu-user-login": str(cfg.get("login") or ""),
    }
    return base, headers


def download_ocr_delete(url_path: str) -> dict:
    """下载到临时目录、OCR、无论成败都删文件。"""
    tmp_dir = Path(tempfile.mkdtemp(prefix="mto-ocr-"))
    dest = tmp_dir / "shot.bin"
    try:
        base, headers = _vps_auth()
        full = url_path if url_path.startswith("http") else base + url_path
        resp = httpx.get(full, headers=headers, timeout=30.0, follow_redirects=True)
        status = int(getattr(resp, "status_code", 200) or 200)
        if status != 200:
            # 以前不看状态码：401/404 的 JSON 错误体被当图片送进 Qwen，白烧 1-2 次调用
            # 还把结果写成「未读出报价」，运维看不出是凭据过期。
            logger.error(
                "MTO 附件下载失败 status={} url={}：{}",
                status,
                full[-60:],
                (getattr(resp, "text", "") or "")[:160],
            )
            return parse_quote_text("")
        dest.write_bytes(resp.content)
        mime = resp.headers.get("content-type") or "image/jpeg"
        if mime and not mime.lower().startswith("image/"):
            logger.error("MTO 附件不是图片（Content-Type={}）：{}", mime[:60], full[-60:])
            return parse_quote_text("")
        if "webp" in mime or full.lower().endswith(".webp"):
            mime = "image/webp"
        return ocr_image_bytes(resp.content, mime)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MTO 下载失败: {}", exc)
        return parse_quote_text("")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def cleanup_temp_files(max_age_hours: float = 6.0) -> dict:
    """清理 MTO 下载残留（隔日清理）。

    download_ocr_delete 正常路径读完即删；进程崩溃/容器重启可能留下
    tempdir/mto-ocr-* 目录。本函数只清理该前缀且超过 max_age_hours 的目录，
    绝不触碰目录外的任何文件。返回 {"removed": n, "freed_bytes": n}。
    """
    import time as _time

    removed = 0
    freed = 0
    cutoff = _time.time() - max(0.0, max_age_hours) * 3600
    tmp_root = Path(tempfile.gettempdir())
    try:
        candidates = list(tmp_root.glob("mto-ocr-*"))
    except OSError as exc:  # noqa: BLE001
        logger.warning("MTO 临时目录扫描失败: {}", exc)
        return {"removed": 0, "freed_bytes": 0, "error": str(exc)[:200]}
    for path in candidates:
        try:
            if path.is_file():
                if path.stat().st_mtime < cutoff:
                    freed += path.stat().st_size
                    path.unlink()
                    removed += 1
                continue
            if not path.is_dir():
                continue
            newest = max(
                (child.stat().st_mtime for child in path.rglob("*") if child.exists()),
                default=path.stat().st_mtime,
            )
            if newest >= cutoff:
                continue
            size = sum(child.stat().st_size for child in path.rglob("*") if child.is_file())
            shutil.rmtree(path, ignore_errors=True)
            if not path.exists():
                freed += size
                removed += 1
        except OSError as exc:  # noqa: BLE001
            logger.warning("MTO 临时文件清理失败 {}: {}", path, exc)
    if removed:
        logger.info("MTO 临时文件已清理 {} 个，释放 {} 字节", removed, freed)
    return {"removed": removed, "freed_bytes": freed}


def review_mto_images(
    messages: list | None, sender_id: int | None, max_images: int | None = None
) -> tuple[int, list[str], list[dict]]:
    """本人当日图片：OCR 报价与目标客户；磁盘文件不保留。

    max_images 默认取 PDCA_MTO_OCR_MAX_IMAGES（8）：一天几十张图时，OCR 是整轮采集
    的瓶颈（实测 44 张图 ≈ 9 分钟），而「每日 4 款方案」用不到那么多张，超出的只记数
    不读图，避免拖过整点推送窗口。
    """
    settings = get_settings()
    limit = max_images
    if limit is None:
        try:
            limit = int(getattr(settings, "mto_ocr_max_images", 8))
        except (TypeError, ValueError):
            limit = 8
    limit = max(1, limit)
    quotes: list[dict] = []
    truncated = [False]
    from app.duzhan_ledger import DUZHAN_BOT_ID

    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("revoked_at"):
            continue
        if str(msg.get("sender_bot_id") or "") == DUZHAN_BOT_ID:
            continue
        if str(msg.get("message_type") or "") != "image":
            continue
        if sender_id is not None and msg.get("sender_user_id") != sender_id:
            continue
        for att in msg.get("attachments") or []:
            if not isinstance(att, dict):
                continue
            if str(att.get("attachment_type") or "") not in ("image", ""):
                continue
            url = str(att.get("url") or "")
            if not url:
                continue
            if len(quotes) >= limit:
                if not truncated[0]:
                    truncated[0] = True
                    logger.info(
                        "MTO 图片超过每人 {} 张上限，多出的不再 OCR: sender={}", limit, sender_id
                    )
                continue
            if settings.qwen_api_key:
                quotes.append(download_ocr_delete(url))
            else:
                name = str(att.get("name") or "image")
                quotes.append(
                    {
                        "model": "",
                        "model_missing": True,
                        "file": name,
                        "sku": "",
                        "delivery": "",
                        "target_customer": "",
                        "usd": None,
                        "wan": None,
                        "qualifies": False,
                        "raw_ok": False,
                    }
                )
    if settings.qwen_api_key:
        qualify_n, names = summarize_quotes(quotes)
        return qualify_n, names, quotes
    # 没配密钥 = 没读图：第一返回值语义是「信息完整报价数」，绝不能拿图片张数顶替，
    # 否则下游（MTO N/4 达标、工时加分）会把截图数当成达标款数。返回 None 让渲染写「待确认」。
    names = [str(item.get("file") or "image") for item in quotes][:6]
    logger.warning("未配置 QWEN 密钥，MTO 只登记了 {} 张图片、未读图", len(quotes))
    return None, names, []
