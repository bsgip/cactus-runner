import asyncio
import atexit
import contextlib
import json
import logging
import logging.config
import logging.handlers
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web
from cactus_test_definitions import TestProcedureConfig

from cactus_runner import __version__
from cactus_runner.app import action, handler
from cactus_runner.app.database import begin_session, initialise_database_connection
from cactus_runner.app.env import (
    APP_HOST,
    APP_PORT,
    ENVOY_ADMIN_BASICAUTH_PASSWORD,
    ENVOY_ADMIN_BASICAUTH_USERNAME,
    ENVOY_ADMIN_URL,
    MOUNT_POINT,
    SERVER_URL,
)
from cactus_runner.app.envoy_admin_client import (
    EnvoyAdminClient,
    EnvoyAdminClientAuthParams,
)
from cactus_runner.app.shared import (
    APPKEY_AGGREGATOR,
    APPKEY_ENVOY_ADMIN_CLIENT,
    APPKEY_ENVOY_ADMIN_INIT_KWARGS,
    APPKEY_PERIOD_SEC,
    APPKEY_PERIODIC_TASK,
    APPKEY_RUNNER_STATE,
    APPKEY_TEST_PROCEDURES,
)
from cactus_runner.models import Aggregator, RunnerState, StepStatus

logger = logging.getLogger(__name__)


class WaitEventError(Exception):
    """Custom exception for wait event errors."""


async def periodic_task(app: web.Application):
    """Periodic task called app[APPKEY_PERIOD_SEC]

    Checks for any expired wait events on enabled listeners
    and triggers their actions.

    Args:
        app (web.Application): The AIOHTTP application instance.

    Raises:
        WaitEventError: If the wait event is missing a start timestamp or duration.
    """
    while True:
        active_test_procedure = app[APPKEY_RUNNER_STATE].active_test_procedure
        if active_test_procedure:
            now = datetime.now(timezone.utc)

            # Loop over enabled listeners with (active) wait events
            for listener in active_test_procedure.listeners:
                if listener.enabled and listener.event.type == "wait":
                    try:
                        wait_start = listener.event.parameters["wait_start_timestamp"]
                    except KeyError:
                        raise WaitEventError("Wait event missing start timestamp ('wait_start_timestamp')")
                    try:
                        wait_duration_sec = listener.event.parameters["duration_seconds"]
                    except KeyError:
                        raise WaitEventError("Wait event missing duration ('duration_seconds')")

                    # Determine if any wait periods have expired
                    if now - wait_start >= timedelta(seconds=wait_duration_sec):
                        # Apply actions
                        async with begin_session() as session:
                            await action.apply_actions(
                                session=session,
                                listener=listener,
                                active_test_procedure=active_test_procedure,
                                envoy_client=app[APPKEY_ENVOY_ADMIN_CLIENT],
                            )

                        # Update step status
                        active_test_procedure.step_status[listener.step] = StepStatus.RESOLVED

        period = app[APPKEY_PERIOD_SEC]
        await asyncio.sleep(period)


async def setup_periodic_task(app: web.Application):
    """Setup periodic task.

    The periodic task is accessible through app[APPKEY_PERIODIC_TASKS].
    The code for the task is defined in the function 'periodic_task'.
    """
    app[APPKEY_PERIODIC_TASK] = asyncio.create_task(periodic_task(app))

    yield

    app[APPKEY_PERIODIC_TASK].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app[APPKEY_PERIODIC_TASK]


async def app_on_startup_handler(app: web.Application) -> None:
    """Handler for on_startup event"""
    init_kwargs = app[APPKEY_ENVOY_ADMIN_INIT_KWARGS]
    app[APPKEY_ENVOY_ADMIN_CLIENT] = EnvoyAdminClient(**init_kwargs)


async def app_on_cleanup_handler(app: web.Application) -> None:
    """Handler for on_cleanup (i.e. after app shutdown) event"""
    await app[APPKEY_ENVOY_ADMIN_CLIENT].close_session()


def create_app() -> web.Application:

    # Ensure the DB connection is up and running before starting the app.
    postgres_dsn = os.getenv("DATABASE_URL")
    if postgres_dsn is None:
        raise Exception("DATABASE_URL environment variable is not specified")
    initialise_database_connection(postgres_dsn)

    app = web.Application()

    # Add routes for Test Runner
    app.router.add_route("GET", MOUNT_POINT + "status", handler.status_handler)
    app.router.add_route("POST", MOUNT_POINT + "init", handler.init_handler)
    app.router.add_route("POST", MOUNT_POINT + "start", handler.start_handler)
    app.router.add_route("POST", MOUNT_POINT + "finalize", handler.finalize_handler)

    # Add catch-all route for proxying all other requests to CSIP-AUS reference server
    app.router.add_route("*", MOUNT_POINT + "{proxyPath:.*}", handler.proxied_request_handler)

    # Set up shared state
    app[APPKEY_AGGREGATOR] = Aggregator()
    app[APPKEY_RUNNER_STATE] = RunnerState()
    app[APPKEY_TEST_PROCEDURES] = TestProcedureConfig.from_resource()
    app[APPKEY_ENVOY_ADMIN_INIT_KWARGS] = {
        "base_url": ENVOY_ADMIN_URL,
        "auth_params": EnvoyAdminClientAuthParams(
            username=ENVOY_ADMIN_BASICAUTH_USERNAME, password=ENVOY_ADMIN_BASICAUTH_PASSWORD
        ),
    }

    # App events
    app.on_startup.append(app_on_startup_handler)
    app.on_cleanup.append(app_on_cleanup_handler)

    DEFAULT_PERIOD_SEC = 10  # seconds
    app[APPKEY_PERIOD_SEC] = DEFAULT_PERIOD_SEC  # Frequency of periodic task

    # Start the periodic task
    app.cleanup_ctx.append(setup_periodic_task)

    return app


def setup_logging(logging_config_file: Path):
    with open(logging_config_file) as f:
        config = json.load(f)

    logging.config.dictConfig(config)

    queue_handler = logging.getHandlerByName("queue_handler")
    if isinstance(queue_handler, logging.handlers.QueueHandler):
        if queue_handler.listener is not None:
            queue_handler.listener.start()
            atexit.register(queue_handler.listener.stop)


def create_app_with_logging() -> web.Application:
    setup_logging(logging_config_file=Path("config/logging/config.json"))
    logger.info(f"Cactus Runner (version={__version__})")
    logger.info(f"{APP_HOST=} {APP_PORT=}")
    logger.info(f"Proxying requests to '{SERVER_URL}'")

    app = create_app()

    return app


app = create_app_with_logging()

if __name__ == "__main__":
    web.run_app(app, port=APP_PORT)
