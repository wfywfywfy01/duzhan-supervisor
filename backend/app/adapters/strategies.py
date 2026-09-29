# -*- coding: utf-8 -*-
"""策略清单适配器（示例数据）。

生产里策略是运营当下在推的活动（新品 / 促销 / 返点…），单独维护、可带到期日；
这里给两条示例策略。接自己的系统时，只要 strategy_ids() 返回当前在查的策略 id。
"""
from __future__ import annotations

STRATEGIES: tuple[dict, ...] = (
    {"id": "promo_a", "label": "示例策略 A（新品触达）", "until": ""},
    {"id": "promo_b", "label": "示例策略 B（阶段促销）", "until": ""},
)


def strategy_ids() -> list[str]:
    return [item["id"] for item in STRATEGIES]
