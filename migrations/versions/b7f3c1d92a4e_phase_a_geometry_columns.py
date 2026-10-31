"""phase A geometry columns

Revision ID: b7f3c1d92a4e
Revises: 5679c20a2fdc
"""
from alembic import op
import sqlalchemy as sa

revision = 'b7f3c1d92a4e'
down_revision = '5679c20a2fdc'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('drawings', sa.Column('is_base', sa.Boolean(), nullable=True))
    op.add_column('projects', sa.Column('takeoff_params', sa.Text(), nullable=True))
    op.add_column('measurements', sa.Column('manual_pinned', sa.Boolean(), nullable=True))
    op.execute("UPDATE projects SET project_type='sample_flat' WHERE project_type='residential'")
    op.execute("UPDATE projects SET project_type='office' WHERE project_type='commercial'")


def downgrade():
    op.drop_column('measurements', 'manual_pinned')
    op.drop_column('projects', 'takeoff_params')
    op.drop_column('drawings', 'is_base')
