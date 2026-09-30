# -*- coding: utf-8 -*-
"""多智能体督战运行时包（规格：docs/多智能体督战系统实施规格.md）。

分层边界：
- flow_controller：确定性调度封装与档位健康检查（P0/P2）；
- group_graph / group_context / group_service：群级 Agent，一套图多实例（P3/P4）；
- supervisor_graph / supervisor_service：主 Agent 决策与部门总结（P7）；
- outbox / events / models / schemas：外发审批、事件与结构化契约（P1）；
- mto_vision_service / asr_doubao / asr_service：视觉与 ASR 能力服务（P5/P6）。
"""
from __future__ import annotations
