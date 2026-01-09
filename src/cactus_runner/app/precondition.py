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
from cactus_runner.app.envoy_admin_client import EnvoyAdminClient

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
    EXECUTE 'ALTER SEQUENCE site_control_group_default_site_control_group_default_id_seq RESTART WITH ' || epoch_time;
    EXECUTE 'ALTER SEQUENCE dynamic_operating_envelope_dynamic_operating_envelope_id_seq RESTART WITH ' || epoch_time;
    EXECUTE 'ALTER SEQUENCE tariff_generated_rate_tariff_generated_rate_id_seq RESTART WITH ' || epoch_time;
END $$;
"""

    async with open_connection() as connection:
        async with connection.begin() as txn:
            await connection.execute(text(reset_sql))
            await txn.commit()


async def reset_playlist_db(envoy_client: EnvoyAdminClient) -> None:
    """Performs a partial database reset suitable for playlist transitions.

    Unlike reset_db() which truncates ALL tables, this preserves:
    - aggregator, aggregator_certificate_assignment, aggregator_domain
    - certificate
    - site, site_der, site_der_rating, site_der_setting, site_der_availability, site_der_status
    - runtime_server_config

    And truncates test-specific transactional data.

    NOTE: The admin API call ensures proper notifications are sent (e.g. DER control
    cancellations occur rather than being silently dropped). This is important for
    maintaining correct client state between playlist tests.
    """
    logger.info("Performing playlist database reset (partial)")

    # Step 1: Call DELETE /site-control-groups admin endpoint
    # This properly archives DOEs and sends notifications to subscribed clients
    try:
        await envoy_client.delete_all_site_control_groups()
        logger.debug("Deleted all site control groups via admin API")
    except Exception as exc:
        logger.warning(f"Failed to delete site control groups via admin API: {exc}")

    # Step 2: Truncate specific tables (not site/aggregator/certificate)
    # Tables to delete - these will not notify the client, keeping state clean for the next test
    tables_to_truncate = [
        # Archive tables
        "archive_site_reading",
        "archive_site_reading_type",
        "archive_subscription",
        "archive_subscription_condition",
        # Calculation logs
        "calculation_log",
        "calculation_log_label_metadata",
        "calculation_log_label_value",
        "calculation_log_variable_metadata",
        "calculation_log_variable_value",
        # Subscriptions and notifications
        "transmit_notification_log",
        "subscription_condition",
        "subscription",
        # DOE/Control (after the admin API call)
        "dynamic_operating_envelope_response",
        # Site events
        "site_log_event",
    ]

    # Build SQL to truncate each table individually with CASCADE
    truncate_statements = "\n    ".join(f"EXECUTE 'TRUNCATE TABLE {table} CASCADE';" for table in tables_to_truncate)

    reset_sql = f"""
DO $$ DECLARE
    epoch_time BIGINT;
BEGIN
    epoch_time := EXTRACT(EPOCH FROM NOW())::BIGINT;

    -- Truncate specific tables (order matters for foreign key dependencies, CASCADE handles this)
    {truncate_statements}

    -- Reset sequences that need epoch time (for new test to get unique IDs)
    EXECUTE 'ALTER SEQUENCE site_control_group_default_site_control_group_default_id_seq RESTART WITH ' || epoch_time;
    EXECUTE 'ALTER SEQUENCE dynamic_operating_envelope_dynamic_operating_envelope_id_seq RESTART WITH ' || epoch_time;
    EXECUTE 'ALTER SEQUENCE tariff_generated_rate_tariff_generated_rate_id_seq RESTART WITH ' || epoch_time;
END $$;
"""

    async with open_connection() as connection:
        async with connection.begin() as txn:
            await connection.execute(text(reset_sql))
            await txn.commit()

    logger.info("Playlist database reset complete - preserved site/aggregator/certs, cleared transactional data")
