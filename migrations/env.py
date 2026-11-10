"""Alembic migrations are the only means of changing live schemas."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from annotatiathon.config import get_settings
from annotatiathon.models import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
# ConfigParser treats percent signs in URL-encoded passwords as interpolation tokens.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(
        url=get_settings().database_url, target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection_config = config.get_section(config.config_ini_section, {})
    engine = engine_from_config(
        connection_config, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
