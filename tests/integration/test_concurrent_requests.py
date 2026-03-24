import asyncio
from datetime import datetime
from urllib.parse import quote

import pytest
from aiohttp import ClientSession, ClientTimeout
from cactus_schema.runner import (
    RunGroup,
    RunRequest,
    RunnerStatus,
    TestCertificates,
    TestConfig,
    TestDefinition,
    TestUser,
)
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from envoy_schema.server.schema.sep2.der import ConnectStatusTypeValue, DERStatus
from envoy_schema.server.schema.sep2.end_device import EndDeviceRequest
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient
from tests.integration.certificate1 import TEST_CERTIFICATE_LFDI, TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())

CONCURRENT_LISTENERS_YAML = """\
Description: Proxy lock concurrent request test
Category: Test
Classes:
  - A
TargetVersions:
  - v1.2
Preconditions:
  immediate_start: false
Criteria:
  checks:
    - type: all-steps-complete
      parameters: {}
Steps:
  ENABLE-LISTENERS:
    event:
      type: GET-request-received
      parameters:
        endpoint: /dcap
    actions:
      - type: enable-steps
        parameters:
          steps:
            - LISTENER-A
            - LISTENER-B
      - type: remove-steps
        parameters:
          steps:
            - ENABLE-LISTENERS

  LISTENER-A:
    event:
      type: POST-request-received
      parameters:
        endpoint: /edev/1/der/1/ders
        serve_request_first: true
    actions:
      - type: remove-steps
        parameters:
          steps:
            - LISTENER-A

  LISTENER-B:
    event:
      type: POST-request-received
      parameters:
        endpoint: /edev/1/der/1/ders
        serve_request_first: true
    actions:
      - type: remove-steps
        parameters:
          steps:
            - LISTENER-B
"""


def _der_status_body() -> bytes:
    now = int(datetime.now().timestamp())
    return DERStatus(genConnectStatus=ConnectStatusTypeValue(dateTime=now, value="01"), readingTime=now).to_xml(
        skip_empty=True, exclude_none=True
    )


def _make_concurrent_yaml(n: int) -> str:
    """Generates a test YAML with an ENABLE-LISTENERS step triggered by GET /edev, and n listeners on GET /dcap."""
    listener_names = [f"LISTENER-{i}" for i in range(n)]
    enable_steps_block = "\n".join(f"            - {name}" for name in listener_names)
    listener_steps = "".join(f"""
  {name}:
    event:
      type: GET-request-received
      parameters:
        endpoint: /dcap
    actions:
      - type: remove-steps
        parameters:
          steps:
            - {name}
""" for name in listener_names)
    return f"""\
Description: Proxy lock stress test ({n} concurrent requests)
Category: Test
Classes:
  - A
TargetVersions:
  - v1.2
Preconditions:
  immediate_start: false
Criteria:
  checks:
    - type: all-steps-complete
      parameters: {{}}
Steps:
  ENABLE-LISTENERS:
    event:
      type: GET-request-received
      parameters:
        endpoint: /edev
    actions:
      - type: enable-steps
        parameters:
          steps:
{enable_steps_block}
      - type: remove-steps
        parameters:
          steps:
            - ENABLE-LISTENERS
{listener_steps}"""


@pytest.mark.slow
@pytest.mark.anyio
async def test_concurrent_posts_both_listeners_triggered(cactus_runner_client: TestClient):
    """Two concurrent POSTs to the same endpoint both trigger their respective listeners thanks to new proxy lock"""

    run_request = RunRequest(
        run_id="concurrent-test",
        test_definition=TestDefinition(
            test_procedure_id=TestProcedureId.ALL_01, yaml_definition=CONCURRENT_LISTENERS_YAML
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
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started is False

    # Register device
    result = await cactus_runner_client.post(
        "/edev",
        headers={"ssl-client-cert": URI_ENCODED_CERT},
        data=EndDeviceRequest(lFDI=TEST_CERTIFICATE_LFDI, sFDI=357827241281, changedTime=1766110684).to_xml(
            skip_empty=True, exclude_none=True
        ),
    )
    assert result.status < 300

    result = await cactus_runner_client.post("/start")
    assert result.status < 300

    # GET /dcap to trigger ENABLE-LISTENERS — activates both LISTENER-A and LISTENER-B
    result = await cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT})
    assert result.status < 300

    # Send two concurrent POSTs to the same endpoint.
    # With the lock: request 1 runs fully (LISTENER-A fires, removes itself), then request 2
    # runs (only LISTENER-B remains, it fires). Both steps complete.
    # Without the lock: both requests see LISTENER-A as active simultaneously, both trigger it,
    # LISTENER-B never fires.
    result_a, result_b = await asyncio.gather(
        cactus_runner_client.post(
            "/edev/1/der/1/ders", headers={"ssl-client-cert": URI_ENCODED_CERT}, data=_der_status_body()
        ),
        cactus_runner_client.post(
            "/edev/1/der/1/ders", headers={"ssl-client-cert": URI_ENCODED_CERT}, data=_der_status_body()
        ),
    )
    assert result_a.status < 300
    assert result_b.status < 300

    # Both listeners must have fired
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        status: RunnerStatus = await RunnerClient.status(session)

    assert status.step_status["LISTENER-A"].completed_at is not None  # Resolved
    assert status.step_status["LISTENER-B"].completed_at is not None  # Resolved


@pytest.mark.slow
@pytest.mark.anyio
async def test_twenty_concurrent_gets_all_listeners_triggered(cactus_runner_client: TestClient):
    N = 20

    run_request = RunRequest(
        run_id="concurrent-stress-test",
        test_definition=TestDefinition(
            test_procedure_id=TestProcedureId.ALL_01, yaml_definition=_make_concurrent_yaml(N)
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

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started is False

    result = await cactus_runner_client.post(
        "/edev",
        headers={"ssl-client-cert": URI_ENCODED_CERT},
        data=EndDeviceRequest(lFDI=TEST_CERTIFICATE_LFDI, sFDI=357827241281, changedTime=1766110684).to_xml(
            skip_empty=True, exclude_none=True
        ),
    )
    assert result.status < 300

    result = await cactus_runner_client.post("/start")
    assert result.status < 300

    # GET /edev triggers ENABLE-LISTENERS — activates all N LISTENER-i steps (which listen on GET /dcap)
    result = await cactus_runner_client.get("/edev?s=0&l=100", headers={"ssl-client-cert": URI_ENCODED_CERT})
    assert result.status < 300

    results = await asyncio.gather(
        *[cactus_runner_client.get("/dcap", headers={"ssl-client-cert": URI_ENCODED_CERT}) for _ in range(N)]
    )
    assert all(r.status < 300 for r in results)

    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(60)) as session:
        status: RunnerStatus = await RunnerClient.status(session)

    for i in range(N):
        assert status.step_status[f"LISTENER-{i}"].completed_at is not None
