import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from kb_registry.db import make_engine, make_sessionmaker
from kb_registry.migrate import upgrade
from kb_registry.models import BaseRow, Project, User
from kb_registry.paths import is_allowed_path

TABLES = {"projects", "users", "memberships", "api_tokens", "blobs", "bases", "base_files", "overlay_items"}


def test_migration_creates_all_tables(db_url):
    upgrade(db_url)
    names = set(inspect(make_engine(db_url)).get_table_names())
    assert TABLES <= names


def test_unique_base_per_commit_set(db_url):
    upgrade(db_url)
    S = make_sessionmaker(make_engine(db_url))
    with S() as s:
        p, u = Project(name="sop"), User(email="a@x", name="A")
        s.add_all([p, u])
        s.flush()
        kw = dict(project_id=p.id, branch="main", commits_key="k1", commits={"a": "1"}, manifest={}, created_by=u.id)
        s.add(BaseRow(version=1, **kw))
        s.flush()
        s.add(BaseRow(version=2, **kw))
        with pytest.raises(IntegrityError):
            s.flush()


@pytest.mark.parametrize("path,ok", [
    ("manifest.json", True), ("base/links.json", True), ("base/modules.json", True), ("base/system.md", True),
    ("base/graph/sop-web.json", True), ("base/graph/a.b_c.json", True),
    ("base/graph/../x.json", False), ("/etc/passwd", False), ("base/index.sqlite", False),
    ("base/graph/a/b.json", False), ("base/../manifest.json", False), ("index.sqlite", False), ("", False),
])
def test_allowed_paths(path, ok):
    assert is_allowed_path(path) is ok
