import atexit
import json
import logging
import logging.config
import os
from pathlib import Path

from aiohttp import web
from cactus_test_definitions import TestProcedureConfig

from cactus_runner import __version__
from cactus_runner.app import handler
from cactus_runner.app.shared import (
    APPKEY_AGGREGATOR,
    APPKEY_RUNNER_STATE,
    APPKEY_TEST_PROCEDURES,
)
from cactus_runner.models import Aggregator, RunnerState

# SERVER_URL is the URL of envoy or another CSIP-AUS compliant server.
DEFAULT_SERVER_URL = "http://localhost:8000"
SERVER_URL = os.getenv("SERVER_URL", DEFAULT_SERVER_URL)

# APP_HOST is the IP address of cactus runner (aiohttp) application
# See https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.run_app
DEFAULT_APP_HOST = "0.0.0.0"  # This is the aiohttp default
APP_HOST = os.getenv("APP_HOST", DEFAULT_APP_HOST)

# APP_PORT is the port the cactus runner application listens on.
DEFAULT_APP_PORT = 8080  # This is the aiohttp default
APP_PORT = int(os.getenv("APP_PORT", DEFAULT_APP_PORT))

# MOUNT_POINT is the base path for all endpoints
MOUNT_POINT = "/"

# If true skips registering an aggregator at beginning of test procedure
DEV_AGGREGATOR_PREREGISTERED = os.getenv("DEV_AGGREGATOR_PREREGISTERED", "false").lower() in ["true", "1", "t"]

# If true skips applying database preconditions at beginning of test procedure
DEV_SKIP_DB_PRECONDITIONS = os.getenv("DEV_SKIP_DB_PRECONDITIONS", "false").lower() in ["true", "1", "t"]

logger = logging.getLogger(__name__)


def create_app() -> web.Application:
    app = web.Application()

    # Add routes for Test Runner
    app.router.add_route("GET", MOUNT_POINT + "status", handler.status_handler)
    app.router.add_route("GET", MOUNT_POINT + "lastrequest", handler.last_proxied_request_handler)
    app.router.add_route("POST", MOUNT_POINT + "start", handler.start_handler)
    app.router.add_route("POST", MOUNT_POINT + "finalize", handler.finalize_handler)

    # Add catch-all route for proxying all other requests to CSIP-AUS reference server
    app.router.add_route("*", MOUNT_POINT + "{proxyPath:.*}", handler.proxied_request_handler)

    # Set up shared state
    app[APPKEY_AGGREGATOR] = Aggregator()
    app[APPKEY_RUNNER_STATE] = RunnerState()
    app[APPKEY_TEST_PROCEDURES] = TestProcedureConfig.from_resource()

    return app


def setup_logging(logging_config_file: Path):
    with open(logging_config_file) as f:
        config = json.load(f)

    logging.config.dictConfig(config)

    queue_handler = logging.getHandlerByName("queue_handler")
    if queue_handler is not None:
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
