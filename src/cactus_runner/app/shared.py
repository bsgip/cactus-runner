import asyncio

from aiohttp import web

from cactus_runner.models import InitialisedCertificates, RunnerState
from cactus_runner.plugin.backends.hookspec import BackendProvider

# aiohttp AppKeys are used to share global state between request handlers
APPKEY_RUNNER_STATE = web.AppKey("runner-state", RunnerState)
APPKEY_INITIALISED_CERTS = web.AppKey("aggregator", InitialisedCertificates)
# APPKEY_ENVOY_ADMIN_CLIENT = web.AppKey("envoy-admin-client", EnvoyAdminClient)
# APPKEY_ENVOY_ADMIN_INIT_KWARGS = web.AppKey("envoy-admin-client-init-kwargs", dict[str, Any])
APPKEY_PERIODIC_TASK = web.AppKey("periodic-task", asyncio.Task[None])
APPKEY_PERIOD_SEC = web.AppKey("period", int)
APPKEY_PROXY_LOCK = web.AppKey("proxy-lock", asyncio.Lock)
APPKEY_BACKEND_PROVIDER = web.AppKey("backend-provider", BackendProvider)
