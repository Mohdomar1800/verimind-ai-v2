"""Run Alembic migrations from code, so no alembic.ini is needed (`verimind db upgrade`)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if database_url:
        # ConfigParser treats '%' as interpolation; escape it (passwords may contain '%').
        cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def upgrade(database_url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def make_revision(message: str, database_url: str | None = None) -> None:
    """Developer helper: autogenerate a migration by diffing the models against the database.
    Files are numbered in order (0006_add_owner_index.py) so the folder reads as a history."""
    cfg = alembic_config(database_url)
    number = len(list((MIGRATIONS_DIR / "versions").glob("[0-9][0-9][0-9][0-9]_*.py"))) + 1
    cfg.set_main_option("file_template", f"{number:04d}_%%(slug)s")
    command.revision(cfg, message=message, autogenerate=True)
