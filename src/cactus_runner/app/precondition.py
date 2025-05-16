import logging
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from zoneinfo import ZoneInfo

from envoy.server.model.aggregator import Aggregator, AggregatorCertificateAssignment
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


async def apply_db_precondition(precondition: str) -> None:

    # Open connection to database
    async with open_connection() as connection:
        # The precondition is either a path to a .sql file
        # or a resource made available through the cactus_test_defintions package
        path = Path(precondition)
        if path.exists():
            await execute_sql_file_for_connection(connection=connection, path_to_sql_file=path)
            logger.info(f"Precondition '{precondition}' applied to database.")
        else:
            # Try access the precondition as a resource
            resource = resources.files("cactus_test_definitions") / precondition
            with resources.as_file(resource) as path:
                # Verify that the file exists
                if not path.exists():
                    raise UnableToApplyDatabasePrecondition(f"'{precondition}' file does not exist")

                await execute_sql_file_for_connection(connection=connection, path_to_sql_file=path)
                logger.info(f"Precondition '{precondition}' applied to database.")


async def register_aggregator(lfdi: str) -> None:
    async with begin_session() as session:
        now = datetime.now(tz=ZoneInfo("UTC"))
        expiry = now + timedelta(hours=24)
        certificate = Certificate(lfdi=lfdi, created=now, expiry=expiry)
        aggregator = Aggregator(name="Cactus", created_time=now, changed_time=now)

        session.add(aggregator)
        session.add(certificate)

        await session.execute(
            insert(Aggregator).values(name="NULL AGGREGATOR", created_time=now, changed_time=now, aggregator_id=0)
        )
        await session.flush()

        certificate_assignment = AggregatorCertificateAssignment(
            certificate_id=certificate.certificate_id, aggregator_id=aggregator.aggregator_id
        )

        session.add(certificate_assignment)
        await session.commit()
