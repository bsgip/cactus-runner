from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.check import run_check
from cactus_runner.app.log import LOG_FILE_CACTUS_RUNNER, LOG_FILE_ENVOY, read_log_file
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    CriteriaEntry,
    RequestEntry,
    RunnerStatus,
    StepStatus,
)


def get_runner_status_summary(step_status: dict[str, StepStatus]):
    completed_steps = sum(s == StepStatus.RESOLVED for s in step_status.values())
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


async def get_active_runner_status(
    session: AsyncSession,
    active_test_procedure: ActiveTestProcedure,
    request_history: list[RequestEntry],
    last_client_interaction: ClientInteraction,
) -> RunnerStatus:

    step_status = active_test_procedure.step_status

    return RunnerStatus(
        timestamp=datetime.now(tz=timezone.utc),
        log_envoy=read_log_file(LOG_FILE_ENVOY),
        log_cactus_runner=read_log_file(LOG_FILE_CACTUS_RUNNER),
        test_procedure_name=active_test_procedure.name,
        last_client_interaction=last_client_interaction,
        criteria=await get_criteria_summary(session, active_test_procedure),
        status_summary=get_runner_status_summary(step_status=step_status),
        step_status=step_status,
        request_history=request_history,
    )


def get_runner_status(last_client_interaction: ClientInteraction) -> RunnerStatus:
    return RunnerStatus(
        timestamp=datetime.now(tz=timezone.utc),
        status_summary="No test procedure running",
        last_client_interaction=last_client_interaction,
        log_envoy=read_log_file(LOG_FILE_ENVOY),
        log_cactus_runner=read_log_file(LOG_FILE_CACTUS_RUNNER),
    )
