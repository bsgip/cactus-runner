import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum, auto
from http import HTTPMethod

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.action import apply_actions
from cactus_runner.app.check import all_checks_passing
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.app import evaluator
from cactus_runner.models import Listener, RunnerState

logger = logging.getLogger(__name__)


INIT_STAGE_STEP_NAME = "Init"
UNMATCHED_STEP_NAME = "Unmatched"


class WaitEventError(Exception):
    """Custom exception for wait event errors."""


class EventTriggerType(IntEnum):
    """The different types of event triggers that can be generated (i.e things that can trigger an Event listener
    to fire)"""

    # HTTP GET/POST/PUT/DELETE (etc) request, initiated by the client to the CSIP-Aus endpoints, raised BEFORE
    # the request is routed to the utility server.
    CLIENT_REQUEST_BEFORE = auto()

    # HTTP GET/POST/PUT/DELETE (etc) request, initiated by the client to the CSIP-Aus endpoints, raised AFTER
    # the request has been routed to the utility server.
    CLIENT_REQUEST_AFTER = auto()

    # Raised on a regular interval in response to a period of time elapsing from the last TIME trigger
    TIME = auto()


@dataclass(frozen=True)
class ClientRequestDetails:
    """Basic details about a HTTP request initiated from a client"""

    method: HTTPMethod  # The HTTP method being used in the request
    path: str  # The requested path (no query params)


@dataclass(frozen=True)
class EventTrigger:
    """Represents a *potential* trigger for an EventListener"""

    type: EventTriggerType  # What is the underlying trigger of this request
    time: datetime  # When was this event triggered (tz aware)
    single_listener: bool  # If True - this can trigger a maximum of one Listener. False can trigger more than one.
    client_request: ClientRequestDetails | None  # Only specified if type == CLIENT_REQUEST


def does_endpoint_match(request: ClientRequestDetails, match: str) -> bool:
    """Performs all logic for matching an "endpoint" to an incoming request's path.

    '*' can be a "wildcard" character for matching a single component of the path (a path component is part of the path
    seperated by '/'). It will NOT partially match

    eg:
    match=/edev/*/derp/1  would match /some/prefix/edev/123/derp/1
    match=/edev/1*3/derp/1  would NOT match /some/prefix/edev/123/derp/1


    Will be tolerant to path prefixes on the incoming request."""

    # If we don't have a wildcard - we can do a really simply method for matching
    WILDCARD = "*"
    if WILDCARD not in match:
        return request.path.endswith(match)

    # Otherwise we need to do a component by component comparison
    # Noting that there may be a variable prefix that the runner doesn't know about
    # To handle this - we start the comparison matching in reverse
    request_components = list(filter(None, request.path.split("/")))  # Remove empty strings
    match_components = list(filter(None, match.split("/")))  # Remove empty strings
    compared_component_count = 0
    for request_component, match_component in zip(reversed(request_components), reversed(match_components)):
        if match_component != WILDCARD and request_component != match_component:
            return False

        compared_component_count += 1

    return compared_component_count == len(match_components)


async def is_listener_triggerable(
    listener: Listener,
    trigger: EventTrigger,
    session: AsyncSession,
) -> bool:
    """Returns True if the specified listener can be triggered by the specified trigger.

    does NOT consider Event.checks - it's a pure comparison on whether the listener is active and whether the
    underlying event matches the specified trigger"""

    if not listener.enabled_time:
        return False

    # Is this listener for the variety of HTTP method "request-received" event types?
    if (
        listener.event.type.endswith("-request-received")
        and trigger.type in {EventTriggerType.CLIENT_REQUEST_AFTER, EventTriggerType.CLIENT_REQUEST_BEFORE}
        and trigger.client_request is not None
    ):
        expected_method_string = listener.event.type.split("-")[0]

        # Make sure the method being listened for matches the method we received
        if HTTPMethod(expected_method_string) != trigger.client_request.method:
            return False

        resolved_params = await evaluator.resolve_variable_expressions_from_parameters(
            session, listener.event.parameters
        )
        endpoint = resolved_params.get("endpoint", evaluator.ResolvedParam(""))
        serve_request_first = resolved_params.get("serve_request_first", evaluator.ResolvedParam(False))

        if not does_endpoint_match(trigger.client_request, endpoint.value):
            return False

        # Make sure that we are listening to the correct before/after serving event
        if serve_request_first.value:
            return trigger.type == EventTriggerType.CLIENT_REQUEST_AFTER
        else:
            return trigger.type == EventTriggerType.CLIENT_REQUEST_BEFORE

    # If this listener is a wait event and the current trigger is time based
    if listener.event.type == "wait" and trigger.type == EventTriggerType.TIME:

        resolved_params = await evaluator.resolve_variable_expressions_from_parameters(
            session, listener.event.parameters
        )
        duration_seconds = resolved_params.get("duration_seconds", evaluator.ResolvedParam(0))

        return (trigger.time - listener.enabled_time).seconds >= duration_seconds.value

    # This event type / trigger doesn't match
    return False


async def handle_event_trigger(
    trigger: EventTrigger, runner_state: RunnerState, session: AsyncSession, envoy_client: EnvoyAdminClient
) -> list[Listener]:
    """Runs through the currently active listeners for runner_state and potentially triggers their actions if trigger
    can be matched to an Event. Time based triggers can potentially trigger multiple listeners, HTTP triggers will only
    match at most a single trigger. Returns all triggered listeners (that had their actions run)

    Args:
        trigger: The trigger being evaluated
        runner_state: The current state of tests - requires an active_test_procedure to do anything
        session: DB session for actions/parameter resolving
        envoy_client: Client for interacting with admin server (for actions/checks that need it)

    returns the list of all Listener's that were triggered by trigger."""
    active_test_procedure = runner_state.active_test_procedure
    if not active_test_procedure:
        logger.info(f"handle_event_trigger: no active test procedure for handling trigger {trigger}")
        return []

    if active_test_procedure.is_finished():
        logger.info(f"handle_event_trigger: active test procedure is finished. Ignoring trigger {trigger}")
        return []

    # Check all listeners against this trigger
    triggered_listeners: list[Listener] = []
    listeners_to_eval = active_test_procedure.listeners.copy()  # We copy this as the underlying list might mutate
    for listener in listeners_to_eval:

        if await is_listener_triggerable(listener, trigger, session):
            logger.info(f"handle_event_trigger: Matched Step {listener.step} for {trigger}")

            if not await all_checks_passing(listener.event.checks, active_test_procedure, session):
                logger.info(f"handle_event_trigger: Step {listener.step} is NOT being triggered due to failing checks.")
                continue

            logger.info(f"handle_event_trigger: Step {listener.step} is being triggered.")
            await apply_actions(
                session=session,
                listener=listener,
                runner_state=runner_state,
                envoy_client=envoy_client,
            )

            triggered_listeners.append(listener)
            if trigger.single_listener:
                break

    return triggered_listeners


def generate_time_trigger() -> EventTrigger:
    """Generates an EventTrigger representing a poll of the TIME event"""
    return EventTrigger(
        type=EventTriggerType.TIME, time=datetime.now(timezone.utc), single_listener=False, client_request=None
    )


def generate_client_request_trigger(request: web.Request, before_serving: bool) -> EventTrigger:
    """Generates an EventTrigger representing the specified web.Request

    Args:
        request: The request to interrogate (body will NOT be read)
        before_serving: Is this an event trigger for BEFORE the request is served to envoy (True) or after (False)"""

    trigger_type = EventTriggerType.CLIENT_REQUEST_BEFORE if before_serving else EventTriggerType.CLIENT_REQUEST_AFTER
    return EventTrigger(
        type=trigger_type,
        time=datetime.now(timezone.utc),
        single_listener=True,
        client_request=ClientRequestDetails(HTTPMethod(request.method), request.path),
    )
