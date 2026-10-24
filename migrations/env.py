"""Alembic environment: runs migrations against settings.database_url
(or the URL a caller sets in the Alembic config, as the tests do)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from insureassist.config import settings
from insureassist.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url") or settings.database_url
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
