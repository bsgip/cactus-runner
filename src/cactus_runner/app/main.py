import atexit
import json
import logging
import logging.config
import logging.handlers
import os
from pathlib import Path

from aiohttp import web
from cactus_test_definitions import TestProcedureConfig

from cactus_runner import __version__
from cactus_runner.app import handler
from cactus_runner.app.database import initialise_database_connection
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
    APPKEY_RUNNER_STATE,
    APPKEY_TEST_PROCEDURES,
)
from cactus_runner.models import Aggregator, RunnerState

logger = logging.getLogger(__name__)


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
