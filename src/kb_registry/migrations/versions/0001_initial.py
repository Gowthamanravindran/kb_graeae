"""initial tables"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None

TZ = sa.DateTime(timezone=True)


def upgrade():
    op.create_table("projects",
                    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                    sa.Column("name", sa.String(200), nullable=False, unique=True),
                    sa.Column("config_yaml", sa.Text, nullable=False, server_default=""),
                    sa.Column("created_at", TZ, nullable=False))
    op.create_table("users",
                    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                    sa.Column("email", sa.String(320), nullable=False, unique=True),
                    sa.Column("name", sa.String(200), nullable=False, server_default=""),
                    sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
                    sa.Column("oidc_sub", sa.String(255), nullable=True, unique=True),
                    sa.Column("created_at", TZ, nullable=False))
    op.create_table("memberships",
                    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
                    sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), primary_key=True),
                    sa.Column("role", sa.Enum("viewer", "contributor", "maintainer", name="role"), nullable=False))
    op.create_table("api_tokens",
                    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("name", sa.String(200), nullable=False, server_default=""),
                    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
                    sa.Column("created_at", TZ, nullable=False),
                    sa.Column("last_used_at", TZ, nullable=True))
    op.create_table("blobs",
                    sa.Column("hash", sa.String(64), primary_key=True),
                    sa.Column("size", sa.Integer, nullable=False),
                    sa.Column("created_at", TZ, nullable=False))
    op.create_table("bases",
                    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
                    sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
                    sa.Column("branch", sa.String(255), nullable=False),
                    sa.Column("commits_key", sa.String(64), nullable=False),
                    sa.Column("commits", sa.JSON, nullable=False),
                    sa.Column("manifest", sa.JSON, nullable=False),
                    sa.Column("version", sa.Integer, nullable=False),
                    sa.Column("parent", sa.Integer, nullable=True),
                    sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("created_at", TZ, nullable=False),
                    sa.UniqueConstraint("project_id", "branch", "commits_key"),
                    sa.UniqueConstraint("project_id", "branch", "version"))
    op.create_table("base_files",
                    sa.Column("base_id", sa.Integer, sa.ForeignKey("bases.id"), primary_key=True),
                    sa.Column("path", sa.String(300), primary_key=True),
                    sa.Column("blob_hash", sa.String(64), sa.ForeignKey("blobs.hash"), nullable=False))
    op.create_index("ix_base_files_blob_hash", "base_files", ["blob_hash"])
    op.create_table("overlay_items",
                    sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), primary_key=True),
                    sa.Column("branch", sa.String(255), primary_key=True),
                    sa.Column("item_id", sa.String(200), primary_key=True),
                    sa.Column("kind", sa.String(20), nullable=False),
                    sa.Column("payload", sa.JSON, nullable=False),
                    sa.Column("payload_sha", sa.String(64), nullable=False),
                    sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("base_id", sa.Integer, sa.ForeignKey("bases.id"), nullable=False),
                    sa.Column("published_at", TZ, nullable=False))


def downgrade():
    for t in ("overlay_items", "base_files", "bases", "blobs", "api_tokens", "memberships", "users", "projects"):
        op.drop_table(t)
    sa.Enum(name="role").drop(op.get_bind(), checkfirst=True)
