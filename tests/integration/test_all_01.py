import io
import zipfile
from urllib.parse import quote

import pytest
from aiohttp import ClientResponse, ClientSession, ClientTimeout
from cactus_schema.runner import RunnerStatus, RunRequest
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


async def assert_success_response(response: ClientResponse):
    if response.status < 200 or response.status >= 300:
        body = await response.read()
        assert False, f"{response.status}: {body}"


@pytest.mark.parametrize(
    "certificate_type, csip_aus_version",
    [("aggregator_certificate", CSIPAusVersion.BETA_1_3_STORAGE), ("device_certificate", CSIPAusVersion.RELEASE_1_2)],
)
@pytest.mark.slow
@pytest.mark.anyio
async def test_all_01_full(
    cactus_runner_client: TestClient, certificate_type: str, csip_aus_version: CSIPAusVersion, run_request_generator
):
    """This is a full integration test of the entire ALL-01 workflow"""

    # Init
    if certificate_type == "aggregator_certificate":
        agg_cert = TEST_CERTIFICATE_PEM.decode()
        device_cert = None
    else:
        agg_cert = None
        device_cert = TEST_CERTIFICATE_PEM.decode()
    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_01, agg_cert, device_cert, csip_aus_version, None
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    # client "Start" is NOT required as ALL-01 is marked as immediate start

    #
    # Test Start
    #

    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    result = await cactus_runner_client.get("/tm", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    result = await cactus_runner_client.get("/edev/1/der", headers={"ssl-client-cert": URI_ENCODED_CERT})
    await assert_success_response(result)

    #
    # Finalize
    #

    result = await cactus_runner_client.post("/finalize")
    await assert_success_response(result)
    assert result.headers["Content-Type"] == "application/zip"
    assert "attachment" in result.headers["Content-Disposition"]

    # Does the ZIP file look like it has data - do some rudimentary inspections
    zip_data = await result.read()
    zip = zipfile.ZipFile(io.BytesIO(zip_data))

    def get_filename(prefix: str, filenames: list[str]) -> str:
        """Find first filename that starts with 'prefix'"""
        for filename in filenames:
            if filename.startswith(prefix):
                return filename
        raise Exception(f"No filename prefixed by '{prefix}' found in filenames.")

    summary_data = zip.read(get_filename(prefix="CactusTestProcedureSummary", filenames=zip.namelist()))
    assert len(summary_data) > 0
    summary = RunnerStatus.from_json(summary_data.decode())
    assert summary.csip_aus_version == csip_aus_version.value
    for step, resolved in summary.step_status.items():
        assert resolved.started_at is not None, step
        assert resolved.completed_at is not None, step

    # Ensure PDF generated ok
    pdf_data = zip.read(get_filename(prefix="CactusTestProcedureReport", filenames=zip.namelist()))
    assert len(pdf_data) > 1024

    # Check that requests are saved in the zip
    request_files = [f for f in zip.namelist() if f.startswith("requests/") and f.endswith(".request")]
    response_files = [f for f in zip.namelist() if f.startswith("requests/") and f.endswith(".response")]

    assert len(request_files) > 3, f"found {len(request_files)}"  # made 4 requests
    assert len(response_files) > 3, f"found {len(response_files)}"
