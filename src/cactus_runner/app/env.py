import os

# SERVER_URL is the URL of envoy or another CSIP-AUS compliant server.
DEFAULT_SERVER_URL = "http://localhost:8000"
SERVER_URL = os.getenv("SERVER_URL", DEFAULT_SERVER_URL)

# Any traffic with this path prefix will be redirected to the SERVER_URL with the ENVOY_PROXY_PREFIX stripped.
# If set to a value (eg "/envoy/api/") a request for dcap would need to sent to runner as "/envoy/api/dcap" and would
# be received at envoy as "/dcap".
ENVOY_PROXY_PREFIX: str = os.getenv("ENVOY_PROXY_PREFIX", "/").strip()
if ENVOY_PROXY_PREFIX != "/":
    ENVOY_PROXY_PREFIX = ENVOY_PROXY_PREFIX.rstrip("/")
if not ENVOY_PROXY_PREFIX.startswith("/"):
    ENVOY_PROXY_PREFIX = "/" + ENVOY_PROXY_PREFIX

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
MOUNT_POINT = os.getenv("MOUNT_POINT", "/").strip()
if MOUNT_POINT != "/":
    MOUNT_POINT = MOUNT_POINT.rstrip("/")
if not MOUNT_POINT.startswith("/"):
    MOUNT_POINT = "/" + MOUNT_POINT

# If true skips verifying the forwarded certificate in requests
DEV_SKIP_AUTHORIZATION_CHECK = os.getenv("DEV_SKIP_AUTHORIZATION_CHECK", "false").lower() in ["true", "1", "t"]

# Request header to perform certificate verifications against
CERT_HEADER = os.getenv("CERT_HEADER", "ssl-client-cert")

# Maximum number of request/response pairs kept on disk at any one time (rolling window)
MAX_REQUEST_PAIRS = int(os.getenv("MAX_REQUEST_PAIRS", "5000"))

# Maximum bytes copied from each log file into the ZIP archive (tail of file).
# Default 32 MB. Prevents huge log files from bloating the archive on long tests.
MAX_LOG_FILE_BYTES = int(os.getenv("MAX_LOG_FILE_BYTES", str(32 * 1024 * 1024)))

# Storage extension media type header, values only allowed when an `accept` or `content-type` header is provided.
HEADER_MEDIA_TYPE = os.getenv("HEADER_MEDIA_TYPE", "application/sep+xml")
HEADER_MEDIA_PARAM_NAME = os.getenv("HEADER_MEDIA_PARAM_NAME", "csipaus")
HEADER_MEDIA_PARAM_VALUE = os.getenv("HEADER_MEDIA_PARAM_VALUE", "1.3")

HEADER_MEDIA_ALL = f"{HEADER_MEDIA_TYPE}; {HEADER_MEDIA_PARAM_NAME}={HEADER_MEDIA_PARAM_VALUE}"
