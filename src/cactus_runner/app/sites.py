from envoy.server.model import Site

from cactus_runner.app.database import (
    begin_session,
)
from cactus_runner.app.envoy_common import get_sites as get_envoy_sites


async def get_sites() -> list[Site]:
    async with begin_session() as session:
        sites = await get_envoy_sites(session=session)

    return list(sites) if sites is not None else []
