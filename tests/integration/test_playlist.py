import io
import zipfile
from urllib.parse import quote

import pytest
from aiohttp import ClientSession, ClientTimeout
from cactus_schema.runner import RunnerStatus, RunRequest, uri
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient, RunnerClientError, ensure_success_response
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


async def run_all_01_requests(client: TestClient) -> None:
    """Make the standard requests needed to complete an ALL-01 test."""
    result = await client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)
    result = await client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)
    result = await client.get("/tm", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)
    result = await client.get("/edev/1/der", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)


def verify_zip_contents(zip_data: bytes, expected_test_name: str) -> None:
    """Verify that a ZIP file contains expected test artifacts."""
    zip_file = zipfile.ZipFile(io.BytesIO(zip_data))
    filenames = zip_file.namelist()

    # Should have summary JSON
    summary_files = [f for f in filenames if f.startswith("CactusTestProcedureSummary")]
    assert len(summary_files) >= 1, f"Missing summary file in {filenames}"

    # Verify summary contains expected test name
    summary_data = zip_file.read(summary_files[0])
    summary = RunnerStatus.from_json(summary_data.decode())
    assert summary.test_procedure_name == expected_test_name, (
        f"Expected test name '{expected_test_name}', got '{summary.test_procedure_name}'"
    )


@pytest.mark.slow
@pytest.mark.anyio
async def test_single_test_init_and_finalize(cactus_runner_client: TestClient, run_request_generator):
    """A single init/run/finalize cycle - no playlist advancement involved."""
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    run_request: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    await run_all_01_requests(cactus_runner_client)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data = await RunnerClient.finalize(session)
        verify_zip_contents(zip_data, TestProcedureId.ALL_01.value)

        # Finalize no longer auto-advances - no active test until /next-test is explicitly called
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == "-"


@pytest.mark.slow
@pytest.mark.anyio
async def test_next_test_advances_to_next_test(cactus_runner_client: TestClient, run_request_generator):
    """Test that /next-test explicitly advances the runner to a new test procedure after finalize.

    This verifies:
    1. The first test runs and finalizes correctly, leaving no active test
    2. /next-test initialises the second test using the certificate details captured at init
    3. The second test runs and finalizes correctly
    """
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    run_request_1: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
    run_request_2: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.initialise(session, run_request_1)
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == TestProcedureId.ALL_01.value

    await run_all_01_requests(cactus_runner_client)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_1 = await RunnerClient.finalize(session)
        assert len(zip_data_1) > 0
        verify_zip_contents(zip_data_1, TestProcedureId.ALL_01.value)

        # No active test until /next-test is called
        status = await RunnerClient.status(session)
        assert status.test_procedure_name == "-"

        next_response = await RunnerClient.next_test(session, run_request_2)
        assert next_response.is_started

        status = await RunnerClient.status(session)
        assert status.test_procedure_name == TestProcedureId.ALL_01.value

    await run_all_01_requests(cactus_runner_client)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_2 = await RunnerClient.finalize(session)
        assert len(zip_data_2) > 0
        verify_zip_contents(zip_data_2, TestProcedureId.ALL_01.value)

        status = await RunnerClient.status(session)
        assert status.test_procedure_name == "-"


@pytest.mark.slow
@pytest.mark.anyio
async def test_next_test_preserves_site_data(cactus_runner_client: TestClient, run_request_generator):
    """Test that site/aggregator data is preserved across a /next-test advancement.

    This verifies the partial database reset works correctly - site registration
    from test 1 should still be valid in test 2.
    """
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    run_request_1: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
    run_request_2: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.initialise(session, run_request_1)

    # First test - register a site via requests
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # This registers the end device
    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # Get DER info (requires registered device)
    result = await cactus_runner_client.get("/edev/1/der", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.finalize(session)
        await RunnerClient.next_test(session, run_request_2)

    # Second test - site should still be registered (preserved by partial reset)
    # The aggregator/certificate registration persists, allowing the same certificate to work
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    # This should still work because the aggregator is preserved
    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await ensure_success_response(result)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        zip_data_2 = await RunnerClient.finalize(session)
        assert len(zip_data_2) > 0


@pytest.mark.anyio
async def test_initialise_list_body_rejected(cactus_runner_client: TestClient, run_request_generator):
    """A JSON list body (the old playlist wire format) is no longer accepted by /initialise."""
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2
    run_request: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        async with session.post(url=uri.Initialise, data=f"[{run_request.to_json()}]") as response:
            assert response.status == 400


@pytest.mark.anyio
async def test_next_test_requires_finalize_first(cactus_runner_client: TestClient, run_request_generator):
    """/next-test is rejected with 409 while a test procedure is still active."""
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2

    run_request_1: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)
    run_request_2: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        await RunnerClient.initialise(session, run_request_1)

        with pytest.raises(RunnerClientError) as exc_info:
            await RunnerClient.next_test(session, run_request_2)
        assert exc_info.value.http_status_code == 409


@pytest.mark.anyio
async def test_next_test_requires_prior_init(cactus_runner_client: TestClient, run_request_generator):
    """/next-test is rejected with 409 if the runner has never been initialised."""
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    csip_version = CSIPAusVersion.RELEASE_1_2
    run_request: RunRequest = run_request_generator(TestProcedureId.ALL_01, agg_cert, None, csip_version, None)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        with pytest.raises(RunnerClientError) as exc_info:
            await RunnerClient.next_test(session, run_request)
        assert exc_info.value.http_status_code == 409
