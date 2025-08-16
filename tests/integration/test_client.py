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
from cactus_test_definitions import CSIPAusVersion, TestProcedureId
from envoy.server.model.site import Site
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient, RunnerClientException
from cactus_runner.models import (
    ClientInteraction,
    InitResponseBody,
    RunnerStatus,
    StartResponseBody,
    StepStatus,
)
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
        (TestProcedureId.ALL_06, CSIPAusVersion.RELEASE_1_2, "my.example.domain", None, RAW_CERT_2, False),
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
):
    """Tests that the embedded client can interact with a full test stack"""

    # Interrogate the init response
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.init(
            session, test_procedure_id, csip_aus_version, aggregator_cert, device_cert, sub_domain
        )
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
        session.add(generate_class_instance(Site, aggregator_id=agg_id, site_id=None))
        await session.commit()

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
    assert_dict_type(str, StepStatus, status_response.step_status)
    assert_nowish(status_response.timestamp_status)

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
):
    """Tests that the embedded client handles failures where the combination of certificates is wrong"""

    # Interrogate the init response
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        # Look for a BAD_REQUEST (http 400)
        with pytest.raises(RunnerClientException, check=lambda e: "400" in str(e)):
            await RunnerClient.init(
                session,
                TestProcedureId.ALL_01,
                CSIPAusVersion.RELEASE_1_2,
                aggregator_certificate=aggregator_cert,
                device_certificate=device_cert,
                subscription_domain=None,
                run_id="abc123",
            )


@pytest.mark.slow
@pytest.mark.anyio
async def test_client_precondition_fails(cactus_runner_client: TestClient):
    """Tests that the embedded client handles failures where the combination of certificates is wrong"""

    aggregator_cert = RAW_CERT_1

    # Interrogate the init response
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        # Create an ALL-20 test session
        await RunnerClient.init(
            session,
            TestProcedureId.ALL_20,
            CSIPAusVersion.RELEASE_1_2,
            aggregator_certificate=aggregator_cert,
            device_certificate=None,
            subscription_domain=None,
            run_id="abc123",
        )

        # This test will expect preconditions to be met (eg registering an EndDevice) - if we try to start now
        # it should fail and report a useful error
        with pytest.raises(RunnerClientException) as exc_info:
            await RunnerClient.start(session)

        assert exc_info.value.http_status_code == HTTPStatus.PRECONDITION_FAILED
        assert exc_info.value.error_message, "Should have some details on the error"

        # This assertion is rather brittle (it's assuming error text in the CheckResult referencing EndDevice)
        assert "EndDevice" in exc_info.value.error_message, "There should be a reference to the missing EndDevice"
