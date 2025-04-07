import http
import logging
import logging.config
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import client, web
from cactus_test_definitions import (
    Action,
    Event,
)
from envoy.server.api.depends.lfdi_auth import LFDIAuthDepends

from cactus_runner import __version__
from cactus_runner.app import auth, precondition
from cactus_runner.app.env import (
    DEV_AGGREGATOR_PREREGISTERED,
    DEV_SKIP_DB_PRECONDITIONS,
    SERVER_URL,
)
from cactus_runner.app.precondition import DATABASE_URL
from cactus_runner.app.shared import (
    APPKEY_AGGREGATOR,
    APPKEY_RUNNER_STATE,
    APPKEY_TEST_PROCEDURES,
)
from cactus_runner.models import (
    ActiveTestProcedure,
    ActiveTestProcedureStatus,
    LastProxiedRequest,
    Listener,
    StepStatus,
)

logger = logging.getLogger(__name__)


class UnknownActionError(Exception):
    """Unknown Cactus Runner Action"""


class DatabaseDumpError(Exception):
    pass


async def start_handler(request: web.Request):
    active_test_procedure = request.app[APPKEY_RUNNER_STATE].active_test_procedure
    test_procedures = request.app[APPKEY_TEST_PROCEDURES]

    # We cannot start another test procedure if one is already running
    if active_test_procedure is not None:
        return web.Response(
            status=http.HTTPStatus.CONFLICT,
            text=f"Test Procedure ({active_test_procedure.name}) already in progress. Starting another test procedure is not permitted.",
        )

    # Get the name of the test procedure from the query parameter
    requested_test_procedure = request.query["test"]
    if requested_test_procedure is None:
        return web.Response(status=http.HTTPStatus.BAD_REQUEST, text="Missing 'test' query parameter.")

    # Get the certificate of the aggregator to register
    aggregator_certificate = request.query["certificate"]
    if aggregator_certificate is None:
        return web.Response(status=http.HTTPStatus.BAD_REQUEST, text="Missing 'certificate' query parameter.")

    # Get the lfdi of the aggregator to register
    aggregator_lfdi = LFDIAuthDepends.generate_lfdi_from_pem(aggregator_certificate)
    if not DEV_AGGREGATOR_PREREGISTERED:
        precondition.register_aggregator(lfdi=aggregator_lfdi)
    else:
        logger.warning("Skipping aggregator registration ('DEV_AGGREGATOR_PREREGISTERED' environment variable is True)")

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
            text=f"Expected valid test procedure for 'test' query parameter. Received '/start=?test={requested_test_procedure}'",
        )

    # Create listeners for all test procedure events
    listeners = []
    for step_name, step in definition.steps.items():
        listeners.append(
            Listener(step=step_name, event=step.event, actions=step.actions, enabled=step.listener_enabled)
        )

    # Set 'active_test_procedure' to the requested test procedure
    active_test_procedure = ActiveTestProcedure(
        name=requested_test_procedure,
        definition=definition,
        listeners=listeners,
        step_status={step: StepStatus.PENDING for step in definition.steps.keys()},
    )

    # Get the database into the correct state for the test procedure
    if DEV_SKIP_DB_PRECONDITIONS:
        logger.warning("Skipping database preconditions ('DEV_SKIP_DB_PRECONDITIONS' environment variable is True)")
    else:
        db_precondition = active_test_procedure.definition.preconditions.db
        precondition.apply_db_precondition(precondition=db_precondition)

    logger.info(
        f"Test Procedure '{active_test_procedure.name}' started",
        extra={"test_procedure": active_test_procedure.name},
    )

    request.app[APPKEY_RUNNER_STATE].active_test_procedure = active_test_procedure

    return web.Response(status=http.HTTPStatus.CREATED, text="Test Procedure Started")


def finalize_zip_contents(json_status_summary: str) -> bytes:
    """Returns the contents of the zipped test procedures artifacts in bytes"""
    # Work in a temporary directory
    with tempfile.TemporaryDirectory() as tempdirname:
        base_path = Path(tempdirname)

        # All the test procedure artifacts should be placed in `archive_dir` to be archived
        archive_dir = base_path / "archive"
        os.mkdir(archive_dir)

        # Create test summary json file
        file_path = archive_dir / "test_procedure_summary.json"
        with open(file_path, "w") as f:
            f.write(json_status_summary)

        # Copy Cactus Runner log file into archive
        source = "logs/cactus_runner.jsonl"
        destination = archive_dir / "cactus_runner.jsonl"
        shutil.copyfile(source, destination)

        # Create db dump
        if DATABASE_URL is None:
            raise DatabaseDumpError("DATABASE_URL environment variable not set")
        else:
            connection_string = DATABASE_URL.replace("+psycopg", "")
            dump_file = str(archive_dir / "envoy_db.dump")
            exectuable_name = "pg_dump"
            command = [
                exectuable_name,
                f"--dbname={connection_string}",
                "-f",
                dump_file,
                "--data-only",
                "--inserts",
                "--no-password",
            ]
            try:
                subprocess.run(command)
            except FileNotFoundError:
                logger.error(
                    f"Unable to create database snapshot ('{exectuable_name}' executable not found). Did you forget to install 'postgresql-client'?"
                )

        # Create the temporary zip file
        ARCHIVE_BASEFILENAME = "finalize"
        ARCHIVE_KIND = "zip"
        shutil.make_archive(str(base_path / ARCHIVE_BASEFILENAME), ARCHIVE_KIND, archive_dir)

        # Read the zip file contents as binary
        archive_path = base_path / f"{ARCHIVE_BASEFILENAME}.{ARCHIVE_KIND}"
        with open(archive_path, mode="rb") as f:
            zip_contents = f.read()
    return zip_contents


def finalize_response(json_status_summary: str) -> web.Response:
    """Creates a finalize test procedure response which includes the test procedure artifacts in zip format"""
    zip_contents = finalize_zip_contents(json_status_summary=json_status_summary)

    SUGGESTED_FILENAME = "finalize.zip"
    return web.Response(
        body=zip_contents,
        headers={
            "Content-Type": "application/zip",
            "Content-Disposition": f"attachment; filename={SUGGESTED_FILENAME}",
        },
    )


async def finalize_handler(request):
    active_test_procedure = request.app[APPKEY_RUNNER_STATE].active_test_procedure

    if active_test_procedure is not None:
        finalized_test_procedure_name = active_test_procedure.name
        json_status_summary = status_from_active_test_procedure(active_test_procedure=active_test_procedure).to_json()

        # Clear the active test procedure
        request.app[APPKEY_RUNNER_STATE].active_test_procedure = None

        logger.info(
            f"Test Procedure '{finalized_test_procedure_name}' finalized",
            extra={"test_procedure": finalized_test_procedure_name},
        )

        return finalize_response(json_status_summary=json_status_summary)
    else:
        return web.Response(
            status=http.HTTPStatus.BAD_REQUEST,
            text="ERROR: Unable to finalize test procedure. No test procedure in progress.",
        )


def status_from_active_test_procedure(active_test_procedure: ActiveTestProcedure) -> ActiveTestProcedureStatus:

    # Determine status summary
    completed_steps = sum(s == StepStatus.RESOLVED for s in active_test_procedure.step_status.values())
    steps = len(active_test_procedure.step_status)
    status_summary = f"{completed_steps}/{steps} steps complete."

    return ActiveTestProcedureStatus(
        test_procedure_name=active_test_procedure.name,
        status_summary=status_summary,
        step_status=active_test_procedure.step_status,
    )


async def status_handler(request):
    active_test_procedure = request.app[APPKEY_RUNNER_STATE].active_test_procedure

    logger.info("Test procedure status requested.")

    if active_test_procedure is not None:
        status = status_from_active_test_procedure(active_test_procedure=active_test_procedure)
        logger.info(
            f"Status of test procedure '{status.test_procedure_name}': {status.step_status}",
            extra={"test_procedure": status.test_procedure_name},
        )

    else:
        status = ActiveTestProcedureStatus(
            test_procedure_name="-", status_summary="No test procedure running", step_status={}
        )
        logger.warning("Status of non-existent test procedure requested.")

    return web.Response(status=http.HTTPStatus.OK, content_type="application/json", text=status.to_json())


async def last_proxied_request_handler(request):
    logger.info("Last proxied request requested.")

    # TODO `last_request` shouldn't be hard-coded.
    last_request = LastProxiedRequest(endpoint="/dcap", status=http.HTTPStatus.OK, timestamp=datetime.now(timezone.utc))

    return web.Response(status=http.HTTPStatus.OK, content_type="application/json", text=last_request.to_json())


def apply_action(action: Action, active_test_procedure: ActiveTestProcedure):

    match action.type:
        case "enable-listeners":
            steps_to_enable = action.parameters["listeners"]
            for listener in active_test_procedure.listeners:
                if listener.step in steps_to_enable:
                    logger.info(f"Enabling listener: {listener}")
                    listener.enabled = True
                    steps_to_enable.remove(listener.step)

            # Warn about any unmatched steps
            if steps_to_enable:
                logger.warning(
                    f"Unable to enable the listeners for the following steps, ({steps_to_enable}). These are not recognised steps in the '{active_test_procedure.name} test procedure"
                )
        case "remove-listeners":
            steps_to_disable = action.parameters["listeners"]
            for listener in active_test_procedure.listeners:
                if listener.step in steps_to_disable:
                    logger.info(f"Remove listener: {listener}")
                    active_test_procedure.listeners.remove(listener)
                    steps_to_disable.remove(listener.step)

            # Warn about any unmatched steps
            if steps_to_disable:
                logger.warning(
                    f"Unable to remove the listener from the following steps, ({steps_to_disable}). These are not recognised steps in the '{active_test_procedure.name}' test procedure"
                )
        case _:
            raise UnknownActionError(f"Unrecognised action '{action}'")


def handle_event(event: Event, active_test_procedure: ActiveTestProcedure) -> Listener | None:

    # Check all listeners
    for listener in active_test_procedure.listeners:
        # Did any of the current listeners match?
        if listener.enabled and listener.event == event:
            logger.info(f"Event matched: {event=}")

            # Perform actions associated with event
            for action in listener.actions:
                logger.info(f"Executing action: {action=}")
                apply_action(action=action, active_test_procedure=active_test_procedure)

            return listener

    return None


async def proxied_request_handler(request):
    if not auth.request_is_authorized(request=request):
        return web.Response(
            status=http.HTTPStatus.FORBIDDEN, text="Forwarded certificate does not match for registered aggregator"
        )

    active_test_procedure = request.app[APPKEY_RUNNER_STATE].active_test_procedure

    proxy_path = request.match_info.get("proxyPath", "No proxyPath placeholder defined")
    local_path = request.rel_url.path_qs
    remote_url = SERVER_URL + local_path

    logger.debug(f"{proxy_path=} {local_path=} {remote_url=}")

    if active_test_procedure is not None:
        # Update the progress of the test procedure
        request_event = Event(type="request-received", parameters={"endpoint": f"/{proxy_path}"})
        listener = handle_event(event=request_event, active_test_procedure=active_test_procedure)

        # The assumes each step only has one event and once the action associated with the event
        # has been handled the step is "complete"
        if listener is not None:
            active_test_procedure.step_status[listener.step] = StepStatus.RESOLVED

    # Forward the request to the reference server
    async with client.request(
        request.method, remote_url, headers=request.headers.copy(), allow_redirects=False, data=await request.read()
    ) as response:
        headers = response.headers.copy()
        body = await response.read()
        return web.Response(headers=headers, status=response.status, body=body)
