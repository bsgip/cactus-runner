import asyncio
import atexit
import contextlib
import json
import logging
import logging.config
import logging.handlers
import traceback
from collections.abc import AsyncGenerator
from http import HTTPStatus
from pathlib import Path

from aiohttp import web
from aiohttp.typedefs import Handler
from cactus_schema.runner import uri

from cactus_runner import __version__
from cactus_runner.app import event, handler
from cactus_runner.app.env import (
    APP_HOST,
    APP_PORT,
    ENVOY_PROXY_PREFIX,
    MOUNT_POINT,
    SERVER_URL,
)
from cactus_runner.app.shared import (
    APPKEY_BACKEND_PROVIDER,
    APPKEY_INITIALISED_CERTS,
    APPKEY_PERIOD_SEC,
    APPKEY_PERIODIC_TASK,
    APPKEY_PROXY_LOCK,
    APPKEY_RUNNER_STATE,
)
from cactus_runner.app.uri import uri_path_join
from cactus_runner.models import InitialisedCertificates, RunnerState
from cactus_runner.plugin.backends.context import generate_plugin_context
from cactus_runner.plugin.backends.hookspec import BackendProvider, create_plugin_manager

logger = logging.getLogger(__name__)


@web.middleware
async def log_error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
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


async def periodic_task(app: web.Application) -> None:
    """Periodic task called app[APPKEY_PERIOD_SEC]

    Args:
        app (web.Application): The AIOHTTP application instance.
    """
    while True:
        try:
            runner_state = app[APPKEY_RUNNER_STATE]
            if runner_state.active_test_procedure is not None and not runner_state.active_test_procedure.is_finished():
                provider = app[APPKEY_BACKEND_PROVIDER]
                backend = await provider.create_backend(
                    context=generate_plugin_context(runner_state.active_test_procedure)
                )
                await event.handle_event_trigger(
                    trigger=event.generate_time_trigger(),
                    runner_state=runner_state,
                    backend=backend,
                )

        except Exception as e:
            # Catch and log uncaught exceptions to prevent periodic task from hanging
            logger.error(f"Uncaught exception in periodic task: {repr(e)}")

        period = app[APPKEY_PERIOD_SEC]
        await asyncio.sleep(period)


async def setup_periodic_task(app: web.Application) -> AsyncGenerator[None, None]:
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
    app[APPKEY_BACKEND_PROVIDER] = BackendProvider(create_plugin_manager())
    await app[APPKEY_BACKEND_PROVIDER].startup()


async def app_on_cleanup_handler(app: web.Application) -> None:
    """Handler for on_cleanup (i.e. after app shutdown) event"""
    await app[APPKEY_BACKEND_PROVIDER].shutdown()


def create_app() -> web.Application:

    app = web.Application(middlewares=[log_error_middleware])

    # Add routes for Test Runner
    app.router.add_route("GET", uri_path_join(MOUNT_POINT, uri.Health), handler.health_handler)
    app.router.add_route("GET", uri_path_join(MOUNT_POINT, uri.Status), handler.status_handler)
    app.router.add_route("POST", uri_path_join(MOUNT_POINT, uri.Initialise), handler.initialise_handler)
    app.router.add_route("POST", uri_path_join(MOUNT_POINT, uri.Start), handler.start_handler)
    app.router.add_route("POST", uri_path_join(MOUNT_POINT, uri.Finalize), handler.finalize_handler)
    app.router.add_route("POST", uri_path_join(MOUNT_POINT, uri.NextTest), handler.next_test_handler)

    # For retrieving request logs
    app.router.add_route("GET", uri_path_join(MOUNT_POINT, uri.Request), handler.get_request_raw_data_handler)
    app.router.add_route("GET", uri_path_join(MOUNT_POINT, uri.RequestList), handler.list_request_ids_handler)

    # For manual 'proceed' signal sent from UI
    app.router.add_route("GET", uri_path_join(MOUNT_POINT, uri.Proceed), handler.proceed_handler)

    # For the well-known file route - must ALWAYS be accessible via root path
    app.router.add_route("GET", uri.CSIPAusWellKnown, handler.csipaus_wellknown_handler)

    # Add catch-all route for proxying all other requests to CSIP-AUS reference server
    app.router.add_route(
        "*", uri_path_join(MOUNT_POINT, ENVOY_PROXY_PREFIX, "/{proxyPath:.*}"), handler.proxied_request_handler
    )

    # Set up shared state
    app[APPKEY_INITIALISED_CERTS] = InitialisedCertificates()
    app[APPKEY_RUNNER_STATE] = RunnerState()

    # App events
    app.on_startup.append(app_on_startup_handler)
    app.on_cleanup.append(app_on_cleanup_handler)

    app[APPKEY_PROXY_LOCK] = asyncio.Lock()

    DEFAULT_PERIOD_SEC = 10  # noqa: N806  # seconds
    app[APPKEY_PERIOD_SEC] = DEFAULT_PERIOD_SEC  # Frequency of periodic task

    # Start the periodic task
    app.cleanup_ctx.append(setup_periodic_task)

    return app


def setup_logging(logging_config_file: Path) -> None:
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
