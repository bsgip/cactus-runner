from unittest.mock import AsyncMock, MagicMock

import pytest
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions import Event

from cactus_runner.app import event
from cactus_runner.app.shared import (
    APPKEY_RUNNER_STATE,
)
from cactus_runner.models import Listener, StepStatus


@pytest.mark.parametrize(
    "test_event,listeners,matching_listener_index",
    [
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                )
            ],
            0,
        ),
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/edev"}),
                    actions=[],
                    enabled=True,
                ),
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                ),
            ],
            1,
        ),
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="POST-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                ),
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                ),
            ],
            1,
        ),
    ],
)
@pytest.mark.asyncio
async def test_handle_event_with_matching_listener(
    test_event: Event, listeners: list[Listener], matching_listener_index: int
):
    # Arrange
    active_test_procedure = MagicMock()
    active_test_procedure.listeners = listeners
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    # Act
    matched_listener, serve_request_first = await event.handle_event(
        session=mock_session,
        event=test_event,
        active_test_procedure=active_test_procedure,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert matched_listener == listeners[matching_listener_index]
    assert not serve_request_first
    assert_mock_session(mock_session)


@pytest.mark.parametrize(
    "test_event,listeners",
    [
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                )
            ],
        ),
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                )
            ],
        ),
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                )
            ],
        ),
    ],
)
@pytest.mark.asyncio
async def test_handle_event_calls_apply_actions(mocker, test_event: Event, listeners: list[Listener]):
    # Arrange
    active_test_procedure = MagicMock()
    active_test_procedure.listeners = listeners
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    mock_apply_actions = mocker.patch("cactus_runner.app.event.apply_actions")

    # Act
    await event.handle_event(
        session=mock_session,
        event=test_event,
        active_test_procedure=active_test_procedure,
        envoy_client=mock_envoy_client,
    )

    # Assert
    mock_apply_actions.assert_called_once()
    assert_mock_session(mock_session)


@pytest.mark.parametrize(
    "test_event,listeners",
    [
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=False,
                )
            ],
        ),  # Events match but the listener is disabled
        (
            Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            [
                Listener(
                    step="step",
                    event=Event(type="POST-request-received", parameters={"endpoint": "/dcap"}),
                    actions=[],
                    enabled=True,
                )
            ],
        ),  # Parameters match but event types differ
        (
            Event(type="POST-request-received", parameters={"endpoint": "/mup"}),
            [
                Listener(
                    step="step",
                    event=Event(type="POST-request-received", parameters={"endpoint": "/edev"}),
                    actions=[],
                    enabled=True,
                )
            ],
        ),  # Event types match but parameters differ
    ],
)
@pytest.mark.asyncio
async def test_handle_event_with_no_matches(test_event: Event, listeners: list[Listener]):
    # Arrange
    active_test_procedure = MagicMock()
    active_test_procedure.listeners = listeners
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    # Act
    listener, serve_request_first = await event.handle_event(
        session=mock_session,
        event=test_event,
        active_test_procedure=active_test_procedure,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert listener is None
    assert not serve_request_first
    assert_mock_session(mock_session)


@pytest.mark.asyncio
async def test_update_test_procedure_progress(pg_empty_config):
    # Arrange
    request_data = ""
    request_read = AsyncMock()
    request_read.return_value = request_data
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"
    request.read = request_read

    active_test_procedure = MagicMock()
    #     request.app[APPKEY_RUNNER_STATE].request_history = []
    #     request.app[APPKEY_RUNNER_STATE].active_test_procedure.step_status = {}
    #
    #     handler.SERVER_URL = ""  # Override the server url
    #
    #     handler.DEV_SKIP_AUTHORIZATION_CHECK = True
    #
    #     response_text = "RESPONSE-TEXT"
    #     response_status = http.HTTPStatus.OK
    #     response_headers = {"X-API-Key": "API-KEY"}
    #     mock_client_request = mocker.patch("aiohttp.client.request")
    #     mock_client_request.return_value.__aenter__.return_value.status = response_status
    #     mock_client_request.return_value.__aenter__.return_value.read.return_value = response_text
    #     mock_client_request.return_value.__aenter__.return_value.headers = response_headers
    #
    #     # spy_handle_event = mocker.spy(handler.event, "handle_event")
    #     mock_handle_event = mocker.patch("cactus_runner.app.handler.update_test_procedure_progress")
    #     mock_handle_event.return_value = (None, False)
    #     matching_step_name = "STEP-NAME"
    #     # mock_handle_event.return_value.step = matching_step_name
    #
    # Act
    matching_step_name, serve_request_first = await event.update_test_procedure_progress(
        request=request, active_test_procedure=active_test_procedure
    )

    # Assert

    # assert request.app[APPKEY_RUNNER_STATE].active_test_procedure.step_status[matching_step_name] == StepStatus.RESOLVED
