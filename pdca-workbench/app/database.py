# -*- coding: utf-8 -*-
"""PostgreSQL / SQLite 数据库引擎与会话。"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine = None
_active_database_url: str | None = None
_db_mode: str = "unknown"


def _sqlite_fallback_url() -> str:
    """PostgreSQL 不可用时使用的本地 SQLite 路径。"""
    path = get_settings().data_dir / "pdca_local.sqlite"
    return f"sqlite:///{path.as_posix()}"


def get_active_database_url() -> str:
    """当前实际使用的数据库连接串。"""
    global _active_database_url
    if _active_database_url is None:
        _active_database_url = get_settings().database_url
    return _active_database_url


def get_db_mode() -> str:
    """postgresql | sqlite | sqlite-fallback | unknown"""
    return _db_mode


def get_engine():
    """懒加载 SQLAlchemy 引擎。"""
    global _engine
    if _engine is None:
        url = get_active_database_url()
        connect_args: dict = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        elif url.startswith("postgresql"):
            connect_args["connect_timeout"] = 3
        _engine = create_engine(
            url,
            echo=False,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=5,
        )
    return _engine


def _probe_url(url: str) -> bool:
    """探测指定连接串是否可用。"""
    connect_args: dict = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    elif url.startswith("postgresql"):
        connect_args["connect_timeout"] = 3
    try:
        probe = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        probe.dispose()
        return True
    except Exception as exc:
        logger.debug("数据库探测失败 {}: {}", url.split("@")[-1], exc)
        return False


def bootstrap_database() -> str:
    """启动时选择数据库：优先 PostgreSQL；生产环境默认禁止回退 SQLite。"""
    global _engine, _active_database_url, _db_mode
    settings = get_settings()
    primary = settings.database_url
    if (
        settings.environment == "production"
        and getattr(settings, "using_default_database_url", False)
    ):
        raise RuntimeError(
            "生产环境必须显式设置 PDCA_DATABASE_URL，禁止使用默认弱口令连接串"
        )


    if primary.startswith("sqlite"):
        _active_database_url = primary
        _engine = None
        _db_mode = "sqlite"
        init_db()
        logger.info("使用 SQLite: {}", primary)
        return _db_mode

    if _probe_url(primary):
        _active_database_url = primary
        _engine = None
        _db_mode = "postgresql"
        init_db()
        logger.info("已连接 PostgreSQL: {}", settings.pg_connection_info.get("database"))
        return _db_mode

    if settings.environment == "production" and not settings.allow_sqlite_fallback:
        raise RuntimeError(
            "生产环境 PostgreSQL 不可用，且未显式设置 PDCA_ALLOW_SQLITE_FALLBACK=1；"
            "为防止数据写入本地 SQLite 造成数据分叉，启动已中止"
        )
    fallback = _sqlite_fallback_url()
    logger.warning(
        "PostgreSQL 不可用（{}），回退本地 SQLite",
        primary.split("@")[-1] if "@" in primary else primary,
    )
    _active_database_url = fallback
    _engine = None
    _db_mode = "sqlite-fallback"
    init_db()
    logger.info("使用 SQLite 回退库: {}", fallback)
    return _db_mode


def check_db_connection() -> bool:
    """探测当前数据库连通性。"""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("数据库连接失败: {}", exc)
        return False


def init_db(apply_patches: bool = True) -> None:
    """创建所有表（首次启动或迁移前）。"""
    from app.auth.models import User  # noqa: F401
    from app.auth.security_state import LoginFailRecord, TokenRevocation  # noqa: F401
    from app.models.daily_report import DailyReport  # noqa: F401
    from app.models.dealer_sales import DealerSales  # noqa: F401
    from app.models.logistics import LogisticsShipment  # noqa: F401
    from app.models.meeting import MeetingRecord  # noqa: F401
    from app.models.onboarding_progress import OnboardingProgress  # noqa: F401
    from app.models.pdca_task import PdcaTask  # noqa: F401
    from app.models.walkin_daily_report import WalkinDailyReport  # noqa: F401
    from app.models.dealer_store import DealerStore  # noqa: F401
    from app.models.dealer_assignment import DealerAssignment  # noqa: F401
    from app.models.monthly_target import MonthlyTarget  # noqa: F401
    from app.models.audit_log import AuditLog  # noqa: F401
    from app.models.tracking_status import TrackingAutoStatus  # noqa: F401
    from app.models.acquisition_login_ticket import AcquisitionLoginTicket  # noqa: F401
    from app.models.customer_profile import CustomerProfile  # noqa: F401
    from app.models.todo_project import TodoProject  # noqa: F401
    from app.models.todo_group_state import TodoGroupState  # noqa: F401
    from app.models.im_replies import ImRemindSend, TodoReply  # noqa: F401
    from app.models.scheduled_job_run import ScheduledJobRun  # noqa: F401
    from app.agents.models import (  # noqa: F401
        AgentDraft,
        AgentEvent,
        AgentOutbox,
        AgentRun,
        MeetingAsrArtifact,
    )
    from app.omega import models as omega_models  # noqa: F401

    SQLModel.metadata.create_all(get_engine())
    if apply_patches:
        _migrate_schema()
    logger.info("数据库表已就绪")


def _migrate_schema() -> None:
    """轻量 schema 补丁（SQLite/PostgreSQL）。"""
    engine = get_engine()
    dialect = engine.dialect.name
    _patches_pg = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS sales_name VARCHAR(128) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS pwd_version INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS dealer_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS owner_key VARCHAR(128) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS team_key VARCHAR(64) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS data_scope VARCHAR(16) DEFAULT ''",
        "ALTER TABLE dealer_stores ADD COLUMN IF NOT EXISTS dealer_level VARCHAR(8) DEFAULT 'L1'",
        "ALTER TABLE dealer_stores ADD COLUMN IF NOT EXISTS sales_owner VARCHAR(64) DEFAULT ''",
        "ALTER TABLE dealer_stores ALTER COLUMN sales_owner TYPE VARCHAR(128)",
        "ALTER TABLE dealer_stores ADD COLUMN IF NOT EXISTS team_key VARCHAR(64) DEFAULT 'overseas'",
        "ALTER TABLE dealer_stores ADD COLUMN IF NOT EXISTS knowledge_dealer_id VARCHAR(36) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_dealer_stores_knowledge_dealer_id ON dealer_stores (knowledge_dealer_id)",
        "ALTER TABLE walkin_daily_reports ADD COLUMN IF NOT EXISTS walkin_visits INTEGER DEFAULT 0",
        "ALTER TABLE walkin_daily_reports ADD COLUMN IF NOT EXISTS cross_visits INTEGER DEFAULT 0",
        "ALTER TABLE walkin_daily_reports ADD COLUMN IF NOT EXISTS recruit_visits INTEGER DEFAULT 0",
        "ALTER TABLE walkin_daily_reports ADD COLUMN IF NOT EXISTS existing_visits INTEGER DEFAULT 0",
        "ALTER TABLE dealer_sales ADD COLUMN IF NOT EXISTS phone_qty INTEGER DEFAULT 0",
        "ALTER TABLE dealer_sales ADD COLUMN IF NOT EXISTS activation_rate FLOAT DEFAULT 0",
        "ALTER TABLE meeting_records ADD COLUMN IF NOT EXISTS source VARCHAR(32) DEFAULT 'legacy'",
        # 待办催办（提醒跟进）字段
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS last_reminded_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS last_reminded_round VARCHAR(32) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS remind_count INTEGER DEFAULT 0",
        # Vemory 会议待办（事实源：Vemory OpenAPI）
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS external_todo_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS meeting_name VARCHAR(256) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS meeting_date VARCHAR(10) DEFAULT ''",
        # 岗位 SOP 收敛字段
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS position VARCHAR(64) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS origin_owner VARCHAR(128) DEFAULT ''",
        # 项目（事项）收敛
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS project_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_external_todo_id ON pdca_tasks (external_todo_id)",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_project_id ON pdca_tasks (project_id)",
        # IM 回复采集
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS reply_text VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS replied_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS owner_locked BOOLEAN DEFAULT FALSE",
        "ALTER TABLE todo_projects ADD COLUMN IF NOT EXISTS reply_text VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE todo_projects ADD COLUMN IF NOT EXISTS replied_at TIMESTAMP",
        "ALTER TABLE todo_replies ADD COLUMN IF NOT EXISTS remind_send_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_todo_replies_remind_send_id ON todo_replies (remind_send_id)",
        # 项目（事项）类型：keyword/meeting/manual
        "ALTER TABLE todo_projects ADD COLUMN IF NOT EXISTS kind VARCHAR(16) DEFAULT 'keyword'",
        # 群认领闭环 + 三源印证打分（2026-09-07）
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS score INTEGER",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS score_at TIMESTAMP",
        # OKR 归属（2026-09-11：待办/项目 ↔ 个人月度 OKR）
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS okr_title VARCHAR(256) DEFAULT ''",
        "ALTER TABLE todo_projects ADD COLUMN IF NOT EXISTS okr_title VARCHAR(256) DEFAULT ''",
        # 多智能体督战运行时扩展（migrations 011 同步）
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS agent_run_id INTEGER",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS group_channel_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS source_ref VARCHAR(256) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS due_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS blocked_reason VARCHAR(512) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS evidence_json TEXT DEFAULT '[]'",
        "ALTER TABLE pdca_tasks ADD COLUMN IF NOT EXISTS verification_status VARCHAR(32) DEFAULT 'unverified'",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_agent_run_id ON pdca_tasks (agent_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_verification_status ON pdca_tasks (verification_status)",
        # 旧进店来源分类（自然进/预约/潜客/介绍/SA）已废弃，替换为 walkin/cross/online/recruit/existing 五分类；
        # 这几列原来是 NOT NULL，不删掉的话新 taxonomy 的 INSERT 会因为缺列违反约束而失败
        "ALTER TABLE walkin_daily_reports DROP COLUMN IF EXISTS prospect_visits",
        "ALTER TABLE walkin_daily_reports DROP COLUMN IF EXISTS appointment_visits",
        "ALTER TABLE walkin_daily_reports DROP COLUMN IF EXISTS referral_visits",
        "ALTER TABLE walkin_daily_reports DROP COLUMN IF EXISTS sa_visits",
    ]
    _patches_sqlite = [
        "ALTER TABLE users ADD COLUMN sales_name VARCHAR(128) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 1",
        "ALTER TABLE users ADD COLUMN pwd_version INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN dealer_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN owner_key VARCHAR(128) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN team_key VARCHAR(64) DEFAULT ''",
        "ALTER TABLE users ADD COLUMN data_scope VARCHAR(16) DEFAULT ''",
        "ALTER TABLE dealer_stores ADD COLUMN dealer_level VARCHAR(8) DEFAULT 'L1'",
        "ALTER TABLE dealer_stores ADD COLUMN sales_owner VARCHAR(64) DEFAULT ''",
        "ALTER TABLE dealer_stores ADD COLUMN team_key VARCHAR(64) DEFAULT 'overseas'",
        "ALTER TABLE dealer_stores ADD COLUMN knowledge_dealer_id VARCHAR(36) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_dealer_stores_knowledge_dealer_id ON dealer_stores (knowledge_dealer_id)",
        "ALTER TABLE walkin_daily_reports ADD COLUMN walkin_visits INTEGER DEFAULT 0",
        "ALTER TABLE walkin_daily_reports ADD COLUMN cross_visits INTEGER DEFAULT 0",
        "ALTER TABLE walkin_daily_reports ADD COLUMN recruit_visits INTEGER DEFAULT 0",
        "ALTER TABLE walkin_daily_reports ADD COLUMN existing_visits INTEGER DEFAULT 0",
        "ALTER TABLE dealer_sales ADD COLUMN phone_qty INTEGER DEFAULT 0",
        "ALTER TABLE dealer_sales ADD COLUMN activation_rate FLOAT DEFAULT 0",
        "ALTER TABLE meeting_records ADD COLUMN source VARCHAR(32) DEFAULT 'legacy'",
        # 待办催办（提醒跟进）字段
        "ALTER TABLE pdca_tasks ADD COLUMN last_reminded_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN last_reminded_round VARCHAR(32) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN remind_count INTEGER DEFAULT 0",
        # Vemory 会议待办（事实源：Vemory OpenAPI）
        "ALTER TABLE pdca_tasks ADD COLUMN external_todo_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN meeting_name VARCHAR(256) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN meeting_date VARCHAR(10) DEFAULT ''",
        # 岗位 SOP 收敛字段
        "ALTER TABLE pdca_tasks ADD COLUMN position VARCHAR(64) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN origin_owner VARCHAR(128) DEFAULT ''",
        # 项目（事项）收敛
        "ALTER TABLE pdca_tasks ADD COLUMN project_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_external_todo_id ON pdca_tasks (external_todo_id)",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_project_id ON pdca_tasks (project_id)",
        # IM 回复采集
        "ALTER TABLE pdca_tasks ADD COLUMN reply_text VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN replied_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN owner_locked BOOLEAN DEFAULT 0",
        "ALTER TABLE todo_projects ADD COLUMN reply_text VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE todo_projects ADD COLUMN replied_at TIMESTAMP",
        "ALTER TABLE todo_replies ADD COLUMN remind_send_id INTEGER",
        "CREATE INDEX IF NOT EXISTS ix_todo_replies_remind_send_id ON todo_replies (remind_send_id)",
        "ALTER TABLE todo_projects ADD COLUMN kind VARCHAR(16) DEFAULT 'keyword'",
        "ALTER TABLE pdca_tasks ADD COLUMN claimed_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN score INTEGER",
        "ALTER TABLE pdca_tasks ADD COLUMN score_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN okr_title VARCHAR(256) DEFAULT ''",
        "ALTER TABLE todo_projects ADD COLUMN okr_title VARCHAR(256) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN agent_run_id INTEGER",
        "ALTER TABLE pdca_tasks ADD COLUMN group_channel_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN source_ref VARCHAR(256) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN due_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN closed_at TIMESTAMP",
        "ALTER TABLE pdca_tasks ADD COLUMN blocked_reason VARCHAR(512) DEFAULT ''",
        "ALTER TABLE pdca_tasks ADD COLUMN evidence_json TEXT DEFAULT '[]'",
        "ALTER TABLE pdca_tasks ADD COLUMN verification_status VARCHAR(32) DEFAULT 'unverified'",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_agent_run_id ON pdca_tasks (agent_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_pdca_tasks_verification_status ON pdca_tasks (verification_status)",
        "ALTER TABLE walkin_daily_reports DROP COLUMN prospect_visits",
        "ALTER TABLE walkin_daily_reports DROP COLUMN appointment_visits",
        "ALTER TABLE walkin_daily_reports DROP COLUMN referral_visits",
        "ALTER TABLE walkin_daily_reports DROP COLUMN sa_visits",
    ]
    with engine.connect() as conn:
        if dialect == "postgresql":
            for sql in _patches_pg:
                try:
                    conn.exec_driver_sql(sql)
                except Exception:
                    logger.warning("PostgreSQL schema patch failed: {}", sql)

                    pass
        else:
            for sql in _patches_sqlite:
                try:
                    conn.exec_driver_sql(sql)
                except Exception:
                    logger.warning("SQLite schema patch failed: {}", sql)

                    pass
        conn.commit()


def get_session():
    """FastAPI 依赖：数据库会话。"""
    with Session(get_engine()) as session:
        yield session
