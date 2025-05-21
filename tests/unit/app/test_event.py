from unittest.mock import MagicMock

import pytest
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions import Event

from cactus_runner.app import event
from cactus_runner.models import Listener


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
    matched_listener = await event.handle_event(
        session=mock_session,
        event=test_event,
        active_test_procedure=active_test_procedure,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert matched_listener == listeners[matching_listener_index]
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
    listener = await event.handle_event(
        session=mock_session,
        event=test_event,
        active_test_procedure=active_test_procedure,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert listener is None
    assert_mock_session(mock_session)
