import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web
from cactus_test_definitions import Event
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.action import apply_actions
from cactus_runner.app.check import all_checks_passing
from cactus_runner.app.database import begin_session
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.app.shared import (
    APPKEY_ENVOY_ADMIN_CLIENT,
)
from cactus_runner.app.variable_resolver import (
    resolve_variable_expressions_from_parameters,
)
from cactus_runner.models import ActiveTestProcedure, Listener, StepStatus

logger = logging.getLogger(__name__)


UNRECOGNISED_STEP_NAME = "IGNORED"


class WaitEventError(Exception):
    """Custom exception for wait event errors."""


async def handle_event(
    event: Event,
    active_test_procedure: ActiveTestProcedure,
    session: AsyncSession,
    envoy_client: EnvoyAdminClient,
    request_served: bool = False,
) -> tuple[Listener | None, bool]:
    """Triggers the action associated with any enabled listeners that match then event.

    Logs an error if the action was able to be executed.

    Args:
        event (Event): An Event to be matched against the test procedures enabled listeners.
        active_test_procedure (ActiveTestProcedure): The currently active test procedure.

    Returns:
        Listener: If successful return the listener that matched the event, else None if no listener matched.
        bool: True if the handling of the event was deferred because the request should
        be served beforehand.
    """
    # Check all listeners
    for listener in active_test_procedure.listeners:
        # Did any of the current listeners match?
        if listener.enabled and listener.event == event:
            logger.info(f"Event matched: {event=}")

            # Some actions can only be applied after the request has been served by the envoy server
            if "serve_request_first" in listener.event.parameters and listener.event.parameters["serve_request_first"]:
                if not request_served:
                    return listener, True

            if not await all_checks_passing(listener.event.checks, active_test_procedure, session):
                logger.info(f"Event on Step {listener.step} is NOT being fired as one or more checks are failing.")
                continue

            # Perform actions associated with event
            await apply_actions(
                session=session,
                listener=listener,
                active_test_procedure=active_test_procedure,
                envoy_client=envoy_client,
            )

            return listener, False

    return None, False


async def handle_wait_event(active_test_procedure: ActiveTestProcedure, envoy_client: EnvoyAdminClient):
    """Checks for any expired wait events on enabled listeners and triggers their actions.

    Args:
        active_test_procedeure (ActiveTestProcedure): The current active test procedure.
        envoy_client (EnvoyAdminClient): An instance of an envoy admin client.

    Raises:
        WaitEventError: If the wait event is missing a start timestamp or duration.
    """
    now = datetime.now(timezone.utc)

    # Loop over enabled listeners with (active) wait events
    for listener in active_test_procedure.listeners:
        if listener.enabled and listener.event.type == "wait":
            async with begin_session() as session:
                resolved_parameters = await resolve_variable_expressions_from_parameters(
                    session, listener.event.parameters
                )
                try:
                    wait_start = resolved_parameters["wait_start_timestamp"]
                except KeyError:
                    raise WaitEventError("Wait event missing start timestamp ('wait_start_timestamp')")
                try:
                    wait_duration_sec = resolved_parameters["duration_seconds"]
                except KeyError:
                    raise WaitEventError("Wait event missing duration ('duration_seconds')")

                # Determine if wait period has expired
                if now - wait_start >= timedelta(seconds=wait_duration_sec):
                    if not await all_checks_passing(listener.event.checks, active_test_procedure, session):
                        logger.info(f"Step {listener.step} is NOT being fired as one or more checks are failing.")
                        continue

                    # Apply actions
                    await apply_actions(
                        session=session,
                        listener=listener,
                        active_test_procedure=active_test_procedure,
                        envoy_client=envoy_client,
                    )

                    # Update step status
                    active_test_procedure.step_status[listener.step] = StepStatus.RESOLVED


async def update_test_procedure_progress(
    request: web.Request, active_test_procedure: ActiveTestProcedure, request_served: bool = False
) -> tuple[str, bool]:
    """Calls handle_event and updates progress test procedure"""
    # Update the progress of the test procedure
    request_event = Event(type=f"{request.method}-request-received", parameters={"endpoint": request.path})
    async with begin_session() as session:
        envoy_client = request.app[APPKEY_ENVOY_ADMIN_CLIENT]
        listener, serve_request_first = await handle_event(
            event=request_event,
            active_test_procedure=active_test_procedure,
            session=session,
            envoy_client=envoy_client,
            request_served=request_served,
        )

    # Update step_status when action associated with event is handled.
    # If we have a listener but serve_request_first is True, event handling has been
    # deferred till after the request has been served.
    if listener and not serve_request_first:
        active_test_procedure.step_status[listener.step] = StepStatus.RESOLVED

    # Determine which step of the test procedure was handled by this event.
    # UNRECOGNISED_STEP_NAME indicates that no listener event was recognised by the
    # test procedure and didn't progress it any further.
    step_name = UNRECOGNISED_STEP_NAME if listener is None else listener.step

    return step_name, serve_request_first
