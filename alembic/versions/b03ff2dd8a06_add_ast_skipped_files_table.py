"""add ast_skipped_files table

Revision ID: b03ff2dd8a06
Revises: 049acf549c5b
Create Date: 2026-07-13 00:55:04.550615

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b03ff2dd8a06'
down_revision: Union[str, Sequence[str], None] = '049acf549c5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ast_skipped_files',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('project_id', sa.String(), nullable=False),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ast_skipped_files_project_id'), 'ast_skipped_files', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_ast_skipped_files_project_id'), table_name='ast_skipped_files')
    op.drop_table('ast_skipped_files')