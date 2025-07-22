import io
import zipfile
from urllib.parse import quote

import pytest
from aiohttp import ClientResponse
from pytest_aiohttp.plugin import TestClient

from cactus_runner.models import RunnerStatus, StepStatus
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


async def assert_success_response(response: ClientResponse):
    if response.status < 200 or response.status >= 300:
        body = await response.read()
        assert False, f"{response.status}: {body}"


@pytest.mark.parametrize("certificate_type", ["aggregator_certificate", "device_certificate"])
@pytest.mark.slow
@pytest.mark.anyio
async def test_all_01_full(cactus_runner_client: TestClient, certificate_type: str):
    """This is a full integration test of the entire ALL-01 workflow"""

    # Init
    result = await cactus_runner_client.post(f"/init?test=ALL-01&{certificate_type}={URI_ENCODED_CERT}")
    await assert_success_response(result)

    # Start
    result = await cactus_runner_client.post("/start")
    await assert_success_response(result)

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
        return ""

    summary_data = zip.read(get_filename(prefix="CactusTestProcedureSummary", filenames=zip.namelist()))
    assert len(summary_data) > 0
    summary = RunnerStatus.from_json(summary_data.decode())
    for step, resolved in summary.step_status.items():
        assert resolved == StepStatus.RESOLVED, step

    # Ensure PDF generated ok
    pdf_data = zip.read(get_filename(prefix="CactusTestProcedureReport", filenames=zip.namelist()))
    assert len(pdf_data) > 1024
