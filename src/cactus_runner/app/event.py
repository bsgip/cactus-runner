import logging
from datetime import datetime, timedelta, timezone

from cactus_test_definitions import Event
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.action import apply_actions
from cactus_runner.app.database import begin_session
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.app.variable_resolver import (
    resolve_variable_expressions_from_parameters,
)
from cactus_runner.models import ActiveTestProcedure, Listener, StepStatus

logger = logging.getLogger(__name__)


class WaitEventError(Exception):
    """Custom exception for wait event errors."""


async def handle_event(
    event: Event, active_test_procedure: ActiveTestProcedure, session: AsyncSession, envoy_client: EnvoyAdminClient
) -> Listener | None:
    """Triggers the action associated with any enabled listeners that match then event.

    Logs an error if the action was able to be executed.

    Args:
        event (Event): An Event to be matched against the test procedures enabled listeners.
        active_test_procedure (ActiveTestProcedure): The currently active test procedure.

    Returns:
        Listener: If successful return the listener that matched the event.
        None: If no listener matched.
    """
    # Check all listeners
    for listener in active_test_procedure.listeners:
        # Did any of the current listeners match?
        if listener.enabled and listener.event == event:
            logger.info(f"Event matched: {event=}")

            # Perform actions associated with event
            await apply_actions(
                session=session,
                listener=listener,
                active_test_procedure=active_test_procedure,
                envoy_client=envoy_client,
            )

            return listener

    return None


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
                    # Apply actions
                    await apply_actions(
                        session=session,
                        listener=listener,
                        active_test_procedure=active_test_procedure,
                        envoy_client=envoy_client,
                    )

                    # Update step status
                    active_test_procedure.step_status[listener.step] = StepStatus.RESOLVED
