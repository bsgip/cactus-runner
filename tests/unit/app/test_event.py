from datetime import datetime, timezone
from http import HTTPMethod
from unittest.mock import MagicMock, patch

import pytest
from assertical.asserts.time import assert_nowish
from assertical.asserts.type import assert_list_type
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions import Event

from cactus_runner.app import event
from cactus_runner.models import ActiveTestProcedure, Listener, RunnerState


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
    assert all([ca.args[0] is mock_session for ca in mock_resolve_variable_expressions_from_parameters.call_args_list])


@pytest.mark.parametrize(
    "runner_state",
    [
        (RunnerState(None, [], None)),  # This is when we have no active test procedure
        (
            RunnerState(
                ActiveTestProcedure("", None, [], {}, "", 0, None, finished_zip_data=bytes([0, 1])),
                [generate_class_instance(Listener, actions=[])],
                None,
            )
        ),  # This is a finished test
    ],
)
@patch("cactus_runner.app.event.is_listener_triggerable")
@pytest.mark.anyio
async def test_handle_event_trigger_shortcircuit_conditions(
    mock_is_listener_triggerable: MagicMock, runner_state: RunnerState
):
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()

    # Act
    result = await event.handle_event_trigger(
        generate_class_instance(event.EventTrigger), runner_state, mock_session, mock_envoy_client
    )

    # Assertgenerate_class_instance(event.EventTrigger)
    assert result == []
    assert_mock_session(mock_session)
    mock_is_listener_triggerable.assert_not_called()


def gen_listener(
    seed,
) -> Listener:
    return generate_class_instance(Listener, seed=seed, actions=[])


@pytest.mark.parametrize(
    "single_listener, listeners, trigger_indexes, check_indexes, expected_indexes",
    [
        (False, [], [], [], []),
        (False, [gen_listener(0)], [], [], []),
        (False, [gen_listener(0)], [0], [], []),
        (False, [gen_listener(0)], [0], [0], [0]),
        (False, [gen_listener(0), gen_listener(1)], [0, 1], [0, 1], [0, 1]),
        (True, [gen_listener(0), gen_listener(1)], [0, 1], [0, 1], [0]),
        (False, [gen_listener(0), gen_listener(1), gen_listener(2)], [0, 2], [1, 2], [2]),
        (False, [gen_listener(0), gen_listener(1), gen_listener(2)], [2], [0, 1, 2], [2]),
        (True, [gen_listener(0), gen_listener(1), gen_listener(2)], [0, 1, 2], [1, 2], [1]),
    ],
)
@patch("cactus_runner.app.event.is_listener_triggerable")
@patch("cactus_runner.app.event.all_checks_passing")
@pytest.mark.anyio
async def test_handle_event_trigger_normal_operation(
    mock_all_checks_passing: MagicMock,
    mock_is_listener_triggerable: MagicMock,
    single_listener: bool,
    listeners: list[Listener],
    trigger_indexes: list[int],
    check_indexes: list[int],
    expected_indexes: list[int],
):
    """Runs various scenarios for testing listeners and validating they pass checks"""
    # Arrange
    mock_session = create_mock_session()
    mock_envoy_client = MagicMock()
    input_trigger = generate_class_instance(event.EventTrigger, single_listener=single_listener)
    input_runner_state = RunnerState(
        ActiveTestProcedure("", None, listeners, {}, "", 0, None, finished_zip_data=None),
        [],
        None,
    )

    # we want a unique "checks" reference for each event listener so we can look it up later
    for idx, l in enumerate(listeners):
        l.event = generate_class_instance(Event, seed=idx, checks=MagicMock(), parameters={})

    def find_index(to_find, items) -> int | None:
        for idx, i in enumerate(items):
            if i is to_find:
                return idx
        return None

    # Mock is_listener_triggerable to return True if the listener is in trigger_indexes
    def do_mock_is_listener_triggerable(listener, trigger, session):
        assert session is mock_session
        assert trigger is input_trigger

        idx = find_index(listener, listeners)
        assert idx is not None, f"Couldn't find listener {listener}. This is a test setup issue."
        return idx in trigger_indexes

    mock_is_listener_triggerable.side_effect = do_mock_is_listener_triggerable

    # Mock all_checks_passing to return True if the checks is in check_indexes
    def do_mock_all_checks_passing(checks, active_test_procedure, session):
        assert session is mock_session
        assert active_test_procedure is input_runner_state.active_test_procedure

        idx = find_index(checks, [listener.event.checks for listener in listeners])
        assert idx is not None, "Couldn't find checks. This is a test setup issue."

        return idx in check_indexes

    mock_all_checks_passing.side_effect = do_mock_all_checks_passing

    # Act
    result = await event.handle_event_trigger(input_trigger, input_runner_state, mock_session, mock_envoy_client)

    # Assert
    assert_list_type(Listener, result, len(expected_indexes))
    for listener in result:
        assert find_index(listener, listeners) in expected_indexes
    assert_mock_session(mock_session)
