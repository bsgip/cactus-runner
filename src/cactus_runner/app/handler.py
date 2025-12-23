import http
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from aiohttp import ContentTypeError, web
from cactus_schema.runner import (
    ClientInteraction,
    ClientInteractionType,
    InitResponseBody,
    RequestData,
    RequestEntry,
    RequestList,
    RunRequest,
    StartResponseBody,
)
from cactus_test_definitions.client import Action, TestProcedure
from envoy.server.api.depends.lfdi_auth import LFDIAuthDepends
from envoy.server.crud.common import convert_lfdi_to_sfdi

from cactus_runner.app import action, auth, event, finalize, precondition, proxy, status
from cactus_runner.app.check import first_failing_check
from cactus_runner.app.database import begin_session
from cactus_runner.app.env import (
    DEV_SKIP_AUTHORIZATION_CHECK,
    MOUNT_POINT,
    SERVER_URL,
)
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.app.health import is_admin_api_healthy, is_db_healthy
from cactus_runner.app.requests_archive import (
    get_all_request_ids,
    read_request_response_files,
    write_request_response_files,
)
from cactus_runner.app.schema_validator import validate_proxy_request_schema
from cactus_runner.app.shared import (
    APPKEY_ENVOY_ADMIN_CLIENT,
    APPKEY_INITIALISED_CERTS,
    APPKEY_RUNNER_STATE,
)
from cactus_runner.models import (
    ActiveTestProcedure,
    ClientCertificateType,
    Listener,
    RunnerState,
    StepInfo,
)

logger = logging.getLogger(__name__)


@dataclass
class StartResult:
    success: bool
    status: http.HTTPStatus
    content_type: str
    content: str


async def attempt_apply_actions(
    actions: list[Action] | None, runner_state: RunnerState, envoy_client: EnvoyAdminClient
):
    if actions:
        async with begin_session() as session:
            for a in actions:
                await action.apply_action(a, runner_state, session, envoy_client)
            await session.commit()  # Actions can write updates to the DB directly


async def attempt_start_for_state(runner_state: RunnerState, envoy_client: EnvoyAdminClient) -> StartResult:
    """Try to transition a runner_state to "started" from the "initialised" state. Returns a StartResult indicating
    the result of that attempt.

    Requires a runner state to be in the "initialised" state."""
    active_test_procedure = runner_state.active_test_procedure

    # We cannot start a test procedure if one hasn't been initialized
    if active_test_procedure is None:
        return StartResult(
            False,
            http.HTTPStatus.CONFLICT,
            "text/plain",
            "Unable to start non-existent test procedure. Try initialising a test procedure before continuing.",
        )

    # We cannot start a test procedure if any of the precondition checks are failing:
    if active_test_procedure.definition.preconditions:
        async with begin_session() as session:
            check_failure = await first_failing_check(
                active_test_procedure.definition.preconditions.checks, active_test_procedure, session
            )
            if check_failure:
                return StartResult(
                    False,
                    http.HTTPStatus.PRECONDITION_FAILED,
                    "text/plain",
                    f"Unable to start test procedure, pre condition check has failed: {check_failure.description}",
                )

    # We cannot start another test procedure if one is already running.
    # If there are active listeners then the test procedure must have already been started.
    listener_state = [listener.enabled_time for listener in active_test_procedure.listeners]
    if any(listener_state):
        return StartResult(
            False,
            http.HTTPStatus.CONFLICT,
            "text/plain",
            f"Test Procedure ({active_test_procedure.name}) already in progress. Starting another test procedure is not permitted.",  # noqa: E501
        )

    # Update last client interaction
    now = datetime.now(timezone.utc)
    runner_state.client_interactions.append(
        ClientInteraction(interaction_type=ClientInteractionType.TEST_PROCEDURE_START, timestamp=now)
    )
    active_test_procedure.started_at = now

    # Fire any precondition actions
    if active_test_procedure.definition.preconditions:
        await attempt_apply_actions(active_test_procedure.definition.preconditions.actions, runner_state, envoy_client)

    # Activate the first listener
    await action.action_enable_steps(active_test_procedure, {"steps": [active_test_procedure.listeners[0].step]})

    logger.info(
        f"Test Procedure '{active_test_procedure.name}' started.",
        extra={"test_procedure": active_test_procedure.name},
    )

    runner_state.active_test_procedure = active_test_procedure

    return StartResult(
        True,
        http.HTTPStatus.OK,
        "application/json",
        StartResponseBody(
            status="Test procedure started.",
            test_procedure=active_test_procedure.name,
            timestamp=datetime.now(timezone.utc),
        ).to_json(),
    )


async def initialise_handler(request: web.Request) -> web.Response:  # noqa: C901
    """Handler for initialise requests.

        Sent by the client to initialise a test procedure.

        The following initialization steps are performed:

        1. All tables in the database are truncated
        2. Register the aggregator (along with its certificate)
        3. Apply database preconditions
        4. Trigger the envoy server to start with the correction configuration.
    .
        Args:
            request: An aiohttp.web.Request instance, the body of the request should be a json encoded instance
            of 'Run'.

        Returns:
            aiohttp.web.Response:
            201 (Created) The body contains a simple json message (status msg, test name and timestamp, is_started) or
            400 (Bad Request) if a RunRequest instance can't be instantiated from the json request body or
            409 (Conflict) if there is already a test procedure initialised or
            400 (Bad Request) if both aggregator and device certificates supplied for neither supplied or
            400 (Bad Request) if invalid test procedure definition supplied or
            412 (Precondition Failed) if an error occurred when applying the test procedure preconditions or
            409 (Conflict)/412 (Precondition Failed) if a test with immediate start failed to start
    """
    try:
        raw_json = await request.text()
    except ContentTypeError:
        return web.Response(status=http.HTTPStatus.BAD_REQUEST, text="Missing JSON body")

    try:
        run_request = RunRequest.from_json(raw_json)
        if isinstance(run_request, list):
            raise ValueError("Run request must be a RunRequest instance and not list[RunRequest]")
    except Exception as e:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST, text=f"Unable to parse JSON body to RunRequest instance {e}"
        )

    active_test_procedure = request.app[APPKEY_RUNNER_STATE].active_test_procedure
    # We cannot initialise another test procedure if one is already active
    if active_test_procedure is not None:
        return web.Response(
            status=http.HTTPStatus.CONFLICT,
            text=f"Test Procedure ({active_test_procedure.name}) already active. Initialising another test procedure is not permitted.",  # noqa: E501
        )

    # Update last client interaction
    request.app[APPKEY_RUNNER_STATE].client_interactions.append(
        ClientInteraction(
            interaction_type=ClientInteractionType.TEST_PROCEDURE_INIT, timestamp=datetime.now(timezone.utc)
        )
    )

    # Reset envoy database
    # This must happen before the aggregator is registered or any test preconditions applied
    logger.debug("Resetting envoy database")
    await precondition.reset_db()

    # Get the certificate of the aggregator to register
    aggregator_certificate = run_request.run_group.test_certificates.aggregator
    if aggregator_certificate is None:
        aggregator_lfdi = None
        logger.info("No aggregator certificate is loaded. All EndDevice's must be registered via a device certificate")
    else:
        aggregator_lfdi = LFDIAuthDepends.generate_lfdi_from_pem(aggregator_certificate)
        logger.info(f"Aggregator will created with certificate lfdi {aggregator_lfdi}.")

    # Get the device certificate to register
    device_certificate = run_request.run_group.test_certificates.device
    if device_certificate is None:
        device_lfdi = None
        logger.info("No device certificate is loaded. All EndDevice's must be registered via aggregator certificate")
    else:
        device_lfdi = LFDIAuthDepends.generate_lfdi_from_pem(device_certificate)
        logger.info(f"Device certificates will only be supported with certificate lfdi {device_lfdi}.")

    subscription_domain = run_request.test_config.subscription_domain
    if subscription_domain is None:
        logger.info("Subscriptions will NOT be creatable - no valid domain (subscription_domain not set)")
    else:
        sanitized_subscription_domain = subscription_domain.replace("\n", "").replace("\r", "")
        logger.info(f"Subscriptions will restricted to the FQDN '{sanitized_subscription_domain}'")

    if run_request.run_id is None:
        logger.info("No run ID has been assigned to this test.")
    else:
        logger.info(f"run ID {run_request.run_id} has been assigned to this test.")

    # Need EITHER device certificate or an aggregator certificate. Can't run both.
    if device_lfdi is not None and aggregator_lfdi is not None:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST,
            text="Cannot use 'aggregator_certificate' and 'device_certificate' at the same time.",
        )
    elif device_lfdi is None and aggregator_lfdi is None:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST, text="Need one of 'aggregator_certificate' or 'device_certificate'."
        )

    # Now install the certificate we intend to use
    client_aggregator_id = await precondition.register_aggregator(
        lfdi=aggregator_lfdi, subscription_domain=subscription_domain
    )
    if aggregator_lfdi is None:
        client_type = ClientCertificateType.DEVICE
        client_lfdi = cast(str, device_lfdi)  # we know its set due to checks above
        client_certificate = cast(str, device_certificate)  # we know its set due to checks above
    else:
        client_type = ClientCertificateType.AGGREGATOR
        client_lfdi = cast(str, aggregator_lfdi)  # we know its set due to checks above
        client_certificate = cast(str, aggregator_certificate)  # we know its set due to checks above
    logger.info(f"Registering a {client_type} certificate {client_lfdi} under aggregator id {client_aggregator_id}")

    # Save the certificate details for later request validation
    request.app[APPKEY_INITIALISED_CERTS].client_certificate_type = client_type
    request.app[APPKEY_INITIALISED_CERTS].client_lfdi = client_lfdi
    request.app[APPKEY_INITIALISED_CERTS].client_certificate = client_certificate

    # Get the definition of the test procedure
    try:
        definition = TestProcedure.from_yaml(run_request.test_definition.yaml_definition)
        if isinstance(definition, list):
            raise ValueError("Definition must be a TestProcedure instance and not list[TestProcedure]")
    except Exception as e:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST,
            text=f"Received invalid test procedure definition {e}",  # noqa: E501
        )

    # Create listeners for all test procedure events
    listeners = []
    for step_name, step in definition.steps.items():
        listeners.append(Listener(step=step_name, event=step.event, actions=step.actions))

    # Set steps to pending (no created/finished time)
    step_status = {step: StepInfo() for step in definition.steps.keys()}

    # Set 'active_test_procedure' to the requested test procedure
    active_test_procedure = ActiveTestProcedure(
        name=run_request.test_definition.test_procedure_id,
        definition=definition,
        csip_aus_version=run_request.run_group.csip_aus_version,
        initialised_at=datetime.now(tz=timezone.utc),
        started_at=None,  # Test hasn't started yet
        listeners=listeners,
        step_status=step_status,
        client_lfdi=client_lfdi,
        client_sfdi=convert_lfdi_to_sfdi(client_lfdi),
        client_aggregator_id=client_aggregator_id,
        client_certificate_type=client_type,
        run_id=run_request.run_id,
        pen=run_request.test_config.pen,
        subscription_domain=run_request.test_config.subscription_domain,
        is_static_url=run_request.test_config.is_static_url,
        run_group_id=run_request.run_group.run_group_id,
        run_group_name=run_request.run_group.name,
        user_id=run_request.test_user.user_id,
        user_name=run_request.test_user.name,
    )

    logger.info(
        f"Test Procedure '{active_test_procedure.name}' initialised.",
        extra={"test_procedure": active_test_procedure.name},
    )

    request.app[APPKEY_RUNNER_STATE].active_test_procedure = active_test_procedure

    # if this test has "init_actions" - now is the time to fire them
    if active_test_procedure.definition.preconditions:
        try:
            await attempt_apply_actions(
                active_test_procedure.definition.preconditions.init_actions,
                request.app[APPKEY_RUNNER_STATE],
                request.app[APPKEY_ENVOY_ADMIN_CLIENT],
            )
        except (action.FailedActionError, action.UnknownActionError) as e:
            return web.Response(
                status=http.HTTPStatus.PRECONDITION_FAILED,
                text=f"Failed to apply preconditions {e}",
                content_type="text/plain",
            )

    # if this test is marked as immediate_start - we can trigger the "start" now
    is_started = False
    if definition.preconditions and definition.preconditions.immediate_start:
        is_started = True
        start_result = await attempt_start_for_state(
            request.app[APPKEY_RUNNER_STATE], request.app[APPKEY_ENVOY_ADMIN_CLIENT]
        )
        if not start_result.success:
            logger.error(f"Unable to trigger immediate start: {start_result.content}")
            return web.Response(
                status=start_result.status,
                text=f"Unable to trigger immediate start: {start_result.content}",
                content_type="text/plain",
            )

    body = InitResponseBody(
        status="Test procedure initialised.",
        test_procedure=run_request.test_definition.test_procedure_id.value,
        timestamp=datetime.now(timezone.utc),
        is_started=is_started,
    )
    return web.Response(status=http.HTTPStatus.CREATED, content_type="application/json", text=body.to_json())


async def start_handler(request: web.Request) -> web.Response:
    """Handler for start requests.

    This handler enables the first listener in the test procedure.

    Args:
        request: An aiohttp.web.Request instance.

    Returns:
        aiohttp.web.Response: The body contains a simple json message (status msg, test name and timestamp) or
        409 (Conflict) if there is no initialised test procedure or
        409 (Conflict) if the test procedure already has enabled listeners (and has presumably already been started)
    """

    result = await attempt_start_for_state(request.app[APPKEY_RUNNER_STATE], request.app[APPKEY_ENVOY_ADMIN_CLIENT])
    return web.Response(status=result.status, text=result.content, content_type=result.content_type)


async def finalize_handler(request: web.Request) -> web.Response:
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
            try:
                zip_contents = await finalize.finish_active_test(runner_state, session)
            except Exception as exc:
                logger.error("Exception trying to finish_active_test. Will yield error zip", exc_info=exc)
                zip_contents = finalize.safely_get_error_zip([f"Exception generating zip: {exc}"])

        # Clear the active test procedure and request history
        runner_state.active_test_procedure = None
        runner_state.request_history.clear()

        logger.info(
            f"Test Procedure '{finalized_test_procedure_name}' finalized",
            extra={"test_procedure": finalized_test_procedure_name},
        )

        # Determine zip filename
        generation_timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        zip_filename = (
            f"CactusTestProcedureArtifacts_{generation_timestamp.isoformat()}_{finalized_test_procedure_name}.zip"
            # f"CactusTestProcedureArtifacts_{finalized_test_procedure_name}.zip"
        )

        return web.Response(
            body=zip_contents,
            headers={
                "Content-Type": "application/zip",
                "Content-Disposition": f'attachment; filename="{zip_filename}"',
            },
        )
    else:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST,
            text="ERROR: Unable to finalize test procedure. No test procedure in progress.",
        )


async def health_handler(request: web.Request) -> web.Response:
    """Handler for health requests. Serves a HTTP 200 if the Runner is fully operational - HTTP 503 otherwise

    Returns:
        aiohttp.web.Response: No response body - Either a HTTP 200 on success or 503 on failure.
    """
    if await is_db_healthy() and await is_admin_api_healthy(request.app[APPKEY_ENVOY_ADMIN_CLIENT]):
        return web.Response(status=http.HTTPStatus.OK)
    else:
        return web.Response(status=http.HTTPStatus.SERVICE_UNAVAILABLE)


async def status_handler(request: web.Request) -> web.Response:
    """Handler for status requests; returns the status of runner.

    Args:
        request: An aiohttp.web.Request instance.

    Returns:
        aiohttp.web.Response: The body (json) contains the status of the runner.
        This will be cropped to the last 15 mins of requests and timeline data to ensure UI does not slow down.
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
                crop_minutes=15,
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


async def get_request_raw_data_handler(request: web.Request) -> web.Response:
    """
    GET /request/{request_id}

    Retrieve raw request/response data for a specific request ID.

    Returns:
        JSON response with request and response content, or 404 if not found.
    """
    try:
        request_id = int(request.match_info["request_id"])
    except (KeyError, ValueError):
        request_data = RequestData(request_id=-1, request=None, response=None)
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST, content_type="application/json", text=request_data.to_json()
        )

    request_content, response_content = read_request_response_files(request_id)

    if request_content is None and response_content is None:
        return web.Response(status=http.HTTPStatus.NOT_FOUND, content_type="application/json")

    request_data = RequestData(request_id=request_id, request=request_content, response=response_content)

    return web.Response(status=http.HTTPStatus.OK, content_type="application/json", text=request_data.to_json())


async def list_request_ids_handler(request: web.Request) -> web.Response:
    """
    GET /requests

    List all available request IDs.

    Returns:
        JSON response with list of request IDs.
    """
    request_ids = get_all_request_ids()

    request_list = RequestList(request_ids=request_ids, count=len(request_ids))

    return web.Response(status=http.HTTPStatus.OK, content_type="application/json", text=request_list.to_json())


async def proxied_request_handler(request: web.Request) -> web.Response:
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
    runner_state.client_interactions.append(
        ClientInteraction(interaction_type=ClientInteractionType.PROXIED_REQUEST, timestamp=request_timestamp)
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
            trigger=event.generate_client_request_trigger(request, mount_point=MOUNT_POINT, before_serving=True),
            runner_state=runner_state,
            session=session,
            envoy_client=envoy_client,
        )
        await session.commit()

    # Proxy the request to the utility server
    proxy_result = await proxy.proxy_request(
        request=request, remote_url=remote_url, active_test_procedure=active_test_procedure
    )

    # Fire "after request" event trigger (only if an event didn't handle the before event)
    if not trigger_handled:
        async with begin_session() as session:
            trigger_handled = await event.handle_event_trigger(
                trigger=event.generate_client_request_trigger(request, mount_point=MOUNT_POINT, before_serving=False),
                runner_state=runner_state,
                session=session,
                envoy_client=envoy_client,
            )
            await session.commit()

    # There will only ever be a maximum of 1 entry in this list
    # The request events will only trigger a max of one listener
    step_name: str = event.INIT_STAGE_STEP_NAME
    if active_test_procedure.is_started():
        step_name = event.UNMATCHED_STEP_NAME
    if trigger_handled:
        handling_listener = trigger_handled[0]
        step_name = handling_listener.step

    # check any request body for schema validity (assumption being that it's XML)
    body_xml_errors = validate_proxy_request_schema(proxy_result)

    # Generate sequential request ID
    request_id = len(runner_state.request_history)

    # Record in request history
    request_entry = RequestEntry(
        url=remote_url,
        path=relative_url,
        method=http.HTTPMethod(method),
        status=http.HTTPStatus(proxy_result.response.status),
        timestamp=request_timestamp,
        step_name=step_name,
        body_xml_errors=body_xml_errors,
        request_id=request_id,
    )
    runner_state.request_history.append(request_entry)

    # Write request/response files to disk
    write_request_response_files(request_id=request_id, proxy_result=proxy_result, entry=request_entry)

    return proxy_result.response
