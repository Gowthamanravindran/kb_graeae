"""users.disabled"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("users") as b:
        b.drop_column("disabled")
