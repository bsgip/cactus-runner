import asyncio
import http
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, call

import pytest
from aiohttp import ContentTypeError
from aiohttp.web import FileResponse, Response
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance
from cactus_schema.runner import (
    ClientInteraction,
    ClientInteractionType,
    InitResponseBody,
    ProceedResponse,
    RequestEntry,
    RunGroup,
    RunnerStatus,
    RunRequest,
    StepStatus,
    TestCertificates,
    TestConfig,
    TestDefinition,
    TestUser,
)
from cactus_test_definitions import CSIPAusVersion
from cactus_test_definitions.client import TestProcedure, TestProcedureId
from cactus_test_definitions.client.test_procedures import get_yaml_contents
from multidict import CIMultiDict

from cactus_runner.app import action, handler
from cactus_runner.app.proxy import ProxyResult
from cactus_runner.app.shared import (
    APPKEY_BACKEND_PROVIDER,
    APPKEY_INITIALISED_CERTS,
    APPKEY_PROXY_LOCK,
    APPKEY_RUNNER_STATE,
)
from cactus_runner.app.uri import uri_proxy_path_extract
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientCertificateType,
    InitialisedCertificates,
    Listener,
    ProxyRouteOverride,
    RunnerState,
)
from cactus_runner.plugin.backends.common import RunnerBackend
from cactus_runner.plugin.backends.hookspec import BackendProvider
from tests.integration.certificate1 import (
    TEST_CERTIFICATE_PEM as TEST_CERTIFICATE_1_PEM,
)
from tests.integration.certificate2 import (
    TEST_CERTIFICATE_PEM as TEST_CERTIFICATE_2_PEM,
)


def mocked_ProxyResult(status: int) -> ProxyResult:
    return ProxyResult("", "", b"", None, CIMultiDict({}), Response(status=status))


def run_request(test_procedure_id: TestProcedureId, use_device_cert: bool = False) -> RunRequest:
    test_certificates = (
        TestCertificates(aggregator=None, device=TEST_CERTIFICATE_1_PEM.decode())
        if use_device_cert
        else TestCertificates(aggregator=TEST_CERTIFICATE_2_PEM.decode(), device=None)
    )
    yaml_definition = get_yaml_contents(test_procedure_id)
    return RunRequest(
        run_id="1",
        test_definition=TestDefinition(test_procedure_id=test_procedure_id, yaml_definition=yaml_definition),
        run_group=RunGroup(
            run_group_id="1",
            name="group 1",
            csip_aus_version=CSIPAusVersion.RELEASE_1_2,
            test_certificates=test_certificates,
        ),
        test_config=TestConfig(pen=12345, subscription_domain="subs.anu.edu.au", is_static_url=True),
        test_user=TestUser(user_id="1", name="user1"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "test_procedure_id,use_device_cert,is_immediate_start",
    [
        (TestProcedureId.ALL_01, False, True),
        (TestProcedureId.ALL_01, True, True),
        (TestProcedureId.ALL_07, True, False),
    ],
)
async def test_initialise_handler(
    test_procedure_id: TestProcedureId, use_device_cert: bool, is_immediate_start: bool, mocker, pg_base_config
):
    # Arrange
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    mock_request = MagicMock()
    mock_request.text = AsyncMock(
        return_value=run_request(test_procedure_id=test_procedure_id, use_device_cert=use_device_cert).to_json()
    )
    mock_request.raise_for_status = MagicMock()
    mock_request.query.get = MagicMock(return_value=None)  # No start_index
    mock_request.app = {
        APPKEY_RUNNER_STATE: Mock(spec=RunnerState),
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_request.app[APPKEY_RUNNER_STATE].client_interactions = []

    mock_backend.register_aggregator.return_value = 1
    mock_attempt_apply_actions = mocker.patch("cactus_runner.app.handler.attempt_apply_actions")
    start_result = MagicMock()
    start_result.success = True
    mock_attempt_start_for_state = mocker.patch(
        "cactus_runner.app.handler.attempt_start_for_state", return_value=start_result
    )

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text

    # Assert - parsed response
    response = InitResponseBody.from_json(raw_response.text)
    assert isinstance(response, InitResponseBody)
    assert raw_response.status == http.HTTPStatus.CREATED
    assert raw_response.content_type == "application/json"
    assert response.status == "Test procedure initialised."
    assert response.test_procedure == test_procedure_id.value
    assert_nowish(response.timestamp)
    assert response.is_started == is_immediate_start

    # Assert - side-effects
    assert len(mock_request.app[APPKEY_RUNNER_STATE].client_interactions) == 1
    assert (
        mock_request.app[APPKEY_RUNNER_STATE].client_interactions[0].interaction_type
        == ClientInteractionType.TEST_PROCEDURE_INIT
    )
    mock_backend.reset_state.assert_called_once()
    mock_backend.register_aggregator.assert_called_once()
    mock_attempt_apply_actions.assert_called_once()
    if is_immediate_start:
        mock_attempt_start_for_state.assert_called_once()


@pytest.mark.asyncio
async def test_initialise_handler_list_body_bad_request(mocker):
    """A JSON list body (the old playlist wire format) is no longer accepted - only a single RunRequest."""
    run_request_1 = run_request(test_procedure_id=TestProcedureId.ALL_01, use_device_cert=False)
    list_json = "[" + run_request_1.to_json() + "]"

    mock_request = MagicMock()
    mock_request.text = AsyncMock(return_value=list_json)
    mock_request.raise_for_status = MagicMock()
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_request.app[APPKEY_RUNNER_STATE].client_interactions = []

    raw_response = await handler.initialise_handler(request=mock_request)

    assert isinstance(raw_response, Response)
    assert raw_response.status == http.HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_body,expected_response_text",
    [("{}", "Unable to parse JSON body to RunRequest instance"), (None, "Missing JSON body")],
)
async def test_new_init_handler_bad_request_invalid_json(request_body: str | None, expected_response_text: str):
    # Arrange
    mock_request = MagicMock()
    if request_body:
        mock_request.text = AsyncMock(return_value=request_body)
    else:
        mock_request.text = AsyncMock(side_effect=ContentTypeError(None, None))  # type: ignore

    mock_request.raise_for_status = MagicMock()

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text
    assert raw_response.text.startswith(expected_response_text)
    assert raw_response.status == http.HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_new_init_handler_conflict_response_if_existing_active_test_procedure():

    # Arrange
    mock_request = MagicMock()
    mock_request.text = AsyncMock(return_value=run_request(test_procedure_id=TestProcedureId.ALL_01).to_json())
    mock_request.raise_for_status = MagicMock()
    mock_request.query.get = MagicMock(return_value=None)

    currently_running_test = "GEN-01"
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure.name = currently_running_test

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text
    assert raw_response.text.startswith(f"Test Procedure ({currently_running_test}) already active.")
    assert raw_response.status == http.HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_new_init_handler_conflict_response_if_certificate_clash():

    # Arrange
    run_request_aggregator_cert = run_request(test_procedure_id=TestProcedureId.ALL_01, use_device_cert=False)
    run_request_device_cert = run_request(test_procedure_id=TestProcedureId.ALL_01, use_device_cert=True)

    # BOTH CERTS
    run_request_both_certs = run_request_aggregator_cert
    run_request_both_certs.run_group.test_certificates.device = (
        run_request_device_cert.run_group.test_certificates.device
    )
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    mock_request = MagicMock()
    mock_request.text = AsyncMock(return_value=run_request_both_certs.to_json())
    mock_request.raise_for_status = MagicMock()
    mock_request.query.get = MagicMock(return_value=None)
    mock_request.app = {
        APPKEY_RUNNER_STATE: Mock(spec=RunnerState),
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_request.app[APPKEY_RUNNER_STATE].client_interactions = []

    mock_backend.register_aggregator.return_value = 1

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text
    assert raw_response.text == "Cannot use 'aggregator_certificate' and 'device_certificate' at the same time."
    assert raw_response.status == http.HTTPStatus.BAD_REQUEST

    # NEITHER CERT
    run_request_neither_cert = run_request_both_certs
    run_request_neither_cert.run_group.test_certificates.aggregator = None
    run_request_neither_cert.run_group.test_certificates.device = None
    mock_request = MagicMock()
    mock_request.text = AsyncMock(return_value=run_request_neither_cert.to_json())
    mock_request.raise_for_status = MagicMock()
    mock_request.query.get = MagicMock(return_value=None)
    mock_request.app = {
        APPKEY_RUNNER_STATE: Mock(spec=RunnerState),
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_request.app[APPKEY_RUNNER_STATE].client_interactions = []

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text
    assert raw_response.text.startswith("Need one of 'aggregator_certificate' or 'device_certificate'.")
    assert raw_response.status == http.HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_new_init_handler_bad_request_invalid_test_procedure():
    # Arrange
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = run_request(test_procedure_id=TestProcedureId.ALL_01)
    request.test_definition.yaml_definition = "invalid test procedure definition"
    mock_request = MagicMock()
    mock_request.text = AsyncMock(return_value=request.to_json())
    mock_request.raise_for_status = MagicMock()
    mock_request.query.get = MagicMock(return_value=None)
    mock_request.app = {
        APPKEY_RUNNER_STATE: Mock(spec=RunnerState),
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_request.app[APPKEY_RUNNER_STATE].client_interactions = []

    mock_backend.register_aggregator.return_value = 1

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text
    assert raw_response.text.startswith("Received invalid test procedure definition")
    assert raw_response.status == http.HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
@pytest.mark.parametrize("precondition_failure", [action.FailedActionError, action.UnknownActionError])
async def test_new_init_handler_precondition_failed_response_if_preconditions_fail(
    precondition_failure, mocker, pg_base_config
):
    # Arrange
    test_procedure_id = TestProcedureId.ALL_01
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    mock_request = MagicMock()
    mock_request.text = AsyncMock(return_value=run_request(test_procedure_id=test_procedure_id).to_json())
    mock_request.raise_for_status = MagicMock()
    mock_request.query.get = MagicMock(return_value=None)
    mock_request.app = {
        APPKEY_RUNNER_STATE: Mock(spec=RunnerState),
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_request.app[APPKEY_RUNNER_STATE].client_interactions = []

    mock_backend.register_aggregator.return_value = 1
    mock_attempt_apply_actions = mocker.patch(
        "cactus_runner.app.handler.attempt_apply_actions", side_effect=precondition_failure
    )

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text
    assert raw_response.text.startswith("Failed to apply preconditions")
    assert raw_response.status == http.HTTPStatus.PRECONDITION_FAILED

    mock_backend.reset_state.assert_called_once()
    mock_backend.register_aggregator.assert_called_once()
    mock_attempt_apply_actions.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_result",
    [
        handler.StartResult(
            False,
            http.HTTPStatus.CONFLICT,
            "text/plain",
            "Unable to start non-existent test procedure. Try initialising a test procedure before continuing.",
        ),
        handler.StartResult(
            False,
            http.HTTPStatus.PRECONDITION_FAILED,
            "text/plain",
            "Unable to start test procedure, pre condition check has failed: dummary check failure description",
        ),
        handler.StartResult(
            False,
            http.HTTPStatus.CONFLICT,
            "text/plain",
            "Test Procedure (ALL-01) already in progress. Starting another test procedure is not permitted.",  # noqa: E501
        ),
    ],
)
async def test_new_init_handler_immediate_start_failure(start_result: handler.StartResult, mocker, pg_base_config):
    # Arrange
    test_procedure_id = TestProcedureId.ALL_01
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    mock_request = MagicMock()
    mock_request.text = AsyncMock(return_value=run_request(test_procedure_id=test_procedure_id).to_json())
    mock_request.raise_for_status = MagicMock()
    mock_request.query.get = MagicMock(return_value=None)
    mock_request.app = {
        APPKEY_RUNNER_STATE: Mock(spec=RunnerState),
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    mock_request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_request.app[APPKEY_RUNNER_STATE].client_interactions = []

    mock_backend.register_aggregator.return_value = 1
    mock_attempt_apply_actions = mocker.patch("cactus_runner.app.handler.attempt_apply_actions")
    mock_attempt_start_for_state = mocker.patch(
        "cactus_runner.app.handler.attempt_start_for_state", return_value=start_result
    )

    # Act
    raw_response = await handler.initialise_handler(request=mock_request)

    # Assert - raw_response
    assert isinstance(raw_response, Response)
    assert raw_response.text
    assert raw_response.text.startswith(
        "Unable to trigger immediate start:",
    )
    assert raw_response.status == start_result.status

    mock_backend.reset_state.assert_called_once()
    mock_backend.register_aggregator.assert_called_once()
    mock_attempt_apply_actions.assert_called_once()
    mock_attempt_start_for_state.assert_called_once()


@pytest.mark.asyncio
async def test_finalize_handler(mocker, tmp_path):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """

    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = MagicMock()
    request.app = {
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_RUNNER_STATE: RunnerState(
            active_test_procedure=generate_class_instance(ActiveTestProcedure, seed=101, optional_is_none=True)
        ),
    }
    zip_path = tmp_path / "test.zip"
    zip_path.write_bytes(bytes([99, 55]))
    mock_finish_active_test = mocker.patch("cactus_runner.app.handler.finalize.finish_active_test")
    mock_finish_active_test.return_value = zip_path

    mock_safely_write_error_zip = mocker.patch("cactus_runner.app.handler.finalize.safely_write_error_zip")

    response = await handler.finalize_handler(request=request)

    assert isinstance(response, FileResponse)
    mock_finish_active_test.assert_called_once()
    mock_safely_write_error_zip.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_handler_finish_error(mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """

    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = MagicMock()
    request.app = {
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_RUNNER_STATE: RunnerState(
            active_test_procedure=generate_class_instance(ActiveTestProcedure, seed=101, optional_is_none=True)
        ),
    }
    mock_finish_active_test = mocker.patch("cactus_runner.app.handler.finalize.finish_active_test")
    mock_finish_active_test.side_effect = Exception("mock exception")

    fake_error_path = MagicMock(spec=Path)
    mock_safely_write_error_zip = mocker.patch(
        "cactus_runner.app.handler.finalize.safely_write_error_zip", return_value=fake_error_path
    )

    response = await handler.finalize_handler(request=request)

    assert isinstance(response, FileResponse)
    mock_finish_active_test.assert_called_once()
    mock_safely_write_error_zip.assert_called_once()


@pytest.mark.asyncio
async def test_finalize_handler_resets_runner_state(mocker):
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = MagicMock()
    request.app = {
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_RUNNER_STATE: RunnerState(
            active_test_procedure=generate_class_instance(
                ActiveTestProcedure,
                seed=101,
                optional_is_none=True,
            ),
            request_history=[generate_class_instance(RequestEntry, seed=202)],
        ),
    }
    mocker.patch("cactus_runner.app.handler.status.get_active_runner_status").return_value = generate_class_instance(
        RunnerStatus, step_status={}
    )

    _ = await handler.finalize_handler(request=request)

    assert request.app[APPKEY_RUNNER_STATE].active_test_procedure is None
    assert not request.app[APPKEY_RUNNER_STATE].request_history  # the list should be empty


@pytest.mark.asyncio
async def test_finalize_handler_handles_no_active_test_procedure():

    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = None

    response = await handler.finalize_handler(request=request)

    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.BAD_REQUEST


def set_initialised_certs(request: MagicMock) -> None:
    certs = request.app[APPKEY_INITIALISED_CERTS]
    certs.client_certificate_type = ClientCertificateType.AGGREGATOR
    certs.client_certificate = "cert-pem"
    certs.client_lfdi = "lfdi-1"
    certs.client_aggregator_id = 1


@pytest.mark.asyncio
async def test_next_test_handler_active_test_conflict():
    request = MagicMock()
    request.text = AsyncMock(return_value=run_request(test_procedure_id=TestProcedureId.ALL_01).to_json())
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = MagicMock()  # still active

    response = await handler.next_test_handler(request=request)

    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_next_test_handler_not_initialised_conflict():
    request = MagicMock()
    request.text = AsyncMock(return_value=run_request(test_procedure_id=TestProcedureId.ALL_01).to_json())
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    certs = request.app[APPKEY_INITIALISED_CERTS]  # never initialised
    certs.client_lfdi = None
    certs.client_aggregator_id = None
    certs.client_certificate_type = None

    response = await handler.next_test_handler(request=request)

    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.CONFLICT


@pytest.mark.asyncio
async def test_next_test_handler_success(mocker):
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = MagicMock()
    request.text = AsyncMock(return_value=run_request(test_procedure_id=TestProcedureId.ALL_01).to_json())
    request.app = {
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_RUNNER_STATE: RunnerState(active_test_procedure=None),
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    set_initialised_certs(request)

    mock_initialize_next_test = mocker.patch(
        "cactus_runner.app.handler.initialize_next_test", new=AsyncMock(return_value=True)
    )

    response = await handler.next_test_handler(request=request)

    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.CREATED
    assert response.content_type == "application/json"
    body = InitResponseBody.from_json(response.text)
    assert isinstance(body, InitResponseBody)
    assert body.is_started is True

    mock_backend.reset_playlist_state.assert_awaited_once()
    mock_initialize_next_test.assert_awaited_once()


@pytest.mark.asyncio
async def test_next_test_handler_immediate_start_failure(mocker):
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = MagicMock()
    request.text = AsyncMock(return_value=run_request(test_procedure_id=TestProcedureId.ALL_01).to_json())
    request.app = {
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_RUNNER_STATE: RunnerState(active_test_procedure=None),
        APPKEY_INITIALISED_CERTS: Mock(spec=InitialisedCertificates),
    }
    set_initialised_certs(request)

    mocker.patch(
        "cactus_runner.app.handler.initialize_next_test",
        new=AsyncMock(side_effect=RuntimeError("Unable to trigger immediate start: boom")),
    )

    response = await handler.next_test_handler(request=request)

    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.PRECONDITION_FAILED


@pytest.mark.parametrize(
    "is_db_healthy, is_admin_api_healthy, expected_status",
    [
        (True, True, http.HTTPStatus.OK),
        (False, True, http.HTTPStatus.SERVICE_UNAVAILABLE),
        (True, False, http.HTTPStatus.SERVICE_UNAVAILABLE),
        (False, False, http.HTTPStatus.SERVICE_UNAVAILABLE),
    ],
)
@pytest.mark.asyncio
async def test_health_handler(is_db_healthy: bool, is_admin_api_healthy: bool, expected_status):
    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = MagicMock()
    request.app = {
        APPKEY_BACKEND_PROVIDER: mock_provider,
    }
    mock_backend.is_healthy.return_value = is_db_healthy and is_admin_api_healthy
    response = await handler.health_handler(request)
    assert isinstance(response, Response)
    assert response.status == expected_status


@pytest.mark.asyncio
async def test_status_handler(mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """
    get_active_runner_status_spy = mocker.spy(handler.status, "get_active_runner_status")

    mock_backend = Mock(spec=RunnerBackend)
    mock_provider = Mock(spec=BackendProvider)
    mock_provider.create_backend = AsyncMock(return_value=mock_backend)
    request = MagicMock()
    request.app = {
        APPKEY_BACKEND_PROVIDER: mock_provider,
        APPKEY_RUNNER_STATE: RunnerState(
            active_test_procedure=generate_class_instance(
                ActiveTestProcedure,
                seed=101,
                optional_is_none=True,
                definition=generate_class_instance(TestProcedure, seed=202),
            )
        ),
    }

    response = await handler.status_handler(request=request)
    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.OK
    assert response.content_type == "application/json"
    get_active_runner_status_spy.assert_called_once()


@pytest.mark.asyncio
async def test_status_handler_handles_no_active_test_procedure(example_client_interaction: ClientInteraction, mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    request.app[APPKEY_RUNNER_STATE].last_client_interaction = example_client_interaction
    get_runner_status_spy = mocker.spy(handler.status, "get_runner_status")

    response = await handler.status_handler(request=request)
    runner_status = RunnerStatus.from_json(response.text)
    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.OK
    assert response.content_type == "application/json"
    assert runner_status.status_summary == "No test procedure running"
    get_runner_status_spy.assert_called_once()


@pytest.mark.parametrize("bad_headers", [{}, {"ssl-client-cert": ""}])
@pytest.mark.asyncio
async def test_proxied_request_handler_performs_authorization(mocker, bad_headers: dict):
    # Arrange
    request = MagicMock()
    request.headers = bad_headers
    request.app[APPKEY_RUNNER_STATE].active_test_procedure.is_finished.return_value = False
    handler.DEV_SKIP_AUTHORIZATION_CHECK = False

    spy_request_is_authorized = mocker.spy(handler.auth, "request_is_authorized")

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    spy_request_is_authorized.assert_called_once()
    spy_request_is_authorized.assert_called_with(request)

    assert isinstance(response, Response)
    # We are not supplying the certificate in the request so we
    # except a 409 (FORBIDDEN) response
    assert response.status == http.HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_proxied_request_handler_before_request_trigger(pg_base_config, mocker):
    # Arrange
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    request.app[APPKEY_RUNNER_STATE].request_history = []
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        communications_disabled=False,
        finished_zip_path=None,
        step_status={"1": StepStatus.PENDING},
    )
    request.app = {}
    request.app[APPKEY_RUNNER_STATE] = RunnerState(active_test_procedure=mock_active_test_procedure)
    request.app[APPKEY_BACKEND_PROVIDER] = Mock(spec=BackendProvider)
    request.app[APPKEY_PROXY_LOCK] = asyncio.Lock()

    handling_listener = generate_class_instance(Listener, actions=[])

    handler.SERVER_URL = ""  # Override the server url

    handler.DEV_SKIP_AUTHORIZATION_CHECK = True
    spy_request_is_authorized = mocker.spy(handler.auth, "request_is_authorized")

    # This trigger is handled by this listener
    mock_handle_event_trigger = mocker.patch("cactus_runner.app.handler.event.handle_event_trigger")
    mock_handle_event_trigger.return_value = [handling_listener]

    mock_generate_client_request_trigger = mocker.patch(
        "cactus_runner.app.handler.event.generate_client_request_trigger"
    )
    mock_trigger = MagicMock()
    mock_generate_client_request_trigger.return_value = mock_trigger

    mock_proxy_request = mocker.patch("cactus_runner.app.proxy.proxy_request")
    expected_response = mocked_ProxyResult(203)  # Set to a random "success" code to ensure it's extracted correctly
    mock_proxy_request.return_value = expected_response

    num_client_interactions_before = len(request.app[APPKEY_RUNNER_STATE].client_interactions)
    mock_validate_proxy_request_schema = mocker.patch("cactus_runner.app.handler.validate_proxy_request_schema")
    expected_validate_result = ["abc-123"]
    mock_validate_proxy_request_schema.return_value = expected_validate_result

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    #  ... verify the result is pulled from proxy response
    assert response is expected_response.response

    #  ... verify we skip authorization
    assert spy_request_is_authorized.call_count == 0

    #  ... verify we check the proxy request for schema errors
    mock_validate_proxy_request_schema.assert_called_once_with(expected_response)

    #  ... verify we update the last client interaction
    assert len(request.app[APPKEY_RUNNER_STATE].client_interactions) == num_client_interactions_before + 1
    assert isinstance(request.app[APPKEY_RUNNER_STATE].last_client_interaction, ClientInteraction)
    assert (
        request.app[APPKEY_RUNNER_STATE].last_client_interaction.interaction_type
        == ClientInteractionType.PROXIED_REQUEST
    )
    assert_nowish(request.app[APPKEY_RUNNER_STATE].last_client_interaction.timestamp)

    #  ... verify aiohttp.client.request is passed values from the request argument
    mock_proxy_request.assert_called_once()

    # ... verify we triggered the "before" handler, but not the after handler
    mock_generate_client_request_trigger.assert_called_once_with(
        uri_proxy_path_extract("/", "/", request), mount_point=handler.MOUNT_POINT, before_serving=True
    )
    mock_handle_event_trigger.assert_called_once()

    #  ... verify we updated the request history
    request_entries = request.app[APPKEY_RUNNER_STATE].request_history
    assert len(request_entries) == 1
    request_entry = request_entries[0]
    assert isinstance(request_entry, RequestEntry)
    assert request_entry.url == request.path_qs
    assert request_entry.path == request.path
    assert request_entry.method == request.method
    assert_nowish(request_entry.timestamp)
    assert request_entry.status == response.status
    assert request_entry.step_name == handling_listener.step
    assert request_entry.body_xml_errors == expected_validate_result


@pytest.mark.asyncio
async def test_proxied_request_handler_replaces_existing_proxied_request_interaction(pg_base_config, mocker):

    # Arrange
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        communications_disabled=False,
        finished_zip_path=None,
        step_status={"1": StepStatus.PENDING},
    )
    request.app = {}
    request.app[APPKEY_RUNNER_STATE] = RunnerState(active_test_procedure=mock_active_test_procedure)
    request.app[APPKEY_BACKEND_PROVIDER] = Mock(spec=BackendProvider)
    request.app[APPKEY_PROXY_LOCK] = asyncio.Lock()

    # Seed a prior PROXIED_REQUEST so the replace branch is exercised
    prior_interaction = ClientInteraction(
        interaction_type=ClientInteractionType.PROXIED_REQUEST, timestamp=datetime(2020, 1, 1, tzinfo=UTC)
    )
    request.app[APPKEY_RUNNER_STATE].client_interactions.append(prior_interaction)
    interactions_before = len(request.app[APPKEY_RUNNER_STATE].client_interactions)

    handler.SERVER_URL = ""
    handler.DEV_SKIP_AUTHORIZATION_CHECK = True
    mocker.patch("cactus_runner.app.handler.event.handle_event_trigger", return_value=[])
    mocker.patch("cactus_runner.app.handler.event.generate_client_request_trigger", return_value=MagicMock())
    mock_proxy_request = mocker.patch("cactus_runner.app.proxy.proxy_request")
    mock_proxy_request.return_value = mocked_ProxyResult(200)
    mocker.patch("cactus_runner.app.handler.validate_proxy_request_schema", return_value=[])

    # Act
    await handler.proxied_request_handler(request=request)

    # Assert - list stays the same length (replace, not append)
    assert len(request.app[APPKEY_RUNNER_STATE].client_interactions) == interactions_before
    assert (
        request.app[APPKEY_RUNNER_STATE].last_client_interaction.interaction_type
        == ClientInteractionType.PROXIED_REQUEST
    )
    assert_nowish(request.app[APPKEY_RUNNER_STATE].last_client_interaction.timestamp)


@pytest.mark.asyncio
async def test_proxied_request_handler_after_request_trigger(pg_base_config, mocker):
    # Arrange
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    request.app[APPKEY_RUNNER_STATE].request_history = []
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        communications_disabled=False,
        finished_zip_path=None,
        step_status={"1": StepStatus.PENDING},
    )
    request.app = {}
    request.app[APPKEY_RUNNER_STATE] = RunnerState(active_test_procedure=mock_active_test_procedure)
    request.app[APPKEY_BACKEND_PROVIDER] = Mock(spec=BackendProvider)
    request.app[APPKEY_PROXY_LOCK] = asyncio.Lock()
    handling_listener = generate_class_instance(Listener, actions=[])

    handler.SERVER_URL = ""  # Override the server url

    handler.DEV_SKIP_AUTHORIZATION_CHECK = True
    spy_request_is_authorized = mocker.spy(handler.auth, "request_is_authorized")

    # This trigger is handled by this listener
    mock_handle_event_trigger: MagicMock = mocker.patch("cactus_runner.app.handler.event.handle_event_trigger")
    mock_handle_event_trigger.side_effect = [[], [handling_listener]]

    mock_generate_client_request_trigger: MagicMock = mocker.patch(
        "cactus_runner.app.handler.event.generate_client_request_trigger"
    )
    mock_before_trigger = MagicMock()
    mock_after_trigger = MagicMock()
    mock_generate_client_request_trigger.side_effect = [mock_before_trigger, mock_after_trigger]

    mock_proxy_request = mocker.patch("cactus_runner.app.proxy.proxy_request")
    expected_response = mocked_ProxyResult(200)
    mock_proxy_request.return_value = expected_response

    mock_validate_proxy_request_schema = mocker.patch("cactus_runner.app.handler.validate_proxy_request_schema")
    expected_validate_result = ["abc-456"]
    mock_validate_proxy_request_schema.return_value = expected_validate_result

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    #  ... verify we skip authorization
    assert spy_request_is_authorized.call_count == 0

    #  ... verify the result is pulled from proxy response
    assert response is expected_response.response

    #  ... verify we update the last client interaction
    assert isinstance(request.app[APPKEY_RUNNER_STATE].last_client_interaction, ClientInteraction)
    assert (
        request.app[APPKEY_RUNNER_STATE].last_client_interaction.interaction_type
        == ClientInteractionType.PROXIED_REQUEST
    )
    assert_nowish(request.app[APPKEY_RUNNER_STATE].last_client_interaction.timestamp)

    #  ... verify aiohttp.client.request is passed values from the request argument
    mock_proxy_request.assert_called_once()

    # ... verify we triggered the "before" handler, but not the after handler
    mock_generate_client_request_trigger.assert_has_calls(
        [
            call(uri_proxy_path_extract("/", "/", request), mount_point=handler.MOUNT_POINT, before_serving=True),
            call(uri_proxy_path_extract("/", "/", request), mount_point=handler.MOUNT_POINT, before_serving=False),
        ]
    )
    assert mock_handle_event_trigger.call_count == 2

    #  ... verify we updated the request history
    request_entries = request.app[APPKEY_RUNNER_STATE].request_history
    assert len(request_entries) == 1
    request_entry = request_entries[0]
    assert isinstance(request_entry, RequestEntry)
    assert request_entry.url == request.path_qs
    assert request_entry.path == request.path
    assert request_entry.method == request.method
    assert_nowish(request_entry.timestamp)
    assert request_entry.status == response.status
    assert request_entry.step_name == handling_listener.step
    assert request_entry.body_xml_errors == expected_validate_result


@pytest.mark.asyncio
async def test_proxied_request_handler_handles_proxy_overrides(pg_base_config, mocker):

    # Arrange
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap?foo=bar"
    request.query_string = "foo=bar"
    request.method = "GET"
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        communications_disabled=False,
        finished_zip_path=None,
        step_status={"1": StepStatus.PENDING},
        proxy_route_overrides=[ProxyRouteOverride("/dcap", "/my/proxy")],
    )
    request.app = {}
    request.app[APPKEY_RUNNER_STATE] = RunnerState(active_test_procedure=mock_active_test_procedure)
    request.app[APPKEY_BACKEND_PROVIDER] = Mock(spec=BackendProvider)
    request.app[APPKEY_PROXY_LOCK] = asyncio.Lock()

    handler.SERVER_URL = "http://example.com:1234"
    handler.DEV_SKIP_AUTHORIZATION_CHECK = True
    mocker.patch("cactus_runner.app.handler.event.handle_event_trigger", return_value=[])
    mocker.patch("cactus_runner.app.handler.event.generate_client_request_trigger", return_value=MagicMock())
    mock_proxy_request: MagicMock = mocker.patch("cactus_runner.app.proxy.proxy_request")
    mock_proxy_request.return_value = mocked_ProxyResult(200)
    mocker.patch("cactus_runner.app.handler.validate_proxy_request_schema", return_value=[])

    # Act
    await handler.proxied_request_handler(request=request)

    # Assert - proxy_to has been rewritten
    assert mock_proxy_request.call_count == 1
    assert mock_proxy_request.call_args_list[0].kwargs["remote_url"] == "http://example.com:1234/my/proxy?foo=bar"
    assert_nowish(request.app[APPKEY_RUNNER_STATE].last_client_interaction.timestamp)


@pytest.mark.asyncio
async def test_proxied_request_handler_logs_error_with_no_active_test_procedure(mocker):
    # Arrange
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_logger_warning = mocker.patch("cactus_runner.app.handler.logger.error")

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    mock_logger_warning.assert_called_once()
    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_proxied_request_handler_logs_error_with_finished_test(mocker):
    # Arrange
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        communications_disabled=False,
        finished_zip_path=Path("."),
        step_status={"1": StepStatus.PENDING},
    )
    mock_logger_warning = mocker.patch("cactus_runner.app.handler.logger.error")

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    mock_logger_warning.assert_called_once()
    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.GONE


@pytest.mark.parametrize("proceed_handled", [True, False])
@pytest.mark.asyncio
async def test_proceed_handler(proceed_handled: bool, pg_base_config, mocker):
    # Arrange
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].active_test_procedure.is_finished.return_value = False
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        communications_disabled=False,
        finished_zip_path=None,
        step_status={"1": StepStatus.PENDING},
    )
    request.app = {}
    request.app[APPKEY_RUNNER_STATE] = RunnerState(active_test_procedure=mock_active_test_procedure)
    request.app[APPKEY_BACKEND_PROVIDER] = Mock(spec=BackendProvider)

    handling_listener = generate_class_instance(Listener, actions=[])

    # This trigger is handled by this listener
    mock_handle_event_trigger = mocker.patch("cactus_runner.app.handler.event.handle_event_trigger")
    mock_handle_event_trigger.return_value = [handling_listener] if proceed_handled else []

    mock_generate_proceed_trigger = mocker.patch("cactus_runner.app.handler.event.generate_proceed_trigger")
    mock_trigger = MagicMock()
    mock_generate_proceed_trigger.return_value = mock_trigger

    num_client_interactions_before = len(request.app[APPKEY_RUNNER_STATE].client_interactions)
    num_requests_before = len(request.app[APPKEY_RUNNER_STATE].request_history)

    # Act
    response = await handler.proceed_handler(request=request)

    # Assert
    #  ... verify we DID NOT update the last client interaction
    assert len(request.app[APPKEY_RUNNER_STATE].client_interactions) == num_client_interactions_before

    # ... verify we DID NOT update the request history
    assert len(request.app[APPKEY_RUNNER_STATE].request_history) == num_requests_before

    # ... verify we triggered the proceed handler
    mock_generate_proceed_trigger.assert_called_once()
    mock_handle_event_trigger.assert_called_once()

    # ... verify we got the response we expected
    assert isinstance(response, Response)
    assert isinstance(response.text, str)
    proceed_response = ProceedResponse.from_json(response.text)
    assert isinstance(proceed_response, ProceedResponse)
    assert proceed_response.handled == proceed_handled


@pytest.mark.asyncio
async def test_proceed_handler_logs_error_with_no_active_test_procedure(mocker):
    # Arrange
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = None
    mock_logger_warning = mocker.patch("cactus_runner.app.handler.logger.error")

    # Act
    response = await handler.proceed_handler(request=request)

    # Assert
    mock_logger_warning.assert_called_once()
    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_proceed_handler_logs_error_with_finished_test(mocker):
    # Arrange
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        communications_disabled=False,
        finished_zip_path=Path("."),
        step_status={"1": StepStatus.PENDING},
    )
    mock_logger_warning = mocker.patch("cactus_runner.app.handler.logger.error")

    # Act
    response = await handler.proceed_handler(request=request)

    # Assert
    mock_logger_warning.assert_called_once()
    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.GONE
