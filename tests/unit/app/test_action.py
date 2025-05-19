import unittest.mock as mock

import pytest
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions import Action, Event

from cactus_runner.app.action import (
    UnknownActionError,
    action_enable_listeners,
    action_remove_listeners,
    apply_action,
)
from cactus_runner.models import ActiveTestProcedure, Listener


def create_testing_active_test_procedure(listeners: list[Listener]) -> ActiveTestProcedure:
    return ActiveTestProcedure("test", None, listeners, {})


@pytest.mark.anyio
async def test_action_enable_listeners():
    # Arrange
    step_name = "step"
    steps_to_enable = [step_name]
    original_steps_to_enable = steps_to_enable.copy()
    listeners = [
        Listener(step=step_name, event=Event(type="", parameters={}), actions=[])
    ]  # listener defaults to disabled but should be enabled during this test
    active_test_procedure = create_testing_active_test_procedure(listeners)
    resolved_parameters = {"listeners": steps_to_enable}

    # Act
    await action_enable_listeners(active_test_procedure, resolved_parameters)

    # Assert
    assert listeners[0].enabled
    assert steps_to_enable == original_steps_to_enable  # Ensure we are not mutating step_to_enable


@pytest.mark.parametrize(
    "steps_to_disable,listeners",
    [
        (
            ["step1"],
            [
                Listener(step="step1", event=Event(type="", parameters={}), actions=[], enabled=True),
            ],
        ),
        (
            ["step1"],
            [
                Listener(step="step1", event=Event(type="", parameters={}), actions=[], enabled=False),
            ],
        ),
        (
            ["step1", "step2"],
            [
                Listener(step="step1", event=Event(type="", parameters={}), actions=[], enabled=True),
                Listener(step="step2", event=Event(type="", parameters={}), actions=[], enabled=True),
            ],
        ),
    ],
)
@pytest.mark.anyio
async def test_action_remove_listeners(steps_to_disable: list[str], listeners: list[Listener]):
    # Arrange
    original_steps_to_disable = steps_to_disable.copy()
    active_test_procedure = create_testing_active_test_procedure(listeners)
    resolved_parameters = {"listeners": steps_to_disable}

    # Act
    await action_remove_listeners(active_test_procedure, resolved_parameters)

    # Assert
    assert len(listeners) == 0  # all listeners removed from list of listeners
    assert steps_to_disable == original_steps_to_disable  # check we are mutating 'steps_to_diable'


@pytest.mark.parametrize(
    "action, apply_function_name",
    [
        (Action(type="enable-listeners", parameters={"listeners": []}), "action_enable_listeners"),
        (Action(type="remove-listeners", parameters={"listeners": []}), "action_remove_listeners"),
    ],
)
@pytest.mark.anyio
async def test_apply_action(mocker, action: Action, apply_function_name: str):
    # Arrange

    mock_apply_function = mocker.patch(f"cactus_runner.app.action.{apply_function_name}")
    mock_session = create_mock_session()
    mock_envoy_client = mock.MagicMock()

    # Act
    await apply_action(action, create_testing_active_test_procedure([]), mock_session, mock_envoy_client)

    # Assert
    mock_apply_function.assert_called_once()
    assert_mock_session(mock_session)


@pytest.mark.anyio
async def test__apply_action_raise_exception_for_unknown_action_type():
    active_test_procedure = mock.MagicMock()
    mock_session = create_mock_session()
    mock_envoy_client = mock.MagicMock()

    with pytest.raises(UnknownActionError):
        await apply_action(
            envoy_client=mock_envoy_client,
            session=mock_session,
            action=Action(type="NOT-A-VALID-ACTION-TYPE", parameters={}),
            active_test_procedure=active_test_procedure,
        )
    assert_mock_session(mock_session)
