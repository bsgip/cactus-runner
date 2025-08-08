import unittest.mock as mock
from datetime import datetime, timezone
from typing import Any

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_test_definitions import CHECK_PARAMETER_SCHEMA, Event, Step, TestProcedure
from cactus_test_definitions.checks import Check
from envoy.server.model.aggregator import Aggregator
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroup
from envoy.server.model.response import DynamicOperatingEnvelopeResponse
from envoy.server.model.site import (
    Site,
    SiteDER,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
)
from envoy.server.model.site_reading import SiteReading, SiteReadingType
from envoy.server.model.subscription import (
    Subscription,
    SubscriptionResource,
    TransmitNotificationLog,
)
from envoy_schema.server.schema.sep2.response import ResponseType
from envoy_schema.server.schema.sep2.types import (
    DataQualifierType,
    DeviceCategory,
    UomType,
)
from sqlalchemy import select

from cactus_runner.app.check import (
    CheckResult,
    FailedCheckError,
    ParamsDERCapabilityContents,
    ParamsDERSettingsContents,
    UnknownCheckError,
    all_checks_passing,
    check_all_notifications_transmitted,
    check_all_steps_complete,
    check_der_capability_contents,
    check_der_settings_contents,
    check_der_status_contents,
    check_end_device_contents,
    check_response_contents,
    check_subscription_contents,
    do_check_readings_for_types,
    do_check_site_readings_and_params,
    is_nth_bit_set_properly,
    response_type_to_string,
    run_check,
)
from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.models import ActiveTestProcedure, Listener

# This is a list of every check type paired with the handler function. This must be kept in sync with
# the checks defined in cactus test definitions (via CHECK_PARAMETER_SCHEMA). This sync will be enforced
CHECK_TYPE_TO_HANDLER: dict[str, str] = {
    "all-steps-complete": "check_all_steps_complete",
    "end-device-contents": "check_end_device_contents",
    "der-settings-contents": "check_der_settings_contents",
    "der-capability-contents": "check_der_capability_contents",
    "der-status-contents": "check_der_status_contents",
    "readings-site-active-power": "check_readings_site_active_power",
    "readings-site-reactive-power": "check_readings_site_reactive_power",
    "readings-site-voltage": "check_readings_site_voltage",
    "readings-der-active-power": "check_readings_der_active_power",
    "readings-der-reactive-power": "check_readings_der_reactive_power",
    "readings-der-voltage": "check_readings_der_voltage",
    "all-notifications-transmitted": "check_all_notifications_transmitted",
    "subscription-contents": "check_subscription_contents",
    "response-contents": "check_response_contents",
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

    return generate_class_instance(
        ActiveTestProcedure, step_status={}, definition=test_procedure, listeners=listeners, finished_zip_data=None
    )


def assert_check_result(cr: CheckResult, expected: bool):
    assert isinstance(cr, CheckResult)
    assert isinstance(cr.passed, bool)
    assert cr.description is None or isinstance(cr.description, str)
    assert cr.passed == expected


@pytest.mark.parametrize(
    "value, n, expected, expected_output",
    [
        (0, 0, True, False),
        (0, 0, False, True),
        (1, 0, True, True),
        (1, 0, False, False),
        (0, 4, True, False),
        (0, 4, False, True),
        (8, 3, False, False),
        (8, 3, True, True),
        (8, 4, False, True),
        (8, 4, True, False),
        (6, 0, False, True),
        (6, 1, True, True),
        (6, 2, True, True),
        (6, 3, False, True),
    ],
)
def test_is_nth_bit_set_properly(value: int, n: int, expected: bool, expected_output: bool):
    actual_output = is_nth_bit_set_properly(value, n, expected)
    assert isinstance(actual_output, bool)
    assert actual_output is expected_output


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
    "active_site, has_connection_point_id, expected",
    [
        (None, True, False),
        (None, False, False),
        (None, None, False),
        (generate_class_instance(Site, nmi=None), True, False),
        (generate_class_instance(Site, nmi=None), False, True),
        (generate_class_instance(Site, nmi=None), None, True),  # Should default has_connection_point_id to False
        (generate_class_instance(Site, nmi=""), True, False),
        (generate_class_instance(Site, nmi=""), False, True),
        (generate_class_instance(Site, nmi=""), None, True),  # Should default has_connection_point_id to False
        (generate_class_instance(Site, nmi="abc123"), True, True),
        (generate_class_instance(Site, nmi="abc123"), False, True),
        (generate_class_instance(Site, nmi="abc123"), None, True),
    ],
)
@mock.patch("cactus_runner.app.check.get_active_site")
@pytest.mark.anyio
async def test_check_end_device_contents_connection_point(
    mock_get_active_site: mock.MagicMock, active_site: Site | None, has_connection_point_id: bool | None, expected: bool
):

    mock_get_active_site.return_value = active_site
    mock_session = create_mock_session()
    resolved_params = {}
    if has_connection_point_id is not None:
        resolved_params["has_connection_point_id"] = has_connection_point_id

    result = await check_end_device_contents(mock_session, resolved_params)
    assert_check_result(result, expected)

    assert_mock_session(mock_session)


@pytest.mark.parametrize(
    "active_site, deviceCategory_anyset, expected",
    [
        (None, "0", False),
        (None, "123", False),
        (None, None, False),
        (generate_class_instance(Site), "0", True),
        (generate_class_instance(Site), None, True),
        (generate_class_instance(Site, device_category=DeviceCategory(0)), "0", True),
        (generate_class_instance(Site, device_category=DeviceCategory(0)), "1", False),
        (generate_class_instance(Site, device_category=DeviceCategory(int("0f", 16))), "0f", True),
        (generate_class_instance(Site, device_category=DeviceCategory(int("0f", 16))), "05", True),
        (generate_class_instance(Site, device_category=DeviceCategory(int("0f", 16))), "10", False),
        (generate_class_instance(Site, device_category=DeviceCategory(int("22A8B", 16))), "20098", True),
        (generate_class_instance(Site, device_category=DeviceCategory(int("42A03", 16))), "20098", False),
    ],
)
@mock.patch("cactus_runner.app.check.get_active_site")
@pytest.mark.anyio
async def test_check_end_device_contents_device_category(
    mock_get_active_site: mock.MagicMock, active_site: Site | None, deviceCategory_anyset: str | None, expected: bool
):

    mock_get_active_site.return_value = active_site
    mock_session = create_mock_session()
    resolved_params = {}
    if deviceCategory_anyset is not None:
        resolved_params["deviceCategory_anyset"] = deviceCategory_anyset

    result = await check_end_device_contents(mock_session, resolved_params)
    assert_check_result(result, expected)

    assert_mock_session(mock_session)


def der_setting_bool_param_scenario(param: str, expected: bool) -> tuple[list, dict[str, bool], bool]:
    """Convenience for generating scenarios for testing the settings boolean param checks"""
    return (
        [
            generate_class_instance(
                Site,
                seed=101,
                aggregator_id=1,
                site_ders=[
                    generate_class_instance(
                        SiteDER,
                        site_der_setting=generate_class_instance(SiteDERSetting),
                    )
                ],
            )
        ],
        {param: expected},
        expected,
    )


DERSETTING_BOOL_PARAM_SCENARIOS = [
    der_setting_bool_param_scenario(p, e)
    for p in ["setMaxW", "setMaxVA", "setMaxVar", "setMaxChargeRateW", "setMaxDischargeRateW", "setMaxWh"]
    for e in [True, False]
]


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
                        generate_class_instance(SiteDER, site_der_setting=generate_class_instance(SiteDERSetting))
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
                            SiteDER, site_der_setting=generate_class_instance(SiteDERSetting, grad_w=12345)
                        )
                    ],
                )
            ],
            {"setGradW": 12345},
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
                            SiteDER, site_der_setting=generate_class_instance(SiteDERSetting, grad_w=12345)
                        )
                    ],
                )
            ],
            {"setGradW": 1234},
            False,
        ),  # setGradW doesn't match value
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
            {},
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
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_setting=generate_class_instance(SiteDERSetting, doe_modes_enabled=int("ff", 16)),
                        )
                    ],
                )
            ],
            {"doeModesEnabled_set": "03"},
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
                            site_der_setting=generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fe", 16)),
                        )
                    ],
                )
            ],
            {"doeModesEnabled_set": "03"},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_setting=generate_class_instance(SiteDERSetting, modes_enabled=int("ff", 16)),
                        )
                    ],
                )
            ],
            {"modesEnabled_set": "03"},
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
                            site_der_setting=generate_class_instance(SiteDERSetting, modes_enabled=int("fe", 16)),
                        )
                    ],
                )
            ],
            {"modesEnabled_set": "03"},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_setting=generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fc", 16)),
                        )
                    ],
                )
            ],
            {"doeModesEnabled_unset": "03"},
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
                            site_der_setting=generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"doeModesEnabled_unset": "03"},
            False,
        ),  # Bit flag 1 set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_setting=generate_class_instance(SiteDERSetting, modes_enabled=int("fc", 16)),
                        )
                    ],
                )
            ],
            {"modesEnabled_unset": "03"},
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
                            site_der_setting=generate_class_instance(SiteDERSetting, modes_enabled=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"modesEnabled_unset": "03"},
            False,
        ),  # Bit flag 1 set on actual value
        *DERSETTING_BOOL_PARAM_SCENARIOS,
    ],
)
@pytest.mark.anyio
async def test_check_der_settings_contents(
    pg_base_config, existing_sites: list[Site], resolved_params: dict[str, Any], expected: bool
):
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await check_der_settings_contents(session, resolved_params)
        assert_check_result(result, expected)


def der_rating_bool_param_scenario(param: str, expected: bool) -> tuple[list, dict[str, bool], bool]:
    """Convenience for generating scenarios for testing the ratings boolean param checks"""
    return (
        [
            generate_class_instance(
                Site,
                seed=101,
                aggregator_id=1,
                site_ders=[
                    generate_class_instance(
                        SiteDER,
                        site_der_rating=generate_class_instance(SiteDERRating),
                    )
                ],
            )
        ],
        {param: expected},
        expected,
    )


DERRATING_BOOL_PARAM_SCENARIOS = [
    der_rating_bool_param_scenario(p, e)
    for p in ["rtgMaxW", "rtgMaxVA", "rtgMaxVar", "rtgMaxChargeRateW", "rtgMaxDischargeRateW", "rtgMaxWh"]
    for e in [True, False]
]


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
                        generate_class_instance(SiteDER, site_der_rating=generate_class_instance(SiteDERRating))
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
                        generate_class_instance(SiteDER, site_der_setting=generate_class_instance(SiteDERSetting))
                    ],
                )
            ],
            {},
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
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("ff", 16)),
                        )
                    ],
                )
            ],
            {"doeModesSupported_set": "03"},
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
                            site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fe", 16)),
                        )
                    ],
                )
            ],
            {"doeModesSupported_set": "03"},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("ff", 16)),
                        )
                    ],
                )
            ],
            {"modesSupported_set": "03"},
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
                            site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fe", 16)),
                        )
                    ],
                )
            ],
            {"modesSupported_set": "03"},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fc", 16)),
                        )
                    ],
                )
            ],
            {"doeModesSupported_unset": "03"},
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
                            site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"doeModesSupported_unset": "03"},
            False,
        ),  # Bit flag 1 set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fc", 16)),
                        )
                    ],
                )
            ],
            {"modesSupported_unset": "03"},
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
                            site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"modesSupported_unset": "03"},
            False,
        ),  # Bit flag 1 set on actual value
        *DERRATING_BOOL_PARAM_SCENARIOS,
    ],
)
@pytest.mark.anyio
async def test_check_der_capability_contents(
    pg_base_config, existing_sites: list[Site], resolved_params: dict[str, Any], expected: bool
):
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await check_der_capability_contents(session, resolved_params)
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
                                SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus_bit0": True, "genConnectStatus_bit1": False, "genConnectStatus_bit2": True},
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
                                SiteDERStatus, generator_connect_status=None, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus_bit0": False},
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
                                SiteDERStatus, generator_connect_status=None, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus_bit1": False},
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
                                SiteDERStatus, generator_connect_status=None, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus_bit2": False},
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
                                SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus_bit0": False, "genConnectStatus_bit1": False, "genConnectStatus_bit2": True},
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
                                SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus_bit0": True, "genConnectStatus_bit1": True, "genConnectStatus_bit2": True},
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
                                SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                            ),
                        )
                    ],
                )
            ],
            {"genConnectStatus_bit0": True, "genConnectStatus_bit1": False, "genConnectStatus_bit2": False},
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
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=0
                            ),
                        )
                    ],
                )
            ],
            {"alarmStatus": 0},
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
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=1
                            ),
                        )
                    ],
                )
            ],
            {"alarmStatus": 0},
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
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=0
                            ),
                        )
                    ],
                )
            ],
            {"alarmStatus": 1},
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
                                SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=3
                            ),
                        )
                    ],
                )
            ],
            {"alarmStatus": 3},
            True,
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
        ([], 3, False),
        ([1, 2, 3], 3, True),  # First SRT has 3 readings
        ([1, 2, 3], 4, False),
        ([1, 2, 3], 2, True),
        ([2, 3], 3, False),
        ([3], 3, False),
        ([1, 2], 2, True),
        ([1], 3, True),
        ([1], 4, False),
        ([99], 0, True),
        ([99], 1, False),
        ([3, 2, 99], 0, True),
        ([3, 2, 99], 2, True),
        ([3, 2, 99], 3, False),
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

    faked_srts = [
        generate_class_instance(SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]

    async with generate_async_session(pg_base_config) as session:

        result = await do_check_readings_for_types(session, faked_srts, minimum_count)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "resolved_parameters, uom, reading_location, qualifier, site_reading_types, expected_min_count",
    [
        ({}, UomType.REAL_POWER_WATT, ReadingLocation.SITE_READING, DataQualifierType.AVERAGE, [], None),
        (
            {},
            UomType.APPARENT_ENERGY_VAH,
            ReadingLocation.DEVICE_READING,
            DataQualifierType.MINIMUM,
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1),
            ],
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
    if len(site_reading_types) != 0:
        assert result is expected_result
        mock_do_check_readings_for_types.assert_called_once_with(mock_session, site_reading_types, expected_min_count)
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
async def test_check_all_notifications_transmitted_no_logs(pg_base_config):
    """check_all_notifications_transmitted should fail if there are no logs"""
    async with generate_async_session(pg_base_config) as session:
        actual = await check_all_notifications_transmitted(session)
        assert_check_result(actual, False)


@pytest.mark.anyio
async def test_check_all_notifications_transmitted_success_logs(pg_base_config):
    """check_all_notifications_transmitted should succeed only all logs are OK"""

    # Fill up the DB with successes
    async with generate_async_session(pg_base_config) as session:
        for i in range(200, 299):
            session.add(
                generate_class_instance(
                    TransmitNotificationLog, seed=i, transmit_notification_log_id=None, http_status_code=i
                )
            )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        actual = await check_all_notifications_transmitted(session)
        assert_check_result(actual, True)


@pytest.mark.anyio
async def test_check_subscription_contents_no_site(pg_base_config):
    """check_subscription_contents should fail if there is no active site"""

    resolved_params = {"subscribed_resource": "/edev/1/derp/2/derc"}

    async with generate_async_session(pg_base_config) as session:
        actual = await check_subscription_contents(resolved_params, session)
        assert_check_result(actual, False)


@pytest.mark.anyio
async def test_check_subscription_contents_no_matches(pg_base_config):
    """check_subscription_contents should fail if there is no matching subscription"""

    resolved_params = {"subscribed_resource": "/edev/1/derp/2/derc"}

    # Fill up the DB with subscriptions
    async with generate_async_session(pg_base_config) as session:
        agg1 = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == 1))).scalar_one()
        agg2 = Aggregator(aggregator_id=2, name="test2", changed_time=datetime(2022, 11, 22, tzinfo=timezone.utc))
        session.add(agg2)

        site1 = generate_class_instance(Site, seed=1001, site_id=1, aggregator_id=1)  # Active Site
        site2 = generate_class_instance(Site, seed=202, site_id=2, aggregator_id=1)
        session.add(site1)
        session.add(site2)
        await session.flush()

        # wrong site_id
        session.add(
            generate_class_instance(
                Subscription,
                seed=202,
                resource_type=SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
                resource_id=2,
                aggregator=agg1,
                scoped_site=site2,
            )
        )

        # Wrong resource type
        session.add(
            generate_class_instance(
                Subscription,
                seed=303,
                resource_type=SubscriptionResource.READING,
                resource_id=2,
                aggregator=agg1,
                scoped_site=site1,
            )
        )

        # Wrong der program
        session.add(
            generate_class_instance(
                Subscription,
                seed=404,
                resource_type=SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
                resource_id=99,
                aggregator=agg1,
                scoped_site=site1,
            )
        )

        # Wrong aggregator
        session.add(
            generate_class_instance(
                Subscription,
                seed=505,
                resource_type=SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
                resource_id=2,
                aggregator=agg2,
                scoped_site=site1,
            )
        )

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        actual = await check_subscription_contents(resolved_params, session)
        assert_check_result(actual, False)


@pytest.mark.anyio
async def test_check_subscription_contents_success(pg_base_config):
    """check_subscription_contents should succeed if there is at least 1 matching subscription"""

    resolved_params = {"subscribed_resource": "/edev/1/derp/2/derc"}

    # Fill up the DB with subscriptions
    async with generate_async_session(pg_base_config) as session:
        agg1 = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == 1))).scalar_one()
        agg2 = Aggregator(aggregator_id=2, name="test2", changed_time=datetime(2022, 11, 22, tzinfo=timezone.utc))
        session.add(agg2)

        site1 = generate_class_instance(Site, seed=1001, site_id=1, aggregator_id=1)  # Active Site
        site2 = generate_class_instance(Site, seed=202, site_id=2, aggregator_id=1)
        session.add(site1)
        session.add(site2)
        await session.flush()

        # wrong site_id
        session.add(
            generate_class_instance(
                Subscription,
                seed=202,
                resource_type=SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
                resource_id=2,
                aggregator=agg1,
                scoped_site=site2,
            )
        )

        # Wrong resource type
        session.add(
            generate_class_instance(
                Subscription,
                seed=303,
                resource_type=SubscriptionResource.READING,
                resource_id=2,
                aggregator=agg1,
                scoped_site=site1,
            )
        )

        # Wrong der program
        session.add(
            generate_class_instance(
                Subscription,
                seed=404,
                resource_type=SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
                resource_id=99,
                aggregator=agg1,
                scoped_site=site1,
            )
        )

        # Wrong aggregator
        session.add(
            generate_class_instance(
                Subscription,
                seed=505,
                resource_type=SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
                resource_id=2,
                aggregator=agg2,
                scoped_site=site1,
            )
        )

        # Will match
        session.add(
            generate_class_instance(
                Subscription,
                seed=606,
                resource_type=SubscriptionResource.DYNAMIC_OPERATING_ENVELOPE,
                resource_id=2,
                aggregator=agg1,
                scoped_site=site1,
            )
        )

        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        subs = (await session.execute(select(Subscription))).scalars().all()
        print(subs)

    async with generate_async_session(pg_base_config) as session:
        actual = await check_subscription_contents(resolved_params, session)
        assert_check_result(actual, True)


@pytest.mark.parametrize("failure_code", [-1, 0, 199, 301, 404, 401, 500])
@pytest.mark.anyio
async def test_check_all_notifications_transmitted_failure_logs(pg_base_config, failure_code):
    """check_all_notifications_transmitted should fail if any logs are not success response"""

    # Fill up the DB with successes and one failure
    async with generate_async_session(pg_base_config) as session:
        for i in range(200, 210):
            session.add(
                generate_class_instance(
                    TransmitNotificationLog, seed=i, transmit_notification_log_id=None, http_status_code=i
                )
            )
        session.add(
            generate_class_instance(
                TransmitNotificationLog, seed=1, transmit_notification_log_id=None, http_status_code=failure_code
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        actual = await check_all_notifications_transmitted(session)
        assert_check_result(actual, False)


@pytest.mark.parametrize("input_val", [-1, {"a": 2}, 99999998, [1, 2, 3], "abc123"])
def test_response_type_to_string_bad_values(input_val):
    output = response_type_to_string(input_val)
    assert isinstance(output, str)
    assert len(output) > 0


def test_response_type_to_string_unique_values():
    all_values: list[str] = []
    for rt in ResponseType:
        output = response_type_to_string(rt)
        assert isinstance(output, str)
        assert len(output) > 0
        assert output == response_type_to_string(rt.value), "int or enum should be identical"

        all_values.append(output)

    assert len(all_values) > 1
    assert len(all_values) == len(set(all_values)), "All values should be unique"


@pytest.mark.anyio
async def test_check_response_contents_latest(pg_base_config):
    """check_response_contents should behave correctly when looking ONLY at the latest Response"""

    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:

        site_control_group = generate_class_instance(SiteControlGroup, seed=101)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        der_control_1 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=303,
            site=site1,
            site_control_group=site_control_group,
            calculation_log_id=None,
        )
        session.add(der_control_1)

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=505,
                response_type=ResponseType.EVENT_CANCELLED,
                created_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope=der_control_1,
            )
        )

        # This is the latest
        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=606,
                response_type=ResponseType.EVENT_COMPLETED,
                created_time=datetime(2024, 11, 11, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope=der_control_1,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=707,
                response_type=ResponseType.EVENT_RECEIVED,
                created_time=datetime(2024, 11, 9, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope=der_control_1,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        # This will check that there is a latest
        assert_check_result(await check_response_contents({"latest": True}, session), True)

        # This will check that there is a latest and that the status matches the filter
        assert_check_result(
            await check_response_contents({"latest": True, "status": ResponseType.EVENT_COMPLETED.value}, session), True
        )

        # This will check that the filter on latest will fail if there is mismatch on the latest record
        assert_check_result(
            await check_response_contents({"latest": True, "status": ResponseType.EVENT_CANCELLED.value}, session),
            False,
        )


@pytest.mark.anyio
async def test_check_response_contents_any(pg_base_config):
    """check_response_contents should behave correctly when looking at ANY of the Responses"""

    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:

        site_control_group = generate_class_instance(SiteControlGroup, seed=101)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        der_control_1 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=303,
            site=site1,
            site_control_group=site_control_group,
            calculation_log_id=None,
        )
        session.add(der_control_1)

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=505,
                response_type=ResponseType.EVENT_CANCELLED,
                created_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope=der_control_1,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=606,
                response_type=ResponseType.EVENT_COMPLETED,
                created_time=datetime(2024, 11, 11, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope=der_control_1,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=707,
                response_type=ResponseType.EVENT_RECEIVED,
                created_time=datetime(2024, 11, 9, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope=der_control_1,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        # This will check that there is any response
        assert_check_result(await check_response_contents({"latest": False}, session), True)
        assert_check_result(await check_response_contents({}, session), True)

        # Checks on existing values
        assert_check_result(
            await check_response_contents({"status": ResponseType.EVENT_COMPLETED.value}, session), True
        )
        assert_check_result(await check_response_contents({"status": ResponseType.EVENT_RECEIVED.value}, session), True)
        assert_check_result(
            await check_response_contents({"status": ResponseType.EVENT_CANCELLED.value}, session), True
        )

        # This will check that the filter will fail if a matching record cant be found
        assert_check_result(
            await check_response_contents({"latest": False, "status": ResponseType.CANNOT_BE_DISPLAYED.value}, session),
            False,
        )


@pytest.mark.anyio
async def test_check_response_contents_empty(pg_base_config):
    """check_response_contents should behave correctly when the DB is empty of responses"""

    async with generate_async_session(pg_base_config) as session:
        # This will check that there is any response
        assert_check_result(await check_response_contents({"latest": False}, session), False)
        assert_check_result(await check_response_contents({"latest": True}, session), False)
        assert_check_result(await check_response_contents({}, session), False)
        assert_check_result(
            await check_response_contents({"status": ResponseType.EVENT_COMPLETED.value}, session), False
        )
        assert_check_result(
            await check_response_contents({"latest": True, "status": ResponseType.EVENT_COMPLETED.value}, session),
            False,
        )


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


def test_params_der_settings_contents_model_has_correct_fields():
    """Ensures aliases for fields matches expected in the param definitions"""
    dscm = ParamsDERSettingsContents()

    assert sorted([f.alias for f in dscm.__pydantic_fields__.values()]) == sorted(
        [f for f in CHECK_PARAMETER_SCHEMA["der-settings-contents"]]
    )


def test_params_der_capability_contents_model_has_correct_fields():
    """Ensures aliases for fields matches expected in the param definitions"""
    dccm = ParamsDERCapabilityContents()

    assert sorted([f.alias for f in dccm.__pydantic_fields__.values()]) == sorted(
        [f for f in CHECK_PARAMETER_SCHEMA["der-capability-contents"]]
    )
