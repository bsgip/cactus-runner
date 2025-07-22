import logging
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from envoy.server.model.aggregator import (
    Aggregator,
    AggregatorCertificateAssignment,
    AggregatorDomain,
)
from envoy.server.model.base import Certificate
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncConnection

from cactus_runner.app.database import begin_session, open_connection

logger = logging.getLogger(__name__)


class UnableToApplyDatabasePrecondition(Exception):
    pass


async def execute_sql_file_for_connection(connection: AsyncConnection, path_to_sql_file: Path) -> None:
    with open(path_to_sql_file) as f:
        sql = f.read()

    async with connection.begin() as txn:
        await connection.execute(text(sql))
        await txn.commit()


async def register_aggregator(lfdi: str | None, subscription_domain: str | None) -> int:
    """returns the aggregator ID that should be used for registering devices"""
    async with begin_session() as session:
        now = datetime.now(tz=ZoneInfo("UTC"))
        expiry = now + timedelta(hours=48)
        aggregator_id = 0

        # Always insert a NULL aggregator (for device certs)
        await session.execute(
            insert(Aggregator).values(name="NULL AGGREGATOR", created_time=now, changed_time=now, aggregator_id=0)
        )

        # Next install the aggregator lfdi (if there is one)
        if lfdi is not None:
            certificate = Certificate(lfdi=lfdi, created=now, expiry=expiry)
            aggregator = Aggregator(name="Cactus", created_time=now, changed_time=now)

            if subscription_domain is not None:
                aggregator.domains = [
                    AggregatorDomain(
                        changed_time=now,
                        domain=subscription_domain,
                    )
                ]

            session.add(aggregator)
            session.add(certificate)
            await session.flush()
            aggregator_id = aggregator.aggregator_id
            certificate_assignment = AggregatorCertificateAssignment(
                certificate_id=certificate.certificate_id, aggregator_id=aggregator.aggregator_id
            )
            session.add(certificate_assignment)
        await session.commit()
    return aggregator_id


async def reset_db() -> None:
    """Truncates all tables in the 'public' schema and resets sequences for id columns."""
    # Adapted from https://stackoverflow.com/a/63227261
    reset_sql = """
DO $$ DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
    END LOOP;
END $$;
"""
    async with open_connection() as connection:
        async with connection.begin() as txn:
            await connection.execute(text(reset_sql))
            await txn.commit()
