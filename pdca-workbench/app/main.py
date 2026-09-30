# -*- coding: utf-8 -*-
"""督战官开源版入口：只挂督战官相关的 API 与配置页。

原项目（PDCA 工作台）还有日报、待办、物流、门店、训练等几十个路由，
与本项目无关，开源版不收录；`app/duzhan_admin` 的 API 契约与内部版一致。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.alerting import notify
from app.auth.csrf import browser_write_is_trusted
from app.config import get_settings
from app.duzhan_admin.router import router as duzhan_admin_router

settings = get_settings()

app = FastAPI(
    title="督战官 Duzhan Supervisor",
    description="多群多智能体督战系统：三追进度、C转B 跟进、小黑屋祝贺、策略核查",
    version="1.0.0",
)

app.include_router(duzhan_admin_router)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """写请求校验来源；浏览器以外的调用（Bearer / 无 Origin）照常放行。"""
    if not browser_write_is_trusted(request):
        return JSONResponse({"detail": "写请求来源不可信，请从本站页面重试"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get("/healthz", tags=["ops"])
def healthz() -> dict:
    """存活探针：只报配置开关，不泄露密钥。"""
    return {
        "status": "ok",
        "duzhan_enabled": bool(getattr(settings, "duzhan_enabled", False)),
        "ctob_enabled": bool(getattr(settings, "ctob_enabled", False)),
        "heiwu_enabled": bool(getattr(settings, "heiwu_enabled", False)),
    }


@app.on_event("startup")
def _startup() -> None:
    from app.database import bootstrap_database, get_db_mode
    from app.scheduler.jobs import start_scheduler

    try:
        mode = bootstrap_database()
        logger.info("数据库已就绪（{}）", mode or get_db_mode())
    except Exception as exc:  # noqa: BLE001 — 起库失败不拦住 API，由调用方看到 500
        logger.exception("数据库初始化失败: {}", exc)
        notify("数据库初始化失败", str(exc)[:200])

    try:
        start_scheduler()
    except Exception as exc:  # noqa: BLE001 — 调度器起不来也要让 API 可用
        logger.exception("调度器启动失败: {}", exc)
        notify("调度器启动失败", str(exc)[:200])


@app.on_event("shutdown")
def _shutdown() -> None:
    from app.scheduler.jobs import stop_scheduler

    stop_scheduler()
