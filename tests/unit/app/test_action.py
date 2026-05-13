import unittest.mock as mock
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import product
from typing import Any

import pytest
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_test_definitions.client import ACTION_PARAMETER_SCHEMA, Action, Event
from envoy.server.model.archive.tariff import (
    ArchiveTariffComponent,
    ArchiveTariffGeneratedRate,
)
from envoy.server.model.doe import (
    DynamicOperatingEnvelope,
    SiteControlGroup,
    SiteControlGroupDefault,
)
from envoy.server.model.server import RuntimeServerConfig
from envoy.server.model.site import Site
from envoy.server.model.tariff import Tariff, TariffComponent, TariffGeneratedRate
from sqlalchemy import func, select

from cactus_runner.app.action import (
    INT16_MAX,
    UnknownActionError,
    _effective_pow10_multiplier,
    action_cancel_active_controls,
    action_cancel_time_tariff_intervals,
    action_communications_status,
    action_create_der_control,
    action_create_der_program,
    action_create_rate_component,
    action_create_tariff_profile,
    action_create_time_tariff_interval,
    action_delete_rate_component,
    action_edev_registration_links,
    action_enable_steps,
    action_register_end_device,
    action_remove_function_set_assignment,
    action_remove_steps,
    action_set_comms_rate,
    action_set_default_der_control,
    apply_action,
    apply_actions,
)
from cactus_runner.app.envoy_common import get_all_site_control_groups
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
ACTION_TYPE_TO_HANDLER: dict[str, str | None] = {
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
    "create-tariff-profile": "action_create_tariff_profile",
    "create-rate-component": "action_create_rate_component",
    "create-time-tariff-interval": "action_create_time_tariff_interval",
    "cancel-time-tariff-intervals": "action_cancel_time_tariff_intervals",
    "delete-rate-component": "action_delete_rate_component",
    "remove-function-set-assignment": "action_remove_function_set_assignment",
}


def test_ACTION_TYPE_TO_HANDLER_in_sync():
    """Tests that every action defined in ACTION_TYPE_TO_HANDLER has an appropriate entry in ACTION_NAMES_WITH_HANDLER

    Failures in this test indicate that ACTION_NAMES_WITH_HANDLER hasn't been kept up to date"""

    # Make sure that every cactus-test-definition action is found in ACTION_TYPE_TO_HANDLER
    for action_type in ACTION_PARAMETER_SCHEMA.keys():
        assert action_type in ACTION_TYPE_TO_HANDLER, f"The action type {action_type} doesn't have a known handler fn"

    # Make sure we don't have any extra definitions not found in cactus-test-definitions
    for action_type in ACTION_TYPE_TO_HANDLER.keys():
        assert action_type in ACTION_PARAMETER_SCHEMA, (
            f"The action type {action_type} isn't defined in the test definitions (has it been removed/renamed)"
        )

    supported_actions = dict(((k, v) for k, v in ACTION_TYPE_TO_HANDLER.items() if v is not None))
    assert len(set(supported_actions.values())) == len(supported_actions), (
        "At least 1 action type have listed the same action handler. This is likely a bug"
    )


def create_testing_runner_state(listeners: list[Listener]) -> RunnerState:
    return RunnerState(
        generate_class_instance(
            ActiveTestProcedure,
            step_status={listener.step: StepInfo() for listener in listeners},
            finished_zip_path=None,
            listeners=listeners,
        ),
        [],
        [],
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
    assert runner_state.active_test_procedure is not None

    # Act
    await action_enable_steps(runner_state.active_test_procedure, resolved_parameters)

    # Assert
    assert listeners[0].enabled_time is not None
    assert_nowish(listeners[0].enabled_time)
    assert listeners[0].enabled_time.tzinfo, "Need timezone aware datetime"
    assert steps_to_enable == original_steps_to_enable  # Ensure we are not mutating step_to_enable
    for step in steps_to_enable:
        assert runner_state.active_test_procedure.step_status[step].get_step_status() == StepStatus.ACTIVE, (
            "Check we update step_status"
        )


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
                    enabled_time=datetime(2000, 1, 1, tzinfo=UTC),
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
                    enabled_time=datetime(2000, 1, 1, tzinfo=UTC),
                ),
                Listener(
                    step="step2",
                    event=Event(type="", parameters={}),
                    actions=[],
                    enabled_time=datetime(2000, 1, 1, tzinfo=UTC),
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
    assert runner_state.active_test_procedure is not None

    # Act
    await action_remove_steps(runner_state.active_test_procedure, resolved_parameters)

    # Assert
    assert len(listeners) == 0  # all steps removed from list of listeners
    assert steps_to_disable == original_steps_to_disable  # check we are mutating 'steps_to_diable'
    for step in steps_to_disable:
        assert runner_state.active_test_procedure.step_status[step].get_step_status() == StepStatus.RESOLVED, (
            "Check we update step_status"
        )


@pytest.mark.parametrize(
    "action, apply_function_name",
    [
        (Action(type=action_type, parameters={}), handler_fn)
        for action_type, handler_fn in ACTION_TYPE_TO_HANDLER.items()
        if handler_fn is not None
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
            enabled_time=datetime(2000, 1, 1, tzinfo=UTC),
        ),  # no actions for listener
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[Action(type="enable-steps", parameters={})],
            enabled_time=datetime(2000, 1, 1, tzinfo=UTC),
        ),  # 1 action for listener
        Listener(
            step="step",
            event=Event(type="GET-request-received", parameters={"endpoint": "/dcap"}),
            actions=[
                Action(type="enable-steps", parameters={}),
                Action(type="remove-steps", parameters={}),
            ],
            enabled_time=datetime(2000, 1, 1, tzinfo=UTC),
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
async def test_action_set_default_der_control_with_derp_id(pg_base_config, envoy_admin_client, cancelled: bool | None):
    """Success tests"""
    # Arrange
    derp_id = 123
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(SiteControlGroup, seed=101, site_control_group_id=derp_id + 1))
        session.add(generate_class_instance(SiteControlGroup, seed=202, site_control_group_id=derp_id))
        await session.commit()
    resolved_params = {
        "opModImpLimW": 10,
        "opModExpLimW": 11,
        "opModGenLimW": 12,
        "opModLoadLimW": 13,
        "setGradW": 14,
        "derp_id": derp_id,
        "opModStorageTargetW": 15,
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
        result = await session.execute(
            select(SiteControlGroupDefault).where(SiteControlGroupDefault.site_control_group_id == derp_id)
        )
        saved_result = result.scalar_one()
        assert saved_result.import_limit_active_watts == 10
        assert saved_result.export_limit_active_watts == 11
        assert saved_result.generation_limit_active_watts == 12
        assert saved_result.load_limit_active_watts == 13
        assert saved_result.ramp_rate_percent_per_second == 14
        assert saved_result.storage_target_active_watts == 15


@pytest.mark.parametrize(
    "site_control_groups, expected_scg_id",
    [
        ([generate_class_instance(SiteControlGroup, seed=101, site_control_group_id=1, primacy=11)], 1),
        (
            [
                generate_class_instance(SiteControlGroup, seed=101, site_control_group_id=1, primacy=11),
                generate_class_instance(SiteControlGroup, seed=202, site_control_group_id=2, primacy=5),
                generate_class_instance(SiteControlGroup, seed=303, site_control_group_id=3, primacy=22),
            ],
            2,
        ),
    ],
)
@pytest.mark.anyio
async def test_action_set_default_der_control_missing_derp_id(
    pg_base_config, envoy_admin_client, site_control_groups: list[SiteControlGroup], expected_scg_id: int
):
    """Success tests when the derp_id is missing (and the DB has to be consulted for a matching DERProgram)"""
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        if site_control_groups:
            session.add_all(site_control_groups)
        await session.commit()
    resolved_params = {
        "opModImpLimW": 10,
        "opModExpLimW": 11,
        "opModGenLimW": 12,
        "opModLoadLimW": 13,
        "setGradW": 14,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_set_default_der_control(
            session=session, envoy_client=envoy_admin_client, resolved_parameters=resolved_params
        )

    # Assert
    async with generate_async_session(pg_base_config) as session:
        assert (await session.execute(select(func.count()).select_from(SiteControlGroupDefault))).scalar() == 1
        updated_rec = await session.execute(
            select(SiteControlGroupDefault).where(SiteControlGroupDefault.site_control_group_id == expected_scg_id)
        )
        saved_result = updated_rec.scalar_one()
        assert saved_result.import_limit_active_watts == 10
        assert saved_result.export_limit_active_watts == 11
        assert saved_result.generation_limit_active_watts == 12
        assert saved_result.load_limit_active_watts == 13
        assert saved_result.ramp_rate_percent_per_second == 14


@pytest.mark.anyio
async def test_action_set_default_der_control_cancelled(pg_base_config, envoy_admin_client):
    """Success tests when cancelling"""
    # Arrange
    derp_id = 456
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(SiteControlGroup, seed=101, site_control_group_id=derp_id))
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
        result = await session.execute(
            select(SiteControlGroupDefault).where(SiteControlGroupDefault.site_control_group_id == derp_id)
        )
        saved_result = result.scalar_one()
        assert saved_result.import_limit_active_watts is None
        assert saved_result.export_limit_active_watts is None
        assert saved_result.generation_limit_active_watts is None
        assert saved_result.load_limit_active_watts is None
        assert saved_result.ramp_rate_percent_per_second is None


@pytest.mark.parametrize(
    "requested_mult, watt_values, expected_mult",
    [
        # No adjustment needed - values fit at requested multiplier
        (0, [Decimal("1000")], 0),
        (0, [Decimal("32767")], 0),
        (2, [Decimal("3276700")], 2),  # 3276700 / 100 = 32767, fits exactly
        # Adjustment needed - values overflow Int16
        (0, [Decimal("100000")], 1),  # 100000 / 10^1 = 10000, fits
        (2, [Decimal("15000000")], 3),  # 15000000 / 10^3 = 15000, fits
        # None and zero values ignored
        (0, [None, Decimal("100"), None], 0),
        (0, [Decimal("0"), Decimal("100")], 0),
        (2, [None, None], 2),
        (2, [], 2),
        # Negative watt values handled via abs
        (0, [Decimal("-100000")], 1),
        # Max across multiple values
        (0, [Decimal("100"), Decimal("100000")], 1),
    ],
)
def test_effective_pow10_multiplier(requested_mult, watt_values, expected_mult):
    """Verify _effective_pow10_multiplier adjusts the multiplier to keep watt values within Int16 range"""
    result = _effective_pow10_multiplier(requested_mult, watt_values)
    assert result == expected_mult

    # Also verify the returned multiplier actually results in Int16-safe values
    for v in watt_values:
        if v is not None and v != 0:
            assert abs(v) / Decimal(10**result) <= INT16_MAX


@pytest.mark.parametrize("fsa_id", [None, 6812])
@pytest.mark.anyio
async def test_action_create_der_control_no_group(pg_base_config, envoy_admin_client, fsa_id):
    # Arrange
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()
    resolved_params = {
        "start": datetime.now(UTC),
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
        "opModStorageTargetW": 0,
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


@pytest.mark.parametrize("fsa_id, end_device_indexes, tag", product([None, 6812], [None, [0, 1]], [None, "Foo"]))
@pytest.mark.anyio
async def test_action_create_der_program(pg_base_config, envoy_admin_client, fsa_id, end_device_indexes, tag):
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )
    resolved_params = {
        "primacy": 17,
    }
    if fsa_id is not None:
        resolved_params["fsa_id"] = fsa_id
    if end_device_indexes is not None:
        resolved_params["end_device_indexes"] = end_device_indexes
    if tag is not None:
        resolved_params["tag"] = tag

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_program(resolved_params, envoy_admin_client, active_test_procedure, session)

    # Assert
    if tag is not None:
        assert tag in active_test_procedure.resource_annotations.der_program_ids_by_alias
        expected_id = active_test_procedure.resource_annotations.der_program_ids_by_alias[tag]
        assert isinstance(expected_id, int) and expected_id > 0
    else:
        assert len(active_test_procedure.resource_annotations.der_program_ids_by_alias) == 0
        expected_id = None

    if fsa_id is None:
        expected_fsa_id = 1
    else:
        expected_fsa_id = fsa_id
    if end_device_indexes is None:
        display_id_clause = "is null"
    else:
        display_id_clause = "> 0"

    if expected_id is None:
        pk_clause = " and site_control_group_id > 0"
    else:
        pk_clause = f" and site_control_group_id = {expected_id}"

    assert (
        pg_base_config.execute(
            f"select count(*) from site_control_group where primacy = 17 and fsa_id = {expected_fsa_id} and"
            + f" display_id {display_id_clause} {pk_clause};"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("fsa_id", [2134, None])
@pytest.mark.anyio
async def test_action_create_der_control_existing_group(pg_base_config, envoy_admin_client, fsa_id):
    # Arrange
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)
    existing_fsa_id = fsa_id if fsa_id is not None else 21515215
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        session.add(generate_class_instance(SiteControlGroup, primacy=2, fsa_id=existing_fsa_id))
        await session.commit()
    resolved_params = {
        "start": datetime.now(UTC),
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
        "opModStorageTargetW": 0,
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


@pytest.mark.parametrize("tag", [None, "456-ABC"])
@pytest.mark.anyio
async def test_action_create_der_control_existing_group_with_tag(pg_base_config, envoy_admin_client, tag):
    # Arrange
    derp_tag = "abc-DEF-123"
    existing_scg_id = 1251161
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        step_status={},
        finished_zip_path=None,
        resource_annotations=ResourceAnnotations(
            der_program_ids_by_alias={(derp_tag + "foo"): existing_scg_id + 1, derp_tag: existing_scg_id}
        ),
    )
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        session.add(generate_class_instance(SiteControlGroup, primacy=2, site_control_group_id=existing_scg_id))
        await session.commit()
    resolved_params = {
        "start": datetime.now(UTC),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "randomizeStart_seconds": 0,
        "ramp_time_seconds": 0,
        "opModEnergize": 0,
        "opModConnect": 0,
        "opModImpLimW": 0,
        "opModExpLimW": 0,
        "opModGenLimW": 0,
        "opModLoadLimW": 0,
        "opModFixedW": 0,
        "der_program_tag": derp_tag,
        "tag": tag,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Assert
    assert pg_base_config.execute("select count(*) from runtime_server_config;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from site_control_group;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 1

    async with generate_async_session(pg_base_config) as session:
        existing_scg = (await session.execute(select(SiteControlGroup))).scalar_one()
        assert existing_scg.site_control_group_id == existing_scg_id
    assert len(active_test_procedure.resource_annotations.der_program_ids_by_alias) == 2

    if tag is None:
        assert len(active_test_procedure.resource_annotations.der_control_ids_by_alias) == 0
    else:
        assert len(active_test_procedure.resource_annotations.der_control_ids_by_alias) == 1


@pytest.mark.anyio
async def test_action_create_der_control_derp_tag_missing(pg_base_config, envoy_admin_client):
    # Arrange
    derp_tag = "abc-DEF-123"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        session.add(generate_class_instance(SiteControlGroup, primacy=2))
        await session.commit()
    resolved_params = {
        "start": datetime.now(UTC),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "randomizeStart_seconds": 0,
        "ramp_time_seconds": 0,
        "opModEnergize": 0,
        "opModConnect": 0,
        "opModImpLimW": 0,
        "opModExpLimW": 0,
        "opModGenLimW": 0,
        "opModLoadLimW": 0,
        "opModFixedW": 0,
        "der_program_tag": derp_tag,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        with pytest.raises(Exception):  # noqa: B017
            await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Assert
    assert pg_base_config.execute("select count(*) from site_control_group;").fetchone()[0] == 1
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 0

    assert len(active_test_procedure.resource_annotations.der_control_ids_by_alias) == 0
    assert len(active_test_procedure.resource_annotations.der_program_ids_by_alias) == 0


@pytest.mark.parametrize("value_seed", [None, 101, 202])
@pytest.mark.anyio
async def test_action_create_der_control_control_values(pg_base_config, envoy_admin_client, value_seed: int | None):
    """Checks that the various DERControl values are properly set for a few variations"""
    # Arrange
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)
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
        "start": datetime.now(UTC),
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
        "opModStorageTargetW": gen_float(value_seed, 9),
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
        assert doe.storage_target_active_watts == gen_float(value_seed, 9)


@pytest.mark.anyio
async def test_action_create_der_control_with_tag(pg_base_config, envoy_admin_client):
    """Verifies that creating a DER control with a tag properly annotates it in the active test procedure"""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()
    tag = "DERC1"
    resolved_params = {
        "start": datetime.now(UTC),
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
async def test_action_create_der_control_with_tag_that_supersedes(pg_base_config, envoy_admin_client):
    """Verifies that creating a DER control with a tag properly annotates it in the active test procedure (even if
    it's superseding an existing control which has caused us troubles in the past)"""
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, aggregator_id=1)
        session.add(site)
        site_ctrl_grp = generate_class_instance(SiteControlGroup, primacy=2, site_control_group_id=1)
        session.add(site_ctrl_grp)

        existing_creation_time = datetime.now(UTC) - timedelta(seconds=20)

        existing_derc = generate_class_instance(
            DynamicOperatingEnvelope,
            dynamic_operating_envelope_id=None,
            calculation_log_id=None,
            site_control_group=site_ctrl_grp,
            site=site,
            start_time=existing_creation_time,
            end_time=existing_creation_time + timedelta(seconds=3600),
            duration_seconds=3600,
            export_limit_watts=123,
            created_time=existing_creation_time,
            superseded=False,
        )
        session.add(existing_derc)

        await session.flush()

        existing_derc_id = existing_derc.dynamic_operating_envelope_id

        await session.commit()

    existing_derc_tag = "DERC-EXISTING"
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        step_status={},
        finished_zip_path=None,
        resource_annotations=ResourceAnnotations(der_control_ids_by_alias={existing_derc_tag: existing_derc_id}),
    )

    inserted_tag = "DERC-NEW"
    resolved_params = {
        "start": datetime.now(UTC),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "opModExpLimW": 456,
        "tag": inserted_tag,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Verify the tag was added to the active test procedure
    derc_id_by_alias = active_test_procedure.resource_annotations.der_control_ids_by_alias
    assert inserted_tag in derc_id_by_alias
    assert derc_id_by_alias[existing_derc_tag] == existing_derc_id
    assert derc_id_by_alias[inserted_tag] != existing_derc_id

    # Verify the tagged control ID matches the created control
    async with generate_async_session(pg_base_config) as session:
        does = (await session.execute(select(DynamicOperatingEnvelope))).scalars().all()
        assert len(does) == 2

        if does[0].dynamic_operating_envelope_id == existing_derc_id:
            existing = does[0]
            inserted = does[1]
        else:
            existing = does[1]
            inserted = does[0]

        assert existing.superseded
        assert not inserted.superseded

        assert existing.changed_time == inserted.changed_time, "Both should've updated together"

        assert existing.export_limit_watts == 123
        assert inserted.export_limit_watts == 456

        assert derc_id_by_alias[existing_derc_tag] == existing.dynamic_operating_envelope_id
        assert derc_id_by_alias[inserted_tag] == inserted.dynamic_operating_envelope_id


@pytest.mark.anyio
async def test_action_create_der_control_with_tag_and_edev_indexes(pg_base_config, envoy_admin_client):
    """Verifies that creating a DER control with a tag AND end_device_indexes bails out"""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()
    tag = "DERC1"
    resolved_params = {
        "start": datetime.now(UTC),
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
        "end_device_indexes": [0, 1],
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        with pytest.raises(Exception):  # noqa: B017
            await action_create_der_control(resolved_params, session, envoy_admin_client, active_test_procedure)

    # Assert nothing in the DB
    assert pg_base_config.execute("select count(*) from dynamic_operating_envelope;").fetchone()[0] == 0

    # Verify the tag was NOT added to the active test procedure
    assert tag not in active_test_procedure.resource_annotations.der_control_ids_by_alias


@pytest.mark.anyio
async def test_action_create_der_control_with_end_device_indexes(pg_base_config, envoy_admin_client):
    """Verifies that creating a DER control with end_device_indexes creates multiple controls with the same display
    id for the appropriate site_ids"""
    # Arrange
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, seed=101, site_id=11, aggregator_id=1))
        session.add(generate_class_instance(Site, seed=202, site_id=22, aggregator_id=1))
        session.add(generate_class_instance(Site, seed=303, site_id=44, aggregator_id=1))
        session.add(generate_class_instance(Site, seed=404, site_id=33, aggregator_id=1))
        await session.commit()

    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)

    resolved_params_1 = {
        "start": datetime.now(UTC),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "opModExpLimW": 10,
        "end_device_indexes": [0, 2],  # These will correspond to site_id 11 and 33
    }
    resolved_params_2 = {
        "start": datetime.now(UTC),
        "duration_seconds": 300,
        "pow_10_multipliers": -1,
        "primacy": 2,
        "opModExpLimW": 20,
        "end_device_indexes": [0, 2],  # These will correspond to site_id 11 and 33
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        # We do this twice so we can ensure display_id is unique PER call
        await action_create_der_control(resolved_params_1, session, envoy_admin_client, active_test_procedure)
        await action_create_der_control(resolved_params_2, session, envoy_admin_client, active_test_procedure)

    # Verify the tagged control ID matches the created control
    async with generate_async_session(pg_base_config) as session:
        all_does = (await session.execute(select(DynamicOperatingEnvelope))).scalars().all()
        assert len(all_does) == 4
        assert len(set([d.export_limit_watts for d in all_does])) == 2, "Two sets of opModExpLimW"

        does_by_display_id: dict[int, list[DynamicOperatingEnvelope]] = {}
        for d in all_does:
            assert d.display_id
            existing = does_by_display_id.get(d.display_id, None)
            if existing is None:
                does_by_display_id[d.display_id] = [d]
            else:
                existing.append(d)

        for grouped_does in does_by_display_id.values():
            assert len(grouped_does) == 2
            assert len(set([d.export_limit_watts for d in grouped_does])) == 1
            assert set([d.site_id for d in all_does]) == {11, 33}


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
                start_time=datetime.now(UTC),
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
        "tp_list_poll_seconds": 17,
        "tti_list_poll_seconds": 18,
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
        assert runtime_config.tp_pollrate_seconds == 17
        assert runtime_config.tti_pollrate_seconds == 18

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
        finished_zip_path=None,
        client_aggregator_id=agg_id,
    )
    resolved_params: dict = {
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
        finished_zip_path=None,
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


@pytest.mark.asyncio
async def test_action_reregister_existing_device(pg_base_config):
    """Test that re-registering an existing end-device doesn't raise an error or create a duplicate site."""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        step_status={},
        client_certificate_type=ClientCertificateType.DEVICE,
        finished_zip_path=None,
        client_aggregator_id=0,
    )
    resolved_params = {"nmi": "1234567890"}

    # Act - register the device twice
    async with generate_async_session(pg_base_config) as session:
        await action_register_end_device(active_test_procedure, resolved_params, session)

    async with generate_async_session(pg_base_config) as session:
        # This should not raise an error
        await action_register_end_device(active_test_procedure, resolved_params, session)

    # Assert - only one site should exist
    async with generate_async_session(pg_base_config) as session:
        sites = (await session.execute(select(Site))).scalars().all()
        assert len(sites) == 1
        assert sites[0].lfdi == active_test_procedure.client_lfdi.upper()


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
        active_test_procedure = create_testing_runner_state([]).active_test_procedure
        assert active_test_procedure is not None
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


@pytest.mark.parametrize(
    "scg_fsa_ids, fsa_id, expected_update_indexes",
    [
        ([], 1, []),
        ([1, 2, 3], 1, [0]),
        ([1, 2, 3], 2, [1]),
        ([1, None, 3, 1, 2, 11, None], 1, [0, 3]),
        ([1, 1], 1, [0, 1]),
        ([1, 1], 2, []),
    ],
)
@pytest.mark.anyio
async def test_action_remove_function_set_assignment(
    pg_base_config, envoy_admin_client, scg_fsa_ids: list[int | None], fsa_id: int, expected_update_indexes: list[int]
):
    # Arrange
    primacies: list[int] = []
    descriptions: list[str] = []
    async with generate_async_session(pg_base_config) as session:
        for idx, scg_fsa_id in enumerate(scg_fsa_ids):
            new_instance = generate_class_instance(
                SiteControlGroup, seed=idx * 101, fsa_id=scg_fsa_id, site_control_group_id=(idx + 1)
            )
            primacies.append(new_instance.primacy)
            descriptions.append(new_instance.description)
            session.add(new_instance)
        await session.commit()

    # Act
    async with generate_async_session(pg_base_config) as session:
        await action_remove_function_set_assignment({"fsa_id": fsa_id}, session, envoy_admin_client)

    # Assert
    async with generate_async_session(pg_base_config) as session:
        scgs = await get_all_site_control_groups(session)
        assert len(scgs) == len(scg_fsa_ids)
        for idx, original_fsa_id, original_primacy, original_description, site_control_group in zip(
            range(len(scg_fsa_ids)), scg_fsa_ids, primacies, descriptions, scgs, strict=True
        ):
            assert site_control_group.primacy == original_primacy
            assert site_control_group.description == original_description
            if idx in expected_update_indexes:
                assert site_control_group.fsa_id is None
                assert_nowish(site_control_group.changed_time)
            else:
                assert site_control_group.fsa_id == original_fsa_id


@pytest.mark.anyio
async def test_action_create_tariff_profile(pg_base_config, envoy_admin_client):
    """Verifies that creating a tariff with a tag properly annotates it in the active test procedure"""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )
    tag = "TP-1"
    resolved_params = {
        "primacy": 1,
        "fsa_id": 2,
        "price_pow_10_multiplier": -1,
        "tag": tag,
    }

    # Act
    await action_create_tariff_profile(resolved_params, envoy_admin_client, active_test_procedure)

    # Assert
    assert pg_base_config.execute("select count(*) from tariff;").fetchone()[0] == 1

    # Verify the tag was added to the active test procedure
    assert tag in active_test_procedure.resource_annotations.tariff_profile_ids_by_alias

    # Verify the tagged tariff ID matches the created control
    async with generate_async_session(pg_base_config) as session:
        tariff = (await session.execute(select(Tariff).limit(1))).scalar_one()
        tagged_tariff_id = active_test_procedure.resource_annotations.tariff_profile_ids_by_alias[tag]
        assert tagged_tariff_id == tariff.tariff_id


@pytest.mark.parametrize(
    "tp_tags, tariff_profile_tag, expect_success",
    [
        ([None], None, True),  # Untagged single parent can reference fine
        (["TPTag"], None, True),  # Tagged single parent can reference fine
        ([None, "TPTag", "TPTag2"], "TPTag", True),  # Multiple parents is OK with tag
        ([None, "TPTag", "TPTag2"], "TPTag2", True),  # Multiple parents is OK with tag
        ([None, "TPTag", "TPTag2"], "TPTagDNE", False),  # Tag reference DNE
        ([None, "TPTag", "TPTag2"], None, False),  # Multiple parents REQUIRES a tag
        ([], None, False),  # No parents to use
        ([], "DNE", False),  # No parents to use
    ],
)
@pytest.mark.anyio
async def test_action_create_rate_component(
    pg_base_config, envoy_admin_client, tp_tags: list[str | None], tariff_profile_tag: str | None, expect_success: bool
):
    """Verifies that creating a rate component in a context that may/may not have tariff profiles behaves as expected"""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )

    # For each parent TariffProfile - create a Tariff to reference
    for idx, tp_tag in enumerate(tp_tags):
        await action_create_tariff_profile(
            {
                "primacy": idx,
                "fsa_id": idx,
                "price_pow_10_multiplier": 0,
                "tag": tp_tag,
            },
            envoy_admin_client,
            active_test_procedure,
        )

    tag = "RC-1"
    resolved_params = {
        "tariff_profile_tag": tariff_profile_tag,
        "role_flags": 1,
        "commodity": 2,
        "data_qualifier": 8,
        "flow_direction": 19,
        "kind": 12,
        "phase": 32,
        "power_of_ten_multiplier": -2,
        "uom": 134,
        "tag": tag,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        if expect_success:
            await action_create_rate_component(resolved_params, envoy_admin_client, active_test_procedure, session)
            expected_tariff_component_count = 1
        else:
            with pytest.raises(Exception):
                await action_create_rate_component(resolved_params, envoy_admin_client, active_test_procedure, session)
            expected_tariff_component_count = 0

    # Assert
    assert (
        pg_base_config.execute("select count(*) from tariff_component;").fetchone()[0]
        == expected_tariff_component_count
    )

    # Verify the tag was added to the active test procedure
    if expect_success:
        assert tag in active_test_procedure.resource_annotations.rate_component_ids_by_alias

        # Verify the tagged tariff ID matches the created tariff component
        async with generate_async_session(pg_base_config) as session:
            tc = (await session.execute(select(TariffComponent).limit(1))).scalar_one()
            tagged_tc_id = active_test_procedure.resource_annotations.rate_component_ids_by_alias[tag]
            assert tagged_tc_id == tc.tariff_component_id

            # Check our TariffComponent is under the correct Tariff
            #
            # This assert only makes sense when we are selecting a specific Tariff - the alternative is that there is
            # only a single Tariff to choose from in which case the test is covered by FK restricions in the DB
            if tariff_profile_tag is not None:
                tagged_tariff_id = active_test_procedure.resource_annotations.tariff_profile_ids_by_alias[
                    tariff_profile_tag
                ]
                assert tagged_tariff_id == tc.tariff_id

            # Sanity check some fields match what we suplied
            assert tc.commodity == 2
            assert tc.power_of_ten_multiplier == -2
            assert tc.flow_direction == 19
            assert tc.role_flags == 1
            assert tc.uom == 134
            assert tc.kind == 12
    else:
        assert tag not in active_test_procedure.resource_annotations.rate_component_ids_by_alias


@pytest.mark.parametrize(
    "rc_tags, rate_component_tag, expect_success",
    [
        ([None], None, True),  # Untagged single parent can reference fine
        (["RCTag"], None, True),  # Tagged single parent can reference fine
        ([None, "RCTag", "RCTag2"], "RCTag", True),  # Multiple parents is OK with tag
        ([None, "RCTag", "RCTag2"], "RCTag2", True),  # Multiple parents is OK with tag
        ([None, "RCTag", "RCTag2"], "RCTagDNE", False),  # Tag reference DNE
        ([None, "RCTag", "RCTag2"], None, False),  # Multiple parents REQUIRES a tag
        ([], None, False),  # No parents to use
        ([], "DNE", False),  # No parents to use
    ],
)
@pytest.mark.anyio
async def test_action_create_time_tariff_interval(
    pg_base_config, envoy_admin_client, rc_tags: list[str | None], rate_component_tag: str | None, expect_success: bool
):
    """Verifies that creating a time tariff interval in a context that may/may not have RateComponents behaves as
    expected"""

    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )

    # Create an EndDevice
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()

    # Create a top level TariffProfile
    await action_create_tariff_profile(
        {"primacy": 0, "fsa_id": 1, "price_pow_10_multiplier": 1},
        envoy_admin_client,
        active_test_procedure,
    )

    # Create each parent RateComponent
    async with generate_async_session(pg_base_config) as session:
        for idx, rc_tag in enumerate(rc_tags):
            await action_create_rate_component(
                {
                    "role_flags": 1,
                    "commodity": 2,
                    "data_qualifier": 8,
                    "flow_direction": 19,
                    "kind": 12,
                    "phase": 32,
                    "power_of_ten_multiplier": idx,
                    "uom": 134,
                    "tag": rc_tag,
                },
                envoy_admin_client,
                active_test_procedure,
                session,
            )

    tag = "TTI-1"
    resolved_params = {
        "rate_component_tag": rate_component_tag,
        "start": datetime(2023, 4, 5, tzinfo=UTC),
        "duration_seconds": 123,
        "price_pow10_encoded_block0": 1,
        "price_pow10_encoded_block1": 2,
        "price_start_pow10_block1": 3,
        "tag": tag,
    }

    # Act
    async with generate_async_session(pg_base_config) as session:
        if expect_success:
            await action_create_time_tariff_interval(
                resolved_params, envoy_admin_client, active_test_procedure, session
            )
            expected_tti_count = 1
        else:
            with pytest.raises(Exception):
                await action_create_time_tariff_interval(
                    resolved_params, envoy_admin_client, active_test_procedure, session
                )
            expected_tti_count = 0

    # Assert
    assert pg_base_config.execute("select count(*) from tariff_generated_rate;").fetchone()[0] == expected_tti_count

    # Verify the tag was added to the active test procedure
    if expect_success:
        assert tag in active_test_procedure.resource_annotations.time_tariff_interval_ids_by_alias

        # Verify the tagged rate ID matches the created rate
        async with generate_async_session(pg_base_config) as session:
            rate = (await session.execute(select(TariffGeneratedRate).limit(1))).scalar_one()
            tagged_rate_id = active_test_procedure.resource_annotations.time_tariff_interval_ids_by_alias[tag]
            assert tagged_rate_id == rate.tariff_generated_rate_id

            # Check our TariffGenerateRate is under the correct TariffComponent
            #
            # This assert only makes sense when we are selecting a specific TariffComponent - the alternative is that
            # there is only a single Tariff to choose from in which case the test is covered by FK restricions in the DB
            if rate_component_tag is not None:
                tagged_tc_id = active_test_procedure.resource_annotations.rate_component_ids_by_alias[
                    rate_component_tag
                ]
                assert tagged_tc_id == rate.tariff_component_id

            # Sanity check some fields match what we suplied
            assert rate.start_time == datetime(2023, 4, 5, tzinfo=UTC)
            assert rate.duration_seconds == 123
            assert rate.price_pow10_encoded == 1
            assert rate.price_pow10_encoded_block_1 == 2
            assert rate.block_1_start_pow10_encoded == 3
    else:
        assert tag not in active_test_procedure.resource_annotations.time_tariff_interval_ids_by_alias


@pytest.mark.parametrize(
    "tti_tags, cancel_tag, expected_cancel_tti_indexes",
    [
        ([], None, []),
        ([], "TTITagDNE", None),
        ([None, "TTITag1", "TTITag2", None], None, [0, 1, 2, 3]),
        ([None, "TTITag1", "TTITag2", None], "TTITag1", [1]),
        ([None, "TTITag1", "TTITag2", None], "TTITag2", [2]),
        ([None, "TTITag1", "TTITag2", None], "TTITagDNE", None),
    ],
)
@pytest.mark.anyio
async def test_action_cancel_time_tariff_intervals(
    pg_base_config, envoy_admin_client, tti_tags, cancel_tag, expected_cancel_tti_indexes
):
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )

    # Create a top level TariffProfile
    await action_create_tariff_profile(
        {"primacy": 0, "fsa_id": 1, "price_pow_10_multiplier": 1}, envoy_admin_client, active_test_procedure
    )

    # Create an EndDevice, RateComponent and the TTIs
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()

        await action_create_rate_component(
            {
                "role_flags": 1,
                "commodity": 2,
                "data_qualifier": 8,
                "flow_direction": 19,
                "kind": 12,
                "phase": 32,
                "power_of_ten_multiplier": 0,
                "uom": 134,
            },
            envoy_admin_client,
            active_test_procedure,
            session,
        )

        for idx, tag in enumerate(tti_tags):
            await action_create_time_tariff_interval(
                {
                    "start": datetime(2023, 4, 5, tzinfo=UTC) + timedelta(hours=idx),
                    "duration_seconds": 123,
                    "price_pow10_encoded_block0": idx,
                    "price_pow10_encoded_block1": 2,
                    "price_start_pow10_block1": 3,
                    "tag": tag,
                },
                envoy_admin_client,
                active_test_procedure,
                session,
            )

        # Map our created tti_id_by_index - we use idx as price so we can track what rates came from where
        rates = (await session.execute(select(TariffGeneratedRate))).scalars().all()
        tti_id_by_index = dict([(r.price_pow10_encoded, r.tariff_generated_rate_id) for r in rates])

    # Act
    async with generate_async_session(pg_base_config) as session:
        if expected_cancel_tti_indexes is None:
            expected_tti_count = len(tti_tags)
            expected_tti_archive_count = 0
            with pytest.raises(Exception):
                await action_cancel_time_tariff_intervals(
                    {"tag": cancel_tag}, envoy_admin_client, active_test_procedure, session
                )
        else:
            expected_tti_count = len(tti_tags) - len(expected_cancel_tti_indexes)
            expected_tti_archive_count = len(expected_cancel_tti_indexes)
            await action_cancel_time_tariff_intervals(
                {"tag": cancel_tag}, envoy_admin_client, active_test_procedure, session
            )

    # Assert DB counts
    assert pg_base_config.execute("select count(*) from tariff_generated_rate;").fetchone()[0] == expected_tti_count
    assert (
        pg_base_config.execute("select count(*) from archive_tariff_generated_rate;").fetchone()[0]
        == expected_tti_archive_count
    )
    assert (
        pg_base_config.execute(
            "select count(*) from archive_tariff_generated_rate where deleted_time is not NULL;"
        ).fetchone()[0]
        == expected_tti_archive_count
    )

    # Assert the deleted records matched our our expectations
    remaining_rate_ids = (await session.execute(select(TariffGeneratedRate.tariff_generated_rate_id))).scalars().all()
    deleted_rate_ids = (
        (await session.execute(select(ArchiveTariffGeneratedRate.tariff_generated_rate_id))).scalars().all()
    )
    for idx in range(len(tti_tags)):
        if expected_cancel_tti_indexes is not None and idx in expected_cancel_tti_indexes:
            assert tti_id_by_index[idx] in deleted_rate_ids
            assert tti_id_by_index[idx] not in remaining_rate_ids
        else:
            assert tti_id_by_index[idx] not in deleted_rate_ids
            assert tti_id_by_index[idx] in remaining_rate_ids


@pytest.mark.parametrize(
    "rate_component_tags, delete_tag, expected_delete_index",
    [
        ([], "RCTagDNE", None),
        ([None, "RCTag11", "RCTag2", None], "TTITagDNE", None),
        ([None, "RCTag11", "RCTag2", None], "RCTag11", 1),
        ([None, "RCTag11", "RCTag2", None], "RCTag2", 2),
    ],
)
@pytest.mark.anyio
async def test_action_delete_rate_component(
    pg_base_config, envoy_admin_client, rate_component_tags, delete_tag, expected_delete_index
):
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, resource_annotations=ResourceAnnotations()
    )

    # Create a top level TariffProfile
    await action_create_tariff_profile(
        {"primacy": 0, "fsa_id": 1, "price_pow_10_multiplier": 1}, envoy_admin_client, active_test_procedure
    )

    # Create an EndDevice, RateComponents (with rates if appropriate)
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_class_instance(Site, aggregator_id=1))
        await session.commit()

        for idx, tag in enumerate(rate_component_tags):
            await action_create_rate_component(
                {
                    "role_flags": 1,
                    "commodity": 2,
                    "data_qualifier": 8,
                    "flow_direction": 19,
                    "kind": 12,
                    "phase": 32,
                    "power_of_ten_multiplier": idx,  # Used for tracking this RateComponent in the DB
                    "uom": 134,
                    "tag": tag,
                },
                envoy_admin_client,
                active_test_procedure,
                session,
            )

            if tag is not None:
                await action_create_time_tariff_interval(
                    {
                        "rate_component_tag": tag,
                        "start": datetime(2023, 4, 5, tzinfo=UTC) + timedelta(hours=idx),
                        "duration_seconds": 123,
                        "price_pow10_encoded_block0": idx,
                        "price_pow10_encoded_block1": 2,
                        "price_start_pow10_block1": 3,
                    },
                    envoy_admin_client,
                    active_test_procedure,
                    session,
                )

        # Map our created rc_id_by_index - we use idx as pow10 mult so we can track what rates came from where
        tcs = (await session.execute(select(TariffComponent))).scalars().all()
        tc_id_by_index = dict([(r.power_of_ten_multiplier, r.tariff_component_id) for r in tcs])

    # Act
    if expected_delete_index is None:
        expected_tc_count = len(rate_component_tags)
        expected_tc_archive_count = 0
        expected_tti_archive_count = 0
        with pytest.raises(Exception):
            await action_delete_rate_component({"tag": delete_tag}, envoy_admin_client, active_test_procedure)
    else:
        expected_tc_count = len(rate_component_tags) - 1
        expected_tc_archive_count = 1
        expected_tti_archive_count = 1
        await action_delete_rate_component({"tag": delete_tag}, envoy_admin_client, active_test_procedure)

    # Assert DB counts
    assert pg_base_config.execute("select count(*) from tariff_component;").fetchone()[0] == expected_tc_count
    assert (
        pg_base_config.execute("select count(*) from archive_tariff_component;").fetchone()[0]
        == expected_tc_archive_count
    )
    assert (
        pg_base_config.execute(
            "select count(*) from archive_tariff_component where deleted_time is not NULL;"
        ).fetchone()[0]
        == expected_tc_archive_count
    )
    # The underlying TTI's should be archived too (this is a sanity check - envoy tests do this in more detail)
    assert (
        pg_base_config.execute("select count(*) from archive_tariff_generated_rate;").fetchone()[0]
        == expected_tti_archive_count
    )

    # Assert the deleted record matched our our expectations
    remaining_tc_ids = (await session.execute(select(TariffComponent.tariff_component_id))).scalars().all()
    deleted_tc_ids = (await session.execute(select(ArchiveTariffComponent.tariff_component_id))).scalars().all()
    for idx in range(len(rate_component_tags)):
        if idx == expected_delete_index:
            assert tc_id_by_index[idx] in deleted_tc_ids
            assert tc_id_by_index[idx] not in remaining_tc_ids
        else:
            assert tc_id_by_index[idx] not in deleted_tc_ids
            assert tc_id_by_index[idx] in remaining_tc_ids
