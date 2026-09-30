# -*- coding: utf-8 -*-
"""门店五件套日报表（每日进店来源 + 成交漏斗 + Sell-out）。

进店来源分五类（2026-07-12 起，替换旧的 自然进/预约/潜客/线上/介绍/SA 六分类）：
  walkin   - 直接进店人数
  cross    - 异业：同业其他奢侈品员工介绍
  online   - 线上：各种社媒渠道
  recruit  - 招聘：招聘的新员工自带客户
  existing - 存量：老客户
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlmodel import Field, SQLModel


class WalkinDailyReport(SQLModel, table=True):
    """经销商每日客流五件套数据。"""

    __tablename__ = "walkin_daily_reports"

    id: Optional[int] = Field(default=None, primary_key=True)
    report_date: str = Field(index=True, max_length=10)   # YYYY-MM-DD
    dealer_id: str = Field(index=True, max_length=64)     # 对应 walkin JSON 里的 store id
    dealer_name: str = Field(max_length=256)

    # 五件套来源人数
    walkin_visits: int = Field(default=0)      # walkin 直接进店
    cross_visits: int = Field(default=0)       # 异业：同业其他奢侈品员工介绍
    online_visits: int = Field(default=0)      # 线上：各种社媒渠道
    recruit_visits: int = Field(default=0)     # 招聘：招聘的新员工自带客户
    existing_visits: int = Field(default=0)    # 存量：老客户

    # 转化漏斗
    touch_count: int = Field(default=0)           # 触摸产品（人次）
    use_count: int = Field(default=0)              # 试用体验（人次）
    wechat_add_count: int = Field(default=0)      # 微信添加数
    deal_count: int = Field(default=0)            # 成交组数
    # 历史字段名保留兼容；前端一直按 $ 录入，实际口径为 USD。
    deal_amount_yuan: float = Field(default=0.0)  # Revenue (USD)

    notes: str = Field(default="", max_length=1024)
    submitted_by: str = Field(default="", max_length=64)
    # 与历史数据一致的 naive UTC 存储；datetime.utcnow() 在 Python 3.12 已弃用。
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

    @property
    def total_visits(self) -> int:
        return (
            self.walkin_visits
            + self.cross_visits
            + self.online_visits
            + self.recruit_visits
            + self.existing_visits
        )


def latest_walkin_reports(rows: Iterable[WalkinDailyReport]) -> list[WalkinDailyReport]:
    """One current report per store/day, without deleting historical duplicates."""
    latest: dict[tuple[str, str], tuple[tuple[datetime, int], WalkinDailyReport]] = {}
    for row in rows:
        timestamp = row.created_at or datetime.min
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        version = (timestamp.astimezone(timezone.utc), row.id or 0)
        key = (row.dealer_id, row.report_date)
        if key not in latest or version > latest[key][0]:
            latest[key] = (version, row)
    return [row for _, row in latest.values()]


def revenue_requires_review(amount: float) -> bool:
    """统一判断门店填报金额是否只展示原值、但不进入经营汇总。"""
    from app.config import get_settings

    settings = get_settings()
    threshold = min(
        settings.max_reported_revenue_usd,
        getattr(settings, "revenue_review_threshold_usd", settings.max_reported_revenue_usd),
    )
    return not math.isfinite(amount) or amount < 0 or amount > threshold
