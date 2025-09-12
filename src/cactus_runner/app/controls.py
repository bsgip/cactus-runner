from envoy.server.model import DynamicOperatingEnvelope

from cactus_runner.app.database import (
    begin_session,
)
from cactus_runner.app.envoy_common import get_site_controls_active_deleted


async def get_controls() -> list[DynamicOperatingEnvelope]:
    async with begin_session() as session:
        controls = await get_site_controls_active_deleted(session=session)

    return list(controls) if controls is not None else []
