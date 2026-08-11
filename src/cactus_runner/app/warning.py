import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from cactus_schema.runner import RequestEntry, WarningEntry
from envoy.server.model.archive.site import ArchiveSiteDERSetting
from envoy.server.model.archive.site_reading import ArchiveSiteReadingType
from envoy.server.model.site import SiteDERSetting
from envoy.server.model.site_reading import SiteReadingType
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.models import ActiveTestProcedure

logger = logging.getLogger(__name__)


def warn(active_test_procedure: ActiveTestProcedure, type: str, description: str, message: str) -> None:  # noqa: A002
    """Records a WarningEntry against the active test procedure. Warnings never affect pass/fail.

    First-write-wins per `type` - if a warning with this type has already been recorded, this call is a no-op."""
    if type in active_test_procedure.warnings:
        return
    active_test_procedure.warnings[type] = WarningEntry(
        type=type, description=description, message=message, timestamp=datetime.now(UTC)
    )


PostTestAnalyser = Callable[[AsyncSession, ActiveTestProcedure, list[RequestEntry]], Awaitable[None]]


# DERSettings fields checked for variation, mapped to their sep2 name for use in the warning message
_DER_SETTINGS_VARIED_FIELDS = {
    "setMaxW": (SiteDERSetting.max_w_value, ArchiveSiteDERSetting.max_w_value),
    "setMaxChargeRateW": (SiteDERSetting.max_charge_rate_w_value, ArchiveSiteDERSetting.max_charge_rate_w_value),
    "setMaxDischargeRateW": (
        SiteDERSetting.max_discharge_rate_w_value,
        ArchiveSiteDERSetting.max_discharge_rate_w_value,
    ),
}


async def _analyse_der_settings_varied(
    session: AsyncSession, active_test_procedure: ActiveTestProcedure, request_history: list[RequestEntry]
) -> None:
    """Flags if setMaxW/setMaxChargeRateW/setMaxDischargeRateW (DERSettings) changed at least once during the
    test - any archived DER setting with a different value than the current value for the same site means it
    changed. The warning message names exactly which field(s) varied."""
    labels = list(_DER_SETTINGS_VARIED_FIELDS.keys())
    row = (
        await session.execute(
            select(
                *(
                    func.bool_or(current_column != archived_column)
                    for current_column, archived_column in _DER_SETTINGS_VARIED_FIELDS.values()
                )
            )
            .select_from(ArchiveSiteDERSetting)
            .join(
                SiteDERSetting,
                SiteDERSetting.site_id == ArchiveSiteDERSetting.site_id,  # Same DER (one per site)
            )
        )
    ).one()
    varied_fields = [label for label, is_varied in zip(labels, row, strict=True) if is_varied]

    if varied_fields:
        warn(
            active_test_procedure,
            "der-settings.set-max-w-varied",
            "setMaxW changed during the test",
            f"The DER's {', '.join(varied_fields)} (DERSettings) value changed at least once during the test. "
            "Altering these values is not standard practise. Site controls in tests use these values to create "
            "control limits, altering them may cause you to fail CACTUS tests.",
        )


async def _analyse_reading_type_varied(
    session: AsyncSession, active_test_procedure: ActiveTestProcedure, request_history: list[RequestEntry]
) -> None:
    """Flags if a MirrorUsagePoint's ReadingType changed during the test."""
    pairs = (
        await session.execute(
            select(SiteReadingType, ArchiveSiteReadingType).join(
                ArchiveSiteReadingType,
                ArchiveSiteReadingType.site_reading_type_id == SiteReadingType.site_reading_type_id,
            )
        )
    ).all()

    field_names = [c.key for c in inspect(SiteReadingType).mapper.column_attrs]
    varied_fields = {
        field
        for current, archived in pairs
        for field in field_names
        if getattr(current, field) != getattr(archived, field)
    }

    if varied_fields:
        warn(
            active_test_procedure,
            "reading-type.varied",
            "A reading type changed during the test",
            f"The {', '.join(sorted(varied_fields))} field(s) of a MirrorUsagePoint's ReadingType changed at "
            "least once during the test. This updates the reading type for all associated readings, including "
            "historical ones already submitted. Please ensure you do not update reading types unless your "
            "previous readings were submitted incorrectly.",
        )


POST_TEST_ANALYSERS: list[PostTestAnalyser] = [_analyse_der_settings_varied, _analyse_reading_type_varied]


async def run_post_test_analysers(
    session: AsyncSession, active_test_procedure: ActiveTestProcedure, request_history: list[RequestEntry]
) -> None:
    """Runs every registered post-test analyser once, for use at test finalisation. Each analyser calls warn() to
    record any warnings it finds. Exceptions are caught and logged per-analyser so one failing analyser can't
    prevent the others (or the rest of finalisation) from running."""
    for analyser in POST_TEST_ANALYSERS:
        try:
            await analyser(session, active_test_procedure, request_history)
        except Exception as exc:
            logger.error(f"Error running post test analyser {analyser}", exc_info=exc)
