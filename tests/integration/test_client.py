import io
import zipfile
from datetime import datetime
from http import HTTPStatus
from urllib.parse import quote

import pytest
from aiohttp import ClientSession, ClientTimeout
from assertical.asserts.time import assert_nowish
from assertical.asserts.type import assert_dict_type
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from cactus_schema.runner import (
    ClientInteraction,
    InitResponseBody,
    RequestData,
    RequestList,
    RunnerStatus,
    RunRequest,
    StartResponseBody,
    StepInfo,
    StepStatus,
)
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from envoy.server.model.site import Site, SiteDER, SiteDERSetting
from pytest_aiohttp.plugin import TestClient

from cactus_runner.app.database import remove_database_connection
from cactus_runner.app.requests_archive import ensure_request_data_dir
from cactus_runner.client import RunnerClient, RunnerClientException
from tests.integration.certificate1 import (
    TEST_CERTIFICATE_PEM as TEST_CERTIFICATE_1_PEM,
)
from tests.integration.certificate2 import (
    TEST_CERTIFICATE_PEM as TEST_CERTIFICATE_2_PEM,
)

RAW_CERT_1 = TEST_CERTIFICATE_1_PEM.decode()
URI_ENCODED_CERT_1 = quote(RAW_CERT_1)

RAW_CERT_2 = TEST_CERTIFICATE_2_PEM.decode()
URI_ENCODED_CERT_2 = quote(RAW_CERT_2)


@pytest.mark.parametrize(
    "test_procedure_id, csip_aus_version, sub_domain, aggregator_cert, device_cert, expect_immediate_start",
    [
        (TestProcedureId.ALL_01, CSIPAusVersion.BETA_1_3_STORAGE, None, RAW_CERT_1, None, True),
        (TestProcedureId.GEN_01, CSIPAusVersion.RELEASE_1_2, "my.example.domain", None, RAW_CERT_2, False),
    ],
)
@pytest.mark.slow
@pytest.mark.anyio
async def test_client_interactions(
    cactus_runner_client: TestClient,
    pg_base_config,
    test_procedure_id: TestProcedureId,
    csip_aus_version: CSIPAusVersion,
    sub_domain: str | None,
    aggregator_cert: str | None,
    device_cert: str | None,
    expect_immediate_start: bool,
    run_request_generator,
):
    """Tests that the embedded client can interact with a full test stack"""

    # Interrogate the init response
    run_request: RunRequest = run_request_generator(
        test_procedure_id, aggregator_cert, device_cert, csip_aus_version, sub_domain
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
    assert isinstance(init_response, InitResponseBody)
    assert init_response.test_procedure == test_procedure_id.value
    assert isinstance(init_response.status, str)
    assert isinstance(init_response.timestamp, datetime)
    assert init_response.is_started is expect_immediate_start
    assert_nowish(init_response.timestamp)

    # Slight workaround - lets simulate an EndDevice being registered (whether we are in pre-start or start doesn't
    # matter)
    async with generate_async_session(pg_base_config) as session:
        agg_id = 1 if aggregator_cert is not None else 0
        new_site = generate_class_instance(Site, aggregator_id=agg_id, site_id=None)
        session.add(new_site)

        new_der = generate_class_instance(SiteDER, site=new_site)
        session.add(new_der)

        new_der_settings = generate_class_instance(SiteDERSetting, site_der=new_der, max_w_multiplier=0)
        session.add(new_der_settings)

        await session.commit()

    # Manually create a test request/response file pair
    storage_dir = ensure_request_data_dir()
    test_request_id = 1
    request_file = storage_dir / f"{test_request_id:03d}-test.request"
    response_file = storage_dir / f"{test_request_id:03d}-test.response"

    request_file.write_text("GET /test HTTP/1.1\nHost: example.com\n\nTest request body")
    response_file.write_text('HTTP/1.1 200 OK\nContent-Type: application/json\n\n{"status": "ok"}')

    # Interrogate start response (if it's an immediate start - you shouldn't be able to start it - it should be started)
    if expect_immediate_start:
        async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
            with pytest.raises(RunnerClientException):
                await RunnerClient.start(session)
    else:
        async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
            start_response = await RunnerClient.start(session)
        assert isinstance(start_response, StartResponseBody)
        assert start_response.test_procedure == test_procedure_id.value
        assert isinstance(start_response.status, str)
        assert isinstance(start_response.timestamp, datetime)
        assert_nowish(start_response.timestamp)

    # Interrogate status response
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        status_response = await RunnerClient.status(session)
    assert isinstance(status_response, RunnerStatus)
    assert isinstance(status_response.status_summary, str)
    assert isinstance(status_response.csip_aus_version, str)
    assert status_response.test_procedure_name == test_procedure_id.value
    assert status_response.csip_aus_version == csip_aus_version.value
    assert isinstance(status_response.last_client_interaction, ClientInteraction)
    assert_dict_type(str, StepInfo, status_response.step_status)
    assert_nowish(status_response.timestamp_status)

    # Interrogate list_requests response
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        request_list_response = await RunnerClient.list_requests(session)
    assert isinstance(request_list_response, RequestList)
    assert isinstance(request_list_response.request_ids, list)
    assert isinstance(request_list_response.count, int)
    assert request_list_response.count == len(request_list_response.request_ids)
    assert request_list_response.count >= 1
    assert test_request_id in request_list_response.request_ids

    # Interrogate get_request response
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        request_data_response = await RunnerClient.get_request(session, test_request_id)
    assert isinstance(request_data_response, RequestData)
    assert request_data_response.request_id == test_request_id
    assert request_data_response.request == "GET /test HTTP/1.1\nHost: example.com\n\nTest request body"
    assert request_data_response.response == 'HTTP/1.1 200 OK\nContent-Type: application/json\n\n{"status": "ok"}'

    # Interrogate a finalize response (assume we don't fire off any CSIP requests)
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        finalize_response = await RunnerClient.finalize(session)
    zip = zipfile.ZipFile(io.BytesIO(finalize_response))

    def get_filename(prefix: str, filenames: list[str]) -> str:
        """Find first filename that starts with 'prefix'"""
        for filename in filenames:
            if filename.startswith(prefix):
                return filename
        return ""

    summary_data = zip.read(get_filename(prefix="CactusTestProcedureSummary", filenames=zip.namelist()))
    assert len(summary_data) > 0
    summary = RunnerStatus.from_json(summary_data.decode())
    assert summary.step_status == status_response.step_status, "This shouldn't have changed between status and finalize"


@pytest.mark.parametrize(
    "aggregator_cert, device_cert",
    [
        (RAW_CERT_1, RAW_CERT_2),  # Can't register two certs at the same time
        (None, None),  # Must register one cert
    ],
)
@pytest.mark.slow
@pytest.mark.anyio
async def test_client_init_bad_cert_combos(
    cactus_runner_client: TestClient,
    aggregator_cert: str | None,
    device_cert: str | None,
    run_request_generator,
):
    """Tests that the embedded client handles failures where the combination of certificates is wrong"""

    # Interrogate the init response
    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_01, aggregator_cert, device_cert, CSIPAusVersion.RELEASE_1_2, None
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        # Look for a BAD_REQUEST (http 400)
        with pytest.raises(RunnerClientException, check=lambda e: "400" in str(e)):
            await RunnerClient.initialise(session, run_request)


@pytest.mark.slow
@pytest.mark.anyio
async def test_client_precondition_fails(cactus_runner_client: TestClient, run_request_generator):
    """Tests that the embedded client handles failures where the combination of certificates is wrong"""

    aggregator_cert = RAW_CERT_1
    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_20, aggregator_cert, None, CSIPAusVersion.RELEASE_1_2, None
    )

    # Interrogate the init response
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        # Create an ALL-20 test session
        await RunnerClient.initialise(session, run_request)

        # This test will expect preconditions to be met (eg registering an EndDevice) - if we try to start now
        # it should fail and report a useful error
        with pytest.raises(RunnerClientException) as exc_info:
            await RunnerClient.start(session)

        assert exc_info.value.http_status_code == HTTPStatus.PRECONDITION_FAILED
        assert exc_info.value.error_message, "Should have some details on the error"

        # This assertion is rather brittle (it's assuming error text in the CheckResult referencing EndDevice)
        assert "EndDevice" in exc_info.value.error_message, "There should be a reference to the missing EndDevice"


@pytest.mark.slow
@pytest.mark.anyio
async def test_status_steps_immediate_start(cactus_runner_client: TestClient, pg_base_config, run_request_generator):
    """Tests that the embedded client will"""

    aggregator_cert = RAW_CERT_1

    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_01, aggregator_cert, None, CSIPAusVersion.RELEASE_1_2, None
    )

    # Init and then fetch status
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started, "ALL-01 should be immediate start"

        status_response = await RunnerClient.status(session)

    assert isinstance(status_response, RunnerStatus)
    assert all(isinstance(s, StepInfo) for s in status_response.step_status.values())

    step_status_counts: dict[StepStatus, int] = {}
    for step_info in status_response.step_status.values():
        status = step_info.get_step_status()
        step_status_counts[status] = step_status_counts.get(status, 0) + 1

    assert step_status_counts.get(StepStatus.ACTIVE, 0) == 1, "One step should initially be active"
    assert step_status_counts.get(StepStatus.RESOLVED, 0) == 0, "No steps should be resolved at the start"

    assert status_response.timeline is not None, "Timeline should've generated something"


@pytest.mark.slow
@pytest.mark.anyio
async def test_pre_init_status(cactus_runner_client: TestClient):
    """Tests that the embedded client will allow fetching of a status before init"""

    # Init and then fetch status
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        status_response = await RunnerClient.status(session)
    assert isinstance(status_response, RunnerStatus)
    assert isinstance(status_response.status_summary, str)
    assert status_response.status_summary


@pytest.mark.slow
@pytest.mark.anyio
async def test_health_ok(cactus_runner_client: TestClient, envoy_admin_client):
    """Tests that the embedded client will return True for the server health check"""

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        health_response = await RunnerClient.health(session)
        assert health_response is True


@pytest.mark.slow
@pytest.mark.anyio
async def test_health_db_dead(cactus_runner_client: TestClient, envoy_admin_client):
    """Tests that the embedded client will return False for the server health check if the DB is down"""

    remove_database_connection()
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        health_response = await RunnerClient.health(session)
        assert health_response is False


@pytest.mark.slow
@pytest.mark.anyio
async def test_health_admin_api_dead(cactus_runner_client_faulty_admin: TestClient):
    """Tests that the embedded client will return False for the server health check if the admin API isn't configured"""

    async with ClientSession(
        base_url=cactus_runner_client_faulty_admin.make_url("/"), timeout=ClientTimeout(30)
    ) as session:
        health_response = await RunnerClient.health(session)
        assert health_response is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "cactus_runner_client_with_mount_point, test_paths",
    [
        (
            "/api/v1",
            [
                ("/api/v1/health", 200),  # Correct
                ("/api/v1/status", 200),  # Correct
                ("/health", 404),  # Missing prefix
                ("/api/v2/health", 404),  # Wrong prefix
                ("/api/v1extra/health", 404),  # without slash
            ],
        ),
        (
            "/mount/point",
            [
                ("/mount/point/health", 200),
                ("/health", 404),
                ("/mount/health", 404),  # Partial
                ("/mount/pointextra/health", 404),  # No slash
            ],
        ),
        (
            "",  # empty (root)
            [
                ("/health", 200),
                ("/status", 200),
                ("/api/v1/health", 400),  # Non-existent path matches catch-all, no test procedure active
            ],
        ),
    ],
    indirect=["cactus_runner_client_with_mount_point"],
)
async def test_mount_point_routing_protection(
    cactus_runner_client_with_mount_point: TestClient,
    test_paths: list[tuple[str, int]],
):
    """Verify mount point routing works correctly with different configurations.

    NOTE: MOUNT_POINT is hardcoded in production. This is a defensive test to ensure
    the routing logic in create_app() correctly handles mount points if the value changes.

    The aiohttp router should reject paths that don't match the mount point pattern
    BEFORE they reach proxied_request_handler.
    """
    async with ClientSession(
        base_url=cactus_runner_client_with_mount_point.make_url("/"), timeout=ClientTimeout(30)
    ) as session:
        for path, expected_status in test_paths:
            async with session.get(path) as response:
                assert response.status == expected_status
