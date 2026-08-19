from cactus_runner.plugin.backends.envoy.admin_client import EnvoyAdminClientAuthParams
from cactus_runner.app.env import ENVOY_ADMIN_URL, ENVOY_ADMIN_BASICAUTH_PASSWORD, ENVOY_ADMIN_BASICAUTH_USERNAME
from cactus_runner.app.database import begin_session
from cactus_runner.plugin.backends.models import RunnerBackendTestContext
import apluggy
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.plugin.backends.common import RunnerBackend
from cactus_runner.plugin.backends.envoy import EnvoyAdminClient, EnvoyBackend

project_name = "cactus_runner.backend"
hookspec = apluggy.HookspecMarker(project_name)
hookimpl = apluggy.HookimplMarker(project_name)


# TEST: temporary implementation only
def create_backend(envoy_client: EnvoyAdminClient) -> RunnerBackend:
    """TEMPORARY IMPLEMENTATION ONLY.

    Factory producing an EnvoyBackend. It will be modified to become the
    hookspec and form the entrypoint for hte plugin architecture.
    """
    return EnvoyBackend(admin_client=envoy_client)


class BackendSpec:
    @hookspec
    async def create_backend(self, context: RunnerBackendTestContext) -> RunnerBackend:  # type: ignore
        """Called via handlers to initiate a backend.

        Args:
            context: Full execution context for this test run (eg. test definition, client lfdi etc.)
        """
        ...


class DefaultEnvoyPlugin:
    @hookimpl(trylast=True)
    async def create_backend(self, context: RunnerBackendTestContext) -> EnvoyBackend:
        admin_client = EnvoyAdminClient(
            base_url=ENVOY_ADMIN_URL,
            auth_params=EnvoyAdminClientAuthParams(
                username=ENVOY_ADMIN_BASICAUTH_USERNAME, password=ENVOY_ADMIN_BASICAUTH_PASSWORD
            ),
        )
        return EnvoyBackend(session=session, admin_client=admin_client, test_context=context)
