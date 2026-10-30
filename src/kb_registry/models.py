from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kb_registry.db import Base

ROLES = ("viewer", "contributor", "maintainer")


def now() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    config_yaml: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Membership(Base):
    __tablename__ = "memberships"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Enum(*ROLES, name="role"))


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Blob(Base):
    __tablename__ = "blobs"
    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BaseRow(Base):
    __tablename__ = "bases"
    __table_args__ = (UniqueConstraint("project_id", "branch", "commits_key"),
                      UniqueConstraint("project_id", "branch", "version"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    branch: Mapped[str] = mapped_column(String(255))
    commits_key: Mapped[str] = mapped_column(String(64))
    commits: Mapped[dict] = mapped_column(JSON)
    manifest: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer)
    parent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BaseFile(Base):
    __tablename__ = "base_files"
    base_id: Mapped[int] = mapped_column(ForeignKey("bases.id"), primary_key=True)
    path: Mapped[str] = mapped_column(String(300), primary_key=True)
    blob_hash: Mapped[str] = mapped_column(ForeignKey("blobs.hash"))


class OverlayItem(Base):
    __tablename__ = "overlay_items"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    branch: Mapped[str] = mapped_column(String(255), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_sha: Mapped[str] = mapped_column(String(64))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    base_id: Mapped[int] = mapped_column(ForeignKey("bases.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# ---- stage 5b: test plans shared by a team (separate tables: reservations need row locks)

class TestPlanRow(Base):
    __test__ = False
    __tablename__ = "test_plans"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    branch: Mapped[str] = mapped_column(String(255), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    changeset: Mapped[dict] = mapped_column(JSON)
    impact: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    next_case: Mapped[int] = mapped_column(Integer, default=0)  # case ids are handed out here, unique per plan


class PlanSlotRow(Base):
    __tablename__ = "plan_slots"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    branch: Mapped[str] = mapped_column(String(255), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    slot_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    reserved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class PlanItemRow(Base):
    __tablename__ = "plan_items"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    branch: Mapped[str] = mapped_column(String(255), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TraceLockRow(Base):
    __tablename__ = "trace_locks"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    branch: Mapped[str] = mapped_column(String(255), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    flow_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    side: Mapped[str] = mapped_column(String(10), primary_key=True)
    holder_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
