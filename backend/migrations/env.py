from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from akshrava_backend.storage import Base

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
database_url = os.getenv("DATABASE_URL")
if database_url:
    # Alembic uses synchronous drivers.  Deployment supplies a normal postgres URL; SQLite is
    # kept useful for local migration rehearsal.
    config.set_main_option("sqlalchemy.url", database_url.replace("+asyncpg", "").replace("+aiosqlite", ""))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
