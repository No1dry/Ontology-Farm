"""PostgreSQL engine and explicit transaction unit of work."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from lzo.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})


class PgUnitOfWork:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.session: Session

    def __enter__(self) -> "PgUnitOfWork":
        self.session = Session(self.engine, expire_on_commit=False)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.session.in_transaction():
                self.session.rollback()
        finally:
            self.session.close()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
