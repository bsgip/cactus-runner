import io
import zipfile
from urllib.parse import quote

import pytest
from aiohttp import ClientResponse, ClientSession, ClientTimeout
from cactus_schema.runner import (
    RunGroup,
    RunnerStatus,
    RunRequest,
    TestCertificates,
    TestConfig,
    TestDefinition,
    TestUser,
)
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from cactus_test_definitions.client.test_procedures import (
    Action,
    Check,
    Criteria,
    Event,
    Preconditions,
    Step,
    TestProcedure,
)
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


async def assert_success_response(response: ClientResponse):
    if response.status < 200 or response.status >= 300:
        body = await response.read()
        assert False, f"{response.status}: {body}"


def generate_fail_message_tp() -> TestProcedure:
    """Generates a simple TestProcedure that requires a client to resolve /dcap exactly ONCE"""
    return TestProcedure(
        description="",
        category="",
        classes=[],
        target_versions=["v1.3"],
        preconditions=Preconditions(immediate_start=True),
        criteria=Criteria([Check("all-steps-complete", {"ignored_steps": ["STEP-2"]})]),
        steps={
            "STEP-1": Step(
                event=Event("GET-request-received", {"endpoint": "/dcap"}),
                actions=[Action("enable-steps", {"steps": ["STEP-2"]}), Action("remove-steps", {"steps": ["STEP-1"]})],
            ),
            "STEP-2": Step(
                event=Event("GET-request-received", {"endpoint": "/dcap"}),
                actions=[Action("finish-test", {"fail_message": "This is a mock failure"})],
            ),
        },
    )


@pytest.mark.parametrize(
    "n_dcap_requests, expected_success",
    [
        (0, False),  # Need one request to pass
        (1, True),
        (2, False),
    ],
)
@pytest.mark.slow
@pytest.mark.anyio
async def test_fail_message_with_fail(cactus_runner_client: TestClient, n_dcap_requests: int, expected_success: bool):
    """This runs a fake test that will fail a client that request dcap twice - will fail by requesting twice"""

    # Init
    tp = generate_fail_message_tp()
    run_request: RunRequest = RunRequest(
        "fake-1",
        TestDefinition(TestProcedureId.ALL_01, tp.to_yaml()),
        RunGroup("rg-1", "rg-1", CSIPAusVersion.RELEASE_1_3, TestCertificates(TEST_CERTIFICATE_PEM.decode(), None)),
        TestConfig(None, True, pen=1234),
        TestUser("user1", "user1"),
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    # client "Start" is NOT required as this test is marked as immediate start

    #
    # Test Start
    #

    for _ in range(n_dcap_requests):
        result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
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
    assert len(summary.criteria) > 0, ",".join([f"{c.type}: {c.success} {c.details}" for c in summary.criteria])
    assert all([c.success for c in summary.criteria]) == expected_success

    # Ensure PDF generated ok
    pdf_data = zip.read(get_filename(prefix="CactusTestProcedureReport", filenames=zip.namelist()))
    assert len(pdf_data) > 1024
