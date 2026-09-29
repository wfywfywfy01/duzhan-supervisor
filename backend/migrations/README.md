# migrations

这里是**生产风格的 alembic 版本文件**（`versions/001_duzhan_agents.py`），
建的就是 `duzhan_agents` 表 —— 和演示用的 `app/db.py` 里 `SQLModel.metadata.create_all` 等价。

- 演示/单机：直接跑服务，启动时自动建表，不需要 alembic。
- 接进你们自己的库：把这份版本文件放进你们的 alembic 目录，按你们的 revision 链改
  `down_revision` 即可；表结构不用动。
