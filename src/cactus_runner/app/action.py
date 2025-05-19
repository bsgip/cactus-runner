import logging
from typing import Any

from cactus_test_definitions import Action
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy.admin_client import EnvoyAdminClient
from cactus_runner.app.variable_resolver import (
    resolve_variable_expressions_from_parameters,
)
from cactus_runner.models import (
    ActiveTestProcedure,
)

logger = logging.getLogger(__name__)


class UnknownActionError(Exception):
    """Unknown Cactus Runner Action"""


class FailedActionError(Exception):
    """Error raised when an action failed to execute"""


async def action_enable_listeners(
    active_test_procedure: ActiveTestProcedure,
    resolved_parameters: dict[str, Any],
):
    """Applies the enable-listeners action to the active test procedures.

    Each listener has a single test procedure step associated with it. A list of step names to enable is therefore
    sufficient to identify the corresponding listeners which are the actual objects that get disabled.

    Step names are defined by the test procedures. They are strings of the form "ALL-01-001", which is the first step
    "001" in the "ALL-01" test procedure.

    Args:
        session: DB session for accessing the envoy database
        active_test_procedure: The currently active test procedure
        resolved_parameters: The fully resolved (expressions replaced with their values) set of action parameters
    """
    steps_to_enable: list[str] = resolved_parameters["listeners"]
    for listener in active_test_procedure.listeners:
        if listener.step in steps_to_enable:
            logger.info(f"ACTION enable-listeners: Enabling listener {listener.step}")
            listener.enabled = True


async def action_remove_listeners(
    active_test_procedure: ActiveTestProcedure,
    resolved_parameters: dict[str, Any],
):
    """Applies the remove-listeners action to the active test procedure.

    Each listener has a single test procedure step associated with it. A list of step names to disable is therefore
    sufficient to identify the corresponding listeners which are the actual objects that get disabled.

    Step names are defined by the test procedures. They are strings of the form "ALL-01-001", which is the first step
    "001" in the "ALL-01" test procedure.

    Args:
        session: DB session for accessing the envoy database
        active_test_procedure: The currently active test procedure
        resolved_parameters: The fully resolved (expressions replaced with their values) set of action parameters
    """
    steps_to_remove: list[str] = resolved_parameters["listeners"]

    listeners_to_remove = []
    for listener in active_test_procedure.listeners:
        if listener.step in steps_to_remove:
            listeners_to_remove.append(listener)

    for listener_to_remove in listeners_to_remove:
        logger.info(f"ACTION remove-listeners: Removing listener: {listener_to_remove}")
        active_test_procedure.listeners.remove(listener_to_remove)  # mutate the original listeners list


async def apply_action(
    action: Action, active_test_procedure: ActiveTestProcedure, session: AsyncSession, envoy_client: EnvoyAdminClient
):
    """Applies the action to the active test procedure.

    Actions describe operations such as activate or disabling listeners.

    Args:
        action (Action): The Action to apply to the active test procedure.
        active_test_procedure (ActiveTestProcedure): The currently active test procedure.

    Raises:
        UnknownActionError: Raised if this function has no implementation for the provided `action.type`.
    """
    resolved_parameters = await resolve_variable_expressions_from_parameters(session, action.parameters)

    try:
        match action.type:
            case "enable-listeners":
                await action_enable_listeners(active_test_procedure, resolved_parameters)
                return

            case "remove-listeners":
                await action_remove_listeners(active_test_procedure, resolved_parameters)
                return
    except Exception as exc:
        logger.error(f"Failed executing action {action}", exc_info=exc)
        raise FailedActionError(f"Failed executing action {action.type}")

    raise UnknownActionError(f"Unrecognised action '{action}'. This is a problem with the test definition")
