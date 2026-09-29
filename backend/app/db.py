# -*- coding: utf-8 -*-
"""SQLite 引擎与会话（演示用；换成任何 SQLAlchemy 支持的库都可以）。"""
from __future__ import annotations

import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

_DB_FILE = Path(os.environ.get("DUZHAN_DB") or (Path(__file__).resolve().parents[1] / "duzhan.db"))
_engine = None


def get_engine():
    """懒加载引擎。"""
    global _engine
    if _engine is None:
        _engine = create_engine(
            "sqlite:///" + _DB_FILE.as_posix(),
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    """建表（演示用 create_all；生产用 alembic，见 migrations/）。"""
    from app.models import duzhan_agent  # noqa: F401  让元数据里有这张表

    SQLModel.metadata.create_all(get_engine())


def get_session():
    """FastAPI 依赖：每个请求一个会话。"""
    with Session(get_engine()) as session:
        yield session
