import http
from unittest.mock import MagicMock, call

import pytest
from aiohttp.web import Response
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance

from cactus_runner.app import handler
from cactus_runner.app.proxy import ProxyResult
from cactus_runner.app.shared import APPKEY_ENVOY_ADMIN_CLIENT, APPKEY_RUNNER_STATE
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    ClientInteractionType,
    Listener,
    RequestEntry,
    RunnerState,
    RunnerStatus,
    StepStatus,
)


def mocked_ProxyResult(status: int) -> ProxyResult:
    return ProxyResult("", "", bytes(), None, {}, Response(status=status))


@pytest.mark.asyncio
async def test_finalize_handler(mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """

    request = MagicMock()
    zip_data = bytes([99, 55])
    mock_finish_active_test = mocker.patch("cactus_runner.app.handler.finalize.finish_active_test")
    mock_finish_active_test.return_value = zip_data

    mock_safely_get_error_zip = mocker.patch("cactus_runner.app.handler.finalize.safely_get_error_zip")

    mocker.patch("cactus_runner.app.handler.begin_session")

    response = await handler.finalize_handler(request=request)

    assert isinstance(response, Response)
    assert response.body == zip_data
    mock_finish_active_test.assert_called_once()
    mock_safely_get_error_zip.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_handler_finish_error(mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """

    request = MagicMock()
    safe_error_data = bytes([0, 4, 1, 1])
    mock_finish_active_test = mocker.patch("cactus_runner.app.handler.finalize.finish_active_test")
    mock_finish_active_test.side_effect = Exception("mock exception")

    mock_safely_get_error_zip = mocker.patch("cactus_runner.app.handler.finalize.safely_get_error_zip")
    mock_safely_get_error_zip.return_value = safe_error_data

    mocker.patch("cactus_runner.app.handler.begin_session")

    response = await handler.finalize_handler(request=request)

    assert isinstance(response, Response)
    assert response.body == safe_error_data
    mock_finish_active_test.assert_called_once()
    mock_safely_get_error_zip.assert_called_once()


@pytest.mark.asyncio
async def test_finalize_handler_resets_runner_state(mocker):
    request = MagicMock()
    request.app[APPKEY_RUNNER_STATE].request_history = [None]  # a non-empty list stand-in
    mocker.patch("cactus_runner.app.handler.begin_session")
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
async def test_health_handler(mocker, is_db_healthy: bool, is_admin_api_healthy: bool, expected_status):
    mocker.patch("cactus_runner.app.handler.is_db_healthy").return_value = is_db_healthy
    mocker.patch("cactus_runner.app.handler.is_admin_api_healthy").return_value = is_admin_api_healthy
    response = await handler.health_handler(MagicMock())
    assert isinstance(response, Response)
    assert response.status == expected_status


@pytest.mark.asyncio
async def test_status_handler(mocker):
    """
    `mocker` is a fixture provided by the `pytest-mock` plugin
    """
    request = MagicMock()
    get_active_runner_status_spy = mocker.spy(handler.status, "get_active_runner_status")
    mocker.patch("cactus_runner.app.handler.begin_session")

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
        finished_zip_data=None,
        step_status={"1": StepStatus.PENDING},
    )
    request.app = {}
    request.app[APPKEY_RUNNER_STATE] = RunnerState(active_test_procedure=mock_active_test_procedure)
    request.app[APPKEY_ENVOY_ADMIN_CLIENT] = MagicMock()

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
    mock_generate_client_request_trigger.assert_called_once_with(request, before_serving=True)
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
        finished_zip_data=None,
        step_status={"1": StepStatus.PENDING},
    )
    request.app = {}
    request.app[APPKEY_RUNNER_STATE] = RunnerState(active_test_procedure=mock_active_test_procedure)
    request.app[APPKEY_ENVOY_ADMIN_CLIENT] = MagicMock()
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
        [call(request, before_serving=True), call(request, before_serving=False)]
    )
    mock_handle_event_trigger.call_count == 2

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
        finished_zip_data=bytes([0, 1]),
        step_status={"1": StepStatus.PENDING},
    )
    mock_logger_warning = mocker.patch("cactus_runner.app.handler.logger.error")

    # Act
    response = await handler.proxied_request_handler(request=request)

    # Assert
    mock_logger_warning.assert_called_once()
    assert isinstance(response, Response)
    assert response.status == http.HTTPStatus.GONE
