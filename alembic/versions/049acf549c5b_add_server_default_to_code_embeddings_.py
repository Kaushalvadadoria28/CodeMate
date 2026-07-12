"""add server default to code_embeddings.id for cocoindex writes

Revision ID: 049acf549c5b
Revises: 2f53af0f20e6
Create Date: 2026-07-12 13:25:34.790287

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '049acf549c5b'
down_revision: Union[str, Sequence[str], None] = '2f53af0f20e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.alter_column(
        "code_embeddings", "id",
        server_default=sa.text("gen_random_uuid()::text")
    )
    # ### end Alembic commands ###


def downgrade():
    op.alter_column("code_embeddings", "id", server_default=None)
    # ### end Alembic commands ###
