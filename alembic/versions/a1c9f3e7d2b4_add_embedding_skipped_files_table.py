"""add embedding_skipped_files table

Revision ID: a1c9f3e7d2b4
Revises: b03ff2dd8a06
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1c9f3e7d2b4'
down_revision: Union[str, Sequence[str], None] = 'b03ff2dd8a06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('embedding_skipped_files',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_embedding_skipped_files_project_id'), 'embedding_skipped_files', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_embedding_skipped_files_project_id'), table_name='embedding_skipped_files')
    op.drop_table('embedding_skipped_files')
