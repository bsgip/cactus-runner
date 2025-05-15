import logging

from cactus_test_definitions import (
    Action,
    Event,
)

from cactus_runner.models import (
    ActiveTestProcedure,
    Listener,
)

logger = logging.getLogger(__name__)


class UnknownActionError(Exception):
    """Unknown Cactus Runner Action"""


class FailedActionError(Exception):
    """Error raised when an action failed to execute"""


def _apply_enable_listeners(steps_to_enable: list[str], listeners: list[Listener], test_procedure_name: str):
    """Applies the enable-listeners action to the active test procedures.

    Each listener has a single test procedure step associated with it. A list of step names to enable is therefore
    sufficient to identify the corresponding listeners which are the actual objects that get disabled.

    Step names are defined by the test procedures. They are strings of the form "ALL-01-001", which is the first step
    "001" in the "ALL-01" test procedure.

    Args:
        steps_to_enable (list[str]): The steps (as a list of step names) to enable.
        listeners (list[Listener]): A list of all the listeners for the active test procedure. Note: This function can
        mutate the elements in this list.
        test_procedure_name: (str): The name of the active test procedure (used for logging)

    """
    steps_to_enable = steps_to_enable.copy()  # copy to prevent mutating argument
    for listener in listeners:
        if listener.step in steps_to_enable:
            logger.info(f"Enabling listener: {listener}")
            listener.enabled = True
            steps_to_enable.remove(listener.step)

    # Warn about any unmatched steps
    if steps_to_enable:
        logger.warning(
            f"Unable to enable the listeners for the following steps, ({steps_to_enable}). These are not recognised steps in the '{test_procedure_name} test procedure"  # noqa: E501
        )


def _apply_remove_listeners(steps_to_disable: list[str], listeners: list[Listener], test_procedure_name: str):
    """Applies the remove-listeners action to the active test procedure.

    Each listener has a single test procedure step associated with it. A list of step names to disable is therefore
    sufficient to identify the corresponding listeners which are the actual objects that get disabled.

    Step names are defined by the test procedures. They are strings of the form "ALL-01-001", which is the first step
    "001" in the "ALL-01" test procedure.

    Args:
        step_to_disable (list[str]): The steps (as a list of step names) to disable.
        listeners (list[Listener]): A list of all the listeners for the active test procedure. Note: This function can
        mutate the elements in this list.
        test_procedure_name: (str): The name of the active test procedure (used for logging)
    """
    steps_to_disable = steps_to_disable.copy()  # copy to prevent mutating argument

    listeners_to_remove = []
    for listener in listeners:
        if listener.step in steps_to_disable:
            listeners_to_remove.append(listener)
            steps_to_disable.remove(listener.step)  # mutate the original steps_to_disable

    for listener_to_remove in listeners_to_remove:
        logger.info(f"Removing listener: {listener_to_remove}")
        listeners.remove(listener_to_remove)  # mutate the original listeners list

    # Warn about any unmatched steps
    if steps_to_disable:
        logger.warning(
            f"Unable to remove the listener from the following steps, ({steps_to_disable}). These are not recognised steps in the '{test_procedure_name}' test procedure"  # noqa: E501
        )


def _apply_action(action: Action, active_test_procedure: ActiveTestProcedure):
    """Applies the action to the active test procedure.

    Actions describe operations such as activate or disabling listeners.

    Args:
        action (Action): The Action to apply to the active test procedure.
        active_test_procedure (ActiveTestProcedure): The currently active test procedure.

    Raises:
        UnknownActionError: Raised if this function has no implementation for the provided `action.type`.
    """
    match action.type:
        case "enable-listeners":
            _apply_enable_listeners(
                steps_to_enable=action.parameters["listeners"],
                listeners=active_test_procedure.listeners,
                test_procedure_name=active_test_procedure.name,
            )

        case "remove-listeners":
            _apply_remove_listeners(
                steps_to_disable=action.parameters["listeners"],
                listeners=active_test_procedure.listeners,
                test_procedure_name=active_test_procedure.name,
            )
        case _:
            raise UnknownActionError(f"Unrecognised action '{action}'")


def handle_event(event: Event, active_test_procedure: ActiveTestProcedure) -> Listener | None:
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
            for action in listener.actions:
                logger.info(f"Executing action: {action=}")
                try:
                    _apply_action(action=action, active_test_procedure=active_test_procedure)
                except (UnknownActionError, FailedActionError) as e:
                    logger.error(f"Error. Unable to execute action for step={listener.step}: {repr(e)}")

            return listener

    return None
