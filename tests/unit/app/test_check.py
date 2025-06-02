import unittest.mock as mock
from typing import Any

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_test_definitions import CHECK_PARAMETER_SCHEMA, Event, Step, TestProcedure
from cactus_test_definitions.checks import Check
from envoy.server.model.site import (
    Site,
    SiteDER,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
)
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy_schema.server.schema.sep2.types import DataQualifierType, UomType

from cactus_runner.app.check import (
    CheckResult,
    FailedCheckError,
    UnknownCheckError,
    all_checks_passing,
    check_all_steps_complete,
    check_connectionpoint_contents,
    check_der_capability_contents,
    check_der_settings_contents,
    check_der_status_contents,
    do_check_readings_for_types,
    do_check_site_readings_and_params,
    run_check,
)
from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.models import ActiveTestProcedure, Listener

# This is a list of every check type paired with the handler function. This must be kept in sync with
# the checks defined in cactus test definitions (via CHECK_PARAMETER_SCHEMA). This sync will be enforced
CHECK_TYPE_TO_HANDLER: dict[str, str] = {
    "all-steps-complete": "check_all_steps_complete",
    "connectionpoint-contents": "check_connectionpoint_contents",
    "der-settings-contents": "check_der_settings_contents",
    "der-capability-contents": "check_der_capability_contents",
    "der-status-contents": "check_der_status_contents",
    "readings-site-active-power": "check_readings_site_active_power",
    "readings-site-reactive-power": "check_readings_site_reactive_power",
    "readings-site-voltage": "check_readings_site_voltage",
    "readings-der-active-power": "check_readings_der_active_power",
    "readings-der-reactive-power": "check_readings_der_reactive_power",
    "readings-der-voltage": "check_readings_der_voltage",
}


def test_CHECK_TYPE_TO_HANDLER_in_sync():
    """Tests that every check defined in CHECK_TYPE_TO_HANDLER has an appropriate entry in CHECK_NAMES_WITH_HANDLER

    Failures in this test indicate that CHECK_NAMES_WITH_HANDLER hasn't been kept up to date"""

    # Make sure that every cactus-test-definition action is found in ACTION_TYPE_TO_HANDLER
    for check_type in CHECK_PARAMETER_SCHEMA.keys():
        assert check_type in CHECK_TYPE_TO_HANDLER, f"The check type {check_type} doesn't have a known handler fn"

    # Make sure we don't have any extra definitions not found in cactus-test-definitions
    for check_type in CHECK_TYPE_TO_HANDLER.keys():
        assert (
            check_type in CHECK_PARAMETER_SCHEMA
        ), f"The check type {check_type} isn't defined in the test definitions (has it been removed/renamed)"

    assert len(set(CHECK_TYPE_TO_HANDLER.values())) == len(
        CHECK_TYPE_TO_HANDLER
    ), "At least 1 action type have listed the same action handler. This is likely a bug"


def generate_active_test_procedure_steps(active_steps: list[str], all_steps: list[str]) -> ActiveTestProcedure:
    """Utility for generating an ActiveTestProcedure from a simplified list of step names"""

    listeners = [generate_class_instance(Listener, step=s, actions=[]) for s in active_steps]

    steps = dict([(s, Step(Event("wait", {}, None), [])) for s in all_steps])
    test_procedure = generate_class_instance(TestProcedure, steps=steps)

    return generate_class_instance(ActiveTestProcedure, step_status={}, definition=test_procedure, listeners=listeners)


def assert_check_result(cr: CheckResult, expected: bool):
    assert isinstance(cr, CheckResult)
    assert isinstance(cr.passed, bool)
    assert cr.description is None or isinstance(cr.description, str)
    assert cr.passed == expected


@pytest.mark.parametrize(
    "active_test_procedure, resolved_parameters, expected",
    [
        (generate_active_test_procedure_steps([], []), {}, True),
        (generate_active_test_procedure_steps(["step-2"], ["step-1", "step-2"]), {}, False),
        (generate_active_test_procedure_steps(["step-2"], ["step-1", "step-2"]), {"ignored_steps": ["step-2"]}, True),
        (generate_active_test_procedure_steps(["step-2"], ["step-1", "step-2"]), {"ignored_steps": ["step-1"]}, False),
        (generate_active_test_procedure_steps(["step-2"], ["step-1", "step-2"]), {"ignored_steps": ["step-X"]}, False),
        (generate_active_test_procedure_steps([], ["step-1", "step-2"]), {}, True),
        (generate_active_test_procedure_steps([], ["step-1", "step-2"]), {"ignored_steps": ["step-1"]}, True),
    ],
)
def test_check_all_steps_complete(
    active_test_procedure: ActiveTestProcedure, resolved_parameters: dict, expected: bool
):
    result = check_all_steps_complete(active_test_procedure, resolved_parameters)
    assert_check_result(result, expected)


@pytest.mark.parametrize(
    "active_site, expected",
    [
        (None, False),
        (generate_class_instance(Site, nmi=None), False),
        (generate_class_instance(Site, nmi=""), False),
        (generate_class_instance(Site, nmi="abc123"), True),
    ],
)
@mock.patch("cactus_runner.app.check.get_active_site")
@pytest.mark.anyio
async def test_check_connectionpoint_contents(
    mock_get_active_site: mock.MagicMock, active_site: Site | None, expected: bool
):

    mock_get_active_site.return_value = active_site
    mock_session = create_mock_session()

    result = await check_connectionpoint_contents(mock_session)
    assert_check_result(result, expected)

    assert_mock_session(mock_session)


@pytest.mark.parametrize(
    "existing_sites, expected",
    [
        ([], False),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(SiteDER, site_der_setting=generate_class_instance(SiteDERSetting))
                    ],
                )
            ],
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(SiteDER, site_der_rating=generate_class_instance(SiteDERRating))
                    ],
                )
            ],
            False,
        ),  # Is setting DERCapability - not DERSetting
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[generate_class_instance(SiteDER)],
                )
            ],
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                )
            ],
            False,
        ),
    ],
)
@pytest.mark.anyio
async def test_check_der_settings_contents(pg_base_config, existing_sites: list[Site], expected: bool):
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await check_der_settings_contents(session)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "existing_sites, expected",
    [
        ([], False),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(SiteDER, site_der_rating=generate_class_instance(SiteDERRating))
                    ],
                )
            ],
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(SiteDER, site_der_setting=generate_class_instance(SiteDERSetting))
                    ],
                )
            ],
            False,
        ),  # Is setting DERSetting not DERCapability
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[generate_class_instance(SiteDER)],
                )
            ],
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                )
            ],
            False,
        ),
    ],
)
@pytest.mark.anyio
async def test_check_der_capability_contents(pg_base_config, existing_sites: list[Site], expected: bool):
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await check_der_capability_contents(session)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "existing_sites, resolved_params, expected",
    [
        ([], {}, False),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(SiteDER, site_der_status=generate_class_instance(SiteDERStatus))
                    ],
                )
            ],
            {},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_status=generate_class_instance(
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus": 999},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_status=generate_class_instance(
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus": 888, "operationalModeStatus": 999},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_status=generate_class_instance(
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus": 999, "operationalModeStatus": 999},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_status=generate_class_instance(
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus": 888, "operationalModeStatus": 888},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(SiteDER, site_der_setting=generate_class_instance(SiteDERSetting))
                    ],
                )
            ],
            {},
            False,
        ),  # Is setting DERSetting not DERStatus
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[generate_class_instance(SiteDER)],
                )
            ],
            {},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                )
            ],
            {},
            False,
        ),
    ],
)
@pytest.mark.anyio
async def test_check_der_status_contents(
    pg_base_config, existing_sites: list[Site], resolved_params: dict[str, Any], expected: bool
):
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await check_der_status_contents(session, resolved_params)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "srt_ids, minimum_count, expected",
    [
        ([], None, True),
        ([], 0, True),
        ([], 3, True),  # No srt_ids - nothing to check
        ([1, 2, 3], 3, False),
        ([1, 2, 3], 2, False),
        ([1, 2, 3], 0, True),
        ([1, 2], 2, True),
        ([1], 3, True),
        ([1], 4, False),
        ([1, 2, 3, 99], 0, True),
        ([1, 2, 99], 2, False),
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_for_types(
    pg_base_config, srt_ids: list[int], minimum_count: int | None, expected: bool
):
    """Tests that do_check_readings_for_types can handle various queries against a static DB model"""
    async with generate_async_session(pg_base_config) as session:
        # Load 3 SiteReadingTypes, the first has 3 readings, the second has 2.
        site = generate_class_instance(Site, aggregator_id=1, site_id=1)
        srt1 = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        srt2 = generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=2, aggregator_id=1, site=site)
        srt3 = generate_class_instance(SiteReadingType, seed=303, site_reading_type_id=3, aggregator_id=1, site=site)

        session.add_all([site, srt1, srt2, srt3])
        session.add(generate_class_instance(SiteReading, seed=11, site_reading_type=srt1))
        session.add(generate_class_instance(SiteReading, seed=22, site_reading_type=srt1))
        session.add(generate_class_instance(SiteReading, seed=33, site_reading_type=srt1))
        session.add(generate_class_instance(SiteReading, seed=44, site_reading_type=srt2))
        session.add(generate_class_instance(SiteReading, seed=55, site_reading_type=srt2))

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await do_check_readings_for_types(session, srt_ids, minimum_count)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "resolved_parameters, uom, reading_location, qualifier, site_reading_types, expected_srt_ids, expected_min_count",
    [
        ({}, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, DataQualifierType.AVERAGE, [], [], None),
        (
            {},
            UomType.APPARENT_ENERGY_VAH,
            ReadingLocation.DEVICE_READING,
            DataQualifierType.MINIMUM,
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1),
            ],
            [1],
            None,
        ),
        (
            {"minimum_count": 123, "foo": 456},
            UomType.BRITISH_THERMAL_UNIT,
            ReadingLocation.DEVICE_READING,
            DataQualifierType.STANDARD,
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=4),
                generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=2),
            ],
            [4, 2],
            123,
        ),
        (
            {"minimum_count": 0},
            UomType.FREQUENCY_HZ,
            ReadingLocation.SITE_READING,
            DataQualifierType.MAXIMUM,
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=2),
            ],
            [2],
            0,
        ),
    ],
)
@mock.patch("cactus_runner.app.check.get_csip_aus_site_reading_types")
@mock.patch("cactus_runner.app.check.do_check_readings_for_types")
@pytest.mark.anyio
async def test_do_check_site_readings_and_params(
    mock_do_check_readings_for_types: mock.MagicMock,
    mock_get_csip_aus_site_reading_types: mock.MagicMock,
    resolved_parameters: dict[str, Any],
    uom: UomType,
    reading_location: ReadingLocation,
    qualifier: DataQualifierType,
    site_reading_types: list[SiteReadingType],
    expected_srt_ids: list[int],
    expected_min_count: int | None,
):
    """Tests that do_check_site_readings_and_params does the basic logic it needs before offloading to
    do_check_readings_for_types"""
    # Arrange
    mock_session = create_mock_session()
    expected_result = generate_class_instance(CheckResult)
    mock_get_csip_aus_site_reading_types.return_value = site_reading_types
    mock_do_check_readings_for_types.return_value = expected_result

    # Act
    result = await do_check_site_readings_and_params(
        mock_session, resolved_parameters, uom, reading_location, qualifier
    )

    # Assert
    assert_mock_session(mock_session)
    mock_get_csip_aus_site_reading_types.assert_called_once_with(mock_session, uom, reading_location, qualifier)

    # If we have 0 SiteReadingTypes - instant failure, no need to run the reading checks
    if len(expected_srt_ids) != 0:
        assert result is expected_result
        mock_do_check_readings_for_types.assert_called_once_with(mock_session, expected_srt_ids, expected_min_count)
    else:
        assert_check_result(result, False)
        mock_do_check_readings_for_types.assert_not_called()


@pytest.mark.parametrize(
    "check, apply_function_name",
    [(Check(type=check_type, parameters={}), handler_fn) for check_type, handler_fn in CHECK_TYPE_TO_HANDLER.items()],
)
@pytest.mark.anyio
async def test_run_check(mocker, check: Check, apply_function_name: str):
    """This test is fully dynamic and pulls from CHECK_TYPE_TO_HANDLER to ensure every check type is tested
    and mocked."""

    # Arrange
    check_result = generate_class_instance(CheckResult)
    mock_run_check_function = mocker.patch(f"cactus_runner.app.check.{apply_function_name}")
    mock_run_check_function.return_value = check_result

    mock_session = create_mock_session()

    # Act
    actual = await run_check(check, generate_active_test_procedure_steps([], []), mock_session)

    # Assert
    assert actual is check_result
    mock_run_check_function.assert_called_once()
    assert_mock_session(mock_session)


@mock.patch("cactus_runner.app.check.do_check_site_readings_and_params")
@pytest.mark.anyio
async def test_check_readings_unique(mock_do_check_site_readings_and_params: mock.MagicMock):
    """There are a lot of "readings" type checks that all share a common utility. This test ensures that all pass down
    a unique set of parameters to make sure no obvious copy paste error has occurred"""

    reading_checks = [
        Check(type=check_type, parameters={})
        for check_type, _ in CHECK_TYPE_TO_HANDLER.items()
        if "reading" in check_type.lower()
    ]
    assert len(reading_checks) > 2, "Expected at least a few 'reading' style checks."

    # Arrange
    check_result = generate_class_instance(CheckResult)
    mock_do_check_site_readings_and_params.return_value = check_result
    mock_session = create_mock_session()

    # Act
    for check in reading_checks:
        actual = await run_check(check, generate_active_test_procedure_steps([], []), mock_session)
        assert actual is check_result

    # Assert
    assert mock_do_check_site_readings_and_params.call_count == len(reading_checks)
    assert len(set((a.args[2:] for a in mock_do_check_site_readings_and_params.call_args_list))) == len(
        reading_checks
    ), "Each call to do_check_site_readings_and_params should have unique params (ignoring session/resolved_params)"

    assert_mock_session(mock_session)


@pytest.mark.anyio
async def test_run_check_check_dne():
    """Trying to run a check that does not exist will raise an appropriate error"""

    # Arrange
    check = Check(type="this-check-does-not-exist", parameters={})
    mock_session = create_mock_session()

    # Act
    with pytest.raises(UnknownCheckError):
        await run_check(check, generate_active_test_procedure_steps([], []), mock_session)

    # Assert
    assert_mock_session(mock_session)


@pytest.mark.parametrize(
    "checks, run_check_results, expected",
    [
        (None, [], True),
        ([Check("1", {}), Check("2", {})], [True, True], True),
        ([Check("1", {}), Check("2", {})], [True, False], False),
        ([Check("1", {}), Check("2", {}), Check("3", {})], [True, True, False], False),
        ([Check("1", {}), Check("2", {}), Check("3", {})], [True, FailedCheckError, True], FailedCheckError),
        ([Check("1", {}), Check("2", {}), Check("3", {})], [True, UnknownCheckError, True], UnknownCheckError),
    ],
)
@mock.patch("cactus_runner.app.check.run_check")
@pytest.mark.anyio
async def test_all_checks_passing(
    mock_run_check: mock.MagicMock,
    checks: list[Check] | None,
    run_check_results: list[bool | type[Exception]],
    expected: bool | type[Exception],
):
    """Tries to trip up all_checks_passing under various combinations of pass/fail/exception"""

    # Arrange
    mock_session = create_mock_session()
    side_effects = []
    for r in run_check_results:
        if isinstance(r, type):
            side_effects.append(r)
        else:
            side_effects.append(CheckResult(r, None))
    mock_run_check.side_effect = side_effects

    # Act
    if isinstance(expected, type):
        with pytest.raises(expected):
            await all_checks_passing(checks, generate_active_test_procedure_steps([], []), mock_session)
    else:
        result = await all_checks_passing(checks, generate_active_test_procedure_steps([], []), mock_session)
        assert isinstance(result, bool)
        assert result == expected

    # Assert
    assert_mock_session(mock_session)
