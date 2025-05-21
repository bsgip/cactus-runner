import os
from typing import Generator


import pytest
from assertical.fixtures.environment import environment_snapshot
from assertical.fixtures.postgres import generate_async_conn_str_from_connection
from assertical.fixtures.fastapi import start_app_with_client
from envoy.server.alembic import upgrade
from psycopg import Connection
from envoy.admin.main import generate_app as admin_gen_app
from envoy.admin.settings import generate_settings as admin_gen_settings

from cactus_runner.app.envoy_admin_client import EnvoyAdminClient, EnvoyAdminClientAuthParams
from cactus_runner.app.database import initialise_database_connection
from tests.adapter import HttpxClientSessionAdapter


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


@pytest.fixture(scope="function")
async def envoy_admin_client(pg_base_config: Connection):
    """Creates an AsyncClient for a test that is configured to talk to the admin server app"""
    settings = admin_gen_settings()
    basic_auth = (settings.admin_username, settings.admin_password)

    # We want a new app instance for every test - otherwise connection pools get shared and we hit problems
    # when trying to run multiple tests sequentially
    app = admin_gen_app(settings)
    async with start_app_with_client(app, client_auth=basic_auth) as httpx_c:
        session = HttpxClientSessionAdapter(httpx_c)
        admin_client = EnvoyAdminClient(
            "http://test", EnvoyAdminClientAuthParams("", "")
        )  # NOTE: these are throw away variables, we replace instance next line
        admin_client._session = session
        yield admin_client
