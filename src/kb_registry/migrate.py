from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade(database_url: str) -> None:
    """Bring the database to the latest schema (alembic upgrade head)."""
    cfg = Config(str(Path(__file__).parent / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(cfg, "head")
