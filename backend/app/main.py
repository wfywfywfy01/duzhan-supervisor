# -*- coding: utf-8 -*-
"""督战官配置服务（演示）：FastAPI + SQLite。

启动：
    pip install -r requirements.txt
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.duzhan_admin.router import router as duzhan_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Duzhan Supervisor", version="0.1.0", lifespan=lifespan)

# 前端 dev server（vite）默认 5173；生产同源部署时这段可以删掉。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(duzhan_router)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
