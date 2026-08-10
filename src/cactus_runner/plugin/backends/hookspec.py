from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.plugin.backends.envoy import EnvoyAdminClient, EnvoyBackend
from cactus_runner.plugin.backends.common import RunnerBackend


# TEST: temporary implementation only
def create_backend(session: AsyncSession, envoy_client: EnvoyAdminClient) -> RunnerBackend:
    """TEMPORARY IMPLEMENTATION ONLY.

    Factory producing an EnvoyBackend. It will be modified to become the
    hookspec and form the entrypoint for hte plugin architecture.
    """
    return EnvoyBackend(session=session, admin_client=envoy_client)
