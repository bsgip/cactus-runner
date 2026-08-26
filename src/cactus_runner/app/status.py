import logging
from datetime import UTC, datetime, timedelta

from cactus_schema.runner import (
    CriteriaEntry,
    DataStreamPoint,
    PreconditionCheckEntry,
    RequestEntry,
    RunnerStatus,
    StepEventStatus,
    StepStatus,
    TimelineDataStreamEntry,
    TimelineStatus,
)

from cactus_runner.app.check import run_check
from cactus_runner.app.log import LOG_FILE_ENVOY_SERVER, read_log_file
from cactus_runner.app.timeline import duration_to_label, generate_timeline
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    StepInfo,
)
from cactus_runner.plugin.backends.common import RunnerBackend

logger = logging.getLogger(__name__)


def get_runner_status_summary(step_status: dict[str, StepInfo]) -> str:
    completed_steps = sum(s.get_step_status() == StepStatus.RESOLVED for s in step_status.values())
    steps = len(step_status)
    return f"{completed_steps}/{steps} steps complete."


async def get_criteria_summary(
    active_test_procedure: ActiveTestProcedure,
    backend: RunnerBackend,
) -> list[CriteriaEntry]:
    if not active_test_procedure.definition.criteria or not active_test_procedure.definition.criteria.checks:
        return []

    criteria: list[CriteriaEntry] = []
    for check in active_test_procedure.definition.criteria.checks:
        try:
            check_result = await run_check(check, active_test_procedure, backend)
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
    active_test_procedure: ActiveTestProcedure,
    backend: RunnerBackend,
) -> list[PreconditionCheckEntry]:
    if not active_test_procedure.definition.preconditions or not active_test_procedure.definition.preconditions.checks:
        return []

    checks: list[PreconditionCheckEntry] = []
    for check in active_test_procedure.definition.preconditions.checks:
        try:
            check_result = await run_check(check, active_test_procedure, backend)
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
    backend: RunnerBackend, basis: datetime, interval_seconds: int, end: datetime
) -> list[TimelineDataStreamEntry]:
    """Takes a timeline snapshot for the active test procedure and then converts it to the JSON compatible equivalent
    for use with status models"""

    timeline = await generate_timeline(backend, basis, interval_seconds, end)
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
    active_test_procedure: ActiveTestProcedure,
    request_history: list[RequestEntry],
    last_client_interaction: ClientInteraction,
    backend: RunnerBackend,
    crop_minutes: int | None = None,  # Allows a partial runner status to be generated for the UI
) -> RunnerStatus:
    now = datetime.now(UTC)
    resolver = backend.get_expression_resolver()

    step_status: dict[str, StepEventStatus] = {}
    for step_name, step_info in active_test_procedure.step_status.items():
        event_status = get_event_status(now, step_name, step_info, active_test_procedure)
        step_status[step_name] = StepEventStatus(step_info.started_at, step_info.completed_at, event_status)

    # If there is a set max w available - return it - otherwise client likely has registered anything yet
    # This is used by both timeline and EndDeviceMetadata classes
    try:
        set_max_w = int(await resolver.resolve_named_variable_der_setting_max_w())
    except Exception as exc:
        logger.error("Failed to resolve a value for setMaxW", exc_info=exc)
        set_max_w = None

    try:
        discharge_max_w = int(await resolver.resolve_named_variable_der_setting_max_discharge_rate_w())
    except Exception as exc:
        logger.error("Failed to resolve a value for setMaxDischargeRateW", exc_info=exc)
        discharge_max_w = None

    try:
        charge_max_w = int(await resolver.resolve_named_variable_der_setting_max_charge_rate_w())
    except Exception as exc:
        logger.error("Failed to resolve a value for setMaxChargeRateW", exc_info=exc)
        charge_max_w = None

    # Resolve the effective device max for each direction, preferring the asymmetric
    # setMaxDischargeRateW (export) / setMaxChargeRateW (import) over setMaxW.
    upper_max_w: int | None = None
    upper_max_label: str | None = None
    lower_max_w: int | None = None
    lower_max_label: str | None = None
    try:
        upper_max_w = set_max_w
        upper_max_label = "setMaxW"
        lower_max_w = set_max_w
        lower_max_label = "setMaxW"
        if discharge_max_w is not None:
            upper_max_w = discharge_max_w
            upper_max_label = "setMaxDischargeRateW"
        if charge_max_w is not None:
            lower_max_w = charge_max_w
            lower_max_label = "setMaxChargeRateW"
    except Exception as exc:
        logger.error("Error resolving device max for timeline", exc_info=exc)

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

            data_streams = await get_timeline_data_streams(backend, basis, interval_seconds, end)
            now_offset = duration_to_label((int((now - basis).total_seconds()) // interval_seconds) * interval_seconds)
            timeline = TimelineStatus(
                data_streams=data_streams,
                set_max_w=set_max_w,
                now_offset=now_offset,
                upper_max_w=upper_max_w,
                upper_max_label=upper_max_label,
                lower_max_w=lower_max_w,
                lower_max_label=lower_max_label,
            )
    except Exception as exc:
        logger.error("Error generating timeline", exc_info=exc)
        timeline = None

    # Populate EndDeviceMetadata from active site
    try:
        end_device_metadata = await backend.get_end_device_metadata()
    except Exception as exc:
        logger.error("Failed to return device metadata from backend.", exc_info=exc)
        end_device_metadata = None

    # Optionally crop request_history to reduce status size for UI
    if crop_minutes is not None:
        cutoff_time = datetime.now(UTC) - timedelta(minutes=crop_minutes)
        request_history = [req for req in request_history if req.timestamp >= cutoff_time]

    return RunnerStatus(
        timestamp_status=datetime.now(tz=UTC),
        timestamp_initialise=active_test_procedure.initialised_at,
        timestamp_start=active_test_procedure.started_at,
        timestamp_finished=active_test_procedure.finished_at,
        csip_aus_version=active_test_procedure.csip_aus_version.value,
        log_envoy=read_log_file(LOG_FILE_ENVOY_SERVER, tail_bytes=64 * 1024),
        test_procedure_name=active_test_procedure.name,
        last_client_interaction=last_client_interaction,
        criteria=await get_criteria_summary(active_test_procedure, backend),
        precondition_checks=await get_precondition_checks_summary(active_test_procedure, backend),
        instructions=await get_current_instructions(active_test_procedure),
        status_summary=get_runner_status_summary(step_status=active_test_procedure.step_status),
        step_status=step_status,
        request_history=request_history,
        timeline=timeline,
        end_device_metadata=end_device_metadata,
        warnings=list(active_test_procedure.warnings.values()),
    )


def get_runner_status(last_client_interaction: ClientInteraction) -> RunnerStatus:
    return RunnerStatus(
        timestamp_status=datetime.now(tz=UTC),
        timestamp_start=None,
        timestamp_initialise=None,
        csip_aus_version="",
        status_summary="No test procedure running",
        last_client_interaction=last_client_interaction,
        log_envoy=read_log_file(LOG_FILE_ENVOY_SERVER, tail_bytes=64 * 1024),
    )
