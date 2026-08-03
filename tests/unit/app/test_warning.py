from datetime import UTC, datetime

import pytest
from assertical.asserts.time import assert_nowish
from assertical.fake.generator import generate_class_instance
from assertical.fixtures.postgres import generate_async_session
from cactus_schema.runner import WarningEntry
from envoy.server.model.archive.site import ArchiveSiteDERSetting
from envoy.server.model.archive.site_reading import ArchiveSiteReadingType
from envoy.server.model.site import Site, SiteDERSetting
from envoy.server.model.site_reading import SiteReadingType
from sqlalchemy import inspect

from cactus_runner.app.warning import (
    POST_TEST_ANALYSERS,
    _analyse_der_settings_varied,
    _analyse_reading_type_varied,
    run_post_test_analysers,
    warn,
)
from cactus_runner.models import ActiveTestProcedure


def _make_active_test_procedure() -> ActiveTestProcedure:
    return generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None, warnings={})


def test_warn_adds_new_entry():
    active_test_procedure = _make_active_test_procedure()

    warn(active_test_procedure, "der-settings.set-max-w-varied", "short description", "full message")

    assert list(active_test_procedure.warnings.keys()) == ["der-settings.set-max-w-varied"]
    entry = active_test_procedure.warnings["der-settings.set-max-w-varied"]
    assert isinstance(entry, WarningEntry)
    assert entry.type == "der-settings.set-max-w-varied"
    assert entry.description == "short description"
    assert entry.message == "full message"
    assert_nowish(entry.timestamp)


def test_warn_is_first_write_wins():
    """A second warn() call with the same type should be a silent no-op - the first message/timestamp is kept"""
    active_test_procedure = _make_active_test_procedure()

    warn(active_test_procedure, "polling.too-frequent./dcap", "first description", "first message")
    first_entry = active_test_procedure.warnings["polling.too-frequent./dcap"]

    warn(active_test_procedure, "polling.too-frequent./dcap", "second description", "second message")

    assert len(active_test_procedure.warnings) == 1
    entry = active_test_procedure.warnings["polling.too-frequent./dcap"]
    assert entry is first_entry
    assert entry.description == "first description"
    assert entry.message == "first message"


def test_warn_distinct_types_both_kept():
    active_test_procedure = _make_active_test_procedure()

    warn(active_test_procedure, "der-settings.set-max-w-varied", "d1", "m1")
    warn(active_test_procedure, "polling.too-frequent./dcap", "d2", "m2")

    assert set(active_test_procedure.warnings.keys()) == {
        "der-settings.set-max-w-varied",
        "polling.too-frequent./dcap",
    }


@pytest.mark.anyio
async def test_run_post_test_analysers_catches_analyser_exceptions(mocker):
    """A failing analyser must not prevent the others from running"""
    active_test_procedure = _make_active_test_procedure()

    async def _boom(session, active_test_procedure, request_history):
        raise ValueError("analyser blew up")

    async def _good(session, active_test_procedure, request_history):
        warn(active_test_procedure, "some.type", "d", "m")

    mocker.patch("cactus_runner.app.warning.POST_TEST_ANALYSERS", [_boom, _good])

    await run_post_test_analysers(
        session=mocker.Mock(), active_test_procedure=active_test_procedure, request_history=[]
    )

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
        await _analyse_der_settings_varied(session, active_test_procedure, [])

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
        await _analyse_der_settings_varied(session, active_test_procedure, [])

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
        await _analyse_reading_type_varied(session, active_test_procedure, [])

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
        await _analyse_reading_type_varied(session, active_test_procedure, [])

    assert list(active_test_procedure.warnings.keys()) == ["reading-type.varied"]
    assert "uom" in active_test_procedure.warnings["reading-type.varied"].message


def test_analyse_reading_type_varied_is_registered():
    assert _analyse_reading_type_varied in POST_TEST_ANALYSERS
