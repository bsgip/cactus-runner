from datetime import UTC, datetime, timedelta

import pytest
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from cactus_schema.runner import WarningEntry
from envoy.server.model.archive.site import ArchiveSiteDERSetting
from envoy.server.model.archive.site_reading import ArchiveSiteReading, ArchiveSiteReadingType
from envoy.server.model.site import Site, SiteDERSetting
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import DataQualifierType, KindType, PhaseCode, UomType
from sqlalchemy import inspect

from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.app.warning import (
    POST_TEST_ANALYSERS,
    _analyse_active_power_exceeds_max_w,
    _analyse_der_settings_varied,
    _analyse_duplicate_readings,
    _analyse_missing_intervals,
    _analyse_reading_type_varied,
    _analyse_voltage_out_of_range,
    _analyse_voltage_phase_count_mismatch,
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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

        # -6250W and 6250W are the inclusive boundaries of the setMaxW range (with 1.25x buffer)
        session.add(generate_class_instance(SiteReading, seed=1001, site_reading_type=srt, value=-6250))
        session.add(generate_class_instance(SiteReading, seed=1002, site_reading_type=srt, value=0))
        session.add(generate_class_instance(SiteReading, seed=1003, site_reading_type=srt, value=6250))
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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
        # Values in tenths of a watt - power_of_ten_multiplier scales 63000 -> 6300W
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

        # srt_a: one under (import) the limit (-6300W), one above setMaxW but within the buffer (5100W), one over
        # (export) the limit (6300W)
        session.add(generate_class_instance(SiteReading, seed=1001, site_reading_type=srt_a, value=-6300))
        session.add(generate_class_instance(SiteReading, seed=1002, site_reading_type=srt_a, value=5100))
        session.add(generate_class_instance(SiteReading, seed=1003, site_reading_type=srt_a, value=6300))

        # srt_b: one over the limit (63000 -> 6300W), rest in range
        session.add(generate_class_instance(SiteReading, seed=2001, site_reading_type=srt_b, value=63000))
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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
        site = generate_class_instance(Site, nmi_checksum=None, seed=101, aggregator_id=1)
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


SITE = ReadingLocation.SITE_READING
DEVICE = ReadingLocation.DEVICE_READING
AVG = DataQualifierType.AVERAGE
MAX = DataQualifierType.MAXIMUM
VOLTS = UomType.VOLTAGE
AN, BN, ABC = PhaseCode.PHASE_AN_S1N, PhaseCode.PHASE_BN, PhaseCode.PHASE_ABC


async def _create_phase_readings(
    pg_base_config, reading_types: list[tuple[UomType, ReadingLocation, DataQualifierType, PhaseCode, int]]
) -> None:
    """Creates a SiteReadingType with the specified number of readings for each element in reading_types"""
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1, nmi_checksum=None)
        session.add(site)
        for i, (uom, location, data_qualifier, phase, count) in enumerate(reading_types):
            srt = generate_class_instance(
                SiteReadingType,
                seed=1000 * (i + 1),
                site=site,
                aggregator_id=1,
                uom=uom,
                role_flags=location,
                data_qualifier=data_qualifier,
                kind=KindType.POWER,
                phase=phase,
            )
            session.add(srt)
            for j in range(count):
                session.add(
                    generate_class_instance(
                        SiteReading,
                        seed=1000 * (i + 1) + j,
                        site_reading_type=srt,
                        time_period_start=READINGS_T0 + timedelta(minutes=j),
                        time_period_seconds=60,
                    )
                )
        await session.commit()


@pytest.mark.parametrize(
    "reading_types",
    [
        pytest.param([], id="no-reading-types"),
        pytest.param([(VOLTS, SITE, AVG, AN, 5)], id="single-phase"),
        pytest.param([(VOLTS, SITE, AVG, AN, 0), (VOLTS, SITE, AVG, ABC, 0)], id="no-readings"),
        pytest.param([(VOLTS, SITE, AVG, AN, 5), (VOLTS, SITE, AVG, ABC, 5)], id="equal"),
        pytest.param([(VOLTS, SITE, AVG, AN, 4), (VOLTS, SITE, AVG, ABC, 5)], id="differ-by-1"),
        pytest.param([(VOLTS, SITE, AVG, AN, 0), (VOLTS, SITE, AVG, ABC, 1)], id="differ-by-1-from-zero"),
        pytest.param([(VOLTS, SITE, AVG, AN, 5), (VOLTS, DEVICE, AVG, ABC, 2)], id="site-vs-device-not-compared"),
        pytest.param([(VOLTS, SITE, AVG, AN, 5), (VOLTS, SITE, MAX, ABC, 2)], id="qualifiers-not-compared"),
        pytest.param(
            [(UomType.REAL_POWER_WATT, SITE, AVG, AN, 5), (UomType.REAL_POWER_WATT, SITE, AVG, ABC, 2)],
            id="not-voltage",
        ),
        pytest.param(
            [(VOLTS, SITE, AVG, AN, 2), (VOLTS, SITE, AVG, AN, 3), (VOLTS, SITE, AVG, ABC, 5)],
            id="same-phase-summed",
        ),
    ],
)
@pytest.mark.anyio
async def test_analyse_voltage_phase_count_mismatch_no_warning(pg_base_config, reading_types):
    await _create_phase_readings(pg_base_config, reading_types)

    async with generate_async_session(pg_base_config) as session:
        assert await _analyse_voltage_phase_count_mismatch(session) is None


@pytest.mark.parametrize(
    "reading_types, expected_lines",
    [
        pytest.param(
            [(VOLTS, SITE, AVG, AN, 3), (VOLTS, SITE, AVG, ABC, 5)],
            ["- roleFlags 0x03, dataQualifier AVERAGE: PHASE_AN_S1N (129): 3, PHASE_ABC (224): 5"],
            id="differ-by-2",
        ),
        pytest.param(
            [(VOLTS, DEVICE, AVG, AN, 0), (VOLTS, DEVICE, AVG, ABC, 2)],
            ["- roleFlags 0x49, dataQualifier AVERAGE: PHASE_AN_S1N (129): 0, PHASE_ABC (224): 2"],
            id="phase-without-any-readings",
        ),
        pytest.param(
            [(VOLTS, SITE, AVG, BN, 4), (VOLTS, SITE, AVG, AN, 3), (VOLTS, SITE, AVG, ABC, 5)],
            ["- roleFlags 0x03, dataQualifier AVERAGE: PHASE_BN (65): 4, PHASE_AN_S1N (129): 3, PHASE_ABC (224): 5"],
            id="tolerance-applies-to-largest-difference",
        ),
        pytest.param(
            [
                (VOLTS, SITE, AVG, AN, 5),
                (VOLTS, SITE, AVG, ABC, 5),
                (VOLTS, DEVICE, AVG, AN, 5),
                (VOLTS, DEVICE, AVG, ABC, 1),
            ],
            ["- roleFlags 0x49, dataQualifier AVERAGE: PHASE_AN_S1N (129): 5, PHASE_ABC (224): 1"],
            id="only-mismatching-location-reported",
        ),
        pytest.param(
            [
                (VOLTS, SITE, AVG, AN, 1),
                (VOLTS, SITE, AVG, ABC, 5),
                (VOLTS, DEVICE, AVG, AN, 5),
                (VOLTS, DEVICE, AVG, ABC, 1),
            ],
            [
                "- roleFlags 0x03, dataQualifier AVERAGE: PHASE_AN_S1N (129): 1, PHASE_ABC (224): 5",
                "- roleFlags 0x49, dataQualifier AVERAGE: PHASE_AN_S1N (129): 5, PHASE_ABC (224): 1",
            ],
            id="both-locations",
        ),
    ],
)
@pytest.mark.anyio
async def test_analyse_voltage_phase_count_mismatch_warns(pg_base_config, reading_types, expected_lines: list[str]):
    await _create_phase_readings(pg_base_config, reading_types)

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_voltage_phase_count_mismatch(session)

    assert warning is not None
    assert warning.type == "readings.voltage-phase-count-mismatch"
    assert_nowish(warning.timestamp)
    assert warning.message.splitlines()[1:] == expected_lines


def test_analyse_voltage_phase_count_mismatch_is_registered():
    assert _analyse_voltage_phase_count_mismatch in POST_TEST_ANALYSERS


READINGS_T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)


async def _create_readings(
    pg_base_config,
    readings: list[tuple[int, int]],
    archived_minutes: list[int] | None = None,
    received_at: datetime | None = None,
) -> None:
    """Creates one SiteReadingType with a live reading for each start time, duration pair plus an archived 60s reading
    starting at each of archived_minutes. Received_at defaults to the end of each live reading's interval."""
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, seed=101, aggregator_id=1, nmi_checksum=None)
        srt = generate_class_instance(
            SiteReadingType, seed=202, site=site, aggregator_id=1, site_reading_type_id=1, mrid="abc123"
        )
        session.add_all([site, srt])
        for i, (start_minute, seconds) in enumerate(readings):
            start = READINGS_T0 + timedelta(minutes=start_minute)
            session.add(
                generate_class_instance(
                    SiteReading,
                    seed=1000 + i,
                    site_reading_type=srt,
                    time_period_start=start,
                    time_period_seconds=seconds,
                    created_time=received_at or start + timedelta(seconds=seconds),
                )
            )
        for i, minute in enumerate(archived_minutes or []):
            session.add(
                generate_class_instance(
                    ArchiveSiteReading,
                    seed=2000 + i,
                    site_reading_type_id=1,
                    time_period_start=READINGS_T0 + timedelta(minutes=minute),
                    time_period_seconds=60,
                )
            )
        await session.commit()


@pytest.mark.parametrize(
    "readings",
    [
        pytest.param([], id="no-readings"),
        pytest.param([(m, 60) for m in range(60)], id="every-interval-present"),
        pytest.param([(m, 60) for m in range(60) if m not in (10, 20, 30, 40)], id="4-missed-of-60-is-fine"),
        pytest.param([(m, 60) for m in range(60) if not 10 <= m < 20], id="10-missed-of-60-under-25%-is-fine"),
        pytest.param([(0, 60), (2, 60)], id="short-test-1-missed-of-3-under-min-count"),
        pytest.param([(m, 60) for m in range(30)] + [(30 + 5 * m, 300) for m in range(6)], id="post-rate-change"),
        pytest.param([(m, 0) for m in range(0, 60, 10)], id="zero-duration-readings-ignored"),
    ],
)
@pytest.mark.anyio
async def test_analyse_missing_intervals_no_warning(pg_base_config, readings: list[tuple[int, int]]):
    await _create_readings(pg_base_config, readings)

    async with generate_async_session(pg_base_config) as session:
        assert await _analyse_missing_intervals(session) is None


@pytest.mark.anyio
async def test_analyse_missing_intervals_backfilled_readings_fill_gaps(pg_base_config):
    """Every reading was received long after its interval (e.g. backfilled after a disconnect) - there are no gaps
    because only the final set of readings is checked."""
    await _create_readings(pg_base_config, [(m, 60) for m in range(60)], received_at=READINGS_T0 + timedelta(hours=1))

    async with generate_async_session(pg_base_config) as session:
        assert await _analyse_missing_intervals(session) is None


@pytest.mark.anyio
async def test_analyse_missing_intervals_warns_on_large_gap(pg_base_config):
    """60 one minute intervals, with the middle 30 never received - 50% missing"""
    await _create_readings(pg_base_config, [(m, 60) for m in range(60) if not 15 <= m < 45])

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_missing_intervals(session)

    assert warning is not None
    assert warning.type == "readings.missing-intervals"
    assert_nowish(warning.timestamp)
    assert "Readings for 1 reading type(s) have large gaps" in warning.message
    assert "backfilling readings missed during a disconnect is fine" in warning.message
    assert (
        "- ReadingType for MirrorUsagePoint mRID abc123: about 30 of 60 60s intervals missing (50%) "
        f"between {READINGS_T0.isoformat()} and {(READINGS_T0 + timedelta(hours=1)).isoformat()}" in warning.message
    )


def test_analyse_missing_intervals_is_registered():
    assert _analyse_missing_intervals in POST_TEST_ANALYSERS


@pytest.mark.parametrize(
    "readings, archived_minutes",
    [
        pytest.param([], [], id="no-readings"),
        pytest.param([(0, 60), (1, 60), (2, 60)], [], id="each-interval-posted-once"),
        pytest.param([(0, 60), (1, 60), (2, 60)], [5], id="archived-reading-without-live-match-is-ignored"),
    ],
)
@pytest.mark.anyio
async def test_analyse_duplicate_readings_no_warning(
    pg_base_config, readings: list[tuple[int, int]], archived_minutes: list[int]
):
    """An archived reading with no matching live reading was archived for another reason (e.g. its MUP was deleted),
    not because it was re-posted."""
    await _create_readings(pg_base_config, readings, archived_minutes)

    async with generate_async_session(pg_base_config) as session:
        assert await _analyse_duplicate_readings(session) is None


@pytest.mark.parametrize(
    "archived_minutes, expected_count",
    [
        pytest.param([0], 1, id="one-interval-posted-twice"),
        pytest.param([0, 0], 1, id="one-interval-posted-three-times-counts-once"),
        pytest.param([0, 0, 2], 2, id="two-different-intervals-reposted"),
    ],
)
@pytest.mark.anyio
async def test_analyse_duplicate_readings_warns(pg_base_config, archived_minutes: list[int], expected_count: int):
    await _create_readings(pg_base_config, [(0, 60), (1, 60), (2, 60)], archived_minutes)

    async with generate_async_session(pg_base_config) as session:
        warning = await _analyse_duplicate_readings(session)

    assert warning is not None
    assert warning.type == "readings.duplicate-intervals"
    assert_nowish(warning.timestamp)
    assert warning.message.startswith(f"{expected_count} reading intervals were posted more than once")


def test_analyse_duplicate_readings_is_registered():
    assert _analyse_duplicate_readings in POST_TEST_ANALYSERS
