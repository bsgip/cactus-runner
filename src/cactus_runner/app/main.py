import asyncio
import atexit
import contextlib
import json
import logging
import logging.config
import logging.handlers
import os
import traceback
from http import HTTPStatus
from pathlib import Path

from aiohttp import web
from cactus_test_definitions.client import TestProcedureConfig

from cactus_runner import __version__
from cactus_runner.app import event, handler
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
    APPKEY_ENVOY_ADMIN_CLIENT,
    APPKEY_ENVOY_ADMIN_INIT_KWARGS,
    APPKEY_INITIALISED_CERTS,
    APPKEY_PERIOD_SEC,
    APPKEY_PERIODIC_TASK,
    APPKEY_RUNNER_STATE,
    APPKEY_TEST_PROCEDURES,
)
from cactus_runner.models import InitialisedCertificates, RunnerState

logger = logging.getLogger(__name__)


@web.middleware
async def log_error_middleware(request, handler):
    try:
        response = await handler(request)
        return response
    except web.HTTPException as exc:
        # Handle HTTP exceptions gracefully
        logger.warning(f"HTTP exception: {exc.status} - {exc.reason}")
        raise
    except Exception as exc:
        # Handle uncaught exceptions
        logger.error(f"Uncaught exception: {exc}", exc_info=exc)

        # We are making the conscious decision to report (in great detail) the contents of our internal errors
        # This is NOT typically best practice but there is nothing sensitive being stored on a Runner instance
        # and it allows for more detailed logging at whatever level is orchestrating the runner instance.
        return web.json_response(
            {
                "error": f"Internal Server Error: {type(exc)} {exc}",
                "traceback": traceback.format_exc(),
            },
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )


async def periodic_task(app: web.Application):
    """Periodic task called app[APPKEY_PERIOD_SEC]

    Args:
        app (web.Application): The AIOHTTP application instance.
    """
    while True:
        try:
            runner_state = app[APPKEY_RUNNER_STATE]
            if runner_state.active_test_procedure is not None and not runner_state.active_test_procedure.is_finished():
                async with begin_session() as session:
                    await event.handle_event_trigger(
                        trigger=event.generate_time_trigger(),
                        runner_state=runner_state,
                        session=session,
                        envoy_client=app[APPKEY_ENVOY_ADMIN_CLIENT],
                    )
                    await session.commit()

        except Exception as e:
            # Catch and log uncaught exceptions to prevent periodic task from hanging
            logger.error(f"Uncaught exception in periodic task: {repr(e)}")

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


def generate_admin_client(app: web.Application) -> EnvoyAdminClient:
    init_kwargs = app[APPKEY_ENVOY_ADMIN_INIT_KWARGS]
    return EnvoyAdminClient(**init_kwargs)


async def app_on_startup_handler(app: web.Application) -> None:
    """Handler for on_startup event"""
    app[APPKEY_ENVOY_ADMIN_CLIENT] = generate_admin_client(app)


async def app_on_cleanup_handler(app: web.Application) -> None:
    """Handler for on_cleanup (i.e. after app shutdown) event"""
    await app[APPKEY_ENVOY_ADMIN_CLIENT].close_session()


def create_app() -> web.Application:

    # Ensure the DB connection is up and running before starting the app.
    postgres_dsn = os.getenv("DATABASE_URL")
    if postgres_dsn is None:
        raise Exception("DATABASE_URL environment variable is not specified")
    initialise_database_connection(postgres_dsn)

    app = web.Application(middlewares=[log_error_middleware])

    # Add routes for Test Runner
    mount = MOUNT_POINT.rstrip("/") + "/" if MOUNT_POINT else "/"  # Ensure MOUNT_POINT ends with / for concatenation
    app.router.add_route("GET", mount + "health", handler.health_handler)
    app.router.add_route("GET", mount + "status", handler.status_handler)
    app.router.add_route("POST", mount + "init", handler.init_handler)
    app.router.add_route("POST", mount + "start", handler.start_handler)
    app.router.add_route("POST", mount + "finalize", handler.finalize_handler)

    # For retrieving request logs
    app.router.add_route("GET", mount + "request/{request_id}", handler.get_request_raw_data_handler)
    app.router.add_route("GET", mount + "requests", handler.list_request_ids_handler)

    # Add catch-all route for proxying all other requests to CSIP-AUS reference server
    app.router.add_route("*", mount + "{proxyPath:.*}", handler.proxied_request_handler)

    # Set up shared state
    app[APPKEY_INITIALISED_CERTS] = InitialisedCertificates()
    app[APPKEY_RUNNER_STATE] = RunnerState()
    app[APPKEY_TEST_PROCEDURES] = TestProcedureConfig.from_resource()
    app[APPKEY_ENVOY_ADMIN_INIT_KWARGS] = {
        "base_url": ENVOY_ADMIN_URL,
        "auth_params": EnvoyAdminClientAuthParams(
            username=ENVOY_ADMIN_BASICAUTH_USERNAME, password=ENVOY_ADMIN_BASICAUTH_PASSWORD
        ),
    }

    # App events
    app.on_startup.append(app_on_startup_handler)  # type: ignore # Something has broken with type defs
    app.on_cleanup.append(app_on_cleanup_handler)  # type: ignore # Something has broken with type defs

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
    try:
        setup_logging(logging_config_file=Path("config/logging/config.json"))
    except Exception as exc:
        logger.error("Error configuring logging", exc_info=exc)
    logger.info(f"Cactus Runner (version={__version__})")
    logger.info(f"{APP_HOST=} {APP_PORT=}")
    logger.info(f"Proxying requests to '{SERVER_URL}'")

    app = create_app()

    return app


app = create_app_with_logging()

if __name__ == "__main__":
    web.run_app(app, port=APP_PORT)
