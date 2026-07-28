from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_admin_client import EnvoyAdminClient
from cactus_runner.plugin.backends.common import RunnerBackend
from cactus_runner.plugin.backends.envoy import EnvoyBackend


# TEST: temporary implementation only
def create_backend(session: AsyncSession, envoy_client: EnvoyAdminClient | None = None) -> RunnerBackend:
    """TEMPORARY IMPLEMENTATION ONLY.

    Factory producing an EnvoyBackend. It will be modified to become the
    hookspec and form the entrypoint for hte plugin architecture.
    """
    return EnvoyBackend(session=session, envoy_client=envoy_client)
