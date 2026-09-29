# -*- coding: utf-8 -*-
"""现有（硬编码）群清单：给「从代码导入」用。

生产里这些群是写在代码里的常量。把配置搬到库里之后，这一段可以删掉；
在那之前，导入功能让你不用手抄 20 个群 ID。
"""
from __future__ import annotations

#: 达标群（业绩/进度督战）
GROUPS: tuple[dict, ...] = (
    {
        "name": "示例达标群 A",
        "channel_id": "11111111-1111-4111-8111-111111111111",
        "lang": "zh",
        "tz": "Asia/Shanghai",
    },
    {
        "name": "示例达标群 B",
        "channel_id": "22222222-2222-4222-8222-222222222222",
        "lang": "en",
        "tz": "Europe/Paris",
    },
)

#: 跟进群（每条线索的跟进督战）
FOLLOW_GROUPS: tuple[dict, ...] = (
    {"name": "示例跟进群 1", "display": "示例跟进人1", "channel_id": "33333333-3333-4333-8333-333333333333"},
    {"name": "示例跟进群 2", "display": "示例跟进人2", "channel_id": "44444444-4444-4444-8444-444444444444"},
)

FOLLOW_CHANNELS: set[str] = {item["channel_id"] for item in FOLLOW_GROUPS}
