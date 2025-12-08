import unittest.mock as mock
from datetime import datetime, timezone
from typing import Any

import pytest
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_test_definitions.client import ACTION_PARAMETER_SCHEMA, Action, Event
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroup
from envoy.server.model.server import RuntimeServerConfig
from envoy.server.model.site import DefaultSiteControl, Site
from sqlalchemy import select

from cactus_runner.app.action import (
    UnknownActionError,
    action_cancel_active_controls,
    action_communications_status,
    action_create_der_control,
    action_create_der_program,
    action_edev_registration_links,
    action_enable_steps,
    action_register_end_device,
    action_remove_steps,
    action_set_comms_rate,
    action_set_default_der_control,
    apply_action,
    apply_actions,
)
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientCertificateType,
    Listener,
    ResourceAnnotations,
    RunnerState,
    StepInfo,
    StepStatus,
)

# This is a list of every action type paired with the handler function. This must be kept in sync with
# the actions defined in cactus test definitions (via ACTION_PARAMETER_SCHEMA). This sync will be enforced
ACTION_TYPE_TO_HANDLER: dict[str, str] = {
    "enable-steps": "action_enable_steps",
    "remove-steps": "action_remove_steps",
    "finish-test": "action_finish_test",
    "set-default-der-control": "action_set_default_der_control",
    "create-der-control": "action_create_der_control",
    "create-der-program": "action_create_der_program",
    "cancel-active-der-controls": "action_cancel_active_controls",
    "set-comms-rate": "action_set_comms_rate",
    "register-end-device": "action_register_end_device",
    "communications-status": "action_communications_status",
    "edev-registration-links": "action_edev_registration_links",
}


def test_ACTION_TYPE_TO_HANDLER_in_sync():
    """Tests that every action defined in ACTION_TYPE_TO_HANDLER has an appropriate entry in ACTION_NAMES_WITH_HANDLER

    Failures in this test indicate that ACTION_NAMES_WITH_HANDLER hasn't been kept up to date"""

    # Make sure that every cactus-test-definition action is found in ACTION_TYPE_TO_HANDLER
    for action_type in ACTION_PARAMETER_SCHEMA.keys():
        assert action_type in ACTION_TYPE_TO_HANDLER, f"The action type {action_type} doesn't have a known handler fn"

    # Make sure we don't have any extra definitions not found in cactus-test-definitions
    for action_type in ACTION_TYPE_TO_HANDLER.keys():
        assert (
            action_type in ACTION_PARAMETER_SCHEMA
        ), f"The action type {action_type} isn't defined in the test definitions (has it been removed/renamed)"

    assert len(set(ACTION_TYPE_TO_HANDLER.values())) == len(
        ACTION_TYPE_TO_HANDLER
    ), "At least 1 action type have listed the same action handler. This is likely a bug"


def create_testing_runner_state(listeners: list[Listener]) -> RunnerState:
    return RunnerState(
        generate_class_instance(
            ActiveTestProcedure,
            step_status={listener.step: StepInfo() for listener in listeners},
            finished_zip_data=None,
            listeners=listeners,
        ),
        [],
        None,
    )


@pytest.mark.anyio
async def test_action_enable_steps():
    # Arrange
    step_name = "step"
    steps_to_enable = [step_name]
    original_steps_to_enable = steps_to_enable.copy()
    listeners = [
        Listener(step=step_name, event=Event(type="", parameters={}), actions=[])
    ]  # listener defaults to disabled but should be enabled during this test
    runner_state = create_testing_runner_state(listeners)
    resolved_parameters = {"steps": steps_to_enable}

    # Act
    await action_enable_steps(runner_state.active_test_procedure, resolved_parameters)

    # Assert
    assert_nowish(listeners[0].enabled_time)
    assert listeners[0].enabled_time.tzinfo, "Need timezone aware datetime"
    assert steps_to_enable == original_steps_to_enable  # Ensure we are not mutating step_to_enable
    for step in steps_to_enable:
        assert (
            runner_state.active_test_procedure.step_status[step].get_step_status() == StepStatus.ACTIVE
        ), "Check we update step_status"


@pytest.mark.parametrize(
    "steps_to_disable,listeners",
    [
        (
            ["step1"],
            [
                Listener(
                    step="step1",
                    event=Event(type="", parameters={}),
                    actions=[],
                    enabled_time=datetime(2000, 1, 1, tzinfo=timezone.utc),
                ),
            ],
        ),
        (
            ["step1"],
            [
                Listener(step="step1", event=Event(type="", parameters={}), actions=[], enabled_time=None),
            ],
        ),
        (
            ["step1", "step2"],
            [
                Listener(
                    step="step1",
                    event=Event(type="", parameters={}),
                    actions=[],
                    enabled_time=datetime(2000, 1, 1, tzinfo=timezone.utc),
                ),
                Listener(
                    step="step2",
                    event=Event(type="", parameters={}),
                    actions=[],
                    enabled_time=datetime(2000, 1, 1, tzinfo=timezone.utc),
                ),
            ],
        ),
    ],
)
@pytest.mark.anyio
async def test_action_remove_steps(steps_to_disable: list[str], listeners: list[Listener]):
    # Arrange
    original_steps_to_disable = steps_to_disable.copy()
    runner_state = create_testing_runner_state(listeners)
    resolved_parameters = {"steps": steps_to_disable}

    # Act
    await action_remove_steps(runner_state.active_test_procedure, resolved_parameters)

    # Assert
    assert len(listeners) == 0  # all steps removed from list of listeners
    assert steps_to_disable == original_steps_to_disable  # check we are mutating 'steps_to_diable'
    for step in steps_to_disable:
        assert (
            runner_state.active_test_procedure.step_status[step].get_step_status() == StepStatus.RESOLVED
        ), "Check we update step_status"


@pytest.mark.parametrize(
    "action, apply_function_name",
    [
        (Action(type=action_type, parameters={}), handler_fn)
        for action_type, handler_fn in ACTION_TYPE_TO_HANDLER.items()
    ],
)
@pytest.mark.anyio
async def test_apply_action(mocker, action: Action, apply_function_name: str):
    """This test is fully dynamic and pulls from ACTION_TYPE_TO_HANDLER to ensure every action type is tested
    and mocked."""

    # Arrange
    mock_apply_function = mocker.patch(f"cactus_runner.app.action.{apply_function_name}")
    mock_session = create_mock_session()
    mock_envoy_client = mock.MagicMock()

    # Act
    await apply_action(action, create_testing_runner_state([]), mock_session, mock_envoy_client)

    # Assert
    mock_apply_function.assert_called_once()
    assert_mock_session(mock_session)


@pytest.mark.anyio
async def test__apply_action_raise_exception_for_unknown_action_type():
    runner_state = mock.MagicMock()
    mock_session = create_mock_session()
    mock_envoy_client = mock.MagicMock()

    with pytest.raises(UnknownActionError):
        await apply_action(
            envoy_client=mock_envoy_client,
            session=mock_session,
            action=Action(type="NOT-A-VALID-ACTION-TYPE", parameters={}),
            runner_state=runner_state,
        )
    assert_mock_session(mock_session)


@pytest.mark.parametrize(
    "listener",
    [
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[],
            enabled_time=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ),  # no actions for listener
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[Action(type="enable-steps", parameters={})],
            enabled_time=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ),  # 1 action for listener
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[
                Action(type="enable-steps", parameters={}),
                Action(type="remove-steps", parameters={}),
            ],
            enabled_time=datetime(2000, 1, 1, tzinfo=timezone.utc),
        ),  # 2 actions for listener
    ],
)
@pytest.mark.anyio
async def test_apply_actions(mocker, listener: Listener):
    # Arrange
    runner_state = mock.MagicMock()
    mock_session = create_mock_session()
    mock_apply_action = mocker.patch("cactus_runner.app.action.apply_action")
    mock_envoy_client = mock.MagicMock()

    # Act
    await apply_actions(
        session=mock_session,
        listener=listener,
        runner_state=runner_state,
        envoy_client=mock_envoy_client,
    )

    # Assert
    assert mock_apply_action.call_count == len(listener.actions)


@pytest.mark.parametrize("cancelled", [True, False, None])
@pytest.mark.anyio
async def test_action_set_default_der_control(pg_base_config, envoy_admin_client, cancelled: bool | None):
    """Success tests"""
    # Arrange
    SITE_ID = 2
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1, site_id=SITE_ID))
        await session.commit()
    resolved_params = {
        "opModImpLimW": 10,
        "opModExpLimW": 11,
        "opModGenLimW": 12,
        "opModLoadLimW": 13,
        "setGradW": 14,
    }
    if cancelled is not None:
        resolved_params["cancelled"] = cancelled

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_set_default_der_control(
            session=session, envoy_client=envoy_admin_client, resolved_parameters=resolved_params
        )

    # Assert
    async with generate_async_session(pg_base_config) as session:
        result = await session.execute(select(DefaultSiteControl).where(DefaultSiteControl.site_id == SITE_ID))
        saved_result = result.scalar_one()
        assert saved_result.import_limit_active_watts == 10
        assert saved_result.export_limit_active_watts == 11
        assert saved_result.generation_limit_active_watts == 12
        assert saved_result.load_limit_active_watts == 13
        assert saved_result.ramp_rate_percent_per_second == 14


@pytest.mark.anyio
async def test_action_set_default_der_control_cancelled(pg_base_config, envoy_admin_client):
    """Success tests when cancelling"""
    # Arrange
    SITE_ID = 2
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1, site_id=SITE_ID))
        await session.commit()
    resolved_params = {
        "cancelled": True,
    }
    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_set_default_der_control(
            session=session, envoy_client=envoy_admin_client, resolved_parameters=resolved_params
        )

    # Assert
    async with generate_async_session(pg_base_config) as session:
        result = await session.execute(select(DefaultSiteControl).where(DefaultSiteControl.site_id == SITE_ID))
        saved_result = result.scalar_one()
        assert saved_result.import_limit_active_watts is None
        assert saved_result.export_limit_active_watts is None
        assert saved_result.generation_limit_active_watts is None
        assert saved_result.load_limit_active_watts is None
        assert saved_result.ramp_rate_percent_per_second is None


@pytest.mark.parametrize("fsa_id", [None, 6812])
@pytest.mark.anyio
async def test_action_create_der_control_no_group(pg_base_config, envoy_admin_client, fsa_id):
    # Arrange
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_data=None)
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()
    resolved_params = {
        "start": datetime.now(timezone.utc),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "randomizeStart_seconds": 0,
        "ramp_time_seconds": 0,
        "opModEnergize": 0,
        "opModConnect": 0,
        "opModImpLimW": 0,
        "opModExpLimW": 0,
        "opModGenLimW": 0,
        "opModLoadLimW": 0,
        "opModFixedW": 0,
    }
    if fsa_id is not None:
        resolved_params["fsa_id"] = fsa_id

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Assert
    assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from site_control_group;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 1

    async with generate_async_session(pg_base_config) as session:
        new_scg = (await session.execute(select(SiteControlGroup))).scalar_one()
        if fsa_id is None:
            assert new_scg.fsa_id == 1
        else:
            assert new_scg.fsa_id == fsa_id


@pytest.mark.parametrize("fsa_id", [None, 6812])
@pytest.mark.anyio
async def test_action_create_der_program(pg_base_config, envoy_admin_client, fsa_id):
    # Arrange
    resolved_params = {
        "primacy": 17,
    }
    if fsa_id is not None:
        resolved_params["fsa_id"] = fsa_id

    # Act
    await action_create_der_program(resolved_params, envoy_admin_client)

    # Assert
    if fsa_id is None:
        expected_fsa_id = 1
    else:
        expected_fsa_id = fsa_id
    assert (
        pg_base_config.execute(
            f"select count(*) from site_control_group where primacy = 17 and fsa_id = {expected_fsa_id};"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("fsa_id", [2134, None])
@pytest.mark.anyio
async def test_action_create_der_control_existing_group(pg_base_config, envoy_admin_client, fsa_id):
    # Arrange
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_data=None)
    existing_fsa_id = fsa_id if fsa_id is not None else 21515215
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        session.add(generate_class_instance(SiteControlGroup, primacy=2, fsa_id=existing_fsa_id))
        await session.commit()
    resolved_params = {
        "start": datetime.now(timezone.utc),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "randomizeStart_seconds": 0,
        "ramp_time_seconds": 0,
        "opModEnergize": 0,
        "opModConnect": 0,
        "opModImpLimW": 0,
        "opModExpLimW": 0,
        "opModGenLimW": 0,
        "opModLoadLimW": 0,
        "opModFixedW": 0,
    }
    if fsa_id is not None:
        resolved_params["fsa_id"] = fsa_id

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Assert
    assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from site_control_group;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 1

    async with generate_async_session(pg_base_config) as session:
        new_scg = (await session.execute(select(SiteControlGroup))).scalar_one()
        assert new_scg.fsa_id == existing_fsa_id


@pytest.mark.parametrize("value_seed", [None, 101, 202])
@pytest.mark.anyio
async def test_action_create_der_control_control_values(pg_base_config, envoy_admin_client, value_seed: int | None):
    """Checks that the various DERControl values are properly set for a few variations"""
    # Arrange
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_data=None)
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()

    def gen_bool(s: int | None, offset: int) -> bool | None:
        if s is None:
            return None

        return ((s + offset) % 2) == 0

    def gen_float(s: int | None, offset: int) -> float | None:
        if s is None:
            return None

        return float(s + offset)

    resolved_params = {
        "start": datetime.now(timezone.utc),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "randomizeStart_seconds": 0,
        "opModEnergize": gen_bool(value_seed, 1),
        "opModConnect": gen_bool(value_seed, 2),
        "opModImpLimW": gen_float(value_seed, 3),
        "opModExpLimW": gen_float(value_seed, 4),
        "opModGenLimW": gen_float(value_seed, 5),
        "opModLoadLimW": gen_float(value_seed, 6),
        "opModFixedW": gen_float(value_seed, 7),
        "ramp_time_seconds": gen_float(value_seed, 8),
    }
    for k in list(resolved_params.keys()):
        if resolved_params[k] is None:
            del resolved_params[k]

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Assert
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 1
    async with generate_async_session(pg_base_config) as session:
        doe = (await session.execute(select(DynamicOperatingEnvelope).limit(1))).scalar_one()
        assert doe.set_energized == gen_bool(value_seed, 1)
        assert doe.set_connected == gen_bool(value_seed, 2)
        assert doe.import_limit_active_watts == gen_float(value_seed, 3)
        assert doe.export_limit_watts == gen_float(value_seed, 4)
        assert doe.generation_limit_active_watts == gen_float(value_seed, 5)
        assert doe.load_limit_active_watts == gen_float(value_seed, 6)
        assert doe.set_point_percentage == gen_float(value_seed, 7)
        assert doe.ramp_time_seconds == gen_float(value_seed, 8)


@pytest.mark.anyio
async def test_action_create_der_control_with_tag(pg_base_config, envoy_admin_client):
    """Verifies that creating a DER control with a tag properly annotates it in the active test procedure"""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_data=None, resource_annotations=ResourceAnnotations()
    )
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()
    tag = "DERC1"
    resolved_params = {
        "start": datetime.now(timezone.utc),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "randomizeStart_seconds": 0,
        "ramp_time_seconds": 0,
        "opModEnergize": 0,
        "opModConnect": 0,
        "opModImpLimW": 0,
        "opModExpLimW": 0,
        "opModGenLimW": 0,
        "opModLoadLimW": 0,
        "opModFixedW": 0,
        "tag": tag,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Assert
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 1

    # Verify the tag was added to the active test procedure
    assert tag in active_test_procedure.resource_annotations.der_control_ids_by_alias

    # Verify the tagged control ID matches the created control
    async with generate_async_session(pg_base_config) as session:
        doe = (await session.execute(select(DynamicOperatingEnvelope).limit(1))).scalar_one()
        tagged_control_id = active_test_procedure.resource_annotations.der_control_ids_by_alias[tag]
        assert tagged_control_id == doe.dynamic_operating_envelope_id


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
async def test_action_set_comms_rate_all_values(pg_base_config, envoy_admin_client):
    # Arrange
    resolved_params = {
        "dcap_poll_seconds": 10,
        "edev_post_seconds": 11,
        "edev_list_poll_seconds": 12,
        "fsa_list_poll_seconds": 13,
        "derp_list_poll_seconds": 14,
        "der_list_poll_seconds": 15,
        "mup_post_seconds": 16,
    }

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, aggregator_id=1, site_id=1)
        session.add(site)
        await session.commit()

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_set_comms_rate(resolved_params, session, envoy_admin_client)

    # Assert
    async with generate_async_session(pg_base_config) as session:
        runtime_config = (await session.execute(select(RuntimeServerConfig).limit(1))).scalar_one()
        site = (await session.execute(select(Site).where(Site.site_id == 1).limit(1))).scalar_one()

        assert_nowish(runtime_config.changed_time)
        assert runtime_config.dcap_pollrate_seconds == 10
        assert runtime_config.edevl_pollrate_seconds == 12
        assert runtime_config.fsal_pollrate_seconds == 13
        assert runtime_config.derpl_pollrate_seconds == 14
        assert runtime_config.derl_pollrate_seconds == 15
        assert runtime_config.mup_postrate_seconds == 16

        assert_nowish(site.changed_time)
        assert site.post_rate_seconds == 11


@pytest.mark.anyio
async def test_action_set_comms_rate_no_values(pg_base_config, envoy_admin_client):
    # Arrange
    resolved_params = {}

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, aggregator_id=1, site_id=1, post_rate_seconds=123)
        session.add(site)
        await session.commit()

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_set_comms_rate(resolved_params, session, envoy_admin_client)

    # Assert
    async with generate_async_session(pg_base_config) as session:
        runtime_config = (await session.execute(select(RuntimeServerConfig).limit(1))).scalar_one_or_none()
        assert runtime_config is None, "No config should've been send to envoy"

        site = (await session.execute(select(Site).where(Site.site_id == 1).limit(1))).scalar_one()
        assert site.post_rate_seconds == 123, "This value shouldn't have changed"


@pytest.mark.parametrize(
    "agg_id, agg_lfdi, agg_sfdi, pin", [(0, None, None, None), (0, None, None, 456), (1, "abc", 123, None)]
)
@pytest.mark.anyio
async def test_action_register_aggregator_end_device_device_cert(
    pg_base_config, agg_id: int, agg_lfdi: str | None, agg_sfdi: int | None, pin: int | None
):
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        step_status={},
        client_certificate_type=ClientCertificateType.DEVICE,
        finished_zip_data=None,
        client_aggregator_id=agg_id,
    )
    resolved_params = {
        "nmi": "1234567890",
    }
    if agg_lfdi is not None:
        resolved_params["aggregator_lfdi"] = agg_lfdi
    if agg_sfdi is not None:
        resolved_params["aggregator_sfdi"] = agg_sfdi
    if pin is not None:
        resolved_params["registration_pin"] = pin

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_register_end_device(active_test_procedure, resolved_params, session)

    # Assert
    async with generate_async_session(pg_base_config) as session:
        sites = (await session.execute(select(Site))).scalars().all()
        assert len(sites) == 1
        site_1 = sites[0]
        assert site_1.lfdi == active_test_procedure.client_lfdi.upper(), "Always store the uppercase lfdi"
        assert site_1.sfdi == active_test_procedure.client_sfdi
        if pin is not None:
            assert site_1.registration_pin == pin


# aggregator_lfdi: 3E4F45AB31EDFE5B67E343E5E4562E3100000000 # Trailing PEN digits set to 0
#         aggregator_sfdi: 16726121139
@pytest.mark.parametrize(
    "pen, agg_lfdi, agg_sfdi, expected_lfdi, expected_sfdi",
    [
        (
            0,
            "3E4F45AB31EDFE5B67E343E5E4562E31XXXXXXXX",
            16726121139,
            "3E4F45AB31EDFE5B67E343E5E4562E3100000000",
            16726121139,
        ),
        (
            0,
            "3e4f45ab31edfe5b67e343e5e4562e31xxxxxxxx",
            16726121139,
            "3E4F45AB31EDFE5B67E343E5E4562E3100000000",
            16726121139,
        ),
        (
            1234,
            "3E4F45AB31EDFE5B67E343E5E4562E31XXXXXXXX",
            123,
            "3E4F45AB31EDFE5B67E343E5E4562E3100001234",
            123,
        ),
        (
            99999999,
            "3E4F45AB31EDFE5B67E343E5E4562E31XXXXXXXX",
            16726121139,
            "3E4F45AB31EDFE5B67E343E5E4562E3199999999",
            16726121139,
        ),
        (
            123,
            None,
            None,
            None,
            None,
        ),
    ],
)
@pytest.mark.anyio
async def test_action_register_aggregator_end_device_agg_cert(
    pg_base_config,
    pen: int,
    agg_lfdi: str | None,
    agg_sfdi: int | None,
    expected_lfdi: str | None,
    expected_sfdi: int | None,
):
    """Checks that an aggregator"""
    agg_id = 1
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        step_status={},
        client_certificate_type=ClientCertificateType.AGGREGATOR,
        pen=pen,
        finished_zip_data=None,
        client_aggregator_id=agg_id,
    )
    resolved_params = {}
    if agg_lfdi is not None:
        resolved_params["aggregator_lfdi"] = agg_lfdi
    if agg_sfdi is not None:
        resolved_params["aggregator_sfdi"] = agg_sfdi

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_register_end_device(active_test_procedure, resolved_params, session)

    # Assert
    async with generate_async_session(pg_base_config) as session:
        sites = (await session.execute(select(Site))).scalars().all()
        assert len(sites) == 1
        site_1 = sites[0]

        if expected_lfdi is None:
            assert site_1.lfdi == active_test_procedure.client_lfdi.upper(), "Always expect uppercase LFDI"
        else:
            assert site_1.lfdi == expected_lfdi.upper(), "Always expect uppercase LFDI"

        if expected_sfdi is None:
            assert site_1.sfdi == active_test_procedure.client_sfdi
        else:
            assert site_1.sfdi == expected_sfdi


@pytest.mark.parametrize(
    "resolved_params, expected",
    [
        ({}, KeyError),
        ({"blah": 123}, KeyError),
        ({"enabled": True}, False),
        ({"enabled": False}, True),
        ({"other": False, "enabled": True}, False),
    ],
)
def test_action_communications_status(resolved_params: dict[str, Any], expected: bool | type[Exception]):
    """NOTE: The expected value is the expected value for comms DISABLED"""
    for initial_comms_disabled_value in [True, False]:
        active_test_procedure = create_testing_runner_state([])
        active_test_procedure.communications_disabled = initial_comms_disabled_value

        if isinstance(expected, type):
            with pytest.raises(expected):
                action_communications_status(active_test_procedure, resolved_params)
            assert active_test_procedure.communications_disabled == initial_comms_disabled_value, "No change on error"
        else:
            action_communications_status(active_test_procedure, resolved_params)
            assert active_test_procedure.communications_disabled == expected


@pytest.mark.parametrize(
    "resolved_params, expected_db_value",
    [
        ({}, KeyError),
        ({"enabled": True}, False),
        ({"enabled": False}, True),
    ],
)
@pytest.mark.anyio
async def test_action_edev_registration_links(
    pg_base_config, envoy_admin_client, resolved_params: dict[str, Any], expected_db_value: bool | type[Exception]
):
    """NOTE: The expected value is the expected value for DISABLE edev registrations"""
    if isinstance(expected_db_value, type):
        with pytest.raises(expected_db_value):
            await action_edev_registration_links(resolved_params, envoy_admin_client)
        assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 0, "No DB update"
    else:
        await action_edev_registration_links(resolved_params, envoy_admin_client)
        assert (
            pg_base_config.execute("select disable_edev_registration from runtime_server_config;").fetchone()[0]
            == expected_db_value
        )
