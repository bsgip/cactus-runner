import logging

from envoy.server.model.aggregator import Aggregator
from sqlalchemy import func, select

from cactus_runner.app.database import begin_session

logger = logging.getLogger(__name__)


async def is_healthy() -> bool:
    """Returns True if the server can access the DB and is otherwise in a "healthy" state"""
    try:
        async with begin_session() as session:
            count = (await session.execute(select(func.count()).select_from(Aggregator))).scalar_one()
            return isinstance(count, int)  # We don't care what the count is - just that DB can serve on
    except Exception as exc:
        logger.error("Exception checking health", exc_info=exc)
        return False
