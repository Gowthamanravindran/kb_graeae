from pathlib import Path

from fastapi.testclient import TestClient

from kb_registry.api import create_app
from kb_registry.auth import hash_token, new_token
from kb_registry.migrate import upgrade
from kb_registry.models import ApiToken, Membership, Project, User
from kb_registry.settings import Settings
from kb_registry.storage import FileBlobStore


class Reg:
    """An in-process registry on SQLite + a folder of blobs, with helpers to seed it."""

    def __init__(self, tmp: Path, **settings):
        tmp.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{tmp / 'reg.db'}"
        upgrade(url)
        self.settings = Settings(database_url=url, storage=f"file://{tmp / 'blobs'}", **settings)
        self.app = create_app(self.settings, FileBlobStore(tmp / "blobs"))
        self.S = self.app.state.sessionmaker
        self.tokens: dict[str, str] = {}

    def user(self, email: str, admin: bool = False) -> str:
        with self.S() as s:
            u = User(email=email, name=email.split("@")[0], is_admin=admin)
            s.add(u)
            s.flush()
            t = new_token()
            s.add(ApiToken(user_id=u.id, name="test", token_hash=hash_token(t)))
            s.commit()
        self.tokens[email] = t
        return t

    def project(self, name: str, config_yaml: str = "") -> None:
        with self.S() as s:
            s.add(Project(name=name, config_yaml=config_yaml))
            s.commit()

    def grant(self, email: str, project: str, role: str) -> None:
        with self.S() as s:
            u = s.query(User).filter_by(email=email).one()
            p = s.query(Project).filter_by(name=project).one()
            s.merge(Membership(user_id=u.id, project_id=p.id, role=role))
            s.commit()

    def client(self, email: str | None = None) -> TestClient:
        c = TestClient(self.app)
        if email:
            c.headers["Authorization"] = f"Bearer {self.tokens[email]}"
        return c


def seeded(tmp: Path, **settings) -> Reg:
    r = Reg(tmp, **settings)
    r.project("mini")
    for email, role in [("maint@x", "maintainer"), ("contrib@x", "contributor"), ("view@x", "viewer")]:
        r.user(email)
        r.grant(email, "mini", role)
    r.user("admin@x", admin=True)
    r.user("stranger@x")
    return r
