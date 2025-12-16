import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from envoy.server.model.site import Site
from cactus_runner.app.check import run_check
from cactus_runner.app.envoy_common import get_active_site
from cactus_runner.app.log import LOG_FILE_ENVOY_SERVER, read_log_file
from cactus_runner.app.resolvers import resolve_named_variable_der_setting_max_w
from cactus_runner.app.timeline import duration_to_label, generate_timeline
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    CriteriaEntry,
    DataStreamPoint,
    EndDeviceMetadata,
    PreconditionCheckEntry,
    RequestEntry,
    RunnerStatus,
    StepInfo,
    StepStatus,
    TimelineDataStreamEntry,
    TimelineStatus,
)

logger = logging.getLogger(__name__)


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


async def get_active_runner_status(
    session: AsyncSession,
    active_test_procedure: ActiveTestProcedure,
    request_history: list[RequestEntry],
    last_client_interaction: ClientInteraction,
    crop_minutes: int | None = None,  # Allows a partial runner status to be generated for the UI
) -> RunnerStatus:

    step_status = active_test_procedure.step_status

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
            now = datetime.now(timezone.utc)
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
            # Get doe_modes_enabled from the first site_der if available
            doe_modes_enabled = None
            if active_site.site_ders:
                first_site_der = active_site.site_ders[0]
                if first_site_der.site_der_setting is not None:
                    doe_modes_enabled = first_site_der.site_der_setting.doe_modes_enabled

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
        status_summary=get_runner_status_summary(step_status=step_status),
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
