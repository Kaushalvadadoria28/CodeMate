"""add updated_at to chat_sessions

Revision ID: 1298bc0f9253
Revises: fc35939074d2
Create Date: 2026-07-08 00:33:17.344451

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1298bc0f9253'
down_revision: Union[str, Sequence[str], None] = 'fc35939074d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ONLY this — remove everything else autogenerate added
    op.add_column('chat_sessions', 
        sa.Column('updated_at', sa.DateTime(timezone=True), 
                  server_default=sa.text('now()'), nullable=True)
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_column('chat_sessions', 'updated_at')
    # ### end Alembic commands ###
