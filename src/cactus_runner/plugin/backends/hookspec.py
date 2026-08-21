import os

import apluggy

from cactus_runner.app.database import begin_session, initialise_database_connection
from cactus_runner.app.env import ENVOY_ADMIN_BASICAUTH_PASSWORD, ENVOY_ADMIN_BASICAUTH_USERNAME, ENVOY_ADMIN_URL
from cactus_runner.plugin.backends.common import RunnerBackend
from cactus_runner.plugin.backends.envoy import EnvoyAdminClient, EnvoyBackend
from cactus_runner.plugin.backends.envoy.admin_client import EnvoyAdminClientAuthParams
from cactus_runner.plugin.backends.models import RunnerBackendTestContext

project_name = "cactus_runner.backend"
hookspec = apluggy.HookspecMarker(project_name)
hookimpl = apluggy.HookimplMarker(project_name)


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

    @hookspec
    async def startup(self) -> None:
        """Run any startup checks necessary before the application should start.

        e.g. DB connection exists. Raise errors on issue to stop application from starting
        """
        ...

    @hookspec
    async def shutdown(self) -> None:
        """Called as part of the final cleanup handler."""
        ...


class DefaultEnvoyPlugin:
    """The default plugin implementation for the project."""

    def __init__(self, admin_client: EnvoyAdminClient | None = None) -> None:
        self._admin_client = admin_client or EnvoyAdminClient(
            base_url=ENVOY_ADMIN_URL,
            auth_params=EnvoyAdminClientAuthParams(
                username=ENVOY_ADMIN_BASICAUTH_USERNAME, password=ENVOY_ADMIN_BASICAUTH_PASSWORD
            ),
        )

    @hookimpl(trylast=True)
    async def create_backend(self, context: RunnerBackendTestContext | None) -> RunnerBackend:
        """Simply returns a created EnvoyBackend instance."""
        return EnvoyBackend(session_factory=begin_session, admin_client=self._admin_client, test_context=context)

    @hookimpl(trylast=True)
    async def startup(self) -> None:
        # Ensure the DB connection is up and running before starting the app.
        postgres_dsn = os.getenv("DATABASE_URL")
        if postgres_dsn is None:
            raise Exception("DATABASE_URL environment variable is not specified")
        initialise_database_connection(postgres_dsn)

    @hookimpl(trylast=True)
    async def shutdown(self) -> None:
        """Closes the admin client session."""
        await self._admin_client.close_session()


class BackendProvider:
    def __init__(self, plugin_manager: apluggy.PluginManager) -> None:
        self._plugin_manager = plugin_manager

    async def create_backend(self, context: RunnerBackendTestContext | None) -> RunnerBackend:
        backends: list[RunnerBackend] | None = await self._plugin_manager.ahook.create_backend(context=context)

        if not backends:
            raise RuntimeError("No backend plugin available")

        return backends[0]

    async def startup(self) -> None:
        """Called at beginning of application creation.

        Exception should be raised if deemed not ok to proceed.
        """
        await self._plugin_manager.ahook.startup()

    async def shutdown(self) -> None:
        """Called at final shutdown of the backend provider."""
        await self._plugin_manager.ahook.shutdown()
