import io
import zipfile
from datetime import datetime
from urllib.parse import quote

import pytest
from aiohttp import ClientResponse, ClientSession, ClientTimeout
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from envoy_schema.server.schema.sep2.der import ConnectStatusTypeValue, DERStatus
from envoy_schema.server.schema.sep2.end_device import EndDeviceRequest
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient
from cactus_runner.models import RunnerStatus, RunRequest, StepStatus
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


async def assert_success_response(response: ClientResponse):
    if response.status < 200 or response.status >= 300:
        body = await response.read()
        assert False, f"{response.status}: {body}"


@pytest.mark.slow
@pytest.mark.anyio
async def test_all_07_full(cactus_runner_client: TestClient, run_request_generator):
    """This is a full integration test of the entire ALL-07 workflow"""

    # Init
    csip_aus_version = CSIPAusVersion.RELEASE_1_2
    agg_cert = TEST_CERTIFICATE_PEM.decode()
    device_cert = None
    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_07, agg_cert, device_cert, csip_aus_version, None
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started is False

    #
    # Pre start - create an EndDevice, register a DERStatus saying it's connected
    #

    result = await cactus_runner_client.post(
        "/edev",
        headers={"ssl-client-cert": URI_ENCODED_CERT},
        data=EndDeviceRequest(
            lFDI="854d10a201ca99e5e90d3c3e1f9bc1c3bd075f3b", sFDI=357827241281, changedTime=1766110684
        ).to_xml(skip_empty=True, exclude_none=True),
    )
    await assert_success_response(result)

    now = int(datetime.now().timestamp())
    result = await cactus_runner_client.post(
        "/edev/1/der/1/ders",
        headers={"ssl-client-cert": URI_ENCODED_CERT},
        data=DERStatus(
            genConnectStatus=ConnectStatusTypeValue(dateTime=now, value="01"),
            readingTime=now,
        ).to_xml(skip_empty=True, exclude_none=True),
    )
    await assert_success_response(result)

    # Start
    result = await cactus_runner_client.post("/start")
    await assert_success_response(result)

    #
    # Test Start
    #

    now = int(datetime.now().timestamp())
    result = await cactus_runner_client.put(
        "/edev/1/der/1/ders",
        headers={"ssl-client-cert": URI_ENCODED_CERT},
        data=DERStatus(
            genConnectStatus=ConnectStatusTypeValue(dateTime=now, value="00"),
            readingTime=now,
        ).to_xml(skip_empty=True, exclude_none=True),
    )
    await assert_success_response(result)

    now = int(datetime.now().timestamp())
    result = await cactus_runner_client.put(
        "/edev/1/der/1/ders",
        headers={"ssl-client-cert": URI_ENCODED_CERT},
        data=DERStatus(
            genConnectStatus=ConnectStatusTypeValue(dateTime=now, value="01"),
            readingTime=now,
        ).to_xml(skip_empty=True, exclude_none=True),
    )
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
    assert summary.csip_aus_version == csip_aus_version.value
    for step, resolved in summary.step_status.items():
        assert resolved.get_step_status() == StepStatus.RESOLVED, step

    # Ensure PDF generated ok
    pdf_data = zip.read(get_filename(prefix="CactusTestProcedureReport", filenames=zip.namelist()))
    assert len(pdf_data) > 1024
