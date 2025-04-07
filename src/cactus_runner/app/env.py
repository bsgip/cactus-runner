import os

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
