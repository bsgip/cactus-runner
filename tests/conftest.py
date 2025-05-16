import os
from typing import Generator

import pytest
from assertical.fixtures.environment import environment_snapshot
from assertical.fixtures.postgres import generate_async_conn_str_from_connection
from envoy.server.alembic import upgrade
from psycopg import Connection

from cactus_runner.app.database import initialise_database_connection


def execute_test_sql_file(cfg: Connection, path_to_sql_file: str) -> None:
    with open(path_to_sql_file) as f:
        sql = f.read()
    with cfg.cursor() as cursor:
        cursor.execute(sql)
        cfg.commit()


@pytest.fixture
def preserved_environment():
    with environment_snapshot():
        yield


@pytest.fixture
def pg_empty_config(postgresql, request: pytest.FixtureRequest) -> Generator[Connection, None, None]:
    """Sets up the testing DB, applies alembic migrations but does NOT add any entities"""

    # Install the DATABASE_URL before running alembic
    postgres_dsn = generate_async_conn_str_from_connection(postgresql)
    os.environ["DATABASE_URL"] = postgres_dsn

    # Run alembic migration
    upgrade()

    # Init connection
    initialise_database_connection(postgres_dsn)

    yield postgresql


@pytest.fixture
def pg_base_config(pg_empty_config):
    """Adds a very minimal config to the database from base_config.sql"""
    execute_test_sql_file(pg_empty_config, "tests/data/sql/base_config.sql")

    yield pg_empty_config


@pytest.fixture
def anyio_backend():
    """async backends to test against
    see: https://anyio.readthedocs.io/en/stable/testing.html"""
    return "asyncio"
