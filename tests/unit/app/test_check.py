import dataclasses
import re
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import pytest
import pytest_mock
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_test_definitions import variable_expressions
from cactus_test_definitions.client import (
    CHECK_PARAMETER_SCHEMA,
    Check,
    Event,
    Step,
    TestProcedure,
)
from envoy.server.model.aggregator import Aggregator
from envoy.server.model.archive.doe import ArchiveDynamicOperatingEnvelope
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
    KindType,
    UomType,
)
from sqlalchemy import select

from cactus_runner.app import evaluator
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
    check_readings_voltage,
    check_response_contents,
    check_subscription_contents,
    do_check_levels_for_period,
    do_check_reading_levels_for_types,
    do_check_reading_type_mrids_match_pen,
    do_check_readings_for_duration,
    do_check_readings_for_types,
    do_check_readings_on_minute_boundary,
    do_check_single_level,
    do_check_site_readings_and_params,
    first_failing_check,
    is_nth_bit_set_properly,
    merge_checks,
    mrid_matches_pen,
    response_type_to_string,
    run_check,
    timestamp_on_minute_boundary,
)
from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.models import ActiveTestProcedure, Listener, ResourceAnnotations

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
    "readings-voltage": "check_readings_voltage",
    "readings-der-active-power": "check_readings_der_active_power",
    "readings-der-reactive-power": "check_readings_der_reactive_power",
    "all-notifications-transmitted": "check_all_notifications_transmitted",
    "subscription-contents": "check_subscription_contents",
    "response-contents": "check_response_contents",
    "readings-der-stored-energy": "check_readings_der_stored_energy",
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
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        pen=0,
        client_certificate_type="Device",
        client_lfdi="",
        step_status={},
        finished_zip_data=None,
    )
    mock_get_active_site.return_value = active_site
    mock_session = create_mock_session()
    resolved_params = {}
    if has_connection_point_id is not None:
        resolved_params["has_connection_point_id"] = has_connection_point_id

    result = await check_end_device_contents(mock_active_test_procedure, mock_session, resolved_params)
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
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        pen=0,
        client_certificate_type="Device",
        client_lfdi="",
        step_status={},
        finished_zip_data=None,
    )

    mock_get_active_site.return_value = active_site
    mock_session = create_mock_session()
    resolved_params = {}
    if deviceCategory_anyset is not None:
        resolved_params["deviceCategory_anyset"] = deviceCategory_anyset

    result = await check_end_device_contents(mock_active_test_procedure, mock_session, resolved_params)
    assert_check_result(result, expected)

    assert_mock_session(mock_session)


@pytest.mark.parametrize(
    "active_test_procedure, site_lfdi, site_sfdi, check_lfdi, expected",
    [
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=0,
                client_certificate_type="Device",
                step_status={},
                finished_zip_data=None,
            ),
            "abc123",
            123456,
            None,
            True,
        ),  # check_lfdi param not supplied (everything else is invalid)
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=1,
                client_certificate_type="Device",
                client_lfdi="3e4f45ab31edfe5b67e343e5e4562e31984e23e5",
                client_sfdi=167261211391,
                step_status={},
                finished_zip_data=None,
            ),
            "3E4F45AB31EDFE5B67E343E5E4562E31984E23E5",
            167261211391,
            True,
            True,
        ),  # pen shouldn't be checked with certificate type "Device" - everything is valid
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=98492395,
                client_certificate_type="Aggregator",
                step_status={},
                finished_zip_data=None,
            ),
            "3E4F45AB31EDFE5B67E343E5E4562E3198492395",
            167261211391,
            True,
            True,
        ),  # check everything
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=int("984e23e5", 16),
                client_certificate_type="Aggregator",
                step_status={},
                finished_zip_data=None,
            ),
            "3E4F45AB31EDFE5B67e343E5E4562E31984E23E5",  # single lowercase e in the middle
            167261211391,
            True,
            False,
        ),  # only upper case hex characters are allowed
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=int("984e23e5", 16),
                client_certificate_type="Aggregator",
                step_status={},
                finished_zip_data=None,
            ),
            "3E4F45AB31EDFE5B6XE343E5E4562E31984E23E5",
            167261211391,
            True,
            False,
        ),  # Random X in the middle of the LFDI (bad character)
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=int("984e23e5", 16),
                client_certificate_type="Aggregator",
                step_status={},
                finished_zip_data=None,
            ),
            "3E4F45AB31EDFE5B67FE343E5E4562E31984E23E5",
            167261211391,
            True,
            False,
        ),  # Extra long LFDI
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=123,
                client_certificate_type="Aggregator",
                step_status={},
                finished_zip_data=None,
            ),
            "3E4F45AB31EDFE5B67E343E5E4562E31984E23E5",
            167261211391,
            True,
            False,
        ),  # PEN is wrong
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=int("984e23e5", 16),
                client_certificate_type="Aggregator",
                step_status={},
                finished_zip_data=None,
            ),
            "3E4F45AB31EDFE5B67E343E5E4562E31984E23E5",
            1234,
            True,
            False,
        ),  # sfdi doesn't match lfdi
    ],
)
@mock.patch("cactus_runner.app.check.get_active_site")
@pytest.mark.anyio
async def test_check_end_device_lfdi(
    mock_get_active_site: mock.MagicMock,
    active_test_procedure: ActiveTestProcedure,
    site_lfdi: str,
    site_sfdi: int,
    check_lfdi: bool | None,
    expected: bool,
):
    active_site = generate_class_instance(
        Site, device_category=DeviceCategory(int("22A8B", 16)), lfdi=site_lfdi, sfdi=site_sfdi
    )
    mock_get_active_site.return_value = active_site
    mock_session = create_mock_session()
    resolved_params = {}
    if check_lfdi is not None:
        resolved_params["check_lfdi"] = check_lfdi

    result = await check_end_device_contents(active_test_procedure, mock_session, resolved_params)
    assert_check_result(result, expected)


DERKey = Literal["site_der_setting", "site_der_rating"]


def der_bool_param_scenario(
    der_key: DERKey,
    der_type: type,
    param: str,
    param_value: bool,
    db_property: str,
    db_property_value: Any,
    expected: bool,
) -> Any:
    """Convenience for generating scenarios for testing the der settings/capability boolean param checks.

    Returns: pytest ParameterSet to be used in pytest.mark.parametrize
    """
    der_props: dict[DERKey, SiteDERSetting | SiteDERRating] = {
        der_key: generate_class_instance(der_type, **{db_property: db_property_value})
    }

    if der_key == "site_der_rating":
        return pytest.param(
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[generate_class_instance(SiteDER, site_der_rating=der_props[der_key])],
                )
            ],
            {param: evaluator.ResolvedParam(param_value)},
            expected,
            id=f"boolcheck-{param}-{param_value}-{db_property}-{db_property_value}-expecting-{expected}",
        )
    if der_key == "site_der_setting":
        return pytest.param(
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[generate_class_instance(SiteDER, site_der_setting=der_props[der_key])],
                )
            ],
            {param: evaluator.ResolvedParam(param_value)},
            expected,
            id=f"boolcheck-{param}-{param_value}-{db_property}-{db_property_value}-expecting-{expected}",
        )


DERSETTING_BOOL_PARAM_SCENARIOS = [
    der_bool_param_scenario("site_der_setting", SiteDERSetting, param, param_value, db_prop, db_prop_value, expected)
    for param, db_prop in [
        ("doeModesEnabled", "doe_modes_enabled"),
        ("setMaxVA", "max_va_value"),
        ("setMaxVar", "max_var_value"),
        ("setMaxVarNeg", "max_var_neg_value"),
        ("setMaxChargeRateW", "max_charge_rate_w_value"),
        ("setMaxDischargeRateW", "max_discharge_rate_w_value"),
        ("setMaxWh", "max_wh_value"),
        ("setMinPFOverExcited", "min_pf_over_excited_displacement"),
        ("setMinPFUnderExcited", "min_pf_under_excited_displacement"),
    ]
    for param_value, db_prop_value, expected in [
        (True, 2, True),
        (True, None, False),
        (False, None, True),
        (False, 2, False),
    ]
] + [
    # These values can't be nulled - so we do a cursory check of the value being set
    der_bool_param_scenario("site_der_setting", SiteDERSetting, "setMaxW", True, "max_w_value", 2, True),
    der_bool_param_scenario("site_der_setting", SiteDERSetting, "setMaxW", False, "max_w_value", 2, False),
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
                            SiteDER,
                            site_der_setting=generate_class_instance(
                                SiteDERSetting,
                                optional_is_none=True,
                                max_charge_rate_w_value=0,
                                max_charge_rate_w_multiplier=0,
                                max_discharge_rate_w_value=0,
                                max_discharge_rate_w_multiplier=0,
                                max_va_value=0,
                                max_va_multiplier=0,
                                max_var_value=0,
                                max_var_multiplier=0,
                                max_var_neg_value=0,
                                max_var_neg_multiplier=0,
                                max_w_value=0,
                                max_w_multiplier=0,
                                min_pf_over_excited_displacement=0,
                                min_pf_over_excited_multiplier=0,
                                min_pf_under_excited_displacement=0,
                                min_pf_under_excited_multiplier=0,
                            ),
                        )
                    ],
                )
            ],
            {
                "setMaxChargeRateW": evaluator.ResolvedParam(True),
                "setMaxDischargeRateW": evaluator.ResolvedParam(True),
                "setMaxVA": evaluator.ResolvedParam(True),
                "setMaxVar": evaluator.ResolvedParam(True),
                "setMaxVarNeg": evaluator.ResolvedParam(True),
                "setMaxW": evaluator.ResolvedParam(True),
                "setMinPFOverExcited": evaluator.ResolvedParam(True),
                "setMinPFUnderExcited": evaluator.ResolvedParam(True),
            },
            True,
        ),  # 0 values are acceptable
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
            {"setGradW": evaluator.ResolvedParam(12345, variable_expressions.Constant(12345))},
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
            {"setGradW": evaluator.ResolvedParam(1234)},
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
            {"doeModesEnabled_set": evaluator.ResolvedParam("03")},
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
            {"doeModesEnabled_set": evaluator.ResolvedParam("03")},
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
            {"modesEnabled_set": evaluator.ResolvedParam("03")},
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
            {"modesEnabled_set": evaluator.ResolvedParam("03")},
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
            {"doeModesEnabled_unset": evaluator.ResolvedParam("03")},
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
            {"doeModesEnabled_unset": evaluator.ResolvedParam("03")},
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
            {"modesEnabled_unset": evaluator.ResolvedParam("03")},
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
            {"modesEnabled_unset": evaluator.ResolvedParam("03")},
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


DERRATING_BOOL_PARAM_SCENARIOS = [
    der_bool_param_scenario("site_der_rating", SiteDERRating, param, param_value, db_prop, db_prop_value, expected)
    for param, db_prop in [
        ("doeModesSupported", "doe_modes_supported"),
        ("rtgMaxVA", "max_va_value"),
        ("rtgMaxVar", "max_var_value"),
        ("rtgMaxVarNeg", "max_var_neg_value"),
        ("rtgMaxChargeRateW", "max_charge_rate_w_value"),
        ("rtgMaxDischargeRateW", "max_discharge_rate_w_value"),
        ("rtgMaxWh", "max_wh_value"),
        ("rtgMinPFOverExcited", "min_pf_over_excited_displacement"),
        ("rtgMinPFUnderExcited", "min_pf_under_excited_displacement"),
    ]
    for param_value, db_prop_value, expected in [
        (True, 2, True),
        (True, None, False),
        (False, None, True),
        (False, 2, False),
    ]
] + [
    # These are non nullable and so we only do a cursory check
    der_bool_param_scenario("site_der_rating", SiteDERRating, "rtgMaxW", True, "max_w_value", 2, True),
    der_bool_param_scenario("site_der_rating", SiteDERRating, "rtgMaxW", False, "max_w_value", 2, False),
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
                        generate_class_instance(
                            SiteDER,
                            site_der_rating=generate_class_instance(
                                SiteDERRating,
                                optional_is_none=True,
                                max_charge_rate_w_value=0,
                                max_charge_rate_w_multiplier=0,
                                max_discharge_rate_w_value=0,
                                max_discharge_rate_w_multiplier=0,
                                max_va_value=0,
                                max_va_multiplier=0,
                                max_var_value=0,
                                max_var_multiplier=0,
                                max_var_neg_value=0,
                                max_var_neg_multiplier=0,
                                max_w_value=0,
                                max_w_multiplier=0,
                                min_pf_over_excited_displacement=0,
                                min_pf_over_excited_multiplier=0,
                                min_pf_under_excited_displacement=0,
                                min_pf_under_excited_multiplier=0,
                            ),
                        )
                    ],
                )
            ],
            {
                "rtgMaxChargeRateW": evaluator.ResolvedParam(True),
                "rtgMaxDischargeRateW": evaluator.ResolvedParam(True),
                "rtgMaxVA": evaluator.ResolvedParam(True),
                "rtgMaxVar": evaluator.ResolvedParam(True),
                "rtgMaxVarNeg": evaluator.ResolvedParam(True),
                "rtgMaxW": evaluator.ResolvedParam(True),
                "rtgMinPFOverExcited": evaluator.ResolvedParam(True),
                "rtgMinPFUnderExcited": evaluator.ResolvedParam(True),
            },
            True,
        ),  # 0 values are acceptable
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
            {"doeModesSupported_set": evaluator.ResolvedParam("03")},
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
            {"doeModesSupported_set": evaluator.ResolvedParam("03")},
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
            {"modesSupported_set": evaluator.ResolvedParam("03")},
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
            {"modesSupported_set": evaluator.ResolvedParam("03")},
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
            {"doeModesSupported_unset": evaluator.ResolvedParam("03")},
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
            {"doeModesSupported_unset": evaluator.ResolvedParam("03")},
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
            {"modesSupported_unset": evaluator.ResolvedParam("03")},
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
            {"modesSupported_unset": evaluator.ResolvedParam("03")},
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


@dataclasses.dataclass
class ReadingTestScenario:
    srt_id: int
    readings: list[int]


LEVEL_SCENARIOS: list[ReadingTestScenario] = [
    ReadingTestScenario(1, [50, 51, 60]),
    ReadingTestScenario(2, [5, 10, 0]),
    ReadingTestScenario(3, [501, 510, 600]),
    ReadingTestScenario(2, [50, 51, 61]),
    ReadingTestScenario(1, [45, 60, 60, 60, 60]),
]


@pytest.mark.parametrize(
    "srt_ids, readings, mult, min_level, max_level, expected",
    [
        # >= 60.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 60.0, None, True),
        # >= 60.1
        ([1], [LEVEL_SCENARIOS[0]], 0, 60.1, None, False),
        # <= 59.9
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 59.9, False),
        # <= 60.0
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 60.0, True),
        # 50.0 <= value <= 70.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 50.0, 70.0, True),
        # 40.0 <= value <= 45.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 40.0, 45.0, False),
        # -40.0 <= value <= 45.0 with pow10 == 1
        ([2], [LEVEL_SCENARIOS[1]], 1, -40.0, 45.0, True),
        # value == 60.0 with pow10 == -1
        ([3], [LEVEL_SCENARIOS[2]], -1, 60.0, 60.0, True),
        # Two reading types with 59.0 <= value <= 62.0
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 59.0, 62.0, True),
        # Two reading type with 60.5 <= value <= 62.0 (one site reading type passes, one fails)
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 60.5, 62.0, False),
        # No readings for the chosen SiteReadingType
        ([3], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 59.0, 62.0, False),
    ],
)
@pytest.mark.anyio
async def test_do_check_single_level(
    pg_base_config,
    srt_ids: list[int],
    readings: list[ReadingTestScenario],
    mult: int,
    min_level: float | None,
    max_level: float | None,
    expected: bool,
):
    """Tests that do_check_single_level can handle various queries against a static DB model"""
    async with generate_async_session(pg_base_config) as session:
        # Load 3 SiteReadingTypes
        site = generate_class_instance(Site, aggregator_id=1, site_id=1)
        srt1 = generate_class_instance(
            SiteReadingType, seed=101, power_of_ten_multiplier=mult, site_reading_type_id=1, aggregator_id=1, site=site
        )
        srt2 = generate_class_instance(
            SiteReadingType, seed=202, power_of_ten_multiplier=mult, site_reading_type_id=2, aggregator_id=1, site=site
        )
        srt3 = generate_class_instance(
            SiteReadingType, seed=303, power_of_ten_multiplier=mult, site_reading_type_id=3, aggregator_id=1, site=site
        )

        session.add_all([site, srt1, srt2, srt3])
        srt_d = {1: srt1, 2: srt2, 3: srt3}

        # Load scenario readings
        time_now = datetime.now()
        for i, reading_scenario in enumerate(readings, 1):
            for j, reading_value in enumerate(reading_scenario.readings, 1):
                session.add(
                    generate_class_instance(
                        SiteReading,
                        seed=i * len(reading_scenario.readings) + j,
                        site_reading_type=srt_d[reading_scenario.srt_id],
                        value=reading_value,
                        time_period_start=time_now + timedelta(minutes=j),
                        time_period_seconds=60,
                        # Purposefully going back in time to show time_period being used to calculate
                        created_time=time_now - timedelta(hours=j),
                    )
                )

        await session.commit()

    faked_srts = [
        generate_class_instance(SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]

    async with generate_async_session(pg_base_config) as session:

        result = await do_check_single_level(session, faked_srts, min_level, max_level)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "srt_ids, readings, mult, min_level, max_level, window_s, expected",
    [
        # >= 60.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 60.0, None, 180, False),
        # Window too small
        ([1], [LEVEL_SCENARIOS[0]], 0, 60.0, None, 30, False),
        # >= 50.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 50.0, None, 180, True),
        # <= 59.9
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 59.9, 180, False),
        # <= 60.0
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 60.0, 180, True),
        # 50.0 <= value <= 70.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 50.0, 70.0, 180, True),
        # 40.0 <= value <= 45.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 40.0, 45.0, 180, False),
        # -40.0 <= value <= 45.0 with pow10 == 1
        ([1], [LEVEL_SCENARIOS[1]], 1, -40.0, 45.0, 180, False),
        # value == 60.0 with pow10 == -1
        ([3], [LEVEL_SCENARIOS[2]], -1, 60.0, 60.0, 180, False),
        # Window size includes first low reading
        ([1], [LEVEL_SCENARIOS[4]], 0, 60.0, 60.0, 600, False),
        # Window size doesn't include first low reading
        ([1], [LEVEL_SCENARIOS[4]], 0, 60.0, 60.0, 180, True),
        # Two reading types with 59.0 <= value <= 62.0
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 50.0, 62.0, 180, True),
        # Two reading type with 60.5 <= value <= 62.0
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 60.5, 62.0, 180, False),
        # Testing window contains window boundary reading (where period start == window start)
        ([2], [LEVEL_SCENARIOS[3]], 0, 50.5, None, 180, False),
        ([2], [LEVEL_SCENARIOS[3]], 0, 50.5, None, 179, True),
    ],
)
@pytest.mark.anyio
async def test_do_check_levels_for_period(
    pg_base_config,
    srt_ids: list[int],
    readings: list[ReadingTestScenario],
    mult: int,
    min_level: float | None,
    max_level: float | None,
    window_s: int,
    expected: bool,
):
    """Tests that do_check_levels_for_period can handle various queries against a static DB model"""
    async with generate_async_session(pg_base_config) as session:
        # Load 3 SiteReadingTypes
        site = generate_class_instance(Site, aggregator_id=1, site_id=1)
        srt1 = generate_class_instance(
            SiteReadingType, seed=101, power_of_ten_multiplier=mult, site_reading_type_id=1, aggregator_id=1, site=site
        )
        srt2 = generate_class_instance(
            SiteReadingType, seed=202, power_of_ten_multiplier=mult, site_reading_type_id=2, aggregator_id=1, site=site
        )
        srt3 = generate_class_instance(
            SiteReadingType, seed=303, power_of_ten_multiplier=mult, site_reading_type_id=3, aggregator_id=1, site=site
        )

        session.add_all([site, srt1, srt2, srt3])
        srt_d = {1: srt1, 2: srt2, 3: srt3}

        time_now = datetime.now()
        # Load scenario readings
        for i, reading_scenario in enumerate(readings, 1):
            for j, reading_value in enumerate(reading_scenario.readings, 1):
                session.add(
                    generate_class_instance(
                        SiteReading,
                        seed=i * len(reading_scenario.readings) + j,
                        site_reading_type=srt_d[reading_scenario.srt_id],
                        value=reading_value,
                        created_time=time_now + timedelta(minutes=j),
                        time_period_start=time_now + timedelta(minutes=j) - timedelta(seconds=60),
                        time_period_seconds=60,
                    )
                )

        await session.commit()

    faked_srts = [
        generate_class_instance(SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]

    async with generate_async_session(pg_base_config) as session:
        window_period = timedelta(seconds=window_s)
        result = await do_check_levels_for_period(session, faked_srts, min_level, max_level, window_period)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "resolved_params, outcome",
    [
        ({"minimum_level": 1, "maximum_level": 2, "window_seconds": 3}, (True, True)),
        ({"minimum_level": 1, "maximum_level": 2}, (True, False)),
        ({"minimum_level": 1}, (True, False)),
        ({"maximum_level": 2}, (True, False)),
        ({}, (False, False)),
    ],
)
@pytest.mark.anyio
async def test_do_check_reading_levels_for_types(
    mocker: pytest_mock.MockerFixture, resolved_params: dict[str, Any], outcome: tuple[bool, bool]
) -> None:
    """Ensures that the matching function works as expected for the correct combinations of resolved parameters.

    Args:
        mocker: the mocker fixture
        resolved_params: dictionary passed in containing parameters resolved during evaluation
        outcome: indicates the combination of called level functions to be expected to have been called
            for the given combination of resolved parameters (bool, bool) relating to windowed level
            and single level respectively
    """
    mock_single_level = mocker.patch("cactus_runner.app.check.do_check_single_level")
    mock_level_period = mocker.patch("cactus_runner.app.check.do_check_levels_for_period")
    session = mocker.AsyncMock()

    result = await do_check_reading_levels_for_types(session, [], resolved_params)
    match outcome:
        case (True, True):
            mock_level_period.assert_called_once()
        case (True, False):
            mock_single_level.assert_called_once()
        case (False, False):
            assert_check_result(result, True)
        case _:
            assert False, "Unhandled test case found"


@pytest.mark.parametrize(
    "srt_ids, expected",
    [
        ([], True),
        ([1], True),
        ([1, 2], False),  # srt 2 readings not-aligned
        ([1, 3], True),
        ([1, 2, 3], False),  # srt 2 readings not-aligned
        ([2, 3], False),  # srt 2 readings not-aligned
        ([3], True),
        ([99], True),
        ([1, 99], True),
        ([2, 99], False),  # srt 2 readings not-aligned
        ([3, 99], True),
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_on_minute_boundary(pg_base_config, srt_ids: list[int], expected: bool):
    """Tests that do_check_readings_for_types can handle various queries against a static DB model"""
    async with generate_async_session(pg_base_config) as session:
        # Load 3 SiteReadingTypes, the first has 3 readings, the second has 2.
        site = generate_class_instance(Site, aggregator_id=1, site_id=1)
        srt1 = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        srt2 = generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=2, aggregator_id=1, site=site)
        srt3 = generate_class_instance(SiteReadingType, seed=303, site_reading_type_id=3, aggregator_id=1, site=site)

        session.add_all([site, srt1, srt2, srt3])
        session.add(
            generate_class_instance(
                SiteReading,
                seed=11,
                site_reading_type=srt1,
                time_period_start=datetime.fromisoformat("2011-11-04T00:05:00"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=22,
                site_reading_type=srt1,
                time_period_start=datetime.fromisoformat("2011-11-04T00:06:00"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=33,
                site_reading_type=srt1,
                time_period_start=datetime.fromisoformat("2011-11-04T00:07:00"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=44,
                site_reading_type=srt2,
                time_period_start=datetime.fromisoformat("2011-11-04T00:05:13"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=55,
                site_reading_type=srt2,
                time_period_start=datetime.fromisoformat("2011-11-04T00:06:23"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=66,
                site_reading_type=srt2,
                time_period_start=datetime.fromisoformat("2011-11-04T00:07:59"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=77,
                site_reading_type=srt3,
                time_period_start=datetime.fromisoformat("2012-12-05T01:55:00"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=88,
                site_reading_type=srt3,
                time_period_start=datetime.fromisoformat("2012-12-05T02:55:00"),
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=99,
                site_reading_type=srt3,
                time_period_start=datetime.fromisoformat("2012-12-05T03:55:00"),
            )
        )

        await session.commit()

    faked_srts = [
        generate_class_instance(SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]

    async with generate_async_session(pg_base_config) as session:
        result = await do_check_readings_on_minute_boundary(session, faked_srts)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "mrid,pen,expected_result",
    [
        ("", 0, False),
        ("FF00000000", 0, True),
        ("FF00123456", 123456, True),
        ("FF00123466", 123456, False),  # off by 1
        ("FF00123456", 123457, False),  # off by 1
        ("FF99999999", 99999999, True),  # the largest pen 99999999
    ],
)
def test_mrid_matches_pen(mrid: str, pen: int, expected_result: bool):
    assert mrid_matches_pen(pen=pen, mrid=mrid) == expected_result


@pytest.mark.parametrize(
    "mrid1,mrid2,group_mrid,pen,expected_result",
    [
        (None, None, None, 0, True),
        ("1100123456", "2200123456", "3300123456", 123456, True),
        ("1100120056", "2200123456", "3300123456", 123456, False),  # mrid doesn't match pen
        ("1100000000", "2200000000", "3300123456", 123456, False),  # mrid doesn't match pen  # mrid doesn't match pen
        ("1100123456", "2200123456", "3300000000", 123456, False),  # group mrid doesn't match pen
        (
            "1100000000",  # mrid doesn't match pen
            "2200000000",  # mrid doesn't match pen
            "3300000000",  # group mrid doesn't match pen
            123456,
            False,
        ),
    ],
)
@pytest.mark.anyio
async def test_do_check_reading_type_mrids_match_pen(
    mrid1: str | None, mrid2: str | None, group_mrid: str | None, pen: int, expected_result: bool
):
    # Arrange
    site = generate_class_instance(Site, aggregator_id=1, site_id=1)
    srts = []
    if mrid1 is not None and group_mrid is not None:
        srt1 = generate_class_instance(
            SiteReadingType,
            seed=101,
            site_reading_type_id=1,
            aggregator_id=1,
            site=site,
            mrid=mrid1,
            group_mrid=group_mrid,
        )
        srts.append(srt1)
    if mrid2 is not None and group_mrid is not None:
        srt2 = generate_class_instance(
            SiteReadingType,
            seed=101,
            site_reading_type_id=1,
            aggregator_id=1,
            site=site,
            mrid=mrid2,
            group_mrid=group_mrid,
        )
        srts.append(srt2)

    # Act
    result = await do_check_reading_type_mrids_match_pen(site_reading_types=srts, pen=pen)

    # Assert
    assert_check_result(result, expected_result)


@pytest.mark.parametrize(
    "resolved_parameters, uom, reading_location, qualifier, kind, site_reading_types, pen, expected_min_count",
    [
        (
            {},
            UomType.REAL_POWER_WATT,
            ReadingLocation.SITE_READING,
            DataQualifierType.AVERAGE,
            KindType.POWER,
            [],
            0,
            None,
        ),
        (
            {},
            UomType.APPARENT_ENERGY_VAH,
            ReadingLocation.DEVICE_READING,
            DataQualifierType.MINIMUM,
            KindType.POWER,
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1),
            ],
            64,
            None,
        ),
        (
            {"minimum_count": 123, "foo": 456},
            UomType.BRITISH_THERMAL_UNIT,
            ReadingLocation.DEVICE_READING,
            DataQualifierType.STANDARD,
            KindType.POWER,
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=4),
                generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=2),
            ],
            888,
            123,
        ),
        (
            {"minimum_count": 0},
            UomType.FREQUENCY_HZ,
            ReadingLocation.SITE_READING,
            DataQualifierType.MAXIMUM,
            KindType.ENERGY,
            [
                generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=2),
            ],
            0,
            0,
        ),
    ],
)
@mock.patch("cactus_runner.app.check.get_csip_aus_site_reading_types")
@mock.patch("cactus_runner.app.check.do_check_readings_for_types")
@mock.patch("cactus_runner.app.check.do_check_readings_on_minute_boundary")
@mock.patch("cactus_runner.app.check.do_check_reading_type_mrids_match_pen")
@mock.patch("cactus_runner.app.check.do_check_readings_for_duration")
@pytest.mark.anyio
async def test_do_check_site_readings_and_params(
    mock_do_check_readings_for_duration: mock.MagicMock,
    mock_do_check_reading_type_mrids_match_pen: mock.MagicMock,
    mock_do_check_readings_on_minute_boundary: mock.MagicMock,
    mock_do_check_readings_for_types: mock.MagicMock,
    mock_get_csip_aus_site_reading_types: mock.MagicMock,
    resolved_parameters: dict[str, Any],
    uom: UomType,
    reading_location: ReadingLocation,
    qualifier: DataQualifierType,
    kind: KindType,
    site_reading_types: list[SiteReadingType],
    pen: int,
    expected_min_count: int | None,
):
    """Tests that do_check_site_readings_and_params does the basic logic it needs before offloading to
    do_check_readings_for_types"""
    # Arrange
    mock_session = create_mock_session()
    expected_result = generate_class_instance(CheckResult)
    mock_get_csip_aus_site_reading_types.return_value = site_reading_types
    mock_do_check_readings_for_types.return_value = expected_result
    mock_do_check_readings_on_minute_boundary.return_value = CheckResult(True, description=None)
    mock_do_check_reading_type_mrids_match_pen.return_value = CheckResult(True, description=None)
    mock_do_check_readings_for_duration.return_value = CheckResult(True, description=None)

    # Act
    result = await do_check_site_readings_and_params(
        mock_session, resolved_parameters, pen, uom, reading_location, qualifier, kind
    )

    # Assert
    assert_mock_session(mock_session)
    mock_get_csip_aus_site_reading_types.assert_called_once_with(mock_session, uom, reading_location, kind, qualifier)

    # If we have 0 SiteReadingTypes - instant failure, no need to run the reading checks
    if len(site_reading_types) != 0:
        assert result == expected_result
        mock_do_check_readings_for_types.assert_called_once_with(mock_session, site_reading_types, expected_min_count)
        mock_do_check_readings_on_minute_boundary.assert_called_once_with(mock_session, site_reading_types)
        mock_do_check_reading_type_mrids_match_pen.assert_called_once_with(site_reading_types, pen)
        mock_do_check_readings_for_duration.assert_called_once_with(mock_session, site_reading_types)
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
        if "reading" in check_type.lower() and check_type != "readings-voltage"  # voltage readings are a special case
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


@pytest.mark.parametrize(
    "site_passed, device_passed, expected_result",
    [(False, False, False), (True, False, True), (False, True, True), (True, True, True)],
)
@mock.patch("cactus_runner.app.check.do_check_site_readings_and_params")
@pytest.mark.anyio
async def test_check_readings_voltage(
    mock_do_check_site_readings_and_params: mock.MagicMock,
    site_passed: bool,
    device_passed: bool,
    expected_result: bool,
):
    """The check for voltage readings is unique as it acts as an "OR" for the device and site level tests. This test
    enumerates the various possibilities for do_check_site_readings_and_params and what the expected check result
    should be under those circumstances"""

    # Arrange
    mock_session = create_mock_session()
    resolved_params = {}
    pen = 123
    site_check_result = generate_class_instance(CheckResult, seed=101, passed=site_passed)
    device_check_result = generate_class_instance(CheckResult, seed=202, passed=device_passed)
    mock_do_check_site_readings_and_params.side_effect = lambda session, params, pen, uom, location, dq: (
        site_check_result if location == ReadingLocation.SITE_READING else device_check_result
    )

    # Act
    result = await check_readings_voltage(mock_session, resolved_params, pen)

    # Assert
    assert_mock_session(mock_session)
    assert isinstance(result, CheckResult)
    assert result.passed is expected_result
    assert site_check_result.description in result.description or device_check_result.description in result.description

    # Cursory look at passed params
    assert mock_do_check_site_readings_and_params.call_count >= 1
    assert all([ca.args[0] is mock_session for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[1] is resolved_params for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[2] is pen for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[3] is UomType.VOLTAGE for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[4] in ReadingLocation for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all(
        [ca.args[5] is DataQualifierType.AVERAGE for ca in mock_do_check_site_readings_and_params.call_args_list]
    )


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
        actual = await check_subscription_contents(resolved_params, session)
        assert_check_result(actual, True)


@pytest.mark.anyio
async def test_check_subscription_contents_success_unscoped(pg_base_config):
    """check_subscription_contents should succeed if there is an unscoped subscription - eg to /edev"""

    resolved_params = {"subscribed_resource": "/edev"}

    # Fill up the DB with subscriptions
    async with generate_async_session(pg_base_config) as session:
        agg1 = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == 1))).scalar_one()
        agg2 = Aggregator(aggregator_id=2, name="test2", changed_time=datetime(2022, 11, 22, tzinfo=timezone.utc))
        session.add(agg2)

        site1 = generate_class_instance(Site, seed=1001, site_id=1, aggregator_id=1)  # Active Site
        session.add(site1)
        await session.flush()

        # Should match
        session.add(
            generate_class_instance(
                Subscription,
                seed=202,
                resource_type=SubscriptionResource.SITE,
                resource_id=None,
                aggregator=agg1,
                scoped_site_id=None,
            )
        )

        await session.commit()

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
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_data=None)
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
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
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
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=707,
                response_type=ResponseType.EVENT_RECEIVED,
                created_time=datetime(2024, 11, 9, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        # This will check that there is a latest
        assert_check_result(await check_response_contents({"latest": True}, session, active_test_procedure), True)

        # This will check that there is a latest and that the status matches the filter
        assert_check_result(
            await check_response_contents(
                {"latest": True, "status": ResponseType.EVENT_COMPLETED.value}, session, active_test_procedure
            ),
            True,
        )

        # This will check that the filter on latest will fail if there is mismatch on the latest record
        assert_check_result(
            await check_response_contents(
                {"latest": True, "status": ResponseType.EVENT_CANCELLED.value}, session, active_test_procedure
            ),
            False,
        )


@pytest.mark.parametrize(
    "status, control_ids, deleted_control_ids, response_status_values, expected",
    [
        (1, [], [], [], True),
        (1, [1], [], [], False),
        (1, [1], [], [(1, 2), (1, 1)], True),
        (1, [1], [2], [(1, 2), (1, 1)], False),  # Deleted control 2 was not responded to
        (3, [1], [], [(1, 2), (1, 1)], False),  # No items with response_status 3
        (None, [1], [], [(1, 2), (1, 1)], True),
        (1, [1, 2], [], [(1, 2), (1, 1)], False),  # Control 2 has no responses
        (1, [1, 2], [], [(1, 2), (1, 1), (2, 2)], False),  # Control 2 has no responses of type 2
        (2, [1, 2], [], [(1, 2), (1, 1), (2, 2)], True),
        (2, [1], [2], [(1, 2), (1, 1), (2, 2)], True),
    ],
)
@pytest.mark.anyio
async def test_check_response_contents_all(
    pg_base_config,
    status: int | None,
    control_ids: list[int],
    deleted_control_ids: list[int],
    response_status_values: list[tuple[int, int]],
    expected: bool,
):
    """check_response_contents should behave correctly when looking at all controls having responses

    response_status_values: tuple[control_id, response_status_type]"""
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_data=None)
    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:

        site_control_group = generate_class_instance(SiteControlGroup, seed=101)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        for idx, control_id in enumerate(control_ids):
            control = generate_class_instance(
                DynamicOperatingEnvelope,
                seed=idx,
                site=site1,
                site_control_group=site_control_group,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        for idx, control_id in enumerate(deleted_control_ids):
            control = generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=idx * 1001,
                site_id=site1.site_id,
                deleted_time=datetime(2022, 11, 14, tzinfo=timezone.utc),
                site_control_group_id=site_control_group.site_control_group_id,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        for idx, t in enumerate(response_status_values):
            (response_control_id, response_status) = t
            session.add(
                generate_class_instance(
                    DynamicOperatingEnvelopeResponse,
                    seed=idx,
                    site=site1,
                    response_type=response_status,
                    dynamic_operating_envelope_id_snapshot=response_control_id,
                )
            )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        params = {"all": True}
        if status is not None:
            params["status"] = status
        assert_check_result(await check_response_contents(params, session, active_test_procedure), expected)


@pytest.mark.anyio
async def test_check_response_contents_any(pg_base_config):
    """check_response_contents should behave correctly when looking at ANY of the Responses"""
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_data=None)
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
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=606,
                response_type=ResponseType.EVENT_COMPLETED,
                created_time=datetime(2024, 11, 11, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=707,
                response_type=ResponseType.EVENT_RECEIVED,
                created_time=datetime(2024, 11, 9, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        # This will check that there is any response
        assert_check_result(await check_response_contents({"latest": False}, session, active_test_procedure), True)
        assert_check_result(await check_response_contents({}, session, active_test_procedure), True)

        # Checks on existing values
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_COMPLETED.value}, session, active_test_procedure
            ),
            True,
        )
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_RECEIVED.value}, session, active_test_procedure
            ),
            True,
        )
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_CANCELLED.value}, session, active_test_procedure
            ),
            True,
        )

        # This will check that the filter will fail if a matching record cant be found
        assert_check_result(
            await check_response_contents(
                {"latest": False, "status": ResponseType.CANNOT_BE_DISPLAYED.value}, session, active_test_procedure
            ),
            False,
        )


@pytest.mark.anyio
async def test_check_response_contents_empty(pg_base_config):
    """check_response_contents should behave correctly when the DB is empty of responses"""
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_data=None)
    async with generate_async_session(pg_base_config) as session:
        # This will check that there is any response
        assert_check_result(await check_response_contents({"latest": False}, session, active_test_procedure), False)
        assert_check_result(await check_response_contents({"latest": True}, session, active_test_procedure), False)
        assert_check_result(await check_response_contents({}, session, active_test_procedure), False)
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_COMPLETED.value}, session, active_test_procedure
            ),
            False,
        )
        assert_check_result(
            await check_response_contents(
                {"latest": True, "status": ResponseType.EVENT_COMPLETED.value}, session, active_test_procedure
            ),
            False,
        )


@pytest.mark.anyio
async def test_check_response_contents_tag_DERC1(pg_base_config):
    """check_response_contents should behave correctly when filtering by tag"""
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, resource_annotations=ResourceAnnotations(), step_status={}, finished_zip_data=None
    )

    # Set up resource annotations with tagged control IDs
    active_test_procedure.resource_annotations.der_control_ids_by_alias = {"DERC1": 100, "DERC2": 200}

    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:

        site_control_group = generate_class_instance(SiteControlGroup, seed=101)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        # Create control with ID 100 (tagged as DERC1)
        der_control_1 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=303,
            site=site1,
            site_control_group=site_control_group,
            calculation_log_id=None,
            dynamic_operating_envelope_id=100,
        )
        session.add(der_control_1)

        # Create control with ID 200 (tagged as DERC2)
        der_control_2 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=304,
            site=site1,
            site_control_group=site_control_group,
            calculation_log_id=None,
            dynamic_operating_envelope_id=200,
        )
        session.add(der_control_2)

        # Add responses for DERC1 (control_id=100)
        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=505,
                response_type=ResponseType.EVENT_RECEIVED,
                created_time=datetime(2024, 11, 9, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope_id_snapshot=100,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=606,
                response_type=ResponseType.EVENT_COMPLETED,
                created_time=datetime(2024, 11, 11, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope_id_snapshot=100,
            )
        )

        # Add response for DERC2 (control_id=200)
        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=707,
                response_type=ResponseType.EVENT_CANCELLED,
                created_time=datetime(2024, 11, 10, tzinfo=timezone.utc),
                site=site1,
                dynamic_operating_envelope_id_snapshot=200,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        # Check responses for DERC1 tag can be found
        assert_check_result(
            await check_response_contents({"subject_tag": "DERC1"}, session, active_test_procedure), True
        )

        # Check latest response for DERC1 is EVENT_COMPLETED
        assert_check_result(
            await check_response_contents({"subject_tag": "DERC1", "latest": True}, session, active_test_procedure),
            True,
        )

        # Check latest response for DERC1 with correct status matches
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "latest": True, "status": ResponseType.EVENT_COMPLETED.value},
                session,
                active_test_procedure,
            ),
            True,
        )

        # Check latest response for DERC1 with wrong status fails
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "latest": True, "status": ResponseType.EVENT_CANCELLED.value},
                session,
                active_test_procedure,
            ),
            False,
        )

        # Check DERC1 has a response of type EVENT_RECEIVED
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "status": ResponseType.EVENT_RECEIVED.value}, session, active_test_procedure
            ),
            True,
        )

        # Check DERC1 does not have a response of type EVENT_CANCELLED
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "status": ResponseType.EVENT_CANCELLED.value}, session, active_test_procedure
            ),
            False,
        )

        # Check DERC2 has EVENT_CANCELLED (different control)
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC2", "status": ResponseType.EVENT_CANCELLED.value}, session, active_test_procedure
            ),
            True,
        )

        # Check non-existent tag returns failure
        assert_check_result(
            await check_response_contents({"subject_tag": "NONEXISTENT"}, session, active_test_procedure),
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
async def test_first_failing_check(
    mock_run_check: mock.MagicMock,
    checks: list[Check] | None,
    run_check_results: list[bool | type[Exception]],
    expected: bool | type[Exception],
):
    """Tries to trip up first_failing_check under various combinations of pass/fail/exception"""

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
            await first_failing_check(checks, generate_active_test_procedure_steps([], []), mock_session)
    else:
        first_failing_result = await first_failing_check(
            checks, generate_active_test_procedure_steps([], []), mock_session
        )

        if expected is True:
            assert first_failing_result is None
        else:
            assert isinstance(first_failing_result, CheckResult)
            assert expected is first_failing_result.passed

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
        all_checks_result = await all_checks_passing(checks, generate_active_test_procedure_steps([], []), mock_session)
        assert isinstance(all_checks_result, bool)
        assert all_checks_result == expected

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


@pytest.mark.parametrize(
    "timestamp_str, expected",
    [("2011-11-04T00:05:23", False), ("2011-11-04T00:05:00.001", False), ("2011-11-04T00:05:00", True)],
)
def test_timestamp_on_minute_boundary(timestamp_str: str, expected: bool):
    timestamp = datetime.fromisoformat(timestamp_str)

    assert timestamp_on_minute_boundary(timestamp) == expected


@pytest.mark.parametrize(
    "checkresults, expected",
    [
        ([CheckResult(True, "1")], CheckResult(True, "1")),
        ([CheckResult(False, "1")], CheckResult(False, "1")),
        (
            [CheckResult(True, "1"), CheckResult(True, "2"), CheckResult(True, "3")],
            CheckResult(True, "1\n2\n3"),
        ),  # all true
        (
            [CheckResult(False, "1"), CheckResult(False, "2"), CheckResult(False, "3")],
            CheckResult(False, "1\n2\n3"),
        ),  # all false
        ([CheckResult(True, "1"), CheckResult(False, "2"), CheckResult(True, "3")], CheckResult(False, "2")),
        ([CheckResult(True, "1"), CheckResult(False, "2"), CheckResult(False, "3")], CheckResult(False, "2\n3")),
        ([CheckResult(True, "1"), CheckResult(True, "2"), CheckResult(False, "3")], CheckResult(False, "3")),
    ],
)
def test_merge_check_results(checkresults: list[CheckResult], expected: CheckResult):
    assert merge_checks(checkresults) == expected


@pytest.mark.parametrize(
    "existing_sites, resolved_params, expected, msg_regex",
    [
        ([], {}, False, r"[Nn]o [Ee]nd[Dd]evice is currently registered"),
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
            {"setGradW": evaluator.ResolvedParam(1234)},
            False,
            r"setGradW [0-9]+ doesn't match (expected)?[: ]+[0-9]+",
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
            r"No DERSetting found for [Ee]nd[Dd]evice [0-9]+",
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
            r"No DERSetting found for [Ee]nd[Dd]evice [0-9]+",
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
            r"No DERSetting found for [Ee]nd[Dd]evice [0-9]+",
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
            {"doeModesEnabled_set": evaluator.ResolvedParam("03")},
            False,
            r"doeModesEnabled_set.* minimum flag .* check hi.* failed",
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
                            site_der_setting=generate_class_instance(SiteDERSetting, modes_enabled=int("fe", 16)),
                        )
                    ],
                )
            ],
            {"modesEnabled_set": evaluator.ResolvedParam("03")},
            False,
            r"modesEnabled_set.* minimum flag .* check hi.* failed",
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
                            site_der_setting=generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"doeModesEnabled_unset": evaluator.ResolvedParam("03")},
            False,
            r"doeModesEnabled_unset.* minimum flag .* check lo.* failed",
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
                            site_der_setting=generate_class_instance(SiteDERSetting, modes_enabled=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"modesEnabled_unset": evaluator.ResolvedParam("03")},
            False,
            r"modesEnabled_unset.* minimum flag .* check lo.* failed",
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
                            site_der_setting=generate_class_instance(SiteDERSetting, max_va_value=12345),
                        )
                    ],
                )
            ],
            {
                "setMaxVA": evaluator.ResolvedParam(
                    False,
                    variable_expressions.Expression(
                        variable_expressions.OperationType.EQ,
                        variable_expressions.NamedVariable(
                            variable_expressions.NamedVariableType.DERSETTING_SET_MAX_VA
                        ),
                        variable_expressions.Constant(54321),
                    ),
                )
            },
            False,
            r"setMaxVA (must|MUST) satisfy (expression)?.*setMaxVA ?== ?54321.*currently set as[: ]{1} ?12345",
        ),  # Expression boolean not met
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_setting=generate_class_instance(SiteDERSetting, max_va_value=12345),
                        )
                    ],
                )
            ],
            {
                "setMaxVA": evaluator.ResolvedParam(
                    False,
                    None,
                )
            },
            False,
            r"setMaxVA (MUST|must).* unset.* currently.*:? 12345",
        ),  # Set boolean not met
    ],
)
@pytest.mark.anyio
async def test_check_der_settings_contents_error_messages_meaningful(
    pg_base_config, existing_sites: list[Site], resolved_params: dict[str, Any], expected: bool, msg_regex: str
):
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await check_der_settings_contents(session, resolved_params)
        assert_check_result(result, expected)
        assert result.description is not None
        assert (
            re.search(msg_regex, result.description) is not None
        ), f"'{msg_regex}' not found in '{result.description}'"


@pytest.mark.parametrize(
    "existing_sites, resolved_params, expected, msg_regex",
    [
        ([], {}, False, r"[Nn]o [Ee]nd ?[Dd]evice is currently registered"),
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
            r"[Nn]o DERCapability found for [Ee]nd ?[Dd]evice [0-9]+",
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
            r"[Nn]o DERCapability found for [Ee]nd ?[Dd]evice [0-9]+",
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
            r"[Nn]o DERCapability found for [Ee]nd ?[Dd]evice [0-9]+",
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
            {"doeModesSupported_set": evaluator.ResolvedParam("03")},
            False,
            r"doeModesSupported_set.* minimum flag .* check hi.* failed",
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
                            site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fe", 16)),
                        )
                    ],
                )
            ],
            {"modesSupported_set": evaluator.ResolvedParam("03")},
            False,
            r"modesSupported_set.* minimum flag .* check hi.* failed",
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
                            site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"doeModesSupported_unset": evaluator.ResolvedParam("03")},
            False,
            r"doeModesSupported_unset.* minimum flag .* check lo.* failed",
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
                            site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fd", 16)),
                        )
                    ],
                )
            ],
            {"modesSupported_unset": evaluator.ResolvedParam("03")},
            False,
            r"modesSupported_unset.* minimum flag .* check lo.* failed",
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
                            site_der_rating=generate_class_instance(SiteDERRating, max_va_value=12345),
                        )
                    ],
                )
            ],
            {
                "rtgMaxVA": evaluator.ResolvedParam(
                    False,
                    variable_expressions.Expression(
                        variable_expressions.OperationType.EQ,
                        variable_expressions.NamedVariable(
                            variable_expressions.NamedVariableType.DERCAPABILITY_RTG_MAX_VA
                        ),
                        variable_expressions.Constant(54321),
                    ),
                )
            },
            False,
            r"rtgMaxVA (must|MUST) satisfy (expression)?.*rtgMaxVA ?== ?54321.*currently set as[: ]{1} ?12345",
        ),  # Expression boolean not met
        (
            [
                generate_class_instance(
                    Site,
                    seed=101,
                    aggregator_id=1,
                    site_ders=[
                        generate_class_instance(
                            SiteDER,
                            site_der_rating=generate_class_instance(SiteDERRating, max_va_value=12345),
                        )
                    ],
                )
            ],
            {
                "rtgMaxVA": evaluator.ResolvedParam(
                    False,
                    None,
                )
            },
            False,
            r"rtgMaxVA (MUST|must).* unset.* currently.*:? 12345",
        ),  # Set boolean not met
    ],
)
@pytest.mark.anyio
async def test_check_der_capability_contents_error_messages_meaningful(
    pg_base_config, existing_sites: list[Site], resolved_params: dict[str, Any], expected: bool, msg_regex: str
):
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        result = await check_der_capability_contents(session, resolved_params)
        assert_check_result(result, expected)
        assert result.description is not None
        assert (
            re.search(msg_regex, result.description) is not None
        ), f"'{msg_regex}' not found in '{result.description}'"


@pytest.mark.parametrize(
    "srt_ids,expected_result",
    [
        ([], True),  # No readings
        ([1], True),  # All valid
        ([2], False),  # Has zero
        ([3], False),  # Not divisible by 60
        ([4], False),  # Both zero and non-divisible
        ([1, 1], True),  # Multiple valid SRTs
        ([1, 2], False),  # One valid, one with zero
        ([2, 3], False),  # One zero, one non-divisible
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_for_duration(pg_base_config, srt_ids: list[int], expected_result: bool):
    """Tests that do_check_readings_for_duration validates time_period_seconds"""
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, aggregator_id=1, site_id=1)
        srt1 = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        srt2 = generate_class_instance(SiteReadingType, seed=102, site_reading_type_id=2, aggregator_id=1, site=site)
        srt3 = generate_class_instance(SiteReadingType, seed=103, site_reading_type_id=3, aggregator_id=1, site=site)
        srt4 = generate_class_instance(SiteReadingType, seed=104, site_reading_type_id=4, aggregator_id=1, site=site)

        session.add_all([site, srt1, srt2, srt3, srt4])

        # SRT1: All valid (60, 120, 300)
        session.add(generate_class_instance(SiteReading, seed=1001, site_reading_type=srt1, time_period_seconds=60))
        session.add(generate_class_instance(SiteReading, seed=1002, site_reading_type=srt1, time_period_seconds=120))
        session.add(generate_class_instance(SiteReading, seed=1003, site_reading_type=srt1, time_period_seconds=300))

        # SRT2: Has zero (60, 0, 120)
        session.add(generate_class_instance(SiteReading, seed=2001, site_reading_type=srt2, time_period_seconds=60))
        session.add(generate_class_instance(SiteReading, seed=2002, site_reading_type=srt2, time_period_seconds=0))
        session.add(generate_class_instance(SiteReading, seed=2003, site_reading_type=srt2, time_period_seconds=120))

        # SRT3: Not divisible by 60 (45, 90)
        session.add(generate_class_instance(SiteReading, seed=3001, site_reading_type=srt3, time_period_seconds=45))
        session.add(generate_class_instance(SiteReading, seed=3002, site_reading_type=srt3, time_period_seconds=90))

        # SRT4: Both zero and non-divisible (0, 45)
        session.add(generate_class_instance(SiteReading, seed=4001, site_reading_type=srt4, time_period_seconds=0))
        session.add(generate_class_instance(SiteReading, seed=4002, site_reading_type=srt4, time_period_seconds=45))

        await session.commit()

    faked_srts = [
        generate_class_instance(SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]

    async with generate_async_session(pg_base_config) as session:
        result = await do_check_readings_for_duration(session=session, site_reading_types=faked_srts)
        assert_check_result(result, expected_result)
