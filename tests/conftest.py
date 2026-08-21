import os
import shutil
import subprocess
import unittest.mock as mock
from collections.abc import Callable, Generator
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlparse

import aiohttp.web as web
import apluggy
import psycopg
import pytest
from assertical.fixtures.environment import environment_snapshot
from assertical.fixtures.fastapi import start_app_with_client
from assertical.fixtures.postgres import generate_async_conn_str_from_connection
from cactus_schema.runner import (
    RunGroup,
    RunRequest,
    TestCertificates,
    TestConfig,
    TestDefinition,
    TestUser,
)
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
from pytest_postgresql.executor import PostgreSQLExecutor
from pytest_postgresql.janitor import DatabaseJanitor

from cactus_runner.app.database import (
    initialise_database_connection,
    remove_database_connection,
)
from cactus_runner.app.main import create_app
from cactus_runner.app.requests_archive import REQUEST_DATA_DIR
from cactus_runner.plugin.backends.envoy.admin_client import (
    EnvoyAdminClient,
    EnvoyAdminClientAuthParams,
)
from cactus_runner.plugin.backends.hookspec import (
    BackendSpec,
    DefaultEnvoyPlugin,
    project_name,
)
from tests.adapter import HttpxClientSessionAdapter

# Name of the throwaway database used (once per test session) to run the full alembic migration
# chain against so its resulting schema/data can be dumped for pg_migrated_schema_dump
MIGRATED_SCHEMA_DB_NAME = "envoy_test_migrated_schema"


def execute_sql_for_connection(cfg: Connection, sql: str) -> None:
    with cfg.cursor() as cursor:
        cursor.execute(sql)  # type: ignore
        cfg.commit()


def execute_test_sql_file(cfg: Connection, path_to_sql_file: str) -> None:
    with open(path_to_sql_file) as f:
        sql = f.read()
    with cfg.cursor() as cursor:
        cursor.execute(sql)  # type: ignore
        cfg.commit()


@pytest.fixture
def preserved_environment():
    with environment_snapshot():
        yield


@pytest.fixture(scope="session")
def pg_migrated_schema_dump(postgresql_proc: PostgreSQLExecutor) -> Generator[str, None, None]:
    """Runs ONCE for the entire test session.

    Creates a dedicated (throwaway) database on the shared postgres instance, runs the full chain
    of alembic migrations against it (via upgrade()) and exports the resulting schema - plus any
    data seeded by the migrations themselves (e.g. default SiteControlGroup/SiteDER rows) - as a
    plain SQL dump via pg_dump.

    pg_empty_config applies this dump directly to each test's (already empty) database rather
    than re-running the full alembic migration chain for every single test - this is a LOT
    quicker as alembic has to plan/execute dozens of migrations individually whereas applying a
    flat SQL dump is comparatively instant.
    """

    janitor = DatabaseJanitor(
        user=postgresql_proc.user,
        host=postgresql_proc.host,
        port=postgresql_proc.port,
        version=postgresql_proc.version,
        dbname=MIGRATED_SCHEMA_DB_NAME,
        password=postgresql_proc.password,
    )
    janitor.init()
    try:
        with environment_snapshot():
            migration_conn = psycopg.connect(
                dbname=MIGRATED_SCHEMA_DB_NAME,
                user=postgresql_proc.user,
                password=postgresql_proc.password,
                host=postgresql_proc.host,
                port=postgresql_proc.port,
            )
            try:
                os.environ["DATABASE_URL"] = generate_async_conn_str_from_connection(migration_conn)

                # This will install all of the alembic migrations - DB is accessed via DATABASE_URL
                upgrade()
            finally:
                migration_conn.close()

        # Resolve pg_dump as a sibling of the pg_ctl binary actually running this instance (rather
        # than relying on "pg_dump" from PATH) - on Debian/Ubuntu, /usr/bin/pg_dump is a wrapper
        # that picks whichever postgres version is "latest" installed when it can't match the
        # target host/port to a locally registered cluster (which a throwaway pytest-postgresql
        # instance never is). That mismatched-version pg_dump can emit syntax the actual server
        # doesn't understand (e.g. PG18's "SET transaction_timeout = 0;" against a PG16 server).
        pg_dump_exe = str(Path(postgresql_proc.executable).parent / "pg_dump")

        pg_dump_result = subprocess.run(
            [
                pg_dump_exe,
                "--inserts",  # Emit data as INSERT statements (instead of COPY) so it can be replayed via psycopg
                "--no-owner",
                "--no-privileges",
                "-h",
                str(postgresql_proc.host),
                "-p",
                str(postgresql_proc.port),
                "-U",
                postgresql_proc.user,
                "-d",
                MIGRATED_SCHEMA_DB_NAME,
            ],
            env={**os.environ, "PGPASSWORD": postgresql_proc.password or ""},
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        janitor.drop()

    # pg_dump (PG 18+) wraps its output in psql-only "\restrict"/"\unrestrict" meta-commands that
    # aren't valid SQL and break execution via psycopg - strip them out, they only guard against
    # psql executing arbitrary functions mid-restore which isn't a concern for this test dump.
    dump_sql = "\n".join(
        line
        for line in pg_dump_result.stdout.splitlines()
        if not line.startswith("\\restrict") and not line.startswith("\\unrestrict")
    )

    yield dump_sql


@pytest.fixture
def pg_empty_config(
    postgresql, preserved_environment, pg_migrated_schema_dump: str, request: pytest.FixtureRequest
) -> Generator[Connection, None, None]:
    """Sets up the testing DB, applies alembic migrations but does NOT add any entities"""

    # Install the DATABASE_URL before running alembic
    postgres_dsn = generate_async_conn_str_from_connection(postgresql)
    os.environ["DATABASE_URL"] = postgres_dsn

    # Rather than re-running the full (slow) alembic migration chain against this test's database,
    # apply the schema/data dump exported once per session by pg_migrated_schema_dump - this is
    # functionally equivalent to calling upgrade() but a lot quicker.
    execute_sql_for_connection(postgresql, pg_migrated_schema_dump)

    # pg_dump's preamble resets this connection's search_path to '' (it fully schema-qualifies
    # everything it emits so it doesn't need one) - restore the normal default so any unqualified
    # SQL run against this connection for the rest of the test resolves as expected.
    execute_sql_for_connection(postgresql, "SET search_path TO public")

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
        admin_client._session = session  # type: ignore
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

            response = await envoy_client.request(
                method,
                proxy_url,
                headers=headers,
                data=request_body,  # type: ignore
            )
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
        # TODO: [JCrowley 21/08/2026] This fixture is specifically tailored to an envoy backend. It would be good to
        # split this out into a separate generic backend kind to enable agnostic testing.
        with mock.patch("cactus_runner.app.main.create_plugin_manager") as mock_create_plugin_manager:
            pm = apluggy.PluginManager(project_name)
            pm.add_hookspecs(BackendSpec)
            pm.register(DefaultEnvoyPlugin(admin_client=envoy_admin_client))
            mock_create_plugin_manager.return_value = pm
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
        # TODO: [JCrowley 21/08/2026] This fixture is specifically tailored to an envoy backend. It would be good to
        # split this out into a separate generic backend kind to enable agnostic testing.
        with mock.patch("cactus_runner.app.main.MOUNT_POINT", mount_point):
            with mock.patch("cactus_runner.app.main.create_plugin_manager") as mock_create_plugin_manager:
                pm = apluggy.PluginManager(project_name)
                pm.add_hookspecs(BackendSpec)
                pm.register(DefaultEnvoyPlugin(admin_client=envoy_admin_client))
                mock_create_plugin_manager.return_value = pm
                async with await aiohttp_client(create_app()) as app:
                    yield app


@pytest.fixture
def run_request_generator() -> Callable[
    [TestProcedureId, str | None, str | None, CSIPAusVersion, str | None], RunRequest
]:
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
