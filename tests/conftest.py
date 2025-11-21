import os
import shutil
import unittest.mock as mock
from http import HTTPStatus
from pathlib import Path
from typing import Callable, Generator
from urllib.parse import urlparse

import aiohttp.web as web
import pytest
from assertical.fixtures.environment import environment_snapshot
from assertical.fixtures.fastapi import start_app_with_client
from assertical.fixtures.postgres import generate_async_conn_str_from_connection
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from cactus_test_definitions.client.test_procedures import get_yaml_contents
from envoy.admin.main import generate_app as admin_gen_app
from envoy.admin.settings import generate_settings as admin_gen_settings
from envoy.server.alembic import upgrade
from envoy.server.main import generate_app as envoy_gen_app
from envoy.server.settings import generate_settings as envoy_gen_settings
from multidict import CIMultiDict
from psycopg import Connection

from cactus_runner.app.database import (
    initialise_database_connection,
    remove_database_connection,
)
from cactus_runner.app.envoy_admin_client import (
    EnvoyAdminClient,
    EnvoyAdminClientAuthParams,
)
from cactus_runner.app.main import create_app
from cactus_runner.app.requests_archive import REQUEST_DATA_DIR
from cactus_runner.models import (
    RunGroup,
    RunRequest,
    TestCertificates,
    TestConfig,
    TestDefinition,
    TestUser,
)
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
def pg_empty_config(
    postgresql, preserved_environment, request: pytest.FixtureRequest
) -> Generator[Connection, None, None]:
    """Sets up the testing DB, applies alembic migrations but does NOT add any entities"""

    # Install the DATABASE_URL before running alembic
    postgres_dsn = generate_async_conn_str_from_connection(postgresql)
    os.environ["DATABASE_URL"] = postgres_dsn

    # Run alembic migration
    upgrade()

    # Init connection
    initialise_database_connection(postgres_dsn)

    yield postgresql

    # Remove connection after tests
    remove_database_connection()


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
async def envoy_admin_client(pg_empty_config: Connection):
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


@pytest.fixture
def ensure_logs_dir():
    """Ensures that the logs directory exists"""
    dir = Path("./logs/")
    exists = dir.exists()

    if not exists:
        dir.mkdir()


@pytest.fixture(scope="function")
async def envoy_server_client(pg_empty_config: Connection):
    """Creates an AsyncClient for a test that is configured to talk to the envoy server app"""

    # We want our tests to operate under the assumption that device registration is enabled
    os.environ["ALLOW_DEVICE_REGISTRATION"] = "true"

    settings = envoy_gen_settings()
    settings.cert_header = "ssl-client-cert"

    # We want a new app instance for every test - otherwise connection pools get shared and we hit problems
    # when trying to run multiple tests sequentially
    app = envoy_gen_app(settings)
    async with start_app_with_client(app) as envoy_client:

        async def envoy_proxy(method: str, headers: CIMultiDict[str], remote_url: str, request_body: bytes):
            # This will come in as fully qualified URI - we want to proxy only the path / query params
            parsed_url = urlparse(remote_url)
            if parsed_url.query:
                proxy_url = parsed_url.path + "?" + parsed_url.query
            else:
                proxy_url = parsed_url.path

            headers = {k: v for k, v in headers.items()}

            response = await envoy_client.request(method, proxy_url, headers=headers, data=request_body)
            response_headers = response.headers.copy()
            return web.Response(headers=response_headers, status=HTTPStatus(response.status_code), body=response.read())

        # We need to substitute out the "normal" HTTP call to envoy with a call to "envoy_client" instead
        # Patch the proxy module to push all envoy requests through to the testing app we just created
        with mock.patch("cactus_runner.app.proxy.do_proxy", side_effect=envoy_proxy):
            yield envoy_client


@pytest.fixture
async def cactus_runner_client(
    pg_empty_config, aiohttp_client, envoy_server_client, envoy_admin_client, ensure_logs_dir
):
    # Clear request data before test
    if REQUEST_DATA_DIR.exists():
        shutil.rmtree(REQUEST_DATA_DIR)

    with environment_snapshot():
        with mock.patch("cactus_runner.app.main.generate_admin_client") as mock_generate_admin_client:
            mock_generate_admin_client.return_value = envoy_admin_client
            async with await aiohttp_client(create_app()) as app:
                yield app


@pytest.fixture
async def cactus_runner_client_faulty_admin(pg_empty_config, aiohttp_client, envoy_server_client, ensure_logs_dir):
    with environment_snapshot():
        async with await aiohttp_client(create_app()) as app:
            yield app


@pytest.fixture
async def cactus_runner_client_with_mount_point(aiohttp_client, envoy_admin_client, request):
    """Client with configurable mount point.

    NOTE: MOUNT_POINT is hardcoded in production. This fixture allows testing
    different configurations to verify routing logic is correct.
    """
    mount_point = getattr(request, "param", "")

    with environment_snapshot():
        with mock.patch("cactus_runner.app.main.MOUNT_POINT", mount_point):
            with mock.patch("cactus_runner.app.main.generate_admin_client") as mock_generate_admin_client:
                mock_generate_admin_client.return_value = envoy_admin_client
                async with await aiohttp_client(create_app()) as app:
                    yield app


@pytest.fixture
def run_request_generator() -> (
    Callable[[TestProcedureId, str | None, str | None, CSIPAusVersion, str | None], RunRequest]
):
    """Yields a function for generating a RunRequest when supplied with a TestProcedureId"""

    def _generate_run_request(
        tp_id: TestProcedureId,
        agg_cert: str | None,
        device_cert: str | None,
        version: CSIPAusVersion,
        sub_domain: str | None,
    ) -> RunRequest:
        yaml_definition = get_yaml_contents(tp_id)
        return RunRequest(
            run_id="abc-123",
            test_definition=TestDefinition(test_procedure_id=tp_id, yaml_definition=yaml_definition),
            run_group=RunGroup(
                run_group_id="1",
                name="group 1",
                csip_aus_version=version,
                test_certificates=TestCertificates(
                    aggregator=agg_cert,
                    device=device_cert,
                ),
            ),
            test_config=TestConfig(pen=12345, subscription_domain=sub_domain, is_static_url=False),
            test_user=TestUser(user_id="123", name="User 123"),
        )

    return _generate_run_request
