import logging

from envoy.server.model.aggregator import Aggregator
from sqlalchemy import func, select

from cactus_runner.app.database import begin_session
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient

logger = logging.getLogger(__name__)


async def is_db_healthy() -> bool:
    """Returns True if the server can access the DB and is otherwise in a "healthy" state"""
    try:
        async with begin_session() as session:
            count = (await session.execute(select(func.count()).select_from(Aggregator))).scalar_one()
            return isinstance(count, int)  # We don't care what the count is - just that DB can serve on
    except Exception as exc:
        logger.error("Exception checking db health", exc_info=exc)
        return False


async def is_admin_api_healthy(client: EnvoyAdminClient) -> bool:
    """Returns True if the server can access the envoy admin API - False otherwise"""
    try:
        # This is a lightweight query to ensure auth/envoy db is accessed
        result = await client.get_aggregators()
        return result is not None
    except Exception as exc:
        logger.error("Exception checking admin api health", exc_info=exc)
        return False
