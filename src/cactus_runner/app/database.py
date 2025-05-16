import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


class DatabaseNotInitialisedError(Exception):
    """Raised if using a function from this module without successfully calling initialise_database_connection"""

    pass


@dataclass
class DatabaseConnection:
    """Describes a configured connection to the database"""

    postgres_dsn: str
    engine: AsyncEngine
    session_maker: async_sessionmaker[AsyncSession]


CURRENT_CONNECTION: DatabaseConnection | None = None


def initialise_database_connection(postgres_dsn: str) -> None:
    global CURRENT_CONNECTION

    engine = create_async_engine(postgres_dsn)
    CURRENT_CONNECTION = DatabaseConnection(postgres_dsn, engine, async_sessionmaker(engine, class_=AsyncSession))


def begin_session() -> AsyncSession:
    """Creates a new session for interacting with the database - this should be used with a context manager eg:

    async with begin_session() as session:
        session.add(...)

    initialise_database_connection must have been called otherwise this will raise a DatabaseNotInitialisedError
    """
    if not CURRENT_CONNECTION:
        raise DatabaseNotInitialisedError("Ensure initialise_database_connection has been called.")

    return CURRENT_CONNECTION.session_maker()


def open_connection() -> AsyncConnection:
    """Creates a new raw database connection for interacting with the database. This should be used with a context
    manager eg:

    async with open_connection() as conn:
        conn.cursor()
        conn.commit()

    initialise_database_connection must have been called otherwise this will raise a DatabaseNotInitialisedError
    """
    if not CURRENT_CONNECTION:
        raise DatabaseNotInitialisedError("Ensure initialise_database_connection has been called.")

    return CURRENT_CONNECTION.engine.connect()


def get_postgres_dsn() -> str:
    """Fetches the postgres DSN (connection string) used by the currently initialised connection.

    initialise_database_connection must have been called otherwise this will raise a DatabaseNotInitialisedError"""
    if not CURRENT_CONNECTION:
        raise DatabaseNotInitialisedError("Ensure initialise_database_connection has been called.")

    return CURRENT_CONNECTION.postgres_dsn
