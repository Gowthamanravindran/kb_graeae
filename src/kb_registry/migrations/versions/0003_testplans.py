"""stage 5b: shared test plans, slots, items and trace locks"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"

_KEY = lambda: [  # noqa: E731
    sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), primary_key=True),
    sa.Column("branch", sa.String(255), primary_key=True),
    sa.Column("plan_id", sa.String(200), primary_key=True),
]


def upgrade():
    op.create_table("test_plans", *_KEY(),
                    sa.Column("changeset", sa.JSON, nullable=False), sa.Column("impact", sa.JSON, nullable=False),
                    sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("next_case", sa.Integer, nullable=False, server_default="0"))
    op.create_table("plan_slots", *_KEY(), sa.Column("slot_id", sa.String(200), primary_key=True),
                    sa.Column("data", sa.JSON, nullable=False), sa.Column("status", sa.String(20), nullable=False),
                    sa.Column("reserved_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
                    sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
                    sa.Column("assigned_to", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
                    sa.Column("case_id", sa.String(200), nullable=True))
    op.create_table("plan_items", *_KEY(), sa.Column("item_id", sa.String(200), primary_key=True),
                    sa.Column("kind", sa.String(20), nullable=False), sa.Column("payload", sa.JSON, nullable=False),
                    sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("published_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("trace_locks", *_KEY(), sa.Column("flow_id", sa.String(200), primary_key=True),
                    sa.Column("side", sa.String(10), primary_key=True),
                    sa.Column("holder_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("until", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for t in ("trace_locks", "plan_items", "plan_slots", "test_plans"):
        op.drop_table(t)
