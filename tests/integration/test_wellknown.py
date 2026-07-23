import json
from http import HTTPStatus
from urllib.parse import quote

import jsonschema
import pytest
from aiohttp import ClientResponse, ClientSession, ClientTimeout
from cactus_schema.runner import RunGroup, RunRequest, TestCertificates, TestConfig, TestDefinition, TestUser
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedureId
from pytest_aiohttp.plugin import TestClient

from cactus_runner.client import RunnerClient
from tests.integration.certificate1 import TEST_CERTIFICATE_PEM

URI_ENCODED_CERT = quote(TEST_CERTIFICATE_PEM.decode())


async def assert_success_response(response: ClientResponse):
    if response.status < 200 or response.status >= 300:
        body = await response.read()
        raise AssertionError(f"{response.status}: {body}")


@pytest.mark.slow
@pytest.mark.anyio
async def test_wellknown_missing(cactus_runner_client: TestClient, run_request_generator):
    """This is a full integration test showing the default well-known file under version 1.2"""

    # Fetch .well-known file - doesn't work before init
    result = await cactus_runner_client.get("/.well-known/csipaus")
    assert result.status == HTTPStatus.INTERNAL_SERVER_ERROR

    # Init
    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_01, TEST_CERTIFICATE_PEM.decode(), None, CSIPAusVersion.RELEASE_1_2, None
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    # Fetch .well-known file under v1.2
    result = await cactus_runner_client.get("/.well-known/csipaus")
    assert result.status == HTTPStatus.GONE


@pytest.mark.slow
@pytest.mark.anyio
async def test_wellknown_default_v13(cactus_runner_client: TestClient, run_request_generator):
    """This is a full integration test showing the default well-known file under version 1.3"""

    # Init
    run_request: RunRequest = run_request_generator(
        TestProcedureId.ALL_01, TEST_CERTIFICATE_PEM.decode(), None, CSIPAusVersion.RELEASE_1_3, None
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    # Fetch .well-known file
    result = await cactus_runner_client.get("/.well-known/csipaus")
    await assert_success_response(result)
    assert result.content_type == "application/json"
    response_body = await result.json()
    assert isinstance(response_body, dict)

    # Should be the "default" .well-known file
    with open("tests/data/json/wellknown.schema.json", "rb") as fp:
        schema = json.load(fp)

    jsonschema.validate(instance=response_body, schema=schema)
    assert "https://csipaus.org/ns/v1.3" in response_body["supportedSchemaVersions"]
    assert response_body["supportedSchemaVersions"]["https://csipaus.org/ns/v1.3"]["dcap"] == ["/dcap"]


@pytest.mark.slow
@pytest.mark.anyio
async def test_wellknown_custom_routes_v13(cactus_runner_client: TestClient):
    """This is a full integration test showing the well-known file with dynamic contents under version 1.3"""

    # Init
    version = CSIPAusVersion.RELEASE_1_3
    run_request = RunRequest(
        run_id="abc-123",
        test_definition=TestDefinition(
            test_procedure_id="MY-TEST",
            yaml_definition="""
category: ''
classes: []
criteria: null
description: ''
target-versions:
    - v1.3

preconditions:
    immediate-start: true
    init-actions:
        - type: create-wellknown-route
          parameters:
            version: abc123
            dcap_paths:
              - /foo/bar

        - type: create-wellknown-route
          parameters:
            version: def456
            dcap_paths:
              - $randuri_1

        - type: create-wellknown-route
          parameters:
            version: hij789
            dcap_paths:
              - $randuri_1
              - $randuri_2
              - /baz

        - type: create-wellknown-route
          parameters:
            version: v1.2.3
            dcap_paths: []

steps:
  STEP1:
    event:
      type: GET-request-received
      parameters:
        endpoint: /dcap
    actions: []

""",
        ),
        run_group=RunGroup(
            run_group_id="1",
            name="group 1",
            csip_aus_version=version,
            test_certificates=TestCertificates(
                aggregator=TEST_CERTIFICATE_PEM.decode(),
                device=None,
            ),
        ),
        test_config=TestConfig(pen=12345, subscription_domain=None, is_static_url=False),
        test_user=TestUser(user_id="123", name="User 123"),
    )
    async with ClientSession(base_url=cactus_runner_client.make_url("/"), timeout=ClientTimeout(30)) as session:
        init_response = await RunnerClient.initialise(session, run_request)
        assert init_response.is_started

    # Fetch .well-known file
    result = await cactus_runner_client.get("/.well-known/csipaus")
    await assert_success_response(result)
    assert result.content_type == "application/json"
    response_body = await result.json()
    assert isinstance(response_body, dict)

    # Should be well formed as per json schema
    with open("tests/data/json/wellknown.schema.json", "rb") as fp:
        schema = json.load(fp)

    jsonschema.validate(instance=response_body, schema=schema)

    versions: dict = response_body["supportedSchemaVersions"]
    assert "abc123" in versions
    assert "def456" in versions
    assert "hij789" in versions
    assert "v1.2.3" in versions
    assert len(versions) == 4

    assert versions["abc123"]["dcap"] == ["/foo/bar"]
    assert versions["v1.2.3"]["dcap"] == []

    # Random uris
    assert len(versions["def456"]["dcap"]) == 1
    assert len(versions["hij789"]["dcap"]) == 3
    assert versions["def456"]["dcap"][0] == versions["hij789"]["dcap"][0]
    assert versions["def456"]["dcap"][0] != versions["hij789"]["dcap"][1]

    assert "randuri" not in versions["def456"]["dcap"][0]
    assert "randuri" not in versions["hij789"]["dcap"][0]
    assert "randuri" not in versions["hij789"]["dcap"][1]
    assert versions["hij789"]["dcap"][2] == "/baz"
