import unittest.mock as mock
from datetime import datetime, timezone

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_test_definitions import Action, Event
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroup
from envoy.server.model.site import Site

from cactus_runner.app.action import (
    UnknownActionError,
    action_cancel_active_controls,
    action_create_der_control,
    action_enable_listeners,
    action_register_end_device,
    action_remove_listeners,
    action_set_default_der_control,
    action_set_poll_rate,
    action_set_post_rate,
    apply_action,
    apply_actions,
)
from cactus_runner.models import ActiveTestProcedure, Listener, StepStatus


def create_testing_active_test_procedure(listeners: list[Listener]) -> ActiveTestProcedure:
    return ActiveTestProcedure("test", None, listeners, {}, "", "")


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


@pytest.mark.parametrize(
    "listener",
    [
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[],
            enabled=True,
        ),  # no actions for listener
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[Action(type="enable-listeners", parameters={})],
            enabled=True,
        ),  # 1 action for listener
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[
                Action(type="enable-listeners", parameters={}),
                Action(type="remove-listeners", parameters={}),
            ],
            enabled=True,
        ),  # 2 actions for listener
    ],
)
@pytest.mark.anyio
async def test_apply_actions(mocker, listener: Listener):
    # Arrange
    active_test_procedure = mock.MagicMock()
    mock_session = create_mock_session()
    mock_apply_action = mocker.patch("cactus_runner.app.action.apply_action")
    mock_envoy_client = mock.MagicMock()

    # Act
    await apply_actions(
        session=mock_session,
        listener=listener,
        active_test_procedure=active_test_procedure,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert mock_apply_action.call_count == len(listener.actions)


@pytest.mark.anyio
async def test_action_set_default_der_control(pg_base_config, envoy_admin_client):
    """Success tests"""
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()
    resolved_params = {
        "opModImpLimW": 10,
        "opModExpLimW": 10,
        "opModGenLimW": 10,
        "opModLoadLimW": 10,
        "setGradW": 10,
    }
    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_set_default_der_control(
            session=session, envoy_client=envoy_admin_client, resolved_parameters=resolved_params
        )

    # Assert
    assert pg_base_config.execute("select count(*) from default_site_control;").fetchone()[0] == 1


@pytest.mark.anyio
async def test_action_create_der_control_no_group(pg_base_config, envoy_admin_client):
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()
    resolved_params = {
        "start": datetime.now(timezone.utc),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "randomizeStart_seconds": 0,
        "opModEnergize": 0,
        "opModConnect": 0,
        "opModImpLimW": 0,
        "opModExpLimW": 0,
        "opModGenLimW": 0,
        "opModLoadLimW": 0,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client)

    # Assert
    assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from site_control_group;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 1


@pytest.mark.anyio
async def test_action_create_der_control_existing_group(pg_base_config, envoy_admin_client):
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        session.add(generate_class_instance(SiteControlGroup, primacy=2))
        await session.commit()
    resolved_params = {
        "start": datetime.now(timezone.utc),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "randomizeStart_seconds": 0,
        "opModEnergize": 0,
        "opModConnect": 0,
        "opModImpLimW": 0,
        "opModExpLimW": 0,
        "opModGenLimW": 0,
        "opModLoadLimW": 0,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client)

    # Assert
    assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from site_control_group;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 1


@pytest.mark.anyio
async def test_action_cancel_active_controls(pg_base_config, envoy_admin_client):
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, aggregator_id=1, site_id=1)
        session.add(site)
        site_ctrl_grp = generate_class_instance(SiteControlGroup, primacy=2, site_control_group_id=1)
        session.add(site_ctrl_grp)
        await session.flush()

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelope,
                calculation_log_id=None,
                site_control_group=site_ctrl_grp,
                site=site,
                start_time=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    # Act
    await action_cancel_active_controls(envoy_admin_client)

    # Assert
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 0


@pytest.mark.anyio
async def test_action_set_poll_rate(pg_base_config, envoy_admin_client):
    # Arrange
    resolved_params = {"rate_seconds": 10}

    # Act
    await action_set_poll_rate(resolved_params, envoy_admin_client)

    # Assert
    assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 1


@pytest.mark.anyio
async def test_action_set_post_rate(pg_base_config, envoy_admin_client):
    # Arrange
    resolved_params = {"rate_seconds": 10}

    # Act
    await action_set_post_rate(resolved_params, envoy_admin_client)

    # Assert
    assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 1


@pytest.mark.anyio
async def test_action_register_end_device(pg_base_config):
    # Arrange
    atp = generate_class_instance(ActiveTestProcedure, step_status={"1": StepStatus.PENDING})
    resolved_params = {
        "nmi": "abc",
        "registration_pin": 1234,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_register_end_device(atp, resolved_params, session)

    # Assert
    assert pg_base_config.execute("select count(*) from site;").fetchone()[0] == 1
