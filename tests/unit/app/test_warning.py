from datetime import UTC, datetime, timedelta

import pytest
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from cactus_schema.runner import WarningEntry
from envoy.server.model.archive.site import ArchiveSiteDERSetting
from envoy.server.model.archive.site_reading import ArchiveSiteReadingType
from envoy.server.model.site import Site, SiteDERSetting
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import UomType
from sqlalchemy import inspect

from cactus_runner.app.warning import (
    POST_TEST_ANALYSERS,
    _analyse_active_power_exceeds_max_w,
    _analyse_der_settings_varied,
    _analyse_reading_type_varied,
    _analyse_voltage_out_of_range,
    append_warnings,
    run_post_test_analysers,
)
from cactus_runner.models import ActiveTestProcedure


def _make_active_test_procedure() -> ActiveTestProcedure:
    return generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None, warnings={})


def test_append_warnings_adds_new_entry():
    active_test_procedure = _make_active_test_procedure()

    warning_entry = WarningEntry(
        "der-settings.set-max-w-varied", "short description", "full message", datetime.now(UTC)
    )

    append_warnings([warning_entry], active_test_procedure)

    assert list(active_test_procedure.warnings.keys()) == ["der-settings.set-max-w-varied"]
    entry = active_test_procedure.warnings["der-settings.set-max-w-varied"]
    assert isinstance(entry, WarningEntry)
    assert entry.type == "der-settings.set-max-w-varied"
    assert entry.description == "short description"
    assert entry.message == "full message"
    assert_nowish(entry.timestamp)


def test_append_warnings_is_first_write_wins():
    """A second warning with the same type should be ignored - the first message/timestamp is kept"""
    active_test_procedure = _make_active_test_procedure()

    warnings = [WarningEntry("polling.too-frequent./dcap", "first description", "first message", datetime.now(UTC))]
    append_warnings(warnings, active_test_procedure)
    first_entry = active_test_procedure.warnings["polling.too-frequent./dcap"]

    warnings.append(
        WarningEntry(
            "polling.too-frequent./dcap",
            "second description",
            "second message",
            datetime.now(UTC) + timedelta(seconds=1),
        )
    )

    append_warnings(warnings, active_test_procedure)

    assert len(active_test_procedure.warnings) == 1
    entry = active_test_procedure.warnings["polling.too-frequent./dcap"]
    assert entry is first_entry
    assert entry.description == "first description"
    assert entry.message == "first message"


def test_append_warnings_distinct_types_both_kept():
    active_test_procedure = _make_active_test_procedure()

    warnings = [
        WarningEntry("der-settings.set-max-w-varied", "d1", "m1", datetime.now(UTC)),
        WarningEntry("polling.too-frequent./dcap", "d2", "m2", datetime.now(UTC)),
    ]

    append_warnings(warnings, active_test_procedure)

    assert set(active_test_procedure.warnings.keys()) == {
        "der-settings.set-max-w-varied",
        "polling.too-frequent./dcap",
    }


@pytest.mark.anyio
async def test_run_post_test_analysers_catches_analyser_exceptions(mocker):
    """A failing analyser must not prevent the others from running"""
    active_test_procedure = _make_active_test_procedure()

    async def _boom(session):
        raise ValueError("analyser blew up")

    async def _good(session):
        return WarningEntry("some.type", "d", "m", datetime.now(UTC))

    mocker.patch("cactus_runner.app.warning.POST_TEST_ANALYSERS", [_boom, _good])

    warnings = await run_post_test_analysers(session=mocker.Mock())
    assert warnings
    append_warnings(warnings, active_test_procedure)
    assert "some.type" in active_test_procedure.warnings


@pytest.mark.anyio
async def test_analyse_der_settings_varied_no_warning_when_unchanged(pg_base_config):
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        site.site_der_setting = generate_class_instance(
            SiteDERSetting,
            seed=202,
            site_id=site.site_id,
            max_w_value=5000,
            max_charge_rate_w_value=1000,
            max_discharge_rate_w_value=1000,
        )
        session.add(site.site_der_setting)

        # Archived entry has identical values - not a change
        session.add(
            generate_class_instance(
                ArchiveSiteDERSetting,
                seed=303,
                site_id=site.site_id,
                archive_id=1,
                archive_time=datetime.now(UTC),
                deleted_time=None,
                max_w_value=5000,
                max_charge_rate_w_value=1000,
                max_discharge_rate_w_value=1000,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_der_settings_varied(session)

    assert warning is None
    assert active_test_procedure.warnings == {}


@pytest.mark.parametrize(
    "current_max_w, archived_max_w, current_charge, archived_charge, "
    "current_discharge, archived_discharge, expected_field",
    [
        (5000, 4000, 1000, 1000, 1000, 1000, "setMaxW"),
        (5000, 5000, 1000, 900, 1000, 1000, "setMaxChargeRateW"),
        (5000, 5000, 1000, 1000, 1000, 800, "setMaxDischargeRateW"),
    ],
)
@pytest.mark.anyio
async def test_analyse_der_settings_varied_warns_when_any_value_changed(
    pg_base_config,
    current_max_w: int,
    archived_max_w: int,
    current_charge: int,
    archived_charge: int,
    current_discharge: int,
    archived_discharge: int,
    expected_field: str,
):
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        site.site_der_setting = generate_class_instance(
            SiteDERSetting,
            seed=202,
            site_id=site.site_id,
            max_w_value=current_max_w,
            max_charge_rate_w_value=current_charge,
            max_discharge_rate_w_value=current_discharge,
        )
        session.add(site.site_der_setting)

        session.add(
            generate_class_instance(
                ArchiveSiteDERSetting,
                seed=303,
                site_id=site.site_id,
                archive_id=1,
                archive_time=datetime.now(UTC),
                deleted_time=None,
                max_w_value=archived_max_w,
                max_charge_rate_w_value=archived_charge,
                max_discharge_rate_w_value=archived_discharge,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_der_settings_varied(session)

    assert warning is not None
    append_warnings([warning], active_test_procedure)
    assert list(active_test_procedure.warnings.keys()) == ["der-settings.set-max-w-varied"]
    assert expected_field in active_test_procedure.warnings["der-settings.set-max-w-varied"].message


def test_analyse_der_settings_varied_is_registered():
    assert _analyse_der_settings_varied in POST_TEST_ANALYSERS


@pytest.mark.anyio
async def test_analyse_reading_type_varied_no_warning_when_unchanged(pg_base_config):
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        srt = generate_class_instance(SiteReadingType, seed=202, site=site, aggregator_id=1)
        session.add(srt)
        await session.flush()

        # Archived entry copies every shared column from srt unchanged - not a change
        shared_values = {c.key: getattr(srt, c.key) for c in inspect(SiteReadingType).mapper.column_attrs}
        session.add(
            generate_class_instance(
                ArchiveSiteReadingType,
                seed=303,
                archive_id=1,
                archive_time=datetime.now(UTC),
                deleted_time=None,
                **shared_values,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_reading_type_varied(session)

    assert warning is None
    assert active_test_procedure.warnings == {}


@pytest.mark.anyio
async def test_analyse_reading_type_varied_warns_when_uom_changed(pg_base_config):
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        srt = generate_class_instance(SiteReadingType, seed=202, site=site, aggregator_id=1, uom=38)
        session.add(srt)
        await session.flush()

        # Archived entry matches srt in every column except uom - the only real variation
        shared_values = {c.key: getattr(srt, c.key) for c in inspect(SiteReadingType).mapper.column_attrs}
        shared_values["uom"] = 61
        session.add(
            generate_class_instance(
                ArchiveSiteReadingType,
                seed=303,
                archive_id=1,
                archive_time=datetime.now(UTC),
                deleted_time=None,
                **shared_values,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_reading_type_varied(session)

    assert warning is not None
    append_warnings([warning], active_test_procedure)
    assert list(active_test_procedure.warnings.keys()) == ["reading-type.varied"]
    assert "uom" in active_test_procedure.warnings["reading-type.varied"].message


def test_analyse_reading_type_varied_is_registered():
    assert _analyse_reading_type_varied in POST_TEST_ANALYSERS


@pytest.mark.anyio
async def test_analyse_active_power_exceeds_max_w_no_warning_when_within_range(pg_base_config):
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        site.site_der_setting = generate_class_instance(
            SiteDERSetting, seed=202, site_id=site.site_id, max_w_value=5000, max_w_multiplier=0
        )
        session.add(site.site_der_setting)

        srt = generate_class_instance(
            SiteReadingType,
            seed=303,
            site=site,
            aggregator_id=1,
            uom=UomType.REAL_POWER_WATT,
            power_of_ten_multiplier=0,
        )
        session.add(srt)
        await session.flush()

        # -5000W and 5000W are the inclusive boundaries of the setMaxW range
        session.add(generate_class_instance(SiteReading, seed=1001, site_reading_type=srt, value=-5000))
        session.add(generate_class_instance(SiteReading, seed=1002, site_reading_type=srt, value=0))
        session.add(generate_class_instance(SiteReading, seed=1003, site_reading_type=srt, value=5000))
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_active_power_exceeds_max_w(session)

    assert warning is None
    assert active_test_procedure.warnings == {}


@pytest.mark.anyio
async def test_analyse_active_power_exceeds_max_w_warns_and_counts_across_reading_types(pg_base_config):
    """Two SiteReadingTypes for the same site with out-of-range readings between them - the warning should
    aggregate a single count across both, with values scaled by power_of_ten_multiplier and compared against
    the site's setMaxW in either direction."""
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        site.site_der_setting = generate_class_instance(
            SiteDERSetting, seed=202, site_id=site.site_id, max_w_value=5000, max_w_multiplier=0
        )
        session.add(site.site_der_setting)

        srt_a = generate_class_instance(
            SiteReadingType,
            seed=303,
            site=site,
            aggregator_id=1,
            uom=UomType.REAL_POWER_WATT,
            power_of_ten_multiplier=0,
        )
        # Values in tenths of a watt - power_of_ten_multiplier scales 51000 -> 5100W
        srt_b = generate_class_instance(
            SiteReadingType,
            seed=404,
            site=site,
            aggregator_id=1,
            uom=UomType.REAL_POWER_WATT,
            power_of_ten_multiplier=-1,
        )
        session.add(srt_a)
        session.add(srt_b)
        await session.flush()

        # srt_a: one under (import) the limit (-5100W), one within range (0W), one over (export) the limit (5100W)
        session.add(generate_class_instance(SiteReading, seed=1001, site_reading_type=srt_a, value=-5100))
        session.add(generate_class_instance(SiteReading, seed=1002, site_reading_type=srt_a, value=0))
        session.add(generate_class_instance(SiteReading, seed=1003, site_reading_type=srt_a, value=5100))

        # srt_b: one over the limit (51000 -> 5100W), rest in range
        session.add(generate_class_instance(SiteReading, seed=2001, site_reading_type=srt_b, value=51000))
        session.add(generate_class_instance(SiteReading, seed=2002, site_reading_type=srt_b, value=0))

        # A non-active-power reading type must be ignored entirely
        srt_other = generate_class_instance(SiteReadingType, seed=505, site=site, aggregator_id=1, uom=UomType.VOLTAGE)
        session.add(srt_other)
        await session.flush()
        session.add(generate_class_instance(SiteReading, seed=5001, site_reading_type=srt_other, value=1000000))

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_active_power_exceeds_max_w(session)

    assert warning is not None
    append_warnings([warning], active_test_procedure)
    assert list(active_test_procedure.warnings.keys()) == ["readings.active-power-exceeds-set-max-w"]
    message = active_test_procedure.warnings["readings.active-power-exceeds-set-max-w"].message
    assert "3 active power readings" in message


def test_analyse_active_power_exceeds_max_w_is_registered():
    assert _analyse_active_power_exceeds_max_w in POST_TEST_ANALYSERS


@pytest.mark.anyio
async def test_analyse_voltage_out_of_range_no_warning_when_all_within_range(pg_base_config):
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        srt = generate_class_instance(
            SiteReadingType, seed=202, site=site, aggregator_id=1, uom=UomType.VOLTAGE, power_of_ten_multiplier=0
        )
        session.add(srt)
        await session.flush()

        # 207V and 253V are the inclusive boundaries of the compliant range
        session.add(generate_class_instance(SiteReading, seed=1001, site_reading_type=srt, value=207))
        session.add(generate_class_instance(SiteReading, seed=1002, site_reading_type=srt, value=230))
        session.add(generate_class_instance(SiteReading, seed=1003, site_reading_type=srt, value=253))
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_voltage_out_of_range(session)

    assert warning is None
    assert active_test_procedure.warnings == {}


@pytest.mark.anyio
async def test_analyse_voltage_out_of_range_warns_and_counts_across_reading_types(pg_base_config):
    """Two SiteReadingTypes (e.g. two phases) with out-of-range readings between them - the warning should
    aggregate a single count across both, with values scaled by power_of_ten_multiplier."""
    active_test_procedure = _make_active_test_procedure()

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1)
        session.add(site)
        await session.flush()

        srt_a = generate_class_instance(
            SiteReadingType,
            seed=202,
            site=site,
            aggregator_id=1,
            uom=UomType.VOLTAGE,
            power_of_ten_multiplier=0,
        )
        # Values in tenths of a volt - power_of_ten_multiplier scales 2300 -> 230V
        srt_b = generate_class_instance(
            SiteReadingType,
            seed=303,
            site=site,
            aggregator_id=1,
            uom=UomType.VOLTAGE,
            power_of_ten_multiplier=-1,
        )
        session.add(srt_a)
        session.add(srt_b)
        await session.flush()

        # srt_a: one under range (206V), one in range (230V), one over range (254V)
        session.add(generate_class_instance(SiteReading, seed=1001, site_reading_type=srt_a, value=206))
        session.add(generate_class_instance(SiteReading, seed=1002, site_reading_type=srt_a, value=230))
        session.add(generate_class_instance(SiteReading, seed=1003, site_reading_type=srt_a, value=254))

        # srt_b: one under range (2050 -> 205V), rest in range
        session.add(generate_class_instance(SiteReading, seed=2001, site_reading_type=srt_b, value=2050))
        session.add(generate_class_instance(SiteReading, seed=2002, site_reading_type=srt_b, value=2300))

        # A non-voltage reading type must be ignored entirely
        srt_other = generate_class_instance(
            SiteReadingType, seed=404, site=site, aggregator_id=1, uom=UomType.FREQUENCY_HZ
        )
        session.add(srt_other)
        await session.flush()
        session.add(generate_class_instance(SiteReading, seed=5001, site_reading_type=srt_other, value=1000000))

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_voltage_out_of_range(session)

    assert warning is not None
    append_warnings([warning], active_test_procedure)
    assert list(active_test_procedure.warnings.keys()) == ["readings.voltage-out-of-range"]
    message = active_test_procedure.warnings["readings.voltage-out-of-range"].message
    assert "3 voltage readings" in message
    assert "207V to 253V" in message


def test_analyse_voltage_out_of_range_is_registered():
    assert _analyse_voltage_out_of_range in POST_TEST_ANALYSERS
