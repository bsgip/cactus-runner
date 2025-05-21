import logging

from cactus_test_definitions import Event
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.action import apply_actions
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.models import (
    ActiveTestProcedure,
    Listener,
)

logger = logging.getLogger(__name__)


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
