from collections.abc import Callable

import apluggy
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.database import begin_session
from cactus_runner.app.env import ENVOY_ADMIN_BASICAUTH_PASSWORD, ENVOY_ADMIN_BASICAUTH_USERNAME, ENVOY_ADMIN_URL
from cactus_runner.plugin.backends.common import RunnerBackend
from cactus_runner.plugin.backends.envoy import EnvoyAdminClient, EnvoyBackend
from cactus_runner.plugin.backends.envoy.admin_client import EnvoyAdminClientAuthParams
from cactus_runner.plugin.backends.models import RunnerBackendTestContext

project_name = "cactus_runner.backend"
hookspec = apluggy.HookspecMarker(project_name)
hookimpl = apluggy.HookimplMarker(project_name)


# TEST: temporary implementation only
def create_backend(session_factory: Callable[..., AsyncSession], envoy_client: EnvoyAdminClient) -> RunnerBackend:
    """TEMPORARY IMPLEMENTATION ONLY.

    Factory producing an EnvoyBackend. It will be modified to become the
    hookspec and form the entrypoint for hte plugin architecture.
    """
    return EnvoyBackend(session_factory=session_factory, admin_client=envoy_client)


def create_plugin_manager() -> apluggy.PluginManager:
    pm = apluggy.PluginManager(project_name)
    pm.add_hookspecs(BackendSpec)

    # Built-in default backend
    pm.register(DefaultEnvoyPlugin())

    return pm


class BackendSpec:
    @hookspec
    async def create_backend(self, context: RunnerBackendTestContext) -> RunnerBackend:  # type: ignore
        """Called via handlers to initiate a backend.

        Args:
            context: Full execution context for this test run (eg. test definition, client lfdi etc.)
        """
        ...


class DefaultEnvoyPlugin:
    def __init_(self) -> None:
        self._admin_client = EnvoyAdminClient(
            base_url=ENVOY_ADMIN_URL,
            auth_params=EnvoyAdminClientAuthParams(
                username=ENVOY_ADMIN_BASICAUTH_USERNAME, password=ENVOY_ADMIN_BASICAUTH_PASSWORD
            ),
        )

    @hookimpl(trylast=True)
    async def create_backend(self, context: RunnerBackendTestContext) -> RunnerBackend:
        return EnvoyBackend(session_factory=begin_session, admin_client=self._admin_client, test_context=context)


class BackendProvider:
    def __init__(self, plugin_manager: apluggy.PluginManager) -> None:
        self._plugin_manager = plugin_manager

    async def create_backend(self, context: RunnerBackendTestContext) -> RunnerBackend:
        backends: list[RunnerBackend] | None = await self._plugin_manager.ahook.create_backend(context=context)

        if not backends:
            raise RuntimeError("No backend plugin available")

        return backends[0]
