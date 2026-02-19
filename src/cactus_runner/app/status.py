import logging
from datetime import datetime, timedelta, timezone

from cactus_schema.runner import (
    CriteriaEntry,
    DataStreamPoint,
    DERCapabilityInfo,
    DERSettingsInfo,
    DERStatusInfo,
    EndDeviceMetadata,
    PreconditionCheckEntry,
    RequestEntry,
    RunnerStatus,
    StepEventStatus,
    StepStatus,
    TimelineDataStreamEntry,
    TimelineStatus,
)
from envoy.server.model.site import Site, SiteDERRating, SiteDERSetting, SiteDERStatus
from envoy_schema.server.schema.sep2.der import (
    AlarmStatusType,
    ConnectStatusType,
    DERControlType,
    DERType,
    DOESupportedMode,
    InverterStatusType,
    LocalControlModeStatusType,
    OperationalModeStatusType,
    StorageModeStatusType,
)
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.check import run_check
from cactus_runner.app.envoy_common import get_active_site
from cactus_runner.app.log import LOG_FILE_ENVOY_SERVER, read_log_file
from cactus_runner.app.resolvers import resolve_named_variable_der_setting_max_w
from cactus_runner.app.timeline import duration_to_label, generate_timeline
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    StepInfo,
)

logger = logging.getLogger(__name__)


def _resolve_value_multiplier(value: int | None, multiplier: int | None) -> int | None:
    """Resolve a sep2 value/multiplier pair to an integer (value * 10^multiplier)."""
    if value is None:
        return None
    return int(value * (10 ** (multiplier if multiplier is not None else 0)))


def _resolve_intflag(bitmap: int | None, flag_type) -> list[str] | None:
    """Resolve an IntFlag bitmap to a list of active flag names."""
    if bitmap is None:
        return None
    return [flag.name for flag in flag_type if bitmap & flag]


def _resolve_intenum(value: int | None, enum_type) -> str | None:
    """Resolve an IntEnum integer value to its name string."""
    if value is None:
        return None
    try:
        return enum_type(value).name
    except ValueError:
        return None


def _build_der_capability(rating: SiteDERRating) -> DERCapabilityInfo:
    return DERCapabilityInfo(
        der_type=_resolve_intenum(rating.der_type, DERType),
        modes_supported=_resolve_intflag(rating.modes_supported, DERControlType),
        max_w=_resolve_value_multiplier(rating.max_w_value, rating.max_w_multiplier),
        max_va=_resolve_value_multiplier(rating.max_va_value, rating.max_va_multiplier),
        max_var=_resolve_value_multiplier(rating.max_var_value, rating.max_var_multiplier),
        max_var_neg=_resolve_value_multiplier(rating.max_var_neg_value, rating.max_var_neg_multiplier),
        max_a=_resolve_value_multiplier(rating.max_a_value, rating.max_a_multiplier),
        max_charge_rate_w=_resolve_value_multiplier(rating.max_charge_rate_w_value, rating.max_charge_rate_w_multiplier),
        max_discharge_rate_w=_resolve_value_multiplier(
            rating.max_discharge_rate_w_value, rating.max_discharge_rate_w_multiplier
        ),
        max_wh=_resolve_value_multiplier(rating.max_wh_value, rating.max_wh_multiplier),
        doe_modes_supported=_resolve_intflag(rating.doe_modes_supported, DOESupportedMode),
    )


def _build_der_settings(setting: SiteDERSetting) -> DERSettingsInfo:
    return DERSettingsInfo(
        modes_enabled=_resolve_intflag(setting.modes_enabled, DERControlType),
        max_w=_resolve_value_multiplier(setting.max_w_value, setting.max_w_multiplier),
        max_va=_resolve_value_multiplier(setting.max_va_value, setting.max_va_multiplier),
        max_var=_resolve_value_multiplier(setting.max_var_value, setting.max_var_multiplier),
        max_var_neg=_resolve_value_multiplier(setting.max_var_neg_value, setting.max_var_neg_multiplier),
        max_charge_rate_w=_resolve_value_multiplier(
            setting.max_charge_rate_w_value, setting.max_charge_rate_w_multiplier
        ),
        max_discharge_rate_w=_resolve_value_multiplier(
            setting.max_discharge_rate_w_value, setting.max_discharge_rate_w_multiplier
        ),
        grad_w=setting.grad_w,
        doe_modes_enabled=_resolve_intflag(setting.doe_modes_enabled, DOESupportedMode),
    )


def _build_der_status(status: SiteDERStatus) -> DERStatusInfo:
    return DERStatusInfo(
        alarm_status=_resolve_intflag(status.alarm_status, AlarmStatusType),
        generator_connect_status=_resolve_intflag(status.generator_connect_status, ConnectStatusType),
        storage_connect_status=_resolve_intflag(status.storage_connect_status, ConnectStatusType),
        inverter_status=_resolve_intenum(status.inverter_status, InverterStatusType),
        operational_mode_status=_resolve_intenum(status.operational_mode_status, OperationalModeStatusType),
        storage_mode_status=_resolve_intenum(status.storage_mode_status, StorageModeStatusType),
        local_control_mode_status=_resolve_intenum(status.local_control_mode_status, LocalControlModeStatusType),
        manufacturer_status=status.manufacturer_status,
        state_of_charge_status=status.state_of_charge_status,
    )


def get_runner_status_summary(step_status: dict[str, StepInfo]):
    completed_steps = sum(s.get_step_status() == StepStatus.RESOLVED for s in step_status.values())
    steps = len(step_status)
    return f"{completed_steps}/{steps} steps complete."


async def get_criteria_summary(
    session: AsyncSession, active_test_procedure: ActiveTestProcedure
) -> list[CriteriaEntry]:
    if not active_test_procedure.definition.criteria or not active_test_procedure.definition.criteria.checks:
        return []

    criteria: list[CriteriaEntry] = []
    for check in active_test_procedure.definition.criteria.checks:
        try:
            check_result = await run_check(check, active_test_procedure, session)
            criteria.append(
                CriteriaEntry(
                    check_result.passed,
                    check.type,
                    "" if check_result.description is None else check_result.description,
                )
            )
        except Exception as exc:
            criteria.append(CriteriaEntry(False, check.type, f"Unexpected error: {exc}"))

    return criteria


async def get_precondition_checks_summary(
    session: AsyncSession, active_test_procedure: ActiveTestProcedure
) -> list[PreconditionCheckEntry]:
    if not active_test_procedure.definition.preconditions or not active_test_procedure.definition.preconditions.checks:
        return []

    checks: list[PreconditionCheckEntry] = []
    for check in active_test_procedure.definition.preconditions.checks:
        try:
            check_result = await run_check(check, active_test_procedure, session)
            checks.append(
                PreconditionCheckEntry(
                    check_result.passed,
                    check.type,
                    "" if check_result.description is None else check_result.description,
                )
            )
        except Exception as exc:
            checks.append(PreconditionCheckEntry(False, check.type, f"Unexpected error: {exc}"))

    return checks


async def get_current_instructions(active_test_procedure: ActiveTestProcedure) -> list[str] | None:
    if active_test_procedure.started_at is None:
        # The test is in the init-phase
        # return the precondition instructions (if present)
        preconditions = active_test_procedure.definition.preconditions
        if preconditions:
            return preconditions.instructions
    else:
        # The test has started
        # return the instructions for any enabled steps
        instructions = []
        for listener in active_test_procedure.listeners:
            if listener.enabled_time:
                step_instructions = active_test_procedure.definition.steps[listener.step].instructions
                if step_instructions is not None:
                    # Add the step name to the end of each instruction
                    step_instructions = [f"{instruction} ({listener.step})" for instruction in step_instructions]
                    instructions.extend(step_instructions)
        if instructions:
            return instructions

    return None


async def get_timeline_data_streams(
    session: AsyncSession, basis: datetime, interval_seconds: int, end: datetime
) -> list[TimelineDataStreamEntry]:
    """Takes a timeline snapshot for the active test procedure and then converts it to the JSON compatible equivalent
    for use with status models"""

    timeline = await generate_timeline(session, basis, interval_seconds, end)
    return [
        TimelineDataStreamEntry(
            label=ds.label,
            stepped=ds.stepped,
            dashed=ds.dashed,
            data=[
                DataStreamPoint(val, duration_to_label(idx * interval_seconds))
                for idx, val in enumerate(ds.offset_watt_values)
            ],
        )
        for ds in timeline.data_streams
    ]


def get_event_status(
    now: datetime, step_name: str, step_info: StepInfo, active_test_procedure: ActiveTestProcedure
) -> str | None:
    """Generates a short, human readable status message for an active step (or None if the test isn't active)"""
    if step_info.get_step_status() != StepStatus.ACTIVE:
        return None

    for listener in active_test_procedure.listeners:
        if listener.step != step_name:
            continue

        event = listener.event
        if event.type == "wait":
            # Figure out how many more seconds are we waiting for
            duration_seconds = event.parameters.get("duration_seconds", None)
            if duration_seconds is None or step_info.started_at is None:
                return "Waiting for ???s."

            finish_time = step_info.started_at + timedelta(seconds=duration_seconds)
            if now >= finish_time:
                return "Triggering..."
            wait_time_seconds = int((finish_time - now).total_seconds())
            return f"Waiting for {wait_time_seconds}s"
        elif event.type == "proceed":
            return "Waiting on signal to proceed"
        else:
            # We have a GET / PUT / DELETE etc event
            method = event.type.split("-")[0]
            endpoint = event.parameters.get("endpoint", "???")
            return f"{method} {endpoint}"

    return None


async def get_active_runner_status(
    session: AsyncSession,
    active_test_procedure: ActiveTestProcedure,
    request_history: list[RequestEntry],
    last_client_interaction: ClientInteraction,
    crop_minutes: int | None = None,  # Allows a partial runner status to be generated for the UI
) -> RunnerStatus:
    now = datetime.now(timezone.utc)

    step_status: dict[str, StepEventStatus] = {}
    for step_name, step_info in active_test_procedure.step_status.items():
        event_status = get_event_status(now, step_name, step_info, active_test_procedure)
        step_status[step_name] = StepEventStatus(step_info.started_at, step_info.completed_at, event_status)

    # If there is a set max w available - return it - otherwise client likely has registered anything yet
    # This is used by both timeline and EndDeviceMetadata classes
    try:
        set_max_w = int(await resolve_named_variable_der_setting_max_w(session))
    except Exception:
        set_max_w = None

    # Try and generate a timeline
    timeline = None
    try:
        basis = active_test_procedure.started_at
        if basis is not None:
            interval_seconds = 20
            end = now + timedelta(seconds=120)

            # Optionally crop to reduce status size for UI
            if crop_minutes is not None:
                crop_start = now - timedelta(minutes=crop_minutes)
                basis = max(basis, crop_start)  # Don't go earlier than crop_start

            data_streams = await get_timeline_data_streams(session, basis, interval_seconds, end)
            now_offset = duration_to_label(((now - basis).seconds // interval_seconds) * interval_seconds)
            timeline = TimelineStatus(data_streams=data_streams, set_max_w=set_max_w, now_offset=now_offset)
    except Exception as exc:
        logger.error("Error generating timeline", exc_info=exc)
        timeline = None

    # Populate EndDeviceMetadata from active site
    end_device_metadata = None
    try:
        active_site: Site | None = await get_active_site(session, include_der_settings=True)
        if active_site is not None:
            doe_modes_enabled = None
            der_capability = None
            der_settings = None
            der_status = None
            if active_site.site_ders:
                first_site_der = active_site.site_ders[0]
                if first_site_der.site_der_setting is not None:
                    doe_modes_enabled = first_site_der.site_der_setting.doe_modes_enabled
                    der_settings = _build_der_settings(first_site_der.site_der_setting)
                if first_site_der.site_der_rating is not None:
                    der_capability = _build_der_capability(first_site_der.site_der_rating)
                if first_site_der.site_der_status is not None:
                    der_status = _build_der_status(first_site_der.site_der_status)

            end_device_metadata = EndDeviceMetadata(
                edevid=active_site.site_id,
                lfdi=active_site.lfdi,
                sfdi=active_site.sfdi,
                nmi=active_site.nmi,
                aggregator_id=active_site.aggregator_id,
                set_max_w=set_max_w,
                doe_modes_enabled=doe_modes_enabled,
                device_category=active_site.device_category,
                timezone_id=active_site.timezone_id,
                der_capability=der_capability,
                der_settings=der_settings,
                der_status=der_status,
            )
    except Exception as exc:
        logger.error("Error getting end device metadata", exc_info=exc)
        end_device_metadata = None

    # Optionally crop request_history to reduce status size for UI
    if crop_minutes is not None:
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=crop_minutes)
        request_history = [req for req in request_history if req.timestamp >= cutoff_time]

    return RunnerStatus(
        timestamp_status=datetime.now(tz=timezone.utc),
        timestamp_initialise=active_test_procedure.initialised_at,
        timestamp_start=active_test_procedure.started_at,
        csip_aus_version=active_test_procedure.csip_aus_version.value,
        log_envoy=read_log_file(LOG_FILE_ENVOY_SERVER),
        test_procedure_name=active_test_procedure.name,
        last_client_interaction=last_client_interaction,
        criteria=await get_criteria_summary(session, active_test_procedure),
        precondition_checks=await get_precondition_checks_summary(session, active_test_procedure),
        instructions=await get_current_instructions(active_test_procedure),
        status_summary=get_runner_status_summary(step_status=active_test_procedure.step_status),
        step_status=step_status,
        request_history=request_history,
        timeline=timeline,
        end_device_metadata=end_device_metadata,
    )


def get_runner_status(last_client_interaction: ClientInteraction) -> RunnerStatus:
    return RunnerStatus(
        timestamp_status=datetime.now(tz=timezone.utc),
        timestamp_start=None,
        timestamp_initialise=None,
        csip_aus_version="",
        status_summary="No test procedure running",
        last_client_interaction=last_client_interaction,
        log_envoy=read_log_file(LOG_FILE_ENVOY_SERVER),
    )
