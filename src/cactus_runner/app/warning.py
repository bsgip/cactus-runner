import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime

from cactus_schema.runner import WarningEntry
from envoy.server.model.archive.site import ArchiveSiteDERSetting
from envoy.server.model.archive.site_reading import ArchiveSiteReadingType
from envoy.server.model.site import SiteDERSetting
from envoy.server.model.site_reading import SiteReadingType
from envoy_schema.server.schema.sep2.types import UomType
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from cactus_runner.app.envoy_common import get_site_readings
from cactus_runner.models import ActiveTestProcedure

logger = logging.getLogger(__name__)


PostTestAnalyser = Callable[[AsyncSession], Awaitable[WarningEntry | None]]


def append_warnings(warnings: Iterable[WarningEntry], active_test_procedure: ActiveTestProcedure) -> None:
    for warning in warnings:
        if active_test_procedure.warnings.get(warning.type) is None:
            active_test_procedure.warnings[warning.type] = warning


# DERSettings fields checked for variation, mapped to their sep2 name for use in the warning message
_DER_SETTINGS_VARIED_FIELDS = {
    "setMaxW": (SiteDERSetting.max_w_value, ArchiveSiteDERSetting.max_w_value),
    "setMaxChargeRateW": (SiteDERSetting.max_charge_rate_w_value, ArchiveSiteDERSetting.max_charge_rate_w_value),
    "setMaxDischargeRateW": (
        SiteDERSetting.max_discharge_rate_w_value,
        ArchiveSiteDERSetting.max_discharge_rate_w_value,
    ),
}


async def _analyse_der_settings_varied(session: AsyncSession) -> WarningEntry | None:
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
        return WarningEntry(
            type="der-settings.set-max-w-varied",
            description="setMaxW changed during the test",
            message=(
                f"The DER's {', '.join(varied_fields)} (DERSettings) value changed at least once during the test. "
                "Altering these values is not standard practise. Site controls in tests use these values to create "
                "control limits, altering them may cause you to fail CACTUS tests."
            ),
            timestamp=datetime.now(UTC),
        )


async def _analyse_reading_type_varied(session: AsyncSession) -> WarningEntry | None:
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
        return WarningEntry(
            type="reading-type.varied",
            description="A reading type changed during the test",
            message=(
                f"The {', '.join(sorted(varied_fields))} field(s) of a MirrorUsagePoint's ReadingType changed at "
                "least once during the test. This updates the reading type for all associated readings, including "
                "historical ones already submitted. Please ensure you do not update reading types unless your "
                "previous readings were submitted incorrectly."
            ),
            timestamp=datetime.now(UTC),
        )


async def _analyse_active_power_exceeds_max_w(session: AsyncSession) -> WarningEntry | None:
    """Flags if any active power (Watts) readings received during the test exceed the DER's setMaxW limit."""
    der_settings_response = await session.execute(select(SiteDERSetting))
    max_w_by_site_id = {
        site_der_setting.site_id: site_der_setting.max_w_value * 10**site_der_setting.max_w_multiplier
        for site_der_setting in der_settings_response.scalars().all()
    }

    response = await session.execute(select(SiteReadingType).where(SiteReadingType.uom == UomType.REAL_POWER_WATT))
    site_reading_types = response.scalars().all()

    out_of_range_count = 0
    for srt in site_reading_types:
        max_w = max_w_by_site_id.get(srt.site_id)
        if max_w is None:
            continue

        readings = await get_site_readings(session, srt)
        for reading in readings:
            active_power = reading.value * 10**srt.power_of_ten_multiplier
            if active_power < -max_w or active_power > max_w:
                out_of_range_count += 1

    if out_of_range_count > 0:
        return WarningEntry(
            type="readings.active-power-exceeds-set-max-w",
            description="Active power readings received exceed setMaxW",
            message=(
                f"{out_of_range_count} active power readings received exceed the DER's setMaxW limit (in the "
                "positive/export or negative/import direction). Active power should never exceed setMaxW."
            ),
            timestamp=datetime.now(UTC),
        )


async def _analyse_voltage_out_of_range(session: AsyncSession) -> WarningEntry | None:
    """Flags if any voltage readings received during the test fall outside the compliant range of
    207V to 253V (230V nominal +/- 10%). Applies per SiteReadingType, so multi-phase sites have each
    phase's readings checked independently against this same band."""
    response = await session.execute(select(SiteReadingType).where(SiteReadingType.uom == UomType.VOLTAGE))
    site_reading_types = response.scalars().all()

    out_of_range_count = 0
    for srt in site_reading_types:
        readings = await get_site_readings(session, srt)
        for reading in readings:
            voltage = reading.value * 10**srt.power_of_ten_multiplier
            if voltage < 207 or voltage > 253:
                out_of_range_count += 1

    if out_of_range_count > 0:
        return WarningEntry(
            type="readings.voltage-out-of-range",
            description="Voltage readings received fall outside the compliant range",
            message=(
                f"{out_of_range_count} voltage readings received fall outside the compliant 207V to 253V range. "
                "Please ensure that your client is accurately reporting voltage and that there is a good reason "
                "for this."
            ),
            timestamp=datetime.now(UTC),
        )


POST_TEST_ANALYSERS: list[PostTestAnalyser] = [
    _analyse_der_settings_varied,
    _analyse_reading_type_varied,
    _analyse_active_power_exceeds_max_w,
    _analyse_voltage_out_of_range,
]


async def run_post_test_analysers(session: AsyncSession) -> list[WarningEntry]:
    """Runs every registered post-test analyser once, for use at test finalisation. Each analyser calls warn() to
    record any warnings it finds. Exceptions are caught and logged per-analyser so one failing analyser can't
    prevent the others (or the rest of finalisation) from running."""
    warnings: list[WarningEntry] = []
    for analyser in POST_TEST_ANALYSERS:
        try:
            result = await analyser(session)
            if result is not None:
                warnings.append(result)
        except Exception as exc:
            logger.error(f"Error running post test analyser {analyser}", exc_info=exc)
    return warnings
