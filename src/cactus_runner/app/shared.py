import asyncio
from typing import Any

from aiohttp import web
from cactus_test_definitions import TestProcedures

from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.models import Aggregator, RunnerState

# aiohttp AppKeys are used to share global state between request handlers
APPKEY_TEST_PROCEDURES = web.AppKey("test-procedures", TestProcedures)
APPKEY_RUNNER_STATE = web.AppKey("runner-state", RunnerState)
APPKEY_AGGREGATOR = web.AppKey("aggregator", Aggregator)
APPKEY_ENVOY_ADMIN_CLIENT = web.AppKey("envoy-admin-client", EnvoyAdminClient)
APPKEY_ENVOY_ADMIN_INIT_KWARGS = web.AppKey("envoy-admin-client-init-kwargs", dict[str, Any])
APPKEY_PERIODIC_TASK = web.AppKey("periodic-task", asyncio.Task[None])
APPKEY_PERIOD_SEC = web.AppKey("period", int)
