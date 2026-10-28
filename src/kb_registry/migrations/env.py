from alembic import context

from kb_registry.db import Base, make_engine
from kb_registry import models  # noqa: F401  (register tables)

url = context.config.get_main_option("sqlalchemy.url")
engine = make_engine(url)
with engine.connect() as conn:
    context.configure(connection=conn, target_metadata=Base.metadata, render_as_batch=url.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()
