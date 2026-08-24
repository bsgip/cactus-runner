from unittest.mock import AsyncMock, Mock, patch

import apluggy
import pytest

from cactus_runner.plugin.backends.hookspec import (
    BackendProvider,
    BackendSpec,
    DefaultEnvoyPlugin,
    create_plugin_manager,
    hookimpl,
    project_name,
)


@pytest.mark.anyio
async def test_default_envoy_plugin_startup_initialises_database(monkeypatch):
    """Verifies that startup() initialises the database connection using the
    DATABASE_URL environment variable.

    This ensures the default plugin performs its required application startup
    work and passes the configured DSN through to
    initialise_database_connection().
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://test")

    plugin = DefaultEnvoyPlugin(admin_client=Mock())

    with patch(
        "cactus_runner.plugin.backends.hookspec.initialise_database_connection"
    ) as mock_initialise:
        await plugin.startup()

    mock_initialise.assert_called_once_with("postgresql://test")


@pytest.mark.anyio
async def test_default_envoy_plugin_startup_requires_database_url(monkeypatch):
    """Verifies that startup() fails when DATABASE_URL is not configured.

    The default plugin should refuse to start if no database connection string
    is available, preventing the application from running in a misconfigured
    state.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)

    plugin = DefaultEnvoyPlugin(admin_client=Mock())

    with pytest.raises(Exception, match="DATABASE_URL"):
        await plugin.startup()


@pytest.mark.anyio
async def test_default_envoy_plugin_shutdown_closes_admin_client():
    """Verifies that shutdown() closes the configured admin client session.

    This ensures backend resources are cleaned up correctly during application
    shutdown and that the plugin delegates cleanup to the underlying
    EnvoyAdminClient.
    """
    admin_client = AsyncMock()

    plugin = DefaultEnvoyPlugin(admin_client=admin_client)

    await plugin.shutdown()

    admin_client.close_session.assert_awaited_once()


@pytest.mark.anyio
async def test_backend_provider_returns_first_backend():
    """Verifies that BackendProvider returns the first backend supplied by the
    plugin manager.

    The provider currently treats the first backend returned by apluggy as the
    selected implementation, so ordering determines which backend becomes
    active.
    """
    backend1 = object()
    backend2 = object()

    pm = AsyncMock()
    pm.ahook.create_backend.return_value = [backend1, backend2]

    provider = BackendProvider(pm)

    result = await provider.create_backend(context=None)

    assert result is backend1


@pytest.mark.anyio
async def test_backend_provider_raises_when_no_backend_available():
    """Verifies that BackendProvider raises an error when no backend plugins
    are available.

    This protects against starting the application without any valid backend
    implementation being registered.
    """
    pm = AsyncMock()
    pm.ahook.create_backend.return_value = []

    provider = BackendProvider(pm)

    with pytest.raises(RuntimeError, match="No backend plugin available"):
        await provider.create_backend(context=None)


class CustomPlugin:
    @hookimpl(tryfirst=True)
    async def create_backend(self, context):
        return "custom-backend"

    @hookimpl(tryfirst=True)
    async def startup(self):
        pass

    @hookimpl(tryfirst=True)
    async def shutdown(self):
        pass


@pytest.mark.anyio
async def test_custom_plugin_is_selected_before_default_plugin():
    """Verifies that a custom plugin overrides DefaultEnvoyPlugin.

    The built-in Envoy plugin is registered with trylast=True so third-party
    plugins can take precedence. This test confirms that a custom plugin using
    tryfirst=True is returned ahead of the default implementation and therefore
    becomes the active backend selected by BackendProvider.
    """
    pm = apluggy.PluginManager(project_name)
    pm.add_hookspecs(BackendSpec)

    pm.register(DefaultEnvoyPlugin(admin_client=Mock()))
    pm.register(CustomPlugin())

    provider = BackendProvider(pm)
    try:
        backend = await provider.create_backend(context=None)

        assert backend == "custom-backend"
    finally:
        for plugin in pm.get_plugins():
            if isinstance(plugin, DefaultEnvoyPlugin):
                await plugin.shutdown()


@pytest.mark.anyio
async def test_create_plugin_manager_registers_default_plugin():
    """Verifies that create_plugin_manager() registers the built-in default
    backend plugin.

    This provides a regression check that a newly created plugin manager always
    contains a working backend implementation even when no external plugins have
    been installed.
    """
    pm = create_plugin_manager()

    backends = await pm.ahook.create_backend(context=None)

    assert len(backends) == 1
