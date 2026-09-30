# -*- coding: utf-8 -*-
"""IM 催办发送记录与回复采集。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class ImRemindSend(SQLModel, table=True):
    """一次催办消息：人 + 消息 id + 该消息列出的任务顺序（用于「第N条」映射）。"""

    __tablename__ = "im_remind_sends"

    id: Optional[int] = Field(default=None, primary_key=True)
    person: str = Field(index=True, max_length=128)
    sent_at: datetime = Field(default_factory=datetime.utcnow)
    message_id: str = Field(default="", max_length=128)
    item_task_ids: str = Field(default="[]")  # JSON 数组，按消息内编号顺序
    project_id: Optional[int] = Field(default=None, index=True)
    round: str = Field(default="", max_length=32)


class TodoReply(SQLModel, table=True):
    """对催办消息的 IM 回复（解析结果 + 人工队列）。"""

    __tablename__ = "todo_replies"

    id: Optional[int] = Field(default=None, primary_key=True)
    person: str = Field(index=True, max_length=128)
    text: str = Field(max_length=1024)
    at: datetime = Field(default_factory=datetime.utcnow)
    signal: str = Field(default="", max_length=32)  # done/progress/blocker/''
    status: str = Field(default="unreviewed", max_length=32)  # unreviewed/applied/ignored
    remind_send_id: Optional[int] = Field(default=None, index=True)
    target_task_id: Optional[int] = Field(default=None)
    target_project_id: Optional[int] = Field(default=None)
