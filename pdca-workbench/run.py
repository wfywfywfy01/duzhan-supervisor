# -*- coding: utf-8 -*-
"""启动 PDCA 工作台生产服务。"""
from __future__ import annotations

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        workers=1,
        reload=False,
        log_level=settings.log_level.lower(),
        server_header=False,
    )


if __name__ == "__main__":
    main()
