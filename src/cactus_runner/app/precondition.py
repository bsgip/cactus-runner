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
        expiry = now + timedelta(days=9999)  # Arbitrarily far in the future - orchestrator handles lifetime
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
    """Truncates all tables in the 'public' schema and resets sequences for id columns.

    Also sets dynamic_operating_envelope_id and tariff_generated_rate_id sequences to start
    from the current epoch time to allow tests to persist a device but receive new DOE's/pricing.
    """

    # Adapted from https://stackoverflow.com/a/63227261
    reset_sql = """
DO $$ DECLARE
    r RECORD;
    epoch_time BIGINT;
BEGIN
    epoch_time := EXTRACT(EPOCH FROM NOW())::BIGINT;
    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
    END LOOP;
    EXECUTE 'ALTER SEQUENCE default_site_control_default_site_control_id_seq RESTART WITH ' || epoch_time;
    EXECUTE 'ALTER SEQUENCE dynamic_operating_envelope_dynamic_operating_envelope_id_seq RESTART WITH ' || epoch_time;
    EXECUTE 'ALTER SEQUENCE tariff_generated_rate_tariff_generated_rate_id_seq RESTART WITH ' || epoch_time;
END $$;
"""

    async with open_connection() as connection:
        async with connection.begin() as txn:
            await connection.execute(text(reset_sql))
            await txn.commit()


async def reset_playlist_db() -> None:
    """
    Selective database reset for playlist mode.
    NOTE/TODO: This should be replaced with API calls to ENVOY admin rather than raw sql. It will then ensure that the
    correct notifications are sent out (e.g. DER control cancellations occur rather than being silently dropped from
    the server). This is a placeholder for runner/orchestrator/UI behaviour.
    """

    # Tables to delete
    deleted_tables = [
        "archive_dynamic_operating_envelope",
        "archive_site_control_group",
        "archive_site_reading",
        "archive_site_reading_type",
        "archive_subscription",
        "archive_subscription_condition",
        "archive_tariff",
        "archive_tariff_generated_rate",
        "calculation_log",
        "calculation_log_label_metadata",
        "calculation_log_label_value",
        "calculation_log_variable_metadata",
        "calculation_log_variable_value",
        "dynamic_operating_envelope",
        "dynamic_operating_envelope_response",
        "site_control_group",
        "site_group",
        "site_group_assignment",
        "site_log_event",
        "site_reading",
        "site_reading_type",
        "subscription",
        "subscription_condition",
        "tariff",
        "tariff_generated_rate",
        "tariff_generated_rate_response",
        "transmit_notification_log",
    ]

    deleted_tables_sql = ", ".join(f"'{t}'" for t in deleted_tables)

    reset_sql = f"""
DO $$ DECLARE
    r RECORD;
    epoch_time BIGINT;
BEGIN
    epoch_time := EXTRACT(EPOCH FROM NOW())::BIGINT;

    -- Truncate all tables specified
    FOR r IN (
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        AND tablename IN ({deleted_tables_sql})
    ) LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE';
    END LOOP;

    -- Reset ID sequences for tables that generate new IDs with each test
    EXECUTE 'ALTER SEQUENCE dynamic_operating_envelope_dynamic_operating_envelope_id_seq RESTART WITH ' || epoch_time;
    EXECUTE 'ALTER SEQUENCE tariff_generated_rate_tariff_generated_rate_id_seq RESTART WITH ' || epoch_time;
END $$;
    """

    async with open_connection() as connection:
        async with connection.begin() as txn:
            await connection.execute(text(reset_sql))
            await txn.commit()

    logger.info("Playlist database reset complete - preserved site/aggregator/certs, cleared transactional data")
