from http import HTTPStatus
from urllib.parse import quote

import pytest
from aiohttp import ClientSession, ClientTimeout
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
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())

# A single step that, once a client hits GET /tm, forces all subsequent requests to {endpoint} to return HTTP 429
FORCE_RESPONSE_STATUS_YAML_TEMPLATE = """\
Description: Faux test for force-response-status
Category: Test
Classes:
  - A
TargetVersions:
  - v1.2
Preconditions:
  immediate_start: true
Criteria:
  checks:
    - type: all-steps-complete
      parameters: {}
Steps:
  FORCE-DCAP-STATUS:
    event:
      type: GET-request-received
      parameters:
        endpoint: /tm
    actions:
      - type: force-response-status
        parameters:
          endpoint: #ENDPOINT_TO_REPLACE#
          status: 429
          retry_after_seconds: 30
      - type: remove-steps
        parameters:
          steps:
            - FORCE-DCAP-STATUS
"""


@pytest.mark.slow
@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint, uri",
    [
        ("/dcap", "/dcap"),
        ("/dcap", "/dcap?foo=bar"),
        ("/edev", "/edev"),
        ("/edev", "/edev?s=0&l=100"),
    ],
)
async def test_force_response_status_with_query_string(cactus_runner_client: TestClient, endpoint: str, uri: str):
    run_request = RunRequest(
        run_id="force-response-status-test",
        test_definition=TestDefinition(
            test_procedure_id=TestProcedureId.ALL_01,
            yaml_definition=FORCE_RESPONSE_STATUS_YAML_TEMPLATE.replace("#ENDPOINT_TO_REPLACE#", endpoint),
        ),
        run_group=RunGroup(
            run_group_id="1",
            name="group 1",
            csip_aus_version=CSIPAusVersion.RELEASE_1_2,
            test_certificates=TestCertificates(aggregator=TEST_CERTIFICATE_PEM.decode(), device=None),
        ),
        test_config=TestConfig(pen=12345, subscription_domain=None, is_static_url=False),
        test_user=TestUser(user_id="1", name="user1"),
    )

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        await RunnerClient.initialise(session, run_request)

    headers = {"ssl-client-cert": URI_ENCODED_CERT}

    # Before the override is active - {endpoint} (with or without a query string) should be served normally
    result = await cactus_runner_client.get(uri, headers=headers)
    assert result.status == HTTPStatus.OK, await result.text()

    # Trigger the step that installs the override
    result = await cactus_runner_client.get("/tm", headers=headers)
    assert result.status == HTTPStatus.OK, await result.text()

    # Now the override should intercept the endpoint regardless of any query string supplied by the client
    result = await cactus_runner_client.get(uri, headers=headers)
    assert result.status == HTTPStatus.TOO_MANY_REQUESTS, await result.text()
    assert result.headers.get("Retry-After") == "30"
