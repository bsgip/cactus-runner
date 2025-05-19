import os

# SERVER_URL is the URL of envoy or another CSIP-AUS compliant server.
DEFAULT_SERVER_URL = "http://localhost:8000"
SERVER_URL = os.getenv("SERVER_URL", DEFAULT_SERVER_URL)

# envoy-admin configurations - this is the upstream admin api for manipulating envoy-db.
DEFAULT_ENVOY_ADMIN_URL = "http://localhost:8001"
ENVOY_ADMIN_URL = os.getenv("ENVOY_ADMIN_URL", DEFAULT_ENVOY_ADMIN_URL)
ENVOY_ADMIN_BASICAUTH_USERNAME = os.environ["ENVOY_ADMIN_BASICAUTH_USERNAME"]
ENVOY_ADMIN_BASICAUTH_PASSWORD = os.environ["ENVOY_ADMIN_BASICAUTH_PASSWORD"]

# APP_HOST is the IP address of cactus runner (aiohttp) application
# See https://docs.aiohttp.org/en/stable/web_reference.html#aiohttp.web.run_app
DEFAULT_APP_HOST = "127.0.0.1"  # This is the aiohttp default
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

# If true skips verifying the forwarded certificate in requests
DEV_SKIP_AUTHORIZATION_CHECK = os.getenv("DEV_SKIP_AUTHORIZATION_CHECK", "false").lower() in ["true", "1", "t"]
