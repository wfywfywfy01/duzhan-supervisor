# -*- coding: utf-8 -*-
"""督战官子 Agent 配置 API：积木 CRUD、结构试跑、从代码导入。"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from app.auth.deps import require_role
from app.auth.models import User
from app.database import get_session
from app.duzhan_admin import service
from app.duzhan_blocks import block_schema, dump_blocks, is_valid_day, parse_blocks
from app.models.duzhan_agent import DuzhanAgent

router = APIRouter(prefix="/api/duzhan-agents", tags=["duzhan-agents"])

#: 独立命名，便于测试与前端按依赖覆盖
require_admin = require_role("admin")


class AgentIn(BaseModel):
    """新建/更新子 Agent 的入参。"""

    name: str = Field(min_length=1, max_length=128, description="子 Agent 名称（唯一）")
    timezone: str = Field(default="Asia/Shanghai", max_length=64, description="推送时区")
    enabled: Optional[bool] = Field(default=None, description="是否启用；不传表示保持原状")
    note: str = Field(default="", max_length=256, description="备注")
    blocks: Any = Field(
        default_factory=lambda: {"blocks": []},
        description="积木配置：{'blocks': [{'type': ...}]}",
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def _timezone_known(cls, value: str) -> str:
        from zoneinfo import ZoneInfo

        text = (value or "").strip() or "Asia/Shanghai"
        try:
            ZoneInfo(text)
            return text
        except Exception:  # noqa: BLE001 — 大小写写错很常见，先按小写索引找回规范名
            canonical = _TIMEZONE_INDEX().get(text.lower())
            if canonical:
                return canonical
            raise ValueError(f"未知时区：{text}")


@lru_cache(maxsize=1)
def _TIMEZONE_INDEX() -> dict[str, str]:
    """小写时区名 → 规范名（用户手输 asia/shanghai 也能落到 Asia/Shanghai）。"""
    from zoneinfo import available_timezones

    return {name.lower(): name for name in available_timezones()}


class ToggleIn(BaseModel):
    enabled: bool = Field(description="true=启用；false=停用")


PREVIEW_MODES = ("structure", "render")


class PreviewIn(BaseModel):
    day: Optional[str] = Field(default=None, max_length=10, description="试跑日期 YYYY-MM-DD")
    hour: Optional[int] = Field(default=None, ge=0, le=23, description="试跑档位（整点）")
    mode: Optional[str] = Field(default=None, description="structure=结构试跑；render=真实内容试跑")

    @field_validator("mode")
    @classmethod
    def _mode_known(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        text = value.strip().lower()
        if text not in PREVIEW_MODES:
            raise ValueError("mode 只能是 structure / render")
        return text

    @field_validator("day")
    @classmethod
    def _day_shape(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        text = value.strip()
        if not is_valid_day(text):
            raise ValueError("试跑日期格式应为 YYYY-MM-DD")
        return text


@router.get("/ai-status", summary="AI 规则今日用量（调用次数 / 缓存条数 / 上限）")
async def ai_status(
    _user: Annotated[User, Depends(require_admin)] = None,
) -> dict:
    from app.duzhan_admin import ai_rules

    return ai_rules.status()


def _get_agent(session: Session, agent_id: int) -> DuzhanAgent:
    row = session.get(DuzhanAgent, agent_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="子 Agent 不存在")
    return row


def _guard_enable(agent: DuzhanAgent) -> None:
    """坏配置可以存草稿，但不允许启用：到点推不出去比不推更糟。"""
    errors = service.validate_agent(agent)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "配置有误，不能启用", "errors": errors},
        )


def _name_taken(session: Session, name: str, *, exclude_id: Optional[int] = None) -> bool:
    statement = select(DuzhanAgent).where(DuzhanAgent.name == name)
    if exclude_id is not None:
        statement = statement.where(DuzhanAgent.id != exclude_id)
    return session.exec(statement).first() is not None


@router.get("", summary="子 Agent 列表")
async def list_agents(
    _user: Annotated[User, Depends(require_admin)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    rows = session.exec(select(DuzhanAgent).order_by(DuzhanAgent.id)).all()
    items = []
    for row in rows:
        blocks = service.blocks_of(row)
        items.append(
            {
                "id": row.id,
                "name": row.name,
                "enabled": bool(row.enabled),
                "timezone": row.timezone,
                "note": row.note,
                "blocks": {"blocks": [block.as_dict() for block in blocks]},
                "errors": service.validate_agent(row),
                "renderer": service.renderer_for(blocks),
                "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            }
        )
    return {"items": items}


@router.get("/block-schema", summary="积木类型定义（前端面板同源）")
async def get_block_schema(
    _user: Annotated[User, Depends(require_admin)] = None,
) -> dict:
    return {"blocks": block_schema()}


@router.post("/seed-from-code", summary="从现有代码导入配置")
async def seed_from_code(
    _user: Annotated[User, Depends(require_admin)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    return service.seed_from_code(session)


@router.post("", summary="新建子 Agent")
async def create_agent(
    payload: AgentIn,
    _user: Annotated[User, Depends(require_admin)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    if _name_taken(session, payload.name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同名子 Agent 已存在")
    row = DuzhanAgent(
        name=payload.name,
        enabled=False,
        timezone=payload.timezone,
        blocks_json=dump_blocks(parse_blocks(payload.blocks)),
        note=payload.note,
    )
    if payload.enabled:
        _guard_enable(row)
        row.enabled = True
    session.add(row)
    session.commit()
    session.refresh(row)
    return service.agent_out(row)  # 新建未显式启用时保持停用


@router.put("/{agent_id}", summary="更新子 Agent")
async def update_agent(
    agent_id: int,
    payload: AgentIn,
    _user: Annotated[User, Depends(require_admin)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    row = _get_agent(session, agent_id)
    if _name_taken(session, payload.name, exclude_id=agent_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同名子 Agent 已存在")
    row.name = payload.name
    row.timezone = payload.timezone
    row.note = payload.note
    row.blocks_json = dump_blocks(parse_blocks(payload.blocks))
    # 不传 enabled 时保持原状态，避免前端漏字段把在跑的配置悄悄停掉
    enable = row.enabled if payload.enabled is None else payload.enabled
    if enable:
        _guard_enable(row)
    row.enabled = enable
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return service.agent_out(row)


@router.post("/{agent_id}/toggle", summary="启用/停用")
async def toggle_agent(
    agent_id: int,
    payload: ToggleIn,
    _user: Annotated[User, Depends(require_admin)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    row = _get_agent(session, agent_id)
    if payload.enabled:
        _guard_enable(row)
    row.enabled = payload.enabled
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)
    return service.agent_out(row)


@router.post("/{agent_id}/preview", summary="试跑：结构（不发消息）/ 真实内容（用快照渲染，也不发消息）")
async def preview_agent(
    agent_id: int,
    payload: PreviewIn | None = None,
    _user: Annotated[User, Depends(require_admin)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    row = _get_agent(session, agent_id)
    body = payload or PreviewIn()
    if body.mode == "render":
        return service.render_preview(row, day=body.day, hour=body.hour)
    return service.preview_agent(row, day=body.day, hour=body.hour)


@router.delete("/{agent_id}", summary="删除子 Agent")
async def delete_agent(
    agent_id: int,
    _user: Annotated[User, Depends(require_admin)] = None,
    session: Annotated[Session, Depends(get_session)] = None,
) -> dict:
    row = _get_agent(session, agent_id)
    session.delete(row)
    session.commit()
    return {"deleted": agent_id}
