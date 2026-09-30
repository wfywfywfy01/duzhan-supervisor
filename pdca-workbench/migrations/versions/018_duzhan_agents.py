# -*- coding: utf-8 -*-
"""督战官子 Agent 配置表：积木式配置落库，运行时可热改。

Revision ID: 018
Revises: 017
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table('duzhan_agents'):
        return
    op.create_table(
        'duzhan_agents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('timezone', sa.String(64), nullable=False, server_default='Asia/Shanghai'),
        sa.Column('blocks_json', sa.Text(), nullable=False, server_default=''),
        sa.Column('note', sa.String(256), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_duzhan_agents_name', 'duzhan_agents', ['name'], unique=True)
    op.create_index('ix_duzhan_agents_enabled', 'duzhan_agents', ['enabled'])


def downgrade() -> None:
    op.drop_index('ix_duzhan_agents_enabled', table_name='duzhan_agents')
    op.drop_index('ix_duzhan_agents_name', table_name='duzhan_agents')
    op.drop_table('duzhan_agents')
