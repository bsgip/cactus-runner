import http
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from aiohttp.web import Response
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance

from cactus_runner.app import event, handler
from cactus_runner.app.shared import APPKEY_RUNNER_STATE
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    ClientInteractionType,
    RequestEntry,
    RunnerStatus,
    StepStatus,
)


@pytest.mark.asyncio
async def test_finalize_handler(mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """

    request = MagicMock()
    get_active_runner_status_spy = mocker.spy(handler.status, "get_active_runner_status")
    create_response_spy = mocker.spy(handler.finalize, "create_response")
    mocker.patch("cactus_runner.app.finalize.get_zip_contents")

    response = await handler.finalize_handler(request=request)

    assert isinstance(response, Response)
    create_response_spy.assert_called_once()
    get_active_runner_status_spy.assert_called_once()


@pytest.mark.asyncio
async def test_finalize_handler_resets_runner_state(mocker):
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].request_history = [None]  # a non-empty list stand-in
    mocker.patch("cactus_runner.app.finalize.create_response")

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


@pytest.mark.asyncio
async def test_status_handler(mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """
    request = MagicMock()
    get_active_runner_status_spy = mocker.spy(handler.status, "get_active_runner_status")

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


@pytest.mark.asyncio
async def test_proxied_request_handler_performs_authorization(mocker):
    # Arrange
    request = MagicMock()
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
async def test_proxied_request_handler(pg_empty_config, mocker):
    # Arrange
    request_data = ""
    request_read = AsyncMock()
    request_read.return_value = request_data
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    request.read = request_read
    request.app[APPKEY_RUNNER_STATE].request_history = []
    request.app[APPKEY_RUNNER_STATE].active_test_procedure = generate_class_instance(
        ActiveTestProcedure, communications_disabled=False, step_status={"1": StepStatus.PENDING}
    )

    handler.SERVER_URL = ""  # Override the server url

    handler.DEV_SKIP_AUTHORIZATION_CHECK = True
    spy_request_is_authorized = mocker.spy(handler.auth, "request_is_authorized")

    response_text = "RESPONSE-TEXT"
    response_status = http.HTTPStatus.OK
    response_headers = {"X-API-Key": "API-KEY"}
    mock_client_request = mocker.patch("aiohttp.client.request")
    mock_client_request.return_value.__aenter__.return_value.status = response_status
    mock_client_request.return_value.__aenter__.return_value.read.return_value = response_text
    mock_client_request.return_value.__aenter__.return_value.headers = response_headers

    mock_update_test_procedure_status = mocker.patch("cactus_runner.app.event.update_test_procedure_progress")
    mock_update_test_procedure_status.return_value = (event.UNRECOGNISED_STEP_NAME, False)

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    #  ... verify we skip authorization
    assert spy_request_is_authorized.call_count == 0

    #  ... verify we update the last client interaction
    assert isinstance(request.app[APPKEY_RUNNER_STATE].last_client_interaction, ClientInteraction)
    assert (
        request.app[APPKEY_RUNNER_STATE].last_client_interaction.interaction_type
        == ClientInteractionType.PROXIED_REQUEST
    )
    assert_nowish(request.app[APPKEY_RUNNER_STATE].last_client_interaction.timestamp)

    #  ... verify aiohttp.client.request is passed values from the request argument
    mock_client_request.assert_called_once_with(
        request.method, request.path_qs, headers=ANY, allow_redirects=False, data=request_data
    )

    #  ... verify we received the expected proxied response
    #      i.e. the one supplied to 'mock_client_request'
    assert isinstance(response, Response)
    assert response.status == response_status
    assert response.text == response_text
    for key, value in response_headers.items():
        assert key in response.headers
        assert response.headers[key] == value

    # ... verify we called 'update_test_procedure_status'
    mock_update_test_procedure_status.assert_called_once()

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
    assert request_entry.step_name == event.UNRECOGNISED_STEP_NAME


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
async def test_proxied_request_handler_disables_communications(pg_empty_config, mocker):
    # Arrange
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    request.app[APPKEY_RUNNER_STATE].active_test_procedure.communications_disabled = True
    mock_client_request = mocker.patch("aiohttp.client.request")

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    assert mock_client_request.call_count == 0
    assert response.status == http.HTTPStatus.INTERNAL_SERVER_ERROR


def test_communications_disabled_defaults_false():
    assert ActiveTestProcedure.communications_disabled is False
