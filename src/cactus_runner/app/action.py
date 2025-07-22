import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from cactus_test_definitions import Action
from envoy.server.model.site import Site
from envoy_schema.admin.schema.config import (
    ControlDefaultRequest,
    RuntimeServerConfigRequest,
    UpdateDefaultValue,
)
from envoy_schema.admin.schema.site import SiteUpdateRequest
from envoy_schema.admin.schema.site_control import (
    SiteControlGroupRequest,
    SiteControlRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.app.envoy_common import get_active_site
from cactus_runner.app.finalize import finish_active_test
from cactus_runner.app.variable_resolver import (
    resolve_variable_expressions_from_parameters,
)
from cactus_runner.models import (
    ActiveTestProcedure,
    Listener,
    RunnerState,
    StepStatus,
)

logger = logging.getLogger(__name__)


class UnknownActionError(Exception):
    """Unknown Cactus Runner Action"""


class FailedActionError(Exception):
    """Error raised when an action failed to execute"""


async def action_enable_steps(
    active_test_procedure: ActiveTestProcedure,
    resolved_parameters: dict[str, Any],
):
    """Applies the enable-steps action to the active test procedures.

    Each listener has a single test procedure step associated with it. A list of step names to enable is therefore
    sufficient to identify the corresponding steps which are the actual objects that get disabled.

    Step names are defined by the test procedures. They are strings of the form "ALL-01-001", which is the first step
    "001" in the "ALL-01" test procedure.

    In addition to enabling steps, this function also records the start time for (newly enabled) steps with
    wait events.

    Args:
        session: DB session for accessing the envoy database
        active_test_procedure: The currently active test procedure
        resolved_parameters: The fully resolved (expressions replaced with their values) set of action parameters
    """
    steps_to_enable: list[str] = resolved_parameters["steps"]
    for listener in active_test_procedure.listeners:
        if listener.step in steps_to_enable:
            logger.info(f"ACTION enable-steps: Enabling step {listener.step}")
            listener.enabled_time = datetime.now(tz=timezone.utc)


async def action_remove_steps(
    active_test_procedure: ActiveTestProcedure,
    resolved_parameters: dict[str, Any],
):
    """Applies the remove-steps action to the active test procedure.

    Each listener has a single test procedure step associated with it. A list of step names to disable is therefore
    sufficient to identify the corresponding steps which are the actual objects that get disabled.

    Step names are defined by the test procedures. They are strings of the form "ALL-01-001", which is the first step
    "001" in the "ALL-01" test procedure.

    Args:
        session: DB session for accessing the envoy database
        active_test_procedure: The currently active test procedure
        resolved_parameters: The fully resolved (expressions replaced with their values) set of action parameters
    """
    steps_to_remove: list[str] = resolved_parameters["steps"]

    listeners_to_remove: list[Listener] = []
    for listener in active_test_procedure.listeners:
        if listener.step in steps_to_remove:
            listeners_to_remove.append(listener)

    for listener in listeners_to_remove:
        logger.info(f"ACTION remove-steps: Removing listener: {listener}")
        active_test_procedure.listeners.remove(listener)  # mutate the original listeners list
        active_test_procedure.step_status[listener.step] = StepStatus.RESOLVED


async def action_finish_test(runner_state: RunnerState, session: AsyncSession):
    await finish_active_test(runner_state, session)


async def action_set_default_der_control(
    resolved_parameters: dict[str, Any], session: AsyncSession, envoy_client: EnvoyAdminClient
):
    # We need to know the "active" site - we are interpreting that as the LAST site created/modified by the client
    active_site = await get_active_site(session)
    if active_site is None:
        raise FailedActionError("Unable to identify an active testing EndDevice / site.")

    import_limit_watts = resolved_parameters.get("opModImpLimW", None)
    export_limit_watts = resolved_parameters.get("opModExpLimW", None)
    gen_limit_watts = resolved_parameters.get("opModGenLimW", None)
    load_limit_watts = resolved_parameters.get("opModLoadLimW", None)
    setGradW = resolved_parameters.get("setGradW", None)
    cancelled = resolved_parameters.get("cancelled", False)

    default_val: UpdateDefaultValue | None = UpdateDefaultValue(value=None) if cancelled else None

    await envoy_client.post_site_control_default(
        active_site.site_id,
        ControlDefaultRequest(
            import_limit_watts=(
                UpdateDefaultValue(value=import_limit_watts) if import_limit_watts is not None else default_val
            ),
            export_limit_watts=(
                UpdateDefaultValue(value=export_limit_watts) if export_limit_watts is not None else default_val
            ),
            generation_limit_watts=(
                UpdateDefaultValue(value=gen_limit_watts) if gen_limit_watts is not None else default_val
            ),
            load_limit_watts=(
                UpdateDefaultValue(value=load_limit_watts) if load_limit_watts is not None else default_val
            ),
            ramp_rate_percent_per_second=UpdateDefaultValue(value=setGradW) if setGradW is not None else default_val,
        ),
    )


async def action_create_der_program(resolved_parameters: dict[str, Any], envoy_client: EnvoyAdminClient):
    primacy: int = int(resolved_parameters["primacy"])  # mandatory param
    fsa_id: int = int(resolved_parameters.get("fsa_id", 1))

    await envoy_client.post_site_control_group(
        SiteControlGroupRequest(description=f"Primacy {primacy}", primacy=primacy, fsa_id=fsa_id)
    )


async def action_create_der_control(
    resolved_parameters: dict[str, Any], session: AsyncSession, envoy_client: EnvoyAdminClient
):
    # We need to know the "active" site - we are interpreting that as the LAST site created/modified by the client
    active_site = await get_active_site(session)
    if active_site is None:
        raise Exception("No active EndDevice could be resolved. Has an EndDevice been registered?")

    start_time: datetime = resolved_parameters["start"]
    duration_seconds: int = resolved_parameters["duration_seconds"]

    # This is handled by updating the system config - we can't set pow10 mult on individual controls
    pow_10mult: int | None = resolved_parameters.get("pow_10_multipliers", None)
    if pow_10mult is not None:
        await envoy_client.update_runtime_config(RuntimeServerConfigRequest(site_control_pow10_encoding=pow_10mult))

    # For primacy/fsa_id - we need to find the site_control_group with the specified values (creating one if required)
    primacy: int = resolved_parameters.get("primacy", 0)
    fsa_id: int | None = resolved_parameters.get("fsa_id", None)
    site_control_group_id: int | None = None
    control_groups_response = await envoy_client.get_all_site_control_groups()
    if control_groups_response.site_control_groups:
        for g in control_groups_response.site_control_groups:
            if g.primacy == primacy and (fsa_id is None or fsa_id == g.fsa_id):
                site_control_group_id = g.site_control_group_id
                break

    # Create our site control group if we don't have an existing one
    if site_control_group_id is None:
        site_control_group_id = await envoy_client.post_site_control_group(
            SiteControlGroupRequest(
                description=f"Primacy {primacy}", primacy=primacy, fsa_id=fsa_id if fsa_id is not None else 1
            )
        )

    randomize_seconds: int | None = resolved_parameters.get("randomizeStart_seconds", None)
    energize: bool | None = resolved_parameters.get("opModEnergize", None)
    connect: bool | None = resolved_parameters.get("opModConnect", None)
    import_limit_watts: Decimal | None = resolved_parameters.get("opModImpLimW", None)
    export_limit_watts: Decimal | None = resolved_parameters.get("opModExpLimW", None)
    gen_limit_watts: Decimal | None = resolved_parameters.get("opModGenLimW", None)
    load_limit_watts: Decimal | None = resolved_parameters.get("opModLoadLimW", None)
    set_point_percent: Decimal | None = resolved_parameters.get("opModFixedW", None)

    await envoy_client.create_site_controls(
        site_control_group_id,
        [
            SiteControlRequest(
                calculation_log_id=None,
                site_id=active_site.site_id,
                duration_seconds=duration_seconds,
                start_time=start_time,
                randomize_start_seconds=randomize_seconds,
                set_energized=energize,
                set_connect=connect,
                import_limit_watts=import_limit_watts,
                export_limit_watts=export_limit_watts,
                generation_limit_watts=gen_limit_watts,
                load_limit_watts=load_limit_watts,
                set_point_percentage=set_point_percent,
            )
        ],
    )


async def action_cancel_active_controls(envoy_client: EnvoyAdminClient):
    control_groups_response = await envoy_client.get_all_site_control_groups()
    if control_groups_response.site_control_groups:
        for g in control_groups_response.site_control_groups:
            await envoy_client.delete_site_controls_in_range(
                g.site_control_group_id,
                datetime(2000, 1, 1, tzinfo=timezone.utc),
                datetime(
                    2100, 1, 1, tzinfo=timezone.utc
                ),  # If this is still in use in 2100... I hope you guys sorted out that climate change thing.
                # Sorry, some of us were trying. Sincerely people in 2025
            )


async def action_set_comms_rate(
    resolved_parameters: dict[str, Any], session: AsyncSession, envoy_client: EnvoyAdminClient
):
    dcap_poll_seconds: int | None = resolved_parameters.get("dcap_poll_seconds", None)
    edev_list_poll_seconds: int | None = resolved_parameters.get("edev_list_poll_seconds", None)
    fsa_list_poll_seconds: int | None = resolved_parameters.get("fsa_list_poll_seconds", None)
    derp_list_poll_seconds: int | None = resolved_parameters.get("derp_list_poll_seconds", None)
    der_list_poll_seconds: int | None = resolved_parameters.get("der_list_poll_seconds", None)
    mup_post_seconds: int | None = resolved_parameters.get("mup_post_seconds", None)
    edev_post_seconds: int | None = resolved_parameters.get("edev_post_seconds", None)

    # If we have any of the server config values set - send that request
    if any(
        [
            dcap_poll_seconds,
            edev_list_poll_seconds,
            der_list_poll_seconds,
            derp_list_poll_seconds,
            fsa_list_poll_seconds,
            mup_post_seconds,
        ]
    ):
        await envoy_client.update_runtime_config(
            RuntimeServerConfigRequest(
                dcap_pollrate_seconds=dcap_poll_seconds,
                edevl_pollrate_seconds=edev_list_poll_seconds,
                derl_pollrate_seconds=der_list_poll_seconds,
                derpl_pollrate_seconds=derp_list_poll_seconds,
                fsal_pollrate_seconds=fsa_list_poll_seconds,
                mup_postrate_seconds=mup_post_seconds,
            )
        )

    # If we are updating the active EndDevice postRate - send that request
    if edev_post_seconds is not None:
        active_site = await get_active_site(session)
        if active_site is None:
            raise Exception("No active EndDevice could be resolved. Has an EndDevice been registered?")

        await envoy_client.update_single_site(
            active_site.site_id,
            SiteUpdateRequest(nmi=None, timezone_id=None, device_category=None, post_rate_seconds=edev_post_seconds),
        )


async def action_register_end_device(
    active_test_procedure: ActiveTestProcedure, resolved_parameters: dict[str, Any], session: AsyncSession
):

    # This is only really used for out of band registration tests - it just needs to work "once"
    nmi: str | None = resolved_parameters.get("nmi", None)
    registration_pin: int | None = resolved_parameters.get("registration_pin", None)
    now = datetime.now(tz=timezone.utc)
    session.add(
        Site(
            nmi=nmi,
            aggregator_id=active_test_procedure.client_aggregator_id,
            timezone_id="Australia/Brisbane",
            created_time=now,
            changed_time=now,
            lfdi=active_test_procedure.client_lfdi,
            sfdi=active_test_procedure.client_sfdi,
            device_category=0,
            registration_pin=registration_pin if registration_pin is not None else 1,
        )
    )
    await session.commit()


def action_communications_status(active_test_procedure: ActiveTestProcedure, resolved_parameters: dict[str, Any]):
    comms_enabled: bool = resolved_parameters["enabled"]
    active_test_procedure.communications_disabled = not comms_enabled


async def action_edev_registration_links(resolved_parameters: dict[str, Any], envoy_client: EnvoyAdminClient):
    """Implements edev-registration-links action"""
    links_enabled: bool = resolved_parameters["enabled"]

    await envoy_client.update_runtime_config(RuntimeServerConfigRequest(disable_edev_registration=not links_enabled))


async def apply_action(
    action: Action, runner_state: RunnerState, session: AsyncSession, envoy_client: EnvoyAdminClient
):
    """Applies the action to the active test procedure.

    Actions describe operations such as activate or disabling steps.

    Args:
        action (Action): The Action to apply to the active test procedure.
        runner_state (RunnerState): The current state of the runner. If not active_test_procedure then this exits early.

    Raises:
        UnknownActionError: Raised if this function has no implementation for the provided `action.type`.
    """
    active_test_procedure = runner_state.active_test_procedure
    if not active_test_procedure:
        return

    resolved_parameters = await resolve_variable_expressions_from_parameters(session, action.parameters)

    logger.info(f"Executing action {action} with parameters {resolved_parameters}")
    try:
        match action.type:
            case "enable-steps":
                await action_enable_steps(active_test_procedure, resolved_parameters)
                return
            case "remove-steps":
                await action_remove_steps(active_test_procedure, resolved_parameters)
                return
            case "finish-test":
                await action_finish_test(runner_state, session)
                return
            case "set-default-der-control":
                await action_set_default_der_control(resolved_parameters, session, envoy_client)
                return
            case "create-der-control":
                await action_create_der_control(resolved_parameters, session, envoy_client)
                return
            case "create-der-program":
                await action_create_der_program(resolved_parameters, envoy_client)
                return
            case "cancel-active-der-controls":
                await action_cancel_active_controls(envoy_client)
                return
            case "set-comms-rate":
                await action_set_comms_rate(resolved_parameters, session, envoy_client)
                return
            case "register-end-device":
                await action_register_end_device(active_test_procedure, resolved_parameters, session)
                return
            case "communications-status":
                action_communications_status(active_test_procedure, resolved_parameters)
                return
            case "edev-registration-links":
                await action_edev_registration_links(resolved_parameters, envoy_client)
                return

    except Exception as exc:
        logger.error(f"Failed executing action {action}", exc_info=exc)
        raise FailedActionError(f"Failed executing action {action.type}")

    raise UnknownActionError(f"Unrecognised action '{action.type}'. This is a problem with the test definition")


async def apply_actions(
    session: AsyncSession,
    listener: Listener,
    runner_state: RunnerState,
    envoy_client: EnvoyAdminClient,
):
    """Applies all actions for the given listener.

    Logs an error if the action was able to be executed.

    Args:
        listener (Listener): An instance of Listener whose actions will be applied.
        active_test_procedure (ActiveTestProcedure): The currently active test procedure.
    """
    for action in listener.actions:
        try:
            await apply_action(session=session, action=action, runner_state=runner_state, envoy_client=envoy_client)
        except (UnknownActionError, FailedActionError) as e:
            logger.error(f"Error. Unable to execute action for step={listener.step}: {repr(e)}")
