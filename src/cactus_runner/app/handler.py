import http
import logging
from datetime import datetime, timezone

from aiohttp import web
from envoy.server.api.depends.lfdi_auth import LFDIAuthDepends
from envoy.server.crud.common import convert_lfdi_to_sfdi

from cactus_runner.app import action, auth, event, finalize, precondition, proxy, status
from cactus_runner.app.check import all_checks_passing
from cactus_runner.app.database import begin_session
from cactus_runner.app.env import (
    DEV_SKIP_AUTHORIZATION_CHECK,
    SERVER_URL,
)
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.app.shared import (
    APPKEY_AGGREGATOR,
    APPKEY_ENVOY_ADMIN_CLIENT,
    APPKEY_RUNNER_STATE,
    APPKEY_TEST_PROCEDURES,
)
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientInteraction,
    ClientInteractionType,
    InitResponseBody,
    Listener,
    RequestEntry,
    RunnerState,
    StartResponseBody,
    StepStatus,
)

logger = logging.getLogger(__name__)


async def init_handler(request: web.Request):
    """Handler for init requests.

        Sent by the client to initialise a test procedure.

        The following initialization steps are performed:

        1. All tables in the database are truncated
        2. Register the aggregator (along with its certificate)
        3. Apply database preconditions
        4. Trigger the envoy server to start with the correction configuration.
    .
        Args:
            request: An aiohttp.web.Request instance. The requests must include the following
            query parameters:
            'test' - the name of the test procedure to initialize
            'certificate' - the PEM encoded certificate to register as belonging to the aggregator
            'subscription_domain' - [Optional] the FQDN to be added to the pub/sub allow list for subscriptions

        Returns:
            aiohttp.web.Response: The body contains a simple json message (status msg, test name and timestamp) or
            409 (Conflict) if there is already a test procedure initialised or
            400 (Bad Request) if either of query parameters ('test' or 'certificate') are missing or
            400 (Bad Request) if no test procedure definition could be found for the requested test
            procedure

    """
    active_test_procedure = request.app[APPKEY_RUNNER_STATE].active_test_procedure
    test_procedures = request.app[APPKEY_TEST_PROCEDURES]

    # We cannot initialise another test procedure if one is already active
    if active_test_procedure is not None:
        return web.Response(
            status=http.HTTPStatus.CONFLICT,
            text=f"Test Procedure ({active_test_procedure.name}) already active. Initialising another test procedure is not permitted.",  # noqa: E501
        )

    # Update last client interaction
    request.app[APPKEY_RUNNER_STATE].last_client_interaction = ClientInteraction(
        interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT, timestamp=datetime.now(timezone.utc)
    )

    # Reset envoy database
    # This must happen before the aggregator is registered or any test preconditions applied
    logger.debug("Resetting envoy database")
    await precondition.reset_db()

    # Get the name of the test procedure from the query parameter
    requested_test_procedure = request.query["test"]
    if requested_test_procedure is None:
        return web.Response(status=http.HTTPStatus.BAD_REQUEST, text="Missing 'test' query parameter.")

    # Get the certificate of the aggregator to register
    aggregator_certificate = request.query["certificate"]
    if aggregator_certificate is None:
        return web.Response(status=http.HTTPStatus.BAD_REQUEST, text="Missing 'certificate' query parameter.")

    subscription_domain = request.query.get("subscription_domain", None)
    if subscription_domain is None:
        logger.info("Subscriptions will NOT be creatable - no valid domain (subscription_domain not set)")
    else:
        logger.info(f"Subscriptions will restricted to the FQDN '{subscription_domain}'")

    # Get the lfdi of the aggregator to register
    aggregator_lfdi = LFDIAuthDepends.generate_lfdi_from_pem(aggregator_certificate)
    await precondition.register_aggregator(lfdi=aggregator_lfdi, subscription_domain=subscription_domain)

    # Save the aggregator details for later request validation
    request.app[APPKEY_AGGREGATOR].certificate = aggregator_certificate
    request.app[APPKEY_AGGREGATOR].lfdi = aggregator_lfdi

    logger.debug(f"{aggregator_certificate=}")
    logger.debug(f"{aggregator_lfdi=}")

    # Get the definition of the test procedure
    try:
        definition = test_procedures.test_procedures[requested_test_procedure]
    except KeyError:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST,
            text=f"Expected valid test procedure for 'test' query parameter. Received '/start=?test={requested_test_procedure}'",  # noqa: E501
        )

    # Create listeners for all test procedure events
    listeners = []
    for step_name, step in definition.steps.items():
        listeners.append(Listener(step=step_name, event=step.event, actions=step.actions))

    # Set 'active_test_procedure' to the requested test procedure
    active_test_procedure = ActiveTestProcedure(
        name=requested_test_procedure,
        definition=definition,
        listeners=listeners,
        step_status={step: StepStatus.PENDING for step in definition.steps.keys()},
        client_lfdi=aggregator_lfdi,
        client_sfdi=convert_lfdi_to_sfdi(aggregator_lfdi),
    )

    logger.info(
        f"Test Procedure '{active_test_procedure.name}' initialised.",
        extra={"test_procedure": active_test_procedure.name},
    )

    request.app[APPKEY_RUNNER_STATE].active_test_procedure = active_test_procedure

    # TODO Should we put a sleep or ping an envoy server healthcheck here before returning a response?

    body = InitResponseBody(
        status="Test procedure initialised.",
        test_procedure=active_test_procedure.name,
        timestamp=datetime.now(timezone.utc),
    )
    return web.Response(status=http.HTTPStatus.CREATED, content_type="application/json", text=body.to_json())


async def start_handler(request: web.Request):
    """Handler for start requests.

    This handler enables the first listener in the test procedure.

    Args:
        request: An aiohttp.web.Request instance.

    Returns:
        aiohttp.web.Response: The body contains a simple json message (status msg, test name and timestamp) or
        409 (Conflict) if there is no initialised test procedure or
        409 (Conflict) if the test procedure already has enabled listeners (and has presumably already been started)
    """
    runner_state = request.app[APPKEY_RUNNER_STATE]
    active_test_procedure = runner_state.active_test_procedure

    # We cannot start a test procedure if one hasn't been initialized
    if active_test_procedure is None:
        return web.Response(
            status=http.HTTPStatus.CONFLICT,
            text="Unable to start non-existent test procedure. Try initialising a test procedure before continuing.",
        )

    # We cannot start a test procedure if any of the precondition checks are failing:
    if active_test_procedure.definition.preconditions:
        async with begin_session() as session:
            if not await all_checks_passing(
                active_test_procedure.definition.preconditions.checks, active_test_procedure, session
            ):
                return web.Response(
                    status=http.HTTPStatus.PRECONDITION_FAILED,
                    text="Unable to start test procedure. One or more preconditions have NOT been met.",
                )

    # We cannot start another test procedure if one is already running.
    # If there are active listeners then the test procedure must have already been started.
    listener_state = [listener.enabled_time for listener in active_test_procedure.listeners]
    if any(listener_state):
        return web.Response(
            status=http.HTTPStatus.CONFLICT,
            text=f"Test Procedure ({active_test_procedure.name}) already in progress. Starting another test procedure is not permitted.",  # noqa: E501
        )

    # Update last client interaction
    runner_state.last_client_interaction = ClientInteraction(
        interaction_type=ClientInteractionType.TEST_PROCEDURE_START, timestamp=datetime.now(timezone.utc)
    )

    # Fire any precondition actions
    if active_test_procedure.definition.preconditions and active_test_procedure.definition.preconditions.actions:
        async with begin_session() as session:
            envoy_client = request.app[APPKEY_ENVOY_ADMIN_CLIENT]

            for a in active_test_procedure.definition.preconditions.actions:
                await action.apply_action(a, runner_state, session, envoy_client)

            await session.commit()  # Actions can write updates to the DB directly

    # Activate the first listener
    if active_test_procedure.listeners:
        active_test_procedure.listeners[0].enabled_time = datetime.now(tz=timezone.utc)

    logger.info(
        f"Test Procedure '{active_test_procedure.name}' started.",
        extra={"test_procedure": active_test_procedure.name},
    )

    runner_state.active_test_procedure = active_test_procedure

    body = StartResponseBody(
        status="Test procedure started.",
        test_procedure=active_test_procedure.name,
        timestamp=datetime.now(timezone.utc),
    )
    return web.Response(status=http.HTTPStatus.OK, content_type="application/json", text=body.to_json())


async def finalize_handler(request):
    """Handler for finalize requests.

    Finalises the test procedure and returns test artifacts in response as a zipped archive.

    The archive contains the following test procedure artifacts,

    - Test Procedure Summary ('test_procedure_summary.json')
    - The runners log ('cactus_runner.jsonl')
    - The utility server log ('envoy.jsonl')
    - A utility server database dump ('envoy_db.dump')

    Args:
        request: An aiohttp.web.Request instance.

    Returns:
        aiohttp.web.Response: The body contains the zipped artifacts from the test procedure run or
        a 400 (Bad Request) if there is no test procedure in progress.
    """
    runner_state: RunnerState = request.app[APPKEY_RUNNER_STATE]

    if runner_state.active_test_procedure is not None:
        finalized_test_procedure_name = runner_state.active_test_procedure.name
        async with begin_session() as session:
            # This will either force the active test procedure to finish
            # (or it will return the results of an earlier finish)
            zip_contents = await finalize.finish_active_test(runner_state, session)

        # Clear the active test procedure and request history
        runner_state.active_test_procedure = None
        runner_state.request_history.clear()

        logger.info(
            f"Test Procedure '{finalized_test_procedure_name}' finalized",
            extra={"test_procedure": finalized_test_procedure_name},
        )

        return web.Response(
            body=zip_contents,
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": "attachment; filename=finalize.zip",
            },
        )
    else:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST,
            text="ERROR: Unable to finalize test procedure. No test procedure in progress.",
        )


async def status_handler(request):
    """Handler for status requests; returns the status of runner.

    Args:
        request: An aiohttp.web.Request instance.

    Returns:
        aiohttp.web.Response: The body (json) contains the status of the runner.
    """
    active_test_procedure = request.app[APPKEY_RUNNER_STATE].active_test_procedure

    logger.info("Test procedure status requested.")

    if active_test_procedure is not None:
        async with begin_session() as session:
            runner_status = await status.get_active_runner_status(
                session=session,
                active_test_procedure=active_test_procedure,
                request_history=request.app[APPKEY_RUNNER_STATE].request_history,
                last_client_interaction=request.app[APPKEY_RUNNER_STATE].last_client_interaction,
            )
        logger.info(
            f"Status of test procedure '{runner_status.test_procedure_name}': {runner_status.step_status}",
            extra={"test_procedure": runner_status.test_procedure_name},
        )

    else:
        runner_status = status.get_runner_status(
            last_client_interaction=request.app[APPKEY_RUNNER_STATE].last_client_interaction
        )
        logger.warning("Status of non-existent test procedure requested.")

    return web.Response(status=http.HTTPStatus.OK, content_type="application/json", text=runner_status.to_json())


async def proxied_request_handler(request: web.Request):
    """Handler for requests that should be forwarded to the utility server.

    The handler also logs all requests to `request.app[APPKEY_RUNNER_STATE].request_history`, tagging
    them with the test procedure step if appropriate otherwise with "IGNORED" if they didn't
    contribute to the progress of the test procedure.

    Requests are not forwarded if there is no active test procedure. Without an active test
    procedure there is no where to record the history of requests which could complicate
    interpreting test artifacts.

    Before forwarding any request to the utility server, the handler performs an authorization check,
    comparing the forwarded certificate (request object) and the aggregator registered with
    the utility server. This check can be disabled by setting the environment variable
    `DEV_SKIP_AUTHORIZATION_CHECK` to True.

    Args:
        request: An aiohttp.web.Request instance.

    Returns:
        aiohttp.web.Response: The forwarded response from the utility server or
        a 403 (forbidden) if the handler's authorization check fails.
    """
    runner_state: RunnerState = request.app[APPKEY_RUNNER_STATE]
    active_test_procedure = runner_state.active_test_procedure

    # Don't proxy requests if there is no active test procedure
    if active_test_procedure is None:
        logger.error(
            f"Request (path={request.path}) not forwarded. An active test procedure is required before requests are proxied."  # noqa: E501
        )
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST, text="Unable to handle request. An active test procedure is required."
        )

    if active_test_procedure.is_finished():
        logger.error(
            f"Request (path={request.path}) not forwarded. {active_test_procedure.name} has been marked as finished."
        )
        return web.Response(
            status=http.HTTPStatus.GONE,
            text=f"{active_test_procedure.name} has been marked as finished. This request will not be logged.",
        )

    # Store timestamp of when the request was received
    request_timestamp = datetime.now(timezone.utc)

    # Only proceed if authorized
    if not (DEV_SKIP_AUTHORIZATION_CHECK or auth.request_is_authorized(request=request)):
        return web.Response(
            status=http.HTTPStatus.FORBIDDEN, text="Forwarded certificate does not match for registered aggregator"
        )

    # Update last client interaction
    runner_state.last_client_interaction = ClientInteraction(
        interaction_type=ClientInteractionType.PROXIED_REQUEST, timestamp=request_timestamp
    )

    # Determine paths, url and HTTP method
    relative_url = request.path
    remote_url = SERVER_URL + request.path_qs
    method = request.method
    logger.debug(f"{relative_url=} {remote_url=} {method=}")

    # Fire "before request" event trigger
    envoy_client: EnvoyAdminClient = request.app[APPKEY_ENVOY_ADMIN_CLIENT]
    async with begin_session() as session:
        trigger_handled = await event.handle_event_trigger(
            trigger=event.generate_client_request_trigger(request, before_serving=True),
            runner_state=runner_state,
            session=session,
            envoy_client=envoy_client,
        )
        await session.commit()

    # Proxy the request to the utility server
    handler_response = await proxy.proxy_request(
        request=request, remote_url=remote_url, active_test_procedure=active_test_procedure
    )

    # Fire "after request" event trigger (only if an event didn't handle the before event)
    if not trigger_handled:
        async with begin_session() as session:
            trigger_handled = await event.handle_event_trigger(
                trigger=event.generate_client_request_trigger(request, before_serving=False),
                runner_state=runner_state,
                session=session,
                envoy_client=envoy_client,
            )
            await session.commit()

    # There will only ever be a maximum of 1 entry in this list
    # The request events will only trigger a max of one listener
    step_name: str = event.UNRECOGNISED_STEP_NAME
    if trigger_handled:
        handling_listener = trigger_handled[0]
        step_name = handling_listener.step

    # Record in request history
    request_entry = RequestEntry(
        url=remote_url,
        path=relative_url,
        method=http.HTTPMethod(method),
        status=http.HTTPStatus(handler_response.status),
        timestamp=request_timestamp,
        step_name=step_name,
    )
    runner_state.request_history.append(request_entry)

    return handler_response
