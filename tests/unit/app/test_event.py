from datetime import datetime, timezone
from http import HTTPMethod
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from assertical.asserts.time import assert_nowish
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions import Event

from cactus_runner.app import event
from cactus_runner.app.shared import (
    APPKEY_RUNNER_STATE,
)
from cactus_runner.models import Listener, StepStatus


def test_generate_time_trigger():
    """Simple sanity check"""
    trigger = event.generate_time_trigger()
    assert isinstance(trigger, event.EventTrigger)
    assert_nowish(trigger.time)
    assert trigger.time.tzinfo
    assert trigger.type == event.EventTriggerType.TIME
    assert trigger.client_request is None


@pytest.mark.parametrize(
    "request_method, request_path, before_serving",
    [
        ("GET", "/", True),
        ("GET", "/", False),
        ("POST", "/foo/bar", True),
        ("POST", "/foo/bar", False),
        ("DELETE", "/foo/bar/baz", True),
        ("DELETE", "/foo/bar/baz", False),
        ("PUT", "/foo/bar", True),
        ("PUT", "/foo/bar/baz", False),
    ],
)
def test_generate_client_request_trigger(request_method: str, request_path: str, before_serving: bool):
    """Checks basic parsing of AIOHttp requests"""

    mock_request = MagicMock()
    mock_request.method = request_method
    mock_request.path = request_path

    trigger = event.generate_client_request_trigger(mock_request, before_serving)
    assert isinstance(trigger, event.EventTrigger)
    assert_nowish(trigger.time)
    assert trigger.time.tzinfo

    if before_serving:
        assert trigger.type == event.EventTriggerType.CLIENT_REQUEST_BEFORE
    else:
        assert trigger.type == event.EventTriggerType.CLIENT_REQUEST_AFTER

    assert isinstance(trigger.client_request, event.ClientRequestDetails)
    assert isinstance(trigger.client_request.method, HTTPMethod)
    assert trigger.client_request.method == request_method
    assert isinstance(trigger.client_request.path, str)
    assert trigger.client_request.path == request_path


@pytest.mark.parametrize(
    "trigger, listener, expected",
    [
        (
            event.EventTrigger(event.EventTriggerType.TIME, datetime(2022, 11, 10, tzinfo=timezone.utc), False, None),
            Listener(
                step="step",
                event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            False,  # Wrong type of event
        ),
        (
            event.EventTrigger(event.EventTriggerType.TIME, datetime(2022, 11, 10, tzinfo=timezone.utc), False, None),
            Listener(
                step="step",
                event=Event(type="unsupported-event-type", parameters={}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            False,  # Unrecognized event type
        ),
        (
            event.EventTrigger(event.EventTriggerType.TIME, datetime(2022, 11, 10, tzinfo=timezone.utc), False, None),
            Listener(
                step="step",
                event=Event(type="wait", parameters={"duration_seconds": 300}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            False,  # This was enabled after the event trigger (negative time)
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.TIME, datetime(2024, 11, 10, 5, 30, 0, tzinfo=timezone.utc), False, None
            ),
            Listener(
                step="step",
                event=Event(type="wait", parameters={"duration_seconds": 300}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, 5, 24, 0, tzinfo=timezone.utc),
            ),
            True,
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.TIME, datetime(2024, 11, 10, 5, 30, 0, tzinfo=timezone.utc), False, None
            ),
            Listener(
                step="step",
                event=Event(type="wait", parameters={"duration_seconds": 300}),
                actions=[],
                enabled_time=None,
            ),
            False,  # This listener is NOT enabled
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.TIME, datetime(2024, 11, 10, 5, 30, 0, tzinfo=timezone.utc), False, None
            ),
            Listener(
                step="step",
                event=Event(type="wait", parameters={"duration_seconds": 300}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, 5, 26, 0, tzinfo=timezone.utc),
            ),
            False,  # Not enough time elapsed
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.GET, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(type="GET-request-received", parameters={"endpoint": "/foo/bar"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            True,
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.POST, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(type="POST-request-received", parameters={"endpoint": "/foo/bar"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            True,
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.PUT, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(type="PUT-request-received", parameters={"endpoint": "/foo/bar"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            True,
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.GET, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(
                    type="GET-request-received", parameters={"endpoint": "/foo/bar", "serve_request_first": False}
                ),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            True,
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_AFTER,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.GET, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(
                    type="GET-request-received", parameters={"endpoint": "/foo/bar", "serve_request_first": True}
                ),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            True,
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_AFTER,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.GET, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(type="GET-request-received", parameters={"endpoint": "/foo/bar"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            False,  # Without serve_request_first: True - Only BEFORE events will fire
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.GET, "/foo"),
            ),
            Listener(
                step="step",
                event=Event(type="GET-request-received", parameters={"endpoint": "/foo/bar"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            False,  # Wrong endpoint
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.GET, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(type="GET-request-received", parameters={"endpoint": "/foo"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            False,  # Wrong endpoint
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.POST, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(type="GET-request-received", parameters={"endpoint": "/foo/bar"}),
                actions=[],
                enabled_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
            ),
            False,  # Wrong method
        ),
        (
            event.EventTrigger(
                event.EventTriggerType.CLIENT_REQUEST_BEFORE,
                datetime(2022, 11, 10, tzinfo=timezone.utc),
                False,
                event.ClientRequestDetails(HTTPMethod.GET, "/foo/bar"),
            ),
            Listener(
                step="step",
                event=Event(type="GET-request-received", parameters={"endpoint": "/foo/bar"}),
                actions=[],
                enabled_time=None,
            ),
            False,  # Not enabled
        ),
    ],
)
@patch("cactus_runner.app.event.resolve_variable_expressions_from_parameters")
@pytest.mark.anyio
async def test_is_listener_triggerable(
    mock_resolve_variable_expressions_from_parameters: MagicMock,
    trigger: event.EventTrigger,
    listener: Listener,
    expected: bool,
):
    """Tests various combinations of listeners and events to see if they could potentially trigger"""

    # Arrange
    mock_session = create_mock_session()
    mock_resolve_variable_expressions_from_parameters.side_effect = lambda session, parameters: parameters

    result = await event.is_listener_triggerable(listener, trigger, mock_session)

    # Assert
    assert isinstance(result, bool)
    assert result == expected
    assert_mock_session(mock_session)
    assert all([ca[0] is mock_session for ca in mock_resolve_variable_expressions_from_parameters.call_args_list])


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
