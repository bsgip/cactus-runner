from unittest.mock import MagicMock

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
    mocker, test_event: Event, listeners: list[Listener], matching_listener_index: int
):
    # Arrange
    mock_all_checks_passing = mocker.patch("cactus_runner.app.event.all_checks_passing")
    mock_all_checks_passing.return_value = True
    runner_state = MagicMock()
    runner_state.active_test_procedure.listeners = listeners
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    # Act
    matched_listener, serve_request_first = await event.handle_event(
        session=mock_session,
        event=test_event,
        runner_state=runner_state,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert matched_listener == listeners[matching_listener_index]
    assert not serve_request_first
    mock_all_checks_passing.assert_called_once()
    assert_mock_session(mock_session)


@pytest.mark.asyncio
async def test_handle_event_with_checks_failing(mocker):
    test_event = Event(type="GET-request-received", parameters={"endpoint": "/dcap"})
    listeners = [
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[],
            enabled=True,
        )
    ]

    # Arrange
    mock_all_checks_passing = mocker.patch("cactus_runner.app.event.all_checks_passing")
    mock_all_checks_passing.return_value = False
    runner_state = MagicMock()
    runner_state.active_test_procedure.listeners = listeners
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    # Act
    matched_listener, serve_request_first = await event.handle_event(
        session=mock_session,
        event=test_event,
        runner_state=runner_state,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert matched_listener is None
    assert not serve_request_first
    mock_all_checks_passing.assert_called_once()
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
    runner_state = MagicMock()
    runner_state.active_test_procedure.listeners = listeners
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    mock_apply_actions = mocker.patch("cactus_runner.app.event.apply_actions")
    mock_all_checks_passing = mocker.patch("cactus_runner.app.event.all_checks_passing")
    mock_all_checks_passing.return_value = True

    # Act
    await event.handle_event(
        session=mock_session,
        event=test_event,
        runner_state=runner_state,
        envoy_client=mock_envoy_client,
    )

    # Assert
    mock_apply_actions.assert_called_once()
    mock_all_checks_passing.assert_called_once()
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
async def test_handle_event_with_no_matches(mocker, test_event: Event, listeners: list[Listener]):
    # Arrange
    mock_all_checks_passing = mocker.patch("cactus_runner.app.event.all_checks_passing")
    mock_all_checks_passing.return_value = True

    runner_state = MagicMock()
    runner_state.active_test_procedure.listeners = listeners

    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    # Act
    listener, serve_request_first = await event.handle_event(
        session=mock_session,
        event=test_event,
        runner_state=runner_state,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert listener is None
    assert not serve_request_first
    assert_mock_session(mock_session)


@pytest.mark.asyncio
async def test_update_test_procedure_progress(pg_empty_config, mocker):
    # Arrange
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"

    active_test_procedure = MagicMock()
    active_test_procedure.step_status = {}

    request.app[APPKEY_RUNNER_STATE].active_test_procedure = active_test_procedure

    step_name = "STEP-NAME"
    serve_request_first = False
    listener = MagicMock()
    listener.step = step_name
    mock_handle_event = mocker.patch("cactus_runner.app.event.handle_event")
    mock_handle_event.return_value = (listener, serve_request_first)

    # Act
    matching_step_name, serve_request_first = await event.update_test_procedure_progress(request=request)

    # Assert
    mock_handle_event.assert_called_once()
    assert matching_step_name == step_name
    assert serve_request_first == serve_request_first
    assert active_test_procedure.step_status[step_name] == StepStatus.RESOLVED


@pytest.mark.asyncio
async def test_update_test_procedure_progress_respects_serve_request_first(pg_empty_config, mocker):
    # Arrange
    request = MagicMock()
    request.path = "/dcap"
    request.path_qs = "/dcap"
    request.method = "GET"

    active_test_procedure = MagicMock()
    active_test_procedure.step_status = {}

    request.app[APPKEY_RUNNER_STATE].active_test_procedure = active_test_procedure

    step_name = "STEP-NAME"
    serve_request_first = True
    listener = MagicMock()
    listener.step = step_name
    listener.parameters = {"serve_request_first": True}
    mock_handle_event = mocker.patch("cactus_runner.app.event.handle_event")
    mock_handle_event.return_value = (listener, serve_request_first)

    # Act
    matching_step_name, serve_request_first = await event.update_test_procedure_progress(request=request)

    # Assert
    mock_handle_event.assert_called_once()
    assert matching_step_name == step_name
    assert serve_request_first == serve_request_first
    assert not request.app[APPKEY_RUNNER_STATE].active_test_procedure.step_status
