import dataclasses
import http
import re
import unittest.mock as mock
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pytest
import pytest_mock
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_schema.runner import RequestEntry
from cactus_test_definitions import variable_expressions
from cactus_test_definitions.client import (
    CHECK_PARAMETER_SCHEMA,
    Check,
    Event,
    Step,
    TestProcedure,
)
from cactus_test_definitions.csipaus import CSIPAusResource
from envoy.server.model import SiteGroup, SiteGroupAssignment
from envoy.server.model.aggregator import Aggregator
from envoy.server.model.archive.doe import ArchiveDynamicOperatingEnvelope
from envoy.server.model.archive.server import ArchiveRuntimeServerConfig
from envoy.server.model.archive.site_reading import ArchiveSiteReading
from envoy.server.model.doe import DynamicOperatingEnvelope, SiteControlGroup
from envoy.server.model.response import DynamicOperatingEnvelopeResponse
from envoy.server.model.server import RuntimeServerConfig
from envoy.server.model.site import (
    Site,
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
    PhaseCode,
    UomType,
)
from sqlalchemy import select

from cactus_runner.app import evaluator
from cactus_runner.app.check import (
    DER_VALUE_EQUIVALENTS,
    DERComparison,
    DERValueEquivalent,
    FailedCheckError,
    ParamsDERCapabilityContents,
    ParamsDERSettingsContents,
    UnknownCheckError,
    all_checks_passing,
    check_all_notifications_transmitted,
    check_all_polls_at_correct_time,
    check_all_steps_complete,
    check_der_capability_contents,
    check_der_settings_contents,
    check_der_status_contents,
    check_end_device_contents,
    check_end_device_count,
    check_readings_site_active_power,
    check_readings_voltage,
    check_resource_requests,
    check_response_contents,
    check_subscription_contents,
    check_voltage_phase_code,
    csip_aus_resource_to_match_uri,
    determine_check_results,
    do_check_latest_reading_level,
    do_check_levels_for_readings,
    do_check_reading_levels_for_types,
    do_check_reading_type_mrids_match_pen,
    do_check_readings_for_duration,
    do_check_readings_for_types,
    do_check_readings_match_post_rate,
    do_check_readings_on_minute_boundary,
    do_check_readings_received_after_interval_end,
    do_check_site_readings_and_params,
    first_failing_check,
    is_nth_bit_set_properly,
    merge_checks,
    mrid_matches_pen,
    resolve_format,
    response_type_to_string,
    run_check,
    timestamp_on_minute_boundary,
)
from cactus_runner.app.envoy_common import ReadingLocation
from cactus_runner.models import (
    ActiveTestProcedure,
    CheckResult,
    Listener,
    ResourceAnnotations,
)
from cactus_runner.plugin import dtos
from cactus_runner.plugin.backends.common import RunnerBackend
from cactus_runner.plugin.backends.envoy import EnvoyAdminClient, EnvoyBackend
from cactus_runner.plugin.backends.envoy.mappers import map_envoy_site_reading_type_to_dto

# This is a list of every check type paired with the handler function. This must be kept in sync with
# the checks defined in cactus test definitions (via CHECK_PARAMETER_SCHEMA). This sync will be enforced
CHECK_TYPE_TO_HANDLER: dict[str, str | None] = {
    "all-steps-complete": "check_all_steps_complete",
    "end-device-contents": "check_end_device_contents",
    "der-settings-contents": "check_der_settings_contents",
    "der-capability-contents": "check_der_capability_contents",
    "der-status-contents": "check_der_status_contents",
    "readings-site-active-power": "check_readings_site_active_power",
    "readings-site-reactive-power": "check_readings_site_reactive_power",
    "readings-voltage": "check_readings_voltage",
    "voltage-phase-code": "check_voltage_phase_code",
    "readings-der-active-power": "check_readings_der_active_power",
    "readings-der-reactive-power": "check_readings_der_reactive_power",
    "all-notifications-transmitted": "check_all_notifications_transmitted",
    "subscription-contents": "check_subscription_contents",
    "response-contents": "check_response_contents",
    "price-response-contents": None,  # Not supported in v1.2
    "readings-der-stored-energy": "check_readings_der_stored_energy",
    "all-polls-at-correct-time": "check_all_polls_at_correct_time",
    "resource-requests": "check_resource_requests",
    "end-device-count": "check_end_device_count",
    "voltage-phase-code": None,  # To be added in a future PR
}


def test_CHECK_TYPE_TO_HANDLER_in_sync():
    """Tests that every check defined in CHECK_TYPE_TO_HANDLER has an appropriate entry in CHECK_NAMES_WITH_HANDLER

    Failures in this test indicate that CHECK_NAMES_WITH_HANDLER hasn't been kept up to date"""

    # Make sure that every cactus-test-definition action is found in ACTION_TYPE_TO_HANDLER
    for check_type in CHECK_PARAMETER_SCHEMA.keys():
        assert check_type in CHECK_TYPE_TO_HANDLER, f"The check type {check_type} doesn't have a known handler fn"

    # Make sure we don't have any extra definitions not found in cactus-test-definitions
    for check_type in CHECK_TYPE_TO_HANDLER.keys():
        assert check_type in CHECK_PARAMETER_SCHEMA, (
            f"The check type {check_type} isn't defined in the test definitions (has it been removed/renamed)"
        )

    supported_checks = dict(((k, v) for k, v in CHECK_TYPE_TO_HANDLER.items() if v is not None))
    assert len(set(supported_checks.values())) == len(supported_checks), (
        "At least 1 check type have listed the same check handler. This is likely a bug"
    )


def generate_active_test_procedure_steps(active_steps: list[str], all_steps: list[str]) -> ActiveTestProcedure:
    """Utility for generating an ActiveTestProcedure from a simplified list of step names"""

    listeners = [generate_class_instance(Listener, step=s, actions=[]) for s in active_steps]

    steps = dict([(s, Step(Event("wait", {}, None), [])) for s in all_steps])
    test_procedure = generate_class_instance(TestProcedure, steps=steps)

    return generate_class_instance(
        ActiveTestProcedure, step_status={}, definition=test_procedure, listeners=listeners, finished_zip_path=None
    )


def assert_check_result(cr: CheckResult, expected: bool | None):
    assert isinstance(cr, CheckResult)
    if expected is None:
        assert cr.passed is None
    else:
        assert isinstance(cr.passed, bool)
        assert cr.passed == expected
    assert cr.description is None or isinstance(cr.description, str)


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
        (generate_class_instance(dtos.Site, nmi=None), True, False),
        (generate_class_instance(dtos.Site, nmi=None), False, True),
        (generate_class_instance(dtos.Site, nmi=None), None, True),  # Should default has_connection_point_id to False
        (generate_class_instance(dtos.Site, nmi=""), True, False),
        (generate_class_instance(dtos.Site, nmi=""), False, True),
        (generate_class_instance(dtos.Site, nmi=""), None, True),  # Should default has_connection_point_id to False
        (generate_class_instance(dtos.Site, nmi="abc123"), True, True),
        (generate_class_instance(dtos.Site, nmi="abc123"), False, True),
        (generate_class_instance(dtos.Site, nmi="abc123"), None, True),
    ],
)
@pytest.mark.anyio
async def test_check_end_device_contents_connection_point(
    active_site: dtos.Site | None, has_connection_point_id: bool | None, expected: bool
):
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        pen=0,
        client_certificate_type="Device",
        client_lfdi="",
        step_status={},
        finished_zip_path=None,
    )

    backend = mock.AsyncMock(spec=RunnerBackend)
    backend.get_active_site.return_value = active_site
    resolved_params = {}
    if has_connection_point_id is not None:
        resolved_params["has_connection_point_id"] = has_connection_point_id

    result = await check_end_device_contents(mock_active_test_procedure, backend, resolved_params)
    assert_check_result(result, expected)

    backend.get_active_site.assert_called_once()


@pytest.mark.parametrize(
    "active_site, deviceCategory_anyset, expected",
    [
        (None, "0", False),
        (None, "123", False),
        (None, None, False),
        (generate_class_instance(dtos.Site), "0", True),
        (generate_class_instance(dtos.Site), None, True),
        (generate_class_instance(dtos.Site, device_category=DeviceCategory(0)), "0", True),
        (generate_class_instance(dtos.Site, device_category=DeviceCategory(0)), "1", False),
        (generate_class_instance(dtos.Site, device_category=DeviceCategory(int("0f", 16))), "0f", True),
        (generate_class_instance(dtos.Site, device_category=DeviceCategory(int("0f", 16))), "05", True),
        (generate_class_instance(dtos.Site, device_category=DeviceCategory(int("0f", 16))), "10", False),
        (generate_class_instance(dtos.Site, device_category=DeviceCategory(int("22A8B", 16))), "20098", True),
        (generate_class_instance(dtos.Site, device_category=DeviceCategory(int("42A03", 16))), "20098", False),
    ],
)
@pytest.mark.anyio
async def test_check_end_device_contents_device_category(
    active_site: dtos.Site | None, deviceCategory_anyset: str | None, expected: bool
):
    mock_active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        pen=0,
        client_certificate_type="Device",
        client_lfdi="",
        step_status={},
        finished_zip_path=None,
    )
    backend = mock.AsyncMock(spec=RunnerBackend)

    backend.get_active_site.return_value = active_site
    resolved_params = {}
    if deviceCategory_anyset is not None:
        resolved_params["deviceCategory_anyset"] = deviceCategory_anyset

    result = await check_end_device_contents(mock_active_test_procedure, backend, resolved_params)
    assert_check_result(result, expected)

    backend.get_active_site.assert_called_once()


@pytest.mark.parametrize(
    "active_test_procedure, site_lfdi, site_sfdi, check_lfdi, expected",
    [
        (
            generate_class_instance(
                ActiveTestProcedure,
                pen=0,
                client_certificate_type="Device",
                step_status={},
                finished_zip_path=None,
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
                finished_zip_path=None,
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
                finished_zip_path=None,
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
                finished_zip_path=None,
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
                finished_zip_path=None,
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
                finished_zip_path=None,
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
                finished_zip_path=None,
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
                finished_zip_path=None,
            ),
            "3E4F45AB31EDFE5B67E343E5E4562E31984E23E5",
            1234,
            True,
            False,
        ),  # sfdi doesn't match lfdi
    ],
)
@pytest.mark.anyio
async def test_check_end_device_lfdi(
    active_test_procedure: ActiveTestProcedure,
    site_lfdi: str,
    site_sfdi: int,
    check_lfdi: bool | None,
    expected: bool,
):
    active_site = generate_class_instance(
        dtos.Site, device_category=DeviceCategory(int("22A8B", 16)), lfdi=site_lfdi, sfdi=site_sfdi
    )
    backend = mock.AsyncMock(spec=RunnerBackend)
    backend.get_active_site.return_value = active_site
    resolved_params = {}
    if check_lfdi is not None:
        resolved_params["check_lfdi"] = check_lfdi

    result = await check_end_device_contents(active_test_procedure, backend, resolved_params)
    assert_check_result(result, expected)

    backend.get_active_site.assert_called_once()


def generate_matching_der_rating(setting: SiteDERSetting, **overrides: Any) -> SiteDERRating:
    """Generates a SiteDERRating whose values are identical to every equivalent value in setting"""
    values: dict[str, Any] = {
        "modes_supported": setting.modes_enabled,
        "doe_modes_supported": setting.doe_modes_enabled,
    }
    for e in DER_VALUE_EQUIVALENTS:
        values[e.value_property] = getattr(setting, e.value_property)
        values[e.multiplier_property] = getattr(setting, e.multiplier_property)
    return generate_class_instance(SiteDERRating, **(values | overrides))


def generate_site_with_der_setting(setting: SiteDERSetting, **rating_overrides: Any) -> Site:
    """Generates a Site with the specified DERSetting and a DERCapability that (unless overridden) matches it"""
    return generate_class_instance(
        Site,
        nmi_checksum=None,
        seed=101,
        aggregator_id=1,
        site_der_setting=setting,
        site_der_rating=generate_matching_der_rating(setting, **rating_overrides),
    )


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
    der_props: dict[DERKey, Any] = {der_key: generate_class_instance(der_type, **{db_property: db_property_value})}

    if der_key == "site_der_rating":
        return pytest.param(
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=der_props[der_key],
                )
            ],
            {param: evaluator.ResolvedParam(param_value)},
            expected,
            id=f"boolcheck-{param}-{param_value}-{db_property}-{db_property_value}-expecting-{expected}",
        )
    if der_key == "site_der_setting":
        return pytest.param(
            [generate_site_with_der_setting(der_props[der_key])],
            {param: evaluator.ResolvedParam(param_value)},
            expected,
            id=f"boolcheck-{param}-{param_value}-{db_property}-{db_property_value}-expecting-{expected}",
        )


@pytest.mark.parametrize(
    "site_ids, min_count, max_count, expected",
    [
        ([], None, None, True),
        ([1, 2, 3], None, None, True),
        ([], 0, 0, True),
        ([], 1, None, False),
        ([], None, 1, True),
        ([1, 2, 3], 1, 3, True),
        ([1, 2, 3], 3, 3, True),
        ([1, 2, 3], 0, 5, True),
        ([1, 2, 3], 4, 4, False),
        ([1, 2, 3], 0, 2, False),
        ([1, 2, 3], 4, 2, False),
    ],
)
@pytest.mark.anyio
async def test_check_end_device_count(
    site_ids: list[int],
    min_count: int | None,
    max_count: int | None,
    expected: bool,
):

    backend = mock.AsyncMock(spec=RunnerBackend)
    backend.get_all_sites.return_value = [
        generate_class_instance(dtos.Site, seed=site_id, site_id=site_id) for site_id in site_ids
    ]
    resolved_params = {}
    if min_count is not None:
        resolved_params["minimum_count"] = min_count
    if max_count is not None:
        resolved_params["maximum_count"] = max_count

    result = await check_end_device_count(backend, resolved_params)
    assert_check_result(result, expected)

    backend.get_all_sites.assert_called_once()


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
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting))],
            {},
            True,
        ),
        (
            [
                generate_site_with_der_setting(
                    generate_class_instance(
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
                    )
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
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, grad_w=12345))],
            {"setGradW": evaluator.ResolvedParam(12345, variable_expressions.Constant(12345))},
            True,
        ),
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, grad_w=12345))],
            {"setGradW": evaluator.ResolvedParam(1234)},
            False,
        ),  # setGradW doesn't match value
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating),
                )
            ],
            {},
            False,
        ),  # Is setting DERCapability - not DERSetting
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                )
            ],
            {},
            False,
        ),
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, doe_modes_enabled=int("ff", 16)))],
            {"doeModesEnabled_set": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fe", 16)))],
            {"doeModesEnabled_set": evaluator.ResolvedParam("03")},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, modes_enabled=int("ff", 16)))],
            {"modesEnabled_set": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, modes_enabled=int("fe", 16)))],
            {"modesEnabled_set": evaluator.ResolvedParam("03")},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fc", 16)))],
            {"doeModesEnabled_unset": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fd", 16)))],
            {"doeModesEnabled_unset": evaluator.ResolvedParam("03")},
            False,
        ),  # Bit flag 1 set on actual value
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, modes_enabled=int("fc", 16)))],
            {"modesEnabled_unset": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, modes_enabled=int("fd", 16)))],
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
    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await check_der_settings_contents(backend, resolved_params)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


def generate_der_settings_and_rating(
    setting_overrides: dict[str, Any], rating_overrides: dict[str, Any]
) -> tuple[dtos.SiteDERSetting, dtos.SiteDERRating]:
    """Generates a setting/rating DTO pair where only the mandatory values (and overrides) are set"""
    setting: dict[str, Any] = {"grad_w": 27, "max_w_value": 5000, "max_w_multiplier": 0} | setting_overrides
    rating: dict[str, Any] = {"max_w_value": 5000, "max_w_multiplier": 0} | rating_overrides
    return (
        generate_class_instance(dtos.SiteDERSetting, optional_is_none=True, **setting),
        generate_class_instance(dtos.SiteDERRating, optional_is_none=True, **rating),
    )


async def do_der_settings_vs_rating_check(
    setting: dtos.SiteDERSetting,
    rating: dtos.SiteDERRating | None,
    resolved_params: dict[str, evaluator.ResolvedParam] | None = None,
) -> CheckResult:
    backend = mock.AsyncMock(spec=RunnerBackend)
    backend.get_active_site.return_value = generate_class_instance(dtos.Site, site_id="1")
    backend.get_der_settings.return_value = setting
    backend.get_der_capability.return_value = rating
    return await check_der_settings_contents(backend, resolved_params or {})


@pytest.mark.parametrize(
    "setting_value, setting_mult, rating_value, rating_mult, lte_expected, gte_expected",
    [
        (100, 0, 100, 0, True, True),
        (99, 0, 100, 0, True, False),
        (101, 0, 100, 0, False, True),
        (0, 0, 0, 0, True, True),
        (0, 0, 100, 0, True, False),
        (-99, 0, -100, 0, False, True),  # setMaxVarNeg style values
        (-101, 0, -100, 0, True, False),
        (5, 3, 5000, 0, True, True),  # Multipliers must be considered
        (5, 3, 4000, 0, False, True),
        (9999, 0, 10, 3, True, False),
        (95, -2, 950, -3, True, True),
        (951, -3, 95, -2, False, True),
        (100, None, 100, 0, True, True),  # Missing multiplier is 0
        (100, None, 10, 1, True, True),
    ],
)
@pytest.mark.parametrize("equivalent", DER_VALUE_EQUIVALENTS, ids=lambda e: e.setting_name)
@pytest.mark.anyio
async def test_check_der_settings_contents_value_vs_capability(
    equivalent: DERValueEquivalent,
    setting_value: int,
    setting_mult: int | None,
    rating_value: int,
    rating_mult: int | None,
    lte_expected: bool,
    gte_expected: bool,
):
    reversed_names = ["setMaxVarNeg", "setMinPFOverExcited", "setMinPFUnderExcited"]
    is_reversed = equivalent.setting_name in reversed_names
    assert equivalent.comparison == (DERComparison.GTE if is_reversed else DERComparison.LTE)

    setting, rating = generate_der_settings_and_rating(
        {equivalent.value_property: setting_value, equivalent.multiplier_property: setting_mult},
        {equivalent.value_property: rating_value, equivalent.multiplier_property: rating_mult},
    )
    result = await do_der_settings_vs_rating_check(setting, rating)

    assert_check_result(result, gte_expected if is_reversed else lte_expected)
    if not result.passed:
        assert result.description is not None
        assert equivalent.setting_name in result.description
        assert equivalent.rating_name in result.description
        assert equivalent.comparison in result.description


@pytest.mark.parametrize(
    "setting_prop, rating_prop, setting_name, rating_name",
    [(e.value_property, e.value_property, e.setting_name, e.rating_name) for e in DER_VALUE_EQUIVALENTS]
    + [
        ("modes_enabled", "modes_supported", "modesEnabled", "modesSupported"),
        ("doe_modes_enabled", "doe_modes_supported", "doeModesEnabled", "doeModesSupported"),
        ("vpp_modes_enabled", "vpp_modes_supported", "vppModesEnabled", "vppModesSupported"),
    ],
)
@pytest.mark.parametrize("value", [0, 1])
@pytest.mark.anyio
async def test_check_der_settings_contents_presence_vs_capability(
    setting_prop: str, rating_prop: str, setting_name: str, rating_name: str, value: int
):
    """Values set in only one of DERSettings/DERCapability are failures (including when the value is 0)"""
    setting, rating = generate_der_settings_and_rating({setting_prop: value}, {rating_prop: None})
    result = await do_der_settings_vs_rating_check(setting, rating)
    assert result == CheckResult(False, f"DERSetting.{setting_name} is set but DERCapability.{rating_name} is missing")

    setting, rating = generate_der_settings_and_rating({setting_prop: None}, {rating_prop: value})
    result = await do_der_settings_vs_rating_check(setting, rating)
    assert result == CheckResult(False, f"DERCapability.{rating_name} is set but DERSetting.{setting_name} is missing")

    setting, rating = generate_der_settings_and_rating({setting_prop: None}, {rating_prop: None})
    assert_check_result(await do_der_settings_vs_rating_check(setting, rating), True)


@pytest.mark.parametrize(
    "enabled, supported, expected",
    [
        (0b000, 0b000, True),
        (0b000, 0b111, True),
        (0b101, 0b111, True),
        (0b111, 0b111, True),
        (0b001, 0b000, False),
        (0b011, 0b100, False),  # numerically smaller - but not a subset
        (0b111, 0b101, False),
    ],
)
@pytest.mark.parametrize(
    "setting_prop, rating_prop, setting_name",
    [
        ("modes_enabled", "modes_supported", "modesEnabled"),
        ("doe_modes_enabled", "doe_modes_supported", "doeModesEnabled"),
        ("vpp_modes_enabled", "vpp_modes_supported", "vppModesEnabled"),
    ],
)
@pytest.mark.anyio
async def test_check_der_settings_contents_modes_vs_capability(
    setting_prop: str, rating_prop: str, setting_name: str, enabled: int, supported: int, expected: bool
):
    setting, rating = generate_der_settings_and_rating({setting_prop: enabled}, {rating_prop: supported})
    result = await do_der_settings_vs_rating_check(setting, rating)
    assert_check_result(result, expected)
    if not expected:
        assert result.description is not None
        assert f"DERSetting.{setting_name}" in result.description


@pytest.mark.anyio
async def test_check_der_settings_contents_no_capability():
    """A missing DERCapability is a failure that's reported alongside any other failures"""
    setting, _ = generate_der_settings_and_rating({}, {})
    result = await do_der_settings_vs_rating_check(setting, None, {"setGradW": evaluator.ResolvedParam(28)})
    assert_check_result(result, False)
    assert result.description is not None
    assert "setGradW 27 doesn't match expected 28" in result.description
    assert "No DERCapability found for EndDevice 1" in result.description


@pytest.mark.parametrize(
    "rating_overrides, expected",
    [
        ({}, True),
        ({"max_w_value": 5, "max_w_multiplier": 3}, True),
        ({"max_w_value": 4, "max_w_multiplier": 3}, False),
        ({"max_va_value": None, "max_va_multiplier": None}, False),
        ({"doe_modes_supported": 0}, False),
    ],
)
@pytest.mark.anyio
async def test_check_der_settings_contents_vs_capability_db(
    pg_base_config, rating_overrides: dict[str, Any], expected: bool
):
    """Ensures the comparison is fed correctly (including multipliers) via the envoy backend"""
    setting = generate_class_instance(SiteDERSetting, max_w_value=5000, max_w_multiplier=0, doe_modes_enabled=3)
    async with generate_async_session(pg_base_config) as session:
        session.add(generate_site_with_der_setting(setting, **rating_overrides))
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        assert_check_result(await check_der_settings_contents(backend, {}), expected)


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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating),
                )
            ],
            {},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_setting=generate_class_instance(SiteDERSetting),
                )
            ],
            {},
            False,
        ),  # Is setting DERSetting not DERCapability
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
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
                    nmi_checksum=None,
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("ff", 16)),
                )
            ],
            {"doeModesSupported_set": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fe", 16)),
                )
            ],
            {"doeModesSupported_set": evaluator.ResolvedParam("03")},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("ff", 16)),
                )
            ],
            {"modesSupported_set": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fe", 16)),
                )
            ],
            {"modesSupported_set": evaluator.ResolvedParam("03")},
            False,
        ),  # Bit flag 1 not set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fc", 16)),
                )
            ],
            {"doeModesSupported_unset": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fd", 16)),
                )
            ],
            {"doeModesSupported_unset": evaluator.ResolvedParam("03")},
            False,
        ),  # Bit flag 1 set on actual value
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fc", 16)),
                )
            ],
            {"modesSupported_unset": evaluator.ResolvedParam("03")},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fd", 16)),
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
    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await check_der_capability_contents(backend, resolved_params)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.parametrize(
    "existing_sites, resolved_params, expected",
    [
        ([], {}, False),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(SiteDERStatus),
                )
            ],
            {},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus_bit0": True, "genConnectStatus_bit1": False, "genConnectStatus_bit2": True},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=None, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus_bit0": False},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=None, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus_bit1": False},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=None, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus_bit2": False},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus_bit0": False, "genConnectStatus_bit1": False, "genConnectStatus_bit2": True},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus_bit0": True, "genConnectStatus_bit1": True, "genConnectStatus_bit2": True},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=5, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus_bit0": True, "genConnectStatus_bit1": False, "genConnectStatus_bit2": False},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus": 999},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus": 888, "operationalModeStatus": 999},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus": 999, "operationalModeStatus": 999},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999
                    ),
                )
            ],
            {"genConnectStatus": 888, "operationalModeStatus": 888},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_setting=generate_class_instance(SiteDERSetting),
                )
            ],
            {},
            False,
        ),  # Is setting DERSetting not DERStatus
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
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
                    nmi_checksum=None,
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=0
                    ),
                )
            ],
            {"alarmStatus": 0},
            True,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=1
                    ),
                )
            ],
            {"alarmStatus": 0},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=0
                    ),
                )
            ],
            {"alarmStatus": 1},
            False,
        ),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_status=generate_class_instance(
                        SiteDERStatus, generator_connect_status=888, operational_mode_status=999, alarm_status=3
                    ),
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
    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await check_der_status_contents(backend, resolved_params)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


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
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
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
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, started_at=None
    )

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        srt_dtos = [map_envoy_site_reading_type_to_dto(srt) for srt in faked_srts]
        result = await do_check_readings_for_types(active_test_procedure, backend, srt_dtos, minimum_count)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.parametrize(
    "srt_ids, minimum_count, expected",
    [
        ([], None, True),
        ([], 0, True),
        ([], 3, False),
        # srt1 has 3 readings total but only 2 occur at/after started_at
        ([1], 2, True),
        ([1], 3, False),
        ([1], 1, True),
        # srt2 has 2 readings, both of which occur strictly before started_at
        ([2], 0, True),
        ([2], 1, False),
        ([1, 2], 2, True),  # Only srt1's post-start readings count towards the max
        ([1, 2], 3, False),
        ([99], 0, True),
        ([99], 1, False),
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_for_types_started_at(
    pg_base_config, srt_ids: list[int], minimum_count: int | None, expected: bool
):
    """Tests that do_check_readings_for_types ignores readings that occurred before
    ActiveTestProcedure.started_at when evaluating minimum_count"""
    started_at = datetime(2024, 1, 1, tzinfo=UTC)

    async with generate_async_session(pg_base_config) as session:
        # srt1 has 3 readings: one strictly before started_at, one ending exactly at started_at (inclusive
        # boundary - should count) and one strictly after started_at.
        # srt2 has 2 readings, both of which end strictly before started_at.
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        srt1 = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        srt2 = generate_class_instance(SiteReadingType, seed=202, site_reading_type_id=2, aggregator_id=1, site=site)

        session.add_all([site, srt1, srt2])
        session.add(
            generate_class_instance(
                SiteReading,
                seed=11,
                site_reading_type=srt1,
                time_period_start=started_at - timedelta(hours=2),
                time_period_seconds=60,
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=22,
                site_reading_type=srt1,
                time_period_start=started_at - timedelta(seconds=60),
                time_period_seconds=60,
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=33,
                site_reading_type=srt1,
                time_period_start=started_at + timedelta(hours=1),
                time_period_seconds=60,
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=44,
                site_reading_type=srt2,
                time_period_start=started_at - timedelta(hours=2),
                time_period_seconds=60,
            )
        )
        session.add(
            generate_class_instance(
                SiteReading,
                seed=55,
                site_reading_type=srt2,
                time_period_start=started_at - timedelta(hours=1),
                time_period_seconds=60,
            )
        )

        await session.commit()

    faked_srts = [
        generate_class_instance(SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, started_at=started_at
    )

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        srt_dtos = [map_envoy_site_reading_type_to_dto(srt) for srt in faked_srts]
        result = await do_check_readings_for_types(active_test_procedure, backend, srt_dtos, minimum_count)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


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
    "srt_ids, readings, mult, min_level, max_level, window_s, expected",
    [
        # No window - not all readings are >= 60.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 60.0, None, None, False),
        # No window - >= 50.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 50.0, None, None, True),
        # No window - <= 59.9
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 59.9, None, False),
        # No window - <= 60.0
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 60.0, None, True),
        # No window - 50.0 <= value <= 70.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 50.0, 70.0, None, True),
        # No window - 40.0 <= value <= 45.0
        ([1], [LEVEL_SCENARIOS[0]], 0, 40.0, 45.0, None, False),
        # No window - -1.0 <= value <= 101.0 with pow10 == 1 (raw [5, 10, 0] scaled to [50, 100, 0])
        ([2], [LEVEL_SCENARIOS[1]], 1, -1.0, 101.0, None, True),
        # No window - 50.0 <= value <= 60.0 with pow10 == -1 (raw [501, 510, 600] scaled to [50.1, 51.0, 60.0])
        ([3], [LEVEL_SCENARIOS[2]], -1, 50.0, 60.0, None, True),
        # No window - two reading types, all readings within 50.0 <= value <= 61.0
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 50.0, 61.0, None, True),
        # No window - two reading types with 60.5 <= value <= 62.0 (not all readings in range)
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 60.5, 62.0, None, False),
        # No window - no readings for the chosen SiteReadingType
        ([3], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 59.0, 62.0, None, False),
        # No window - a low outlier reading anywhere in the test's history fails the check
        ([1], [LEVEL_SCENARIOS[4]], 0, 60.0, 60.0, None, False),
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
async def test_do_check_levels_for_readings(
    pg_base_config,
    srt_ids: list[int],
    readings: list[ReadingTestScenario],
    mult: int,
    min_level: float | None,
    max_level: float | None,
    window_s: int | None,
    expected: bool,
):
    """Tests that do_check_levels_for_readings can handle various queries against a static DB model, both with
    and without a window_period"""
    async with generate_async_session(pg_base_config) as session:
        # Load 3 SiteReadingTypes
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
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
        generate_class_instance(
            dtos.SiteReadingType, seed=srt_id, power_of_ten_multiplier=mult, site_reading_type_id=str(srt_id)
        )
        for srt_id in srt_ids
    ]

    async with generate_async_session(pg_base_config) as session:
        mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        window_period = timedelta(seconds=window_s) if window_s is not None else None
        result = await do_check_levels_for_readings(backend, faked_srts, min_level, max_level, window_period)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.parametrize(
    "srt_ids, readings, mult, min_level, max_level, expected",
    [
        # last reading in LEVEL_SCENARIOS[0] = 60, check larger, smaller, equal
        ([1], [LEVEL_SCENARIOS[0]], 0, 60.0, None, True),
        ([1], [LEVEL_SCENARIOS[0]], 0, 60.1, None, False),
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 59.9, False),
        ([1], [LEVEL_SCENARIOS[0]], 0, None, 60.0, True),
        ([1], [LEVEL_SCENARIOS[0]], 0, 50.0, 70.0, True),
        ([1], [LEVEL_SCENARIOS[0]], 0, 40.0, 45.0, False),
        # last reading in LEVEL_SCENARIOS[1] = 0
        ([2], [LEVEL_SCENARIOS[1]], 1, -40.0, 45.0, True),
        # last reading in LEVEL_SCENARIOS[2] = 600, but give it a pow10 of -1 = 60
        ([3], [LEVEL_SCENARIOS[2]], -1, 60.0, 60.0, True),
        # Two reading types with 59.0 <= value <= 62.0
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 59.0, 62.0, True),
        # one site reading type passes, one fails
        ([1, 2], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 60.5, 62.0, False),
        # No readings for the chosen SiteReadingType
        ([3], [LEVEL_SCENARIOS[0], LEVEL_SCENARIOS[3]], 0, 59.0, 62.0, False),
        # Only the latest reading matters - an early low outlier doesn't fail the check
        ([1], [LEVEL_SCENARIOS[4]], 0, 60.0, 60.0, True),
    ],
)
@pytest.mark.anyio
async def test_do_check_latest_reading_level(
    pg_base_config,
    srt_ids: list[int],
    readings: list[ReadingTestScenario],
    mult: int,
    min_level: float | None,
    max_level: float | None,
    expected: bool,
):
    """Tests that do_check_latest_reading_level only considers the single most recent reading per type"""
    async with generate_async_session(pg_base_config) as session:
        # Load 3 SiteReadingTypes
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
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

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        srts = await session.execute(select(SiteReadingType))
        srt_dtos = [
            map_envoy_site_reading_type_to_dto(srt)
            for srt in srts.scalars().all()
            if int(srt.site_reading_type_id) in srt_ids
        ]
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await do_check_latest_reading_level(backend, srt_dtos, min_level, max_level)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.parametrize(
    "resolved_params, expected_window_period, expected_length, called",
    [
        ({"minimum_level": 1, "maximum_level": 2, "window_seconds": 3}, timedelta(seconds=3), None, True),
        ({"minimum_level": 1, "maximum_level": 2}, None, None, True),
        ({"minimum_level": 1}, None, None, True),
        ({"maximum_level": 2}, None, None, True),
        ({"maximum_level": 2, "reading_length_seconds": 60}, None, 60, True),
        ({"maximum_level": 2, "reading_length_seconds": 0}, None, 0, True),
        ({"window_seconds": 3, "reading_length_seconds": 60}, timedelta(seconds=3), 60, True),
        ({"reading_length_seconds": 60}, None, None, False),  # Purely a filter - has no levels to apply to
        ({"reading_length_seconds": 60, "minimum_count": 1}, None, None, False),
        ({}, None, None, False),
    ],
)
@pytest.mark.anyio
async def test_do_check_reading_levels_for_types(
    mocker: pytest_mock.MockerFixture,
    resolved_params: dict[str, Any],
    expected_window_period: timedelta | None,
    expected_length: int | None,
    called: bool,
) -> None:
    """Ensures that the matching function works as expected for the correct combinations of resolved parameters.

    Args:
        mocker: the mocker fixture
        resolved_params: dictionary passed in containing parameters resolved during evaluation
        expected_window_period: the window_period do_check_levels_for_readings should be called with
        expected_length: the reading_length_seconds do_check_levels_for_readings should be called with
        called: whether do_check_levels_for_readings should be called at all
    """
    mock_levels = mocker.patch("cactus_runner.app.check.do_check_levels_for_readings")
    session = mocker.AsyncMock()

    result = await do_check_reading_levels_for_types(session, [], resolved_params)
    if called:
        mock_levels.assert_called_once_with(session, [], mock.ANY, mock.ANY, expected_window_period, expected_length)
    else:
        mock_levels.assert_not_called()
        assert_check_result(result, True)


@pytest.mark.parametrize(
    "resolved_params",
    [
        {"minimum_level": 1, "maximum_level": 2, "latest_reading_only": True},
        {"minimum_level": 1, "latest_reading_only": True},
        {"latest_reading_only": True},  # No levels at all - still routes to the latest-reading check
        {"latest_reading_only": True, "reading_length_seconds": 60},
        {"latest_reading_only": True, "reading_length_seconds": 0},
        {"latest_reading_only": True, "reading_length_seconds": 60, "minimum_count": 1, "maximum_level": 2},
    ],
)
@pytest.mark.anyio
async def test_do_check_reading_levels_for_types_latest_reading_only(
    mocker: pytest_mock.MockerFixture,
    resolved_params: dict[str, Any],
) -> None:
    """latest_reading_only=True should route to do_check_latest_reading_level instead of the windowed/whole-test
    check, and should never call do_check_levels_for_readings"""
    mock_levels = mocker.patch("cactus_runner.app.check.do_check_levels_for_readings")
    mock_latest = mocker.patch("cactus_runner.app.check.do_check_latest_reading_level")
    mock_latest.return_value = CheckResult(True, None)
    session = mocker.AsyncMock()

    result = await do_check_reading_levels_for_types(session, [], resolved_params)

    mock_levels.assert_not_called()
    mock_latest.assert_called_once_with(
        session,
        [],
        resolved_params.get("minimum_level"),
        resolved_params.get("maximum_level"),
        resolved_params.get("reading_length_seconds"),
    )
    assert_check_result(result, True)


# (length_seconds, value) tuples - each reading starts as the preceding reading ends
READING_LENGTHS_60 = [(60, 10), (60, 11)]
READING_LENGTHS_60_300 = [*READING_LENGTHS_60, (300, 20)]
READING_LENGTHS_60_300_60 = [*READING_LENGTHS_60_300, (60, 30)]


async def seed_reading_lengths(
    pg_base_config, readings_by_srt_id: dict[int, list[tuple[int, int]]], start: datetime
) -> list[dtos.SiteReadingType]:
    """Seeds the DB with back to back readings, received the moment they end. Returns the (valid) site active power
    SiteReadingTypes"""
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        session.add(site)
        for srt_id, readings in readings_by_srt_id.items():
            srt = generate_class_instance(
                SiteReadingType,
                seed=srt_id * 101,
                site_reading_type_id=srt_id,
                aggregator_id=1,
                site=site,
                power_of_ten_multiplier=0,
                uom=UomType.REAL_POWER_WATT,
                kind=KindType.POWER,
                data_qualifier=DataQualifierType.AVERAGE,
                role_flags=ReadingLocation.SITE_READING,
            )
            session.add(srt)
            reading_start = start
            for i, (length_seconds, value) in enumerate(readings):
                reading_end = reading_start + timedelta(seconds=length_seconds)
                session.add(
                    generate_class_instance(
                        SiteReading,
                        seed=srt_id * 1001 + i,
                        site_reading_type=srt,
                        value=value,
                        time_period_start=reading_start,
                        time_period_seconds=length_seconds,
                        created_time=reading_end,
                    )
                )
                reading_start = reading_end
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        srts = (await session.execute(select(SiteReadingType).order_by(SiteReadingType.site_reading_type_id))).scalars()
        return [map_envoy_site_reading_type_to_dto(srt) for srt in srts.all()]


@pytest.mark.parametrize(
    "readings_by_srt_id, started_offset_seconds, minimum_count, length, expected",
    [
        ({1: READING_LENGTHS_60_300_60}, None, 4, None, True),
        ({1: READING_LENGTHS_60_300_60}, None, 5, None, False),
        ({1: READING_LENGTHS_60_300_60}, None, 3, 60, True),
        ({1: READING_LENGTHS_60_300_60}, None, 4, 60, False),
        ({1: READING_LENGTHS_60_300_60}, None, 1, 300, True),
        ({1: READING_LENGTHS_60_300_60}, None, 2, 300, False),
        ({1: READING_LENGTHS_60_300_60}, None, 1, 120, False),
        ({1: READING_LENGTHS_60_300_60}, None, 0, 120, True),
        ({1: READING_LENGTHS_60_300_60}, None, None, 120, True),  # Nothing to count
        ({1: READING_LENGTHS_60}, None, 1, 0, False),  # A length of 0 is NOT the same as None
        ({1: [(60, 11), (0, 10)]}, None, 1, 0, True),
        ({1: [(60, 11), (0, 10)]}, None, 2, 0, False),
        # Counts are per SiteReadingType - not across all SiteReadingTypes
        ({1: READING_LENGTHS_60, 2: READING_LENGTHS_60_300}, None, 1, 300, True),
        ({1: READING_LENGTHS_60_300, 2: READING_LENGTHS_60_300}, None, 2, 300, False),
        # Length filter combines with the test start time (only the final 300s / 60s readings are in the test)
        ({1: READING_LENGTHS_60_300_60}, 150, 1, 60, True),
        ({1: READING_LENGTHS_60_300_60}, 150, 2, 60, False),
        ({1: READING_LENGTHS_60_300_60}, 150, 1, 300, True),
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_for_types_reading_length(
    pg_base_config,
    readings_by_srt_id: dict[int, list[tuple[int, int]]],
    started_offset_seconds: int | None,
    minimum_count: int | None,
    length: int | None,
    expected: bool,
):
    """Tests that only readings of the specified length count towards minimum_count"""
    start = datetime(2024, 1, 1, tzinfo=UTC)
    srt_dtos = await seed_reading_lengths(pg_base_config, readings_by_srt_id, start)
    started_at = None if started_offset_seconds is None else start + timedelta(seconds=started_offset_seconds)
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, started_at=started_at
    )

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        result = await do_check_readings_for_types(active_test_procedure, backend, srt_dtos, minimum_count, length)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "readings, min_level, max_level, window_s, length, expected",
    [
        # No window
        (READING_LENGTHS_60_300_60, 20, 20, None, None, False),
        (READING_LENGTHS_60_300_60, 20, 20, None, 300, True),
        (READING_LENGTHS_60_300_60, 10, 30, None, 60, True),
        (READING_LENGTHS_60_300_60, 11, 30, None, 60, False),
        (READING_LENGTHS_60_300_60, 10, 29, None, 60, False),
        (READING_LENGTHS_60_300_60, 0, 100, None, 120, False),  # No readings of this length
        (READING_LENGTHS_60, 0, 100, None, 0, False),  # A length of 0 is NOT the same as None
        ([(60, 11), (0, 10)], 10, 10, None, 0, True),
        # Windowed
        (READING_LENGTHS_60_300_60, 30, 30, 360, None, False),
        (READING_LENGTHS_60_300_60, 30, 30, 360, 60, True),
        (READING_LENGTHS_60_300_60, 20, 20, 360, 300, True),
        (READING_LENGTHS_60_300_60, 30, 30, 420, 60, False),  # Now includes the second 60s reading
        (READING_LENGTHS_60_300_60, 20, 20, 359, 300, False),  # 300s reading isn't wholly in the window
        (READING_LENGTHS_60_300, 11, 11, 60, 60, False),  # Window is relative to the (300s) latest reading
    ],
)
@pytest.mark.anyio
async def test_do_check_levels_for_readings_reading_length(
    pg_base_config,
    readings: list[tuple[int, int]],
    min_level: float | None,
    max_level: float | None,
    window_s: int | None,
    length: int | None,
    expected: bool,
):
    """Tests that level checks only consider readings of the specified length"""
    srt_dtos = await seed_reading_lengths(pg_base_config, {1: readings}, datetime(2024, 1, 1, tzinfo=UTC))

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        window_period = timedelta(seconds=window_s) if window_s is not None else None
        result = await do_check_levels_for_readings(backend, srt_dtos, min_level, max_level, window_period, length)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "readings_by_srt_id, min_level, max_level, length, expected",
    [
        ({1: READING_LENGTHS_60}, None, None, 60, True),
        ({1: READING_LENGTHS_60}, None, None, 300, False),
        ({1: READING_LENGTHS_60_300}, None, None, None, True),
        ({1: READING_LENGTHS_60_300}, None, None, 300, True),
        ({1: READING_LENGTHS_60_300}, None, None, 60, False),  # Older 60s readings exist but they AREN'T the latest
        ({1: READING_LENGTHS_60_300_60}, None, None, 60, True),
        ({1: READING_LENGTHS_60_300_60}, None, None, 300, False),
        ({1: READING_LENGTHS_60}, None, None, 0, False),  # A length of 0 is NOT the same as None
        ({1: []}, None, None, 60, False),
        # Combined with levels
        ({1: READING_LENGTHS_60_300}, 20, 20, 300, True),
        ({1: READING_LENGTHS_60_300}, 21, None, 300, False),
        ({1: READING_LENGTHS_60_300}, None, 19, 300, False),
        ({1: READING_LENGTHS_60_300}, 11, 11, 60, False),
        # EVERY SiteReadingType must match
        ({1: READING_LENGTHS_60_300, 2: READING_LENGTHS_60_300}, None, None, 300, True),
        ({1: READING_LENGTHS_60_300, 2: READING_LENGTHS_60}, None, None, 300, False),
        ({1: READING_LENGTHS_60, 2: READING_LENGTHS_60_300}, None, None, 300, False),
    ],
)
@pytest.mark.anyio
async def test_do_check_latest_reading_level_reading_length(
    pg_base_config,
    readings_by_srt_id: dict[int, list[tuple[int, int]]],
    min_level: float | None,
    max_level: float | None,
    length: int | None,
    expected: bool,
):
    """Tests that the latest reading (of ANY length) must match the specified length"""
    srt_dtos = await seed_reading_lengths(pg_base_config, readings_by_srt_id, datetime(2024, 1, 1, tzinfo=UTC))

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        result = await do_check_latest_reading_level(backend, srt_dtos, min_level, max_level, length)
        assert_check_result(result, expected)


@pytest.mark.parametrize(
    "readings, parameters, expected",
    [
        # ALL-10 - waiting for the post rate to move 60s -> 300s -> 60s
        (READING_LENGTHS_60, {"latest_reading_only": True, "minimum_count": 1, "reading_length_seconds": 60}, True),
        (READING_LENGTHS_60, {"latest_reading_only": True, "minimum_count": 1, "reading_length_seconds": 300}, False),
        (
            READING_LENGTHS_60_300,
            {"latest_reading_only": True, "minimum_count": 1, "reading_length_seconds": 300},
            True,
        ),
        (
            READING_LENGTHS_60_300,  # The initial 60s readings can't satisfy the final 60s check
            {"latest_reading_only": True, "minimum_count": 1, "reading_length_seconds": 60},
            False,
        ),
        (
            READING_LENGTHS_60_300_60,
            {"latest_reading_only": True, "minimum_count": 1, "reading_length_seconds": 60},
            True,
        ),
        # minimum_count considers ALL readings of that length - not just the latest
        (
            READING_LENGTHS_60_300_60,
            {"latest_reading_only": True, "minimum_count": 3, "reading_length_seconds": 60},
            True,
        ),
        (
            READING_LENGTHS_60_300_60,
            {"latest_reading_only": True, "minimum_count": 4, "reading_length_seconds": 60},
            False,
        ),
        # Without latest_reading_only
        (READING_LENGTHS_60_300, {"minimum_count": 2, "reading_length_seconds": 60}, True),
        (READING_LENGTHS_60_300, {"minimum_count": 2, "reading_length_seconds": 300}, False),
        (
            READING_LENGTHS_60_300,
            {"minimum_count": 2, "maximum_level": 11, "reading_length_seconds": 60},
            True,
        ),
        (READING_LENGTHS_60_300, {"minimum_count": 2, "maximum_level": 11}, False),
    ],
)
@mock.patch("cactus_runner.app.check.do_check_readings_match_post_rate")
@mock.patch("cactus_runner.app.check.do_check_reading_type_mrids_match_pen")
@pytest.mark.anyio
async def test_check_readings_site_active_power_reading_length(
    mock_do_check_reading_type_mrids_match_pen: mock.MagicMock,
    mock_do_check_readings_match_post_rate: mock.MagicMock,
    pg_base_config,
    readings: list[tuple[int, int]],
    parameters: dict[str, Any],
    expected: bool,
):
    """Tests reading_length_seconds in combination with other parameters via a full reading check"""
    mock_do_check_readings_match_post_rate.return_value = CheckResult(True, None)
    mock_do_check_reading_type_mrids_match_pen.return_value = CheckResult(True, None)
    await seed_reading_lengths(pg_base_config, {1: readings}, datetime(2024, 1, 1, tzinfo=UTC))
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, started_at=None
    )

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        result = await check_readings_site_active_power(active_test_procedure, backend, parameters, 0)
        assert_check_result(result, expected)


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
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
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
        generate_class_instance(dtos.SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await do_check_readings_on_minute_boundary(backend, faked_srts)
        assert_check_result(result, expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


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
    site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
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
    srt_dtos = [map_envoy_site_reading_type_to_dto(srt) for srt in srts]
    # Act
    result = await do_check_reading_type_mrids_match_pen(site_reading_types=srt_dtos, pen=pen)

    # Assert
    assert_check_result(result, expected_result)


@pytest.mark.parametrize(
    "resolved_parameters, uom, reading_location, qualifier, kind, site_reading_type_count, pen, expected_min_count",
    [
        (
            {},
            UomType.REAL_POWER_WATT,
            ReadingLocation.SITE_READING,
            DataQualifierType.AVERAGE,
            KindType.POWER,
            0,
            0,
            None,
        ),
        (
            {},
            UomType.APPARENT_ENERGY_VAH,
            ReadingLocation.DEVICE_READING,
            DataQualifierType.MINIMUM,
            KindType.POWER,
            1,
            64,
            None,
        ),
        (
            {"minimum_count": 123, "foo": 456, "reading_length_seconds": 300},
            UomType.BRITISH_THERMAL_UNIT,
            ReadingLocation.DEVICE_READING,
            DataQualifierType.STANDARD,
            KindType.POWER,
            2,
            888,
            123,
        ),
        (
            {"minimum_count": 0},
            UomType.FREQUENCY_HZ,
            ReadingLocation.SITE_READING,
            DataQualifierType.MAXIMUM,
            KindType.ENERGY,
            2,
            0,
            0,
        ),
    ],
)
@mock.patch("cactus_runner.app.check.do_check_readings_received_after_interval_end")
@mock.patch("cactus_runner.app.check.do_check_readings_for_types")
@mock.patch("cactus_runner.app.check.do_check_readings_on_minute_boundary")
@mock.patch("cactus_runner.app.check.do_check_reading_type_mrids_match_pen")
@mock.patch("cactus_runner.app.check.do_check_readings_for_duration")
@mock.patch("cactus_runner.app.check.do_check_readings_match_post_rate")
@pytest.mark.anyio
async def test_do_check_site_readings_and_params(
    mock_do_check_readings_match_post_rate: mock.MagicMock,
    mock_do_check_readings_for_duration: mock.MagicMock,
    mock_do_check_reading_type_mrids_match_pen: mock.MagicMock,
    mock_do_check_readings_on_minute_boundary: mock.MagicMock,
    mock_do_check_readings_for_types: mock.MagicMock,
    mock_do_check_readings_received_after_interval_end: mock.MagicMock,
    resolved_parameters: dict[str, Any],
    uom: UomType,
    reading_location: ReadingLocation,
    qualifier: DataQualifierType,
    kind: KindType,
    site_reading_type_count: int,
    pen: int,
    expected_min_count: int | None,
):
    """Tests that do_check_site_readings_and_params does the basic logic it needs before offloading to
    do_check_readings_for_types"""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, started_at=None
    )
    active_site = generate_class_instance(dtos.Site)
    site_reading_types = [
        generate_class_instance(
            dtos.SiteReadingType,
            seed=i,
            site_id=active_site.site_id,
            uom=uom,
            data_qualifier=qualifier,
            kind=kind,
            role_flags=reading_location,
        )
        for i in range(site_reading_type_count)
    ]
    mock_backend = mock.AsyncMock(spec=RunnerBackend)
    mock_backend.get_active_site.return_value = active_site
    mock_backend.get_site_reading_types.return_value = site_reading_types
    expected_result = generate_class_instance(CheckResult, seed=1)
    mock_do_check_readings_for_types.return_value = expected_result
    mock_do_check_readings_on_minute_boundary.return_value = CheckResult(True, description=None)
    mock_do_check_reading_type_mrids_match_pen.return_value = CheckResult(True, description=None)
    mock_do_check_readings_for_duration.return_value = CheckResult(True, description=None)
    mock_do_check_readings_match_post_rate.return_value = CheckResult(True, description=None)
    mock_do_check_readings_received_after_interval_end.return_value = CheckResult(True, description=None)

    # Act
    result = await do_check_site_readings_and_params(
        active_test_procedure, mock_backend, resolved_parameters, pen, uom, reading_location, qualifier, kind
    )

    # Assert
    mock_backend.get_active_site.assert_called_once()
    mock_backend.get_site_reading_types.assert_called_once_with(site_ids=[active_site.site_id])

    # If we have 0 SiteReadingTypes - instant failure, no need to run the reading checks
    if len(site_reading_types) != 0:
        assert result == expected_result
        mock_do_check_readings_for_types.assert_called_once_with(
            active_test_procedure,
            mock_backend,
            site_reading_types,
            expected_min_count,
            resolved_parameters.get("reading_length_seconds"),
        )
        mock_do_check_readings_on_minute_boundary.assert_called_once_with(mock_backend, site_reading_types)
        mock_do_check_reading_type_mrids_match_pen.assert_called_once_with(site_reading_types, pen)
        mock_do_check_readings_for_duration.assert_called_once_with(mock_backend, site_reading_types)
        mock_do_check_readings_match_post_rate.assert_called_once_with(mock_backend, site_reading_types)
        if qualifier == DataQualifierType.AVERAGE:
            mock_do_check_readings_received_after_interval_end.assert_called_once_with(mock_backend, site_reading_types)
        else:
            mock_do_check_readings_received_after_interval_end.assert_not_called()  # Not an interval average
    else:
        assert_check_result(result, False)
        mock_do_check_readings_for_types.assert_not_called()


@pytest.mark.parametrize(
    "site_reading_types, expected_passed",
    [
        # only incorrect roleflags, no correct readings: fail
        (
            [
                generate_class_instance(
                    dtos.SiteReadingType,
                    seed=1,
                    site_reading_type_id="1",
                    role_flags=0x01,
                    site_id="1",
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    kind=KindType.POWER,
                )
            ],
            False,
        ),
        # multiple incorrect roleflags, no correct readings: fail
        (
            [
                generate_class_instance(
                    dtos.SiteReadingType,
                    seed=1,
                    site_reading_type_id="1",
                    role_flags=0x01,
                    site_id="1",
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    kind=KindType.POWER,
                ),
                generate_class_instance(
                    dtos.SiteReadingType,
                    seed=2,
                    site_reading_type_id="2",
                    role_flags=0x02,
                    site_id="1",
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    kind=KindType.POWER,
                ),
            ],
            False,
        ),
        # incorrect roleflags alongside correct readings: roleflag check skipped, passes
        (
            [
                generate_class_instance(
                    dtos.SiteReadingType,
                    seed=1,
                    site_reading_type_id="1",
                    role_flags=ReadingLocation.SITE_READING,
                    site_id="1",
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    kind=KindType.POWER,
                ),
                generate_class_instance(
                    dtos.SiteReadingType,
                    seed=2,
                    site_reading_type_id="2",
                    role_flags=0x02,
                    site_id="1",
                    uom=UomType.REAL_POWER_WATT,
                    data_qualifier=DataQualifierType.AVERAGE,
                    kind=KindType.POWER,
                ),
            ],
            True,
        ),
    ],
)
@mock.patch("cactus_runner.app.check.do_check_readings_received_after_interval_end")
@mock.patch("cactus_runner.app.check.do_check_readings_for_types")
@mock.patch("cactus_runner.app.check.do_check_readings_on_minute_boundary")
@mock.patch("cactus_runner.app.check.do_check_reading_type_mrids_match_pen")
@mock.patch("cactus_runner.app.check.do_check_readings_for_duration")
@mock.patch("cactus_runner.app.check.do_check_readings_match_post_rate")
@pytest.mark.anyio
async def test_do_check_site_readings_and_params_roleflags(
    mock_do_check_readings_match_post_rate: mock.MagicMock,
    mock_do_check_readings_for_duration: mock.MagicMock,
    mock_do_check_reading_type_mrids_match_pen: mock.MagicMock,
    mock_do_check_readings_on_minute_boundary: mock.MagicMock,
    mock_do_check_readings_for_types: mock.MagicMock,
    mock_do_check_readings_received_after_interval_end: mock.MagicMock,
    site_reading_types: list[dtos.SiteReadingType],
    expected_passed: bool,
):
    """Tests roleflag handling: fails when only incorrect roleflags are returned, passes when correct
    site_reading_types are also present."""
    # Arrange
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, started_at=None
    )
    mock_backend = mock.AsyncMock(spec=RunnerBackend)
    mock_backend.get_active_site.return_value = generate_class_instance(dtos.Site, seed=1, site_id="1")
    mock_backend.get_site_reading_types.return_value = site_reading_types
    mock_do_check_readings_for_types.return_value = CheckResult(True, description=None)
    mock_do_check_readings_on_minute_boundary.return_value = CheckResult(True, description=None)
    mock_do_check_reading_type_mrids_match_pen.return_value = CheckResult(True, description=None)
    mock_do_check_readings_for_duration.return_value = CheckResult(True, description=None)
    mock_do_check_readings_match_post_rate.return_value = CheckResult(True, description=None)
    mock_do_check_readings_received_after_interval_end.return_value = CheckResult(True, description=None)

    # Act
    result = await do_check_site_readings_and_params(
        active_test_procedure,
        mock_backend,
        {},
        pen=12345,
        uom=UomType.REAL_POWER_WATT,
        reading_location=ReadingLocation.SITE_READING,
        data_qualifier=DataQualifierType.AVERAGE,
        kind=KindType.POWER,
    )

    # Assert
    assert_check_result(result, expected_passed)
    if [sr for sr in site_reading_types if sr.role_flags == ReadingLocation.SITE_READING]:
        mock_do_check_readings_for_types.assert_called_once()
    else:
        mock_do_check_readings_for_types.assert_not_called()
        assert result.description is not None
        if [sr for sr in site_reading_types if sr.role_flags != ReadingLocation.SITE_READING]:
            assert "roleFlags" in result.description


@pytest.mark.parametrize(
    "check, apply_function_name",
    [
        (Check(type=check_type, parameters={}), handler_fn)
        for check_type, handler_fn in CHECK_TYPE_TO_HANDLER.items()
        if handler_fn is not None
    ],
)
@pytest.mark.anyio
async def test_run_check(mocker, check: Check, apply_function_name: str):
    """This test is fully dynamic and pulls from CHECK_TYPE_TO_HANDLER to ensure every check type is tested
    and mocked."""

    # Arrange
    check_result = generate_class_instance(CheckResult)
    mock_run_check_function = mocker.patch(f"cactus_runner.app.check.{apply_function_name}")
    mock_run_check_function.return_value = check_result

    mock_backend = mock.Mock(spec=RunnerBackend)

    # Act
    actual = await run_check(check, generate_active_test_procedure_steps([], []), mock_backend)

    # Assert
    assert actual is check_result
    mock_run_check_function.assert_called_once()
    mock_backend.get_expression_resolver.assert_called_once()
    assert len(mock_backend.mock_calls) == 1


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
    mock_backend = mock.Mock(spec=RunnerBackend)

    # Act
    for check in reading_checks:
        actual = await run_check(check, generate_active_test_procedure_steps([], []), mock_backend)
        assert actual is check_result

    # Assert
    assert mock_do_check_site_readings_and_params.call_count == len(reading_checks)
    assert len(set(a.args[3:] for a in mock_do_check_site_readings_and_params.call_args_list)) == len(reading_checks), (
        "Each call to do_check_site_readings_and_params should have unique params "
        "(ignoring active_test_procedure/session/resolved_params)"
    )

    mock_backend.get_expression_resolver.assert_called()
    assert len(mock_backend.mock_calls) == len(reading_checks)


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
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, started_at=None
    )
    mock_session = create_mock_session()
    resolved_params = {}
    pen = 123
    site_check_result = generate_class_instance(CheckResult, seed=101, passed=site_passed)
    device_check_result = generate_class_instance(CheckResult, seed=202, passed=device_passed)
    mock_do_check_site_readings_and_params.side_effect = (
        lambda active_test_procedure, session, params, pen, uom, location, dq: (
            site_check_result if location == ReadingLocation.SITE_READING else device_check_result
        )
    )

    # Act
    result = await check_readings_voltage(active_test_procedure, mock_session, resolved_params, pen)

    # Assert
    assert_mock_session(mock_session)
    assert isinstance(result, CheckResult)
    assert result.passed is expected_result
    assert result.description is not None
    assert site_check_result.description is not None
    assert device_check_result.description is not None
    assert site_check_result.description in result.description or device_check_result.description in result.description

    # Cursory look at passed params
    assert mock_do_check_site_readings_and_params.call_count >= 1
    assert all([ca.args[0] is active_test_procedure for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[1] is mock_session for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[2] is resolved_params for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[3] is pen for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[4] is UomType.VOLTAGE for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all([ca.args[5] in ReadingLocation for ca in mock_do_check_site_readings_and_params.call_args_list])
    assert all(
        [ca.args[6] is DataQualifierType.AVERAGE for ca in mock_do_check_site_readings_and_params.call_args_list]
    )


SITE = ReadingLocation.SITE_READING
DEVICE = ReadingLocation.DEVICE_READING
AN, BN, CN, ABC = PhaseCode.PHASE_AN_S1N, PhaseCode.PHASE_BN, PhaseCode.PHASE_CN_S2N, PhaseCode.PHASE_ABC


@pytest.mark.parametrize(
    "site_phases, device_phases, expected, expected_in_description",
    [
        # Single MMR
        ([AN], [], True, ["site level", "129"]),
        ([BN], [], True, ["65"]),
        ([CN], [], True, ["33"]),
        ([ABC], [], True, ["224"]),
        ([], [AN], True, ["DER/device level", "129"]),
        ([PhaseCode.NOT_APPLICABLE], [], False, ["site level", "phase 0"]),
        ([PhaseCode.PHASE_A_S1], [], False, ["phase 128"]),
        ([PhaseCode.PHASE_AB], [], False, ["phase 132"]),
        # Multiple MMRs
        ([AN, BN, CN, ABC], [], True, ["129, 65, 33, 224"]),
        ([ABC, AN], [], True, []),
        ([ABC, ABC], [], True, []),
        ([AN, BN, CN], [], False, ["PHASE_ABC (224)"]),
        ([AN, AN], [], False, ["PHASE_ABC (224)"]),
        ([ABC, PhaseCode.NOT_APPLICABLE], [], False, ["phase 0"]),
        # Site and device are assessed independently
        ([AN, BN, CN, ABC], [AN], True, ["site level", "DER/device level"]),
        ([AN], [BN], True, []),
        ([ABC], [AN, BN], False, ["DER/device level"]),
        ([AN, BN], [ABC], False, ["site level"]),
        ([AN, BN], [PhaseCode.NOT_APPLICABLE], False, ["site level", "DER/device level"]),
        # Nothing registered
        ([], [], False, ["No site level or DER/device level"]),
    ],
)
@pytest.mark.anyio
async def test_check_voltage_phase_code(
    site_phases: list[PhaseCode],
    device_phases: list[PhaseCode],
    expected: bool,
    expected_in_description: list[str],
):
    site = generate_class_instance(dtos.Site, seed=1, site_id="1")
    located_phases = [(SITE, p) for p in site_phases] + [(DEVICE, p) for p in device_phases]
    mock_backend = mock.AsyncMock(spec=RunnerBackend)
    mock_backend.get_active_site.return_value = site
    mock_backend.get_site_reading_types.return_value = [
        generate_class_instance(
            dtos.SiteReadingType,
            seed=100 * (i + 1),
            site_id=site.site_id,
            uom=UomType.VOLTAGE,
            kind=KindType.POWER,
            data_qualifier=DataQualifierType.AVERAGE,
            role_flags=location,
            phase=phase,
        )
        for i, (location, phase) in enumerate(located_phases)
    ]

    result = await check_voltage_phase_code(mock_backend)

    assert_check_result(result, expected)
    assert result.description is not None
    for expected_text in expected_in_description:
        assert expected_text in result.description
    assert all(ca.kwargs == {"site_ids": ["1"]} for ca in mock_backend.get_site_reading_types.call_args_list)


@pytest.mark.parametrize(
    "ignored_values",
    [
        {"uom": UomType.REAL_POWER_WATT},
        {"kind": KindType.ENERGY},
        {"data_qualifier": DataQualifierType.MAXIMUM},
        {"role_flags": 0},
    ],
)
@pytest.mark.anyio
async def test_check_voltage_phase_code_ignores_other_reading_types(ignored_values: dict[str, Any]):
    """Only the average voltage readings at the site / device should be considered"""
    valid_values: dict[str, Any] = {
        "site_id": "1",
        "uom": UomType.VOLTAGE,
        "kind": KindType.POWER,
        "data_qualifier": DataQualifierType.AVERAGE,
        "role_flags": SITE,
    }
    valid = generate_class_instance(dtos.SiteReadingType, seed=101, phase=AN, **valid_values)
    ignored = generate_class_instance(
        dtos.SiteReadingType, seed=202, phase=PhaseCode.NOT_APPLICABLE, **(valid_values | ignored_values)
    )
    mock_backend = mock.AsyncMock(spec=RunnerBackend)
    mock_backend.get_active_site.return_value = generate_class_instance(dtos.Site, seed=1, site_id="1")

    mock_backend.get_site_reading_types.return_value = [valid, ignored]
    assert_check_result(await check_voltage_phase_code(mock_backend), True)

    mock_backend.get_site_reading_types.return_value = [ignored]
    assert_check_result(await check_voltage_phase_code(mock_backend), False)


@pytest.mark.anyio
async def test_check_voltage_phase_code_no_active_site():
    mock_backend = mock.AsyncMock(spec=RunnerBackend)
    mock_backend.get_active_site.return_value = None

    assert_check_result(await check_voltage_phase_code(mock_backend), False)
    mock_backend.get_site_reading_types.assert_not_called()


@pytest.mark.anyio
async def test_check_all_notifications_transmitted_no_logs(pg_base_config):
    """check_all_notifications_transmitted should fail if there are no logs"""
    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_all_notifications_transmitted(backend)
        assert_check_result(actual, False)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


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

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_all_notifications_transmitted(backend)
        assert_check_result(actual, True)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_subscription_contents_no_site_edev_list(pg_base_config):
    """check_subscription_contents should match subscriptions to /edev even if there is NO site registered"""

    agg_id = 1
    resolved_params = {"subscribed_resource": "/edev"}
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, client_aggregator_id=agg_id
    )

    async with generate_async_session(pg_base_config) as session:
        agg = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == agg_id))).scalar_one()
        session.add(
            generate_class_instance(
                Subscription,
                resource_type=SubscriptionResource.SITE,
                resource_id=None,
                scoped_site_id=None,
                aggregator=agg,
            )
        )
        await session.commit()

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_subscription_contents(resolved_params, backend, active_test_procedure)
        assert_check_result(actual, True)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_subscription_contents_no_matches(pg_base_config):
    """check_subscription_contents should fail if there is no matching subscription"""

    agg_id = 1
    resolved_params = {"subscribed_resource": "/edev/1/derp/2/derc"}
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, client_aggregator_id=agg_id
    )

    # Fill up the DB with subscriptions
    async with generate_async_session(pg_base_config) as session:
        agg1 = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == agg_id))).scalar_one()
        agg2 = Aggregator(aggregator_id=2, name="test2", changed_time=datetime(2022, 11, 22, tzinfo=UTC))
        session.add(agg2)

        site1 = generate_class_instance(
            Site, nmi_checksum=None, seed=1001, site_id=1, aggregator_id=agg_id
        )  # Active Site
        site2 = generate_class_instance(Site, nmi_checksum=None, seed=202, site_id=2, aggregator_id=agg_id)
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

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_subscription_contents(resolved_params, backend, active_test_procedure)
        assert_check_result(actual, False)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_subscription_contents_success(pg_base_config):
    """check_subscription_contents should succeed if there is at least 1 matching subscription"""

    agg_id = 1
    resolved_params = {"subscribed_resource": "/edev/1/derp/2/derc"}
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, client_aggregator_id=agg_id
    )

    # Fill up the DB with subscriptions
    async with generate_async_session(pg_base_config) as session:
        agg1 = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == agg_id))).scalar_one()
        agg2 = Aggregator(aggregator_id=2, name="test2", changed_time=datetime(2022, 11, 22, tzinfo=UTC))
        session.add(agg2)

        site1 = generate_class_instance(
            Site, nmi_checksum=None, seed=1001, site_id=1, aggregator_id=agg_id
        )  # Active Site
        site2 = generate_class_instance(Site, nmi_checksum=None, seed=202, site_id=2, aggregator_id=agg_id)
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

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_subscription_contents(resolved_params, backend, active_test_procedure)
        assert_check_result(actual, True)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_subscription_contents_success_unscoped(pg_base_config):
    """check_subscription_contents should succeed if there is an unscoped subscription - eg to /edev"""

    agg_id = 1
    resolved_params = {"subscribed_resource": "/edev"}
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, step_status={}, finished_zip_path=None, client_aggregator_id=agg_id
    )

    # Fill up the DB with subscriptions
    async with generate_async_session(pg_base_config) as session:
        agg1 = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == 1))).scalar_one()
        agg2 = Aggregator(aggregator_id=2, name="test2", changed_time=datetime(2022, 11, 22, tzinfo=UTC))
        session.add(agg2)

        site1 = generate_class_instance(
            Site, nmi_checksum=None, seed=1001, site_id=1, aggregator_id=agg_id
        )  # Active Site
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

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_subscription_contents(resolved_params, backend, active_test_procedure)
        assert_check_result(actual, True)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


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

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_all_notifications_transmitted(backend)
        assert_check_result(actual, False)
        assert actual.description is not None
        assert "not presented by the runner backend" in actual.description, "No Subscription exists for the log"

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_all_notifications_transmitted_failure_reports_notification_uri(pg_base_config):
    """check_all_notifications_transmitted should report the notification_uri of the failing subscription"""

    async with generate_async_session(pg_base_config) as session:
        agg = (await session.execute(select(Aggregator).where(Aggregator.aggregator_id == 1))).scalar_one()
        session.add(
            generate_class_instance(
                Subscription, subscription_id=1, aggregator=agg, scoped_site_id=None, notification_uri="https://dev/1"
            )
        )
        session.add(
            generate_class_instance(
                TransmitNotificationLog,
                transmit_notification_log_id=None,
                subscription_id_snapshot=1,
                http_status_code=500,
            )
        )
        await session.commit()

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        actual = await check_all_notifications_transmitted(backend)
        assert_check_result(actual, False)
        assert actual.description is not None
        assert "https://dev/1" in actual.description

    mock_admin_client.assert_not_called()


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
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)
    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:
        site_control_group = generate_class_instance(SiteControlGroup, seed=101, required_site_group_id=None)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, nmi_checksum=None, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        sg1 = generate_class_instance(SiteGroup, seed=303)
        session.add(sg1)
        await session.flush()

        session.add(generate_class_instance(SiteGroupAssignment, group=sg1, site=site1))

        der_control_1 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=303,
            site_group_id=sg1.site_group_id,
            site_control_group=site_control_group,
            calculation_log_id=None,
        )
        session.add(der_control_1)

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=505,
                response_type=ResponseType.EVENT_CANCELLED,
                created_time=datetime(2024, 11, 10, tzinfo=UTC),
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
                created_time=datetime(2024, 11, 11, tzinfo=UTC),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=707,
                response_type=ResponseType.EVENT_RECEIVED,
                created_time=datetime(2024, 11, 9, tzinfo=UTC),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )
        await session.commit()

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        # This will check that there is a latest
        assert_check_result(await check_response_contents({"latest": True}, backend, active_test_procedure), True)

        # This will check that there is a latest and that the status matches the filter
        assert_check_result(
            await check_response_contents(
                {"latest": True, "status": ResponseType.EVENT_COMPLETED.value}, backend, active_test_procedure
            ),
            True,
        )

        # This will check that the filter on latest will fail if there is mismatch on the latest record
        assert_check_result(
            await check_response_contents(
                {"latest": True, "status": ResponseType.EVENT_CANCELLED.value}, backend, active_test_procedure
            ),
            False,
        )

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


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
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)
    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:
        site_control_group = generate_class_instance(SiteControlGroup, seed=101, required_site_group_id=None)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, nmi_checksum=None, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        sg1 = generate_class_instance(SiteGroup, seed=303)
        session.add(sg1)
        await session.flush()

        session.add(generate_class_instance(SiteGroupAssignment, group=sg1, site=site1))

        for idx, control_id in enumerate(control_ids):
            control = generate_class_instance(
                DynamicOperatingEnvelope,
                seed=idx,
                site_group_id=sg1.site_group_id,
                site_control_group=site_control_group,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        for idx, control_id in enumerate(deleted_control_ids):
            control = generate_class_instance(
                ArchiveDynamicOperatingEnvelope,
                seed=idx * 1001,
                site_group_id=sg1.site_group_id,
                deleted_time=datetime(2022, 11, 14, tzinfo=UTC),
                site_control_group_id=site_control_group.site_control_group_id,
                calculation_log_id=None,
                dynamic_operating_envelope_id=control_id,
            )
            session.add(control)

        for idx, t in enumerate(response_status_values):
            response_control_id, response_status = t
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

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        params: dict = {"all": True}
        if status is not None:
            params["status"] = status
        assert_check_result(await check_response_contents(params, backend, active_test_procedure), expected)

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_response_contents_any(pg_base_config):
    """check_response_contents should behave correctly when looking at ANY of the Responses"""
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)
    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:
        site_control_group = generate_class_instance(SiteControlGroup, seed=101, required_site_group_id=None)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, nmi_checksum=None, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        sg1 = generate_class_instance(SiteGroup, seed=303)
        session.add(sg1)
        await session.flush()

        session.add(generate_class_instance(SiteGroupAssignment, group=sg1, site=site1))

        der_control_1 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=303,
            site_group_id=sg1.site_group_id,
            site_control_group=site_control_group,
            calculation_log_id=None,
        )
        session.add(der_control_1)

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=505,
                response_type=ResponseType.EVENT_CANCELLED,
                created_time=datetime(2024, 11, 10, tzinfo=UTC),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=606,
                response_type=ResponseType.EVENT_COMPLETED,
                created_time=datetime(2024, 11, 11, tzinfo=UTC),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=707,
                response_type=ResponseType.EVENT_RECEIVED,
                created_time=datetime(2024, 11, 9, tzinfo=UTC),
                site=site1,
                dynamic_operating_envelope_id_snapshot=der_control_1.dynamic_operating_envelope_id,
            )
        )
        await session.commit()

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        # This will check that there is any response
        assert_check_result(await check_response_contents({"latest": False}, backend, active_test_procedure), True)
        assert_check_result(await check_response_contents({}, backend, active_test_procedure), True)

        # Checks on existing values
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_COMPLETED.value}, backend, active_test_procedure
            ),
            True,
        )
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_RECEIVED.value}, backend, active_test_procedure
            ),
            True,
        )
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_CANCELLED.value}, backend, active_test_procedure
            ),
            True,
        )

        # This will check that the filter will fail if a matching record cant be found
        assert_check_result(
            await check_response_contents(
                {"latest": False, "status": ResponseType.CANNOT_BE_DISPLAYED.value}, backend, active_test_procedure
            ),
            False,
        )

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_response_contents_empty(pg_base_config):
    """check_response_contents should behave correctly when the DB is empty of responses"""
    active_test_procedure = generate_class_instance(ActiveTestProcedure, step_status={}, finished_zip_path=None)
    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        # This will check that there is any response
        assert_check_result(await check_response_contents({"latest": False}, backend, active_test_procedure), False)
        assert_check_result(await check_response_contents({"latest": True}, backend, active_test_procedure), False)
        assert_check_result(await check_response_contents({}, backend, active_test_procedure), False)
        assert_check_result(
            await check_response_contents(
                {"status": ResponseType.EVENT_COMPLETED.value}, backend, active_test_procedure
            ),
            False,
        )
        assert_check_result(
            await check_response_contents(
                {"latest": True, "status": ResponseType.EVENT_COMPLETED.value}, backend, active_test_procedure
            ),
            False,
        )

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_check_response_contents_tag_DERC1(pg_base_config):
    """check_response_contents should behave correctly when filtering by tag"""
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, resource_annotations=ResourceAnnotations(), step_status={}, finished_zip_path=None
    )

    # Set up resource annotations with tagged control IDs
    active_test_procedure.resource_annotations.der_control_ids_by_alias = {"DERC1": "100", "DERC2": "200"}

    # Fill up the DB with responses
    async with generate_async_session(pg_base_config) as session:
        site_control_group = generate_class_instance(SiteControlGroup, seed=101, required_site_group_id=None)
        session.add(site_control_group)

        site1 = generate_class_instance(Site, nmi_checksum=None, seed=202, site_id=1, aggregator_id=1)
        session.add(site1)

        sg1 = generate_class_instance(SiteGroup, seed=303)
        session.add(sg1)
        await session.flush()

        session.add(generate_class_instance(SiteGroupAssignment, group=sg1, site=site1))

        # Create control with ID 100 (tagged as DERC1)
        der_control_1 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=303,
            site_group_id=sg1.site_group_id,
            site_control_group=site_control_group,
            calculation_log_id=None,
            dynamic_operating_envelope_id=100,
        )
        session.add(der_control_1)

        # Create control with ID 200 (tagged as DERC2)
        der_control_2 = generate_class_instance(
            DynamicOperatingEnvelope,
            seed=304,
            site_group_id=sg1.site_group_id,
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
                created_time=datetime(2024, 11, 9, tzinfo=UTC),
                site=site1,
                dynamic_operating_envelope_id_snapshot=100,
            )
        )

        session.add(
            generate_class_instance(
                DynamicOperatingEnvelopeResponse,
                seed=606,
                response_type=ResponseType.EVENT_COMPLETED,
                created_time=datetime(2024, 11, 11, tzinfo=UTC),
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
                created_time=datetime(2024, 11, 10, tzinfo=UTC),
                site=site1,
                dynamic_operating_envelope_id_snapshot=200,
            )
        )
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        mock_admin_client = mock.Mock(spec=EnvoyBackend)
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        # Check responses for DERC1 tag can be found
        assert_check_result(
            await check_response_contents({"subject_tag": "DERC1"}, backend, active_test_procedure), True
        )

        # Check latest response for DERC1 is EVENT_COMPLETED
        assert_check_result(
            await check_response_contents({"subject_tag": "DERC1", "latest": True}, backend, active_test_procedure),
            True,
        )

        # Check latest response for DERC1 with correct status matches
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "latest": True, "status": ResponseType.EVENT_COMPLETED.value},
                backend,
                active_test_procedure,
            ),
            True,
        )

        # Check latest response for DERC1 with wrong status fails
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "latest": True, "status": ResponseType.EVENT_CANCELLED.value},
                backend,
                active_test_procedure,
            ),
            False,
        )

        # Check DERC1 has a response of type EVENT_RECEIVED
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "status": ResponseType.EVENT_RECEIVED.value}, backend, active_test_procedure
            ),
            True,
        )

        # Check DERC1 does not have a response of type EVENT_CANCELLED
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC1", "status": ResponseType.EVENT_CANCELLED.value}, backend, active_test_procedure
            ),
            False,
        )

        # Check DERC2 has EVENT_CANCELLED (different control)
        assert_check_result(
            await check_response_contents(
                {"subject_tag": "DERC2", "status": ResponseType.EVENT_CANCELLED.value}, backend, active_test_procedure
            ),
            True,
        )

        # Check non-existent tag returns failure
        assert_check_result(
            await check_response_contents({"subject_tag": "NONEXISTENT"}, backend, active_test_procedure),
            False,
        )

        # Currently not relying on admin api for checks. This may change.
        mock_admin_client.assert_not_called()


@pytest.mark.anyio
async def test_run_check_check_dne():
    """Trying to run a check that does not exist will raise an appropriate error"""

    # Arrange
    check = Check(type="this-check-does-not-exist", parameters={})
    mock_backend = mock.Mock(spec=RunnerBackend)

    # Act
    with pytest.raises(UnknownCheckError):
        await run_check(check, generate_active_test_procedure_steps([], []), mock_backend)

    # Assert
    mock_backend.get_expression_resolver.assert_called_once()
    assert len(mock_backend.mock_calls) == 1


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
    mock_backend = mock.Mock(spec=RunnerBackend)
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
            await first_failing_check(checks, generate_active_test_procedure_steps([], []), mock_backend)
    else:
        first_failing_result = await first_failing_check(
            checks, generate_active_test_procedure_steps([], []), mock_backend
        )

        if expected is True:
            assert first_failing_result is None
        else:
            assert isinstance(first_failing_result, CheckResult)
            assert expected is first_failing_result.passed

    # Assert
    assert not mock_backend.mock_calls


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
    mock_backend = mock.Mock(spec=RunnerBackend)
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
            await all_checks_passing(checks, generate_active_test_procedure_steps([], []), mock_session, mock_backend)
    else:
        all_checks_result = await all_checks_passing(
            checks, generate_active_test_procedure_steps([], []), mock_session, mock_backend
        )
        assert isinstance(all_checks_result, bool)
        assert all_checks_result == expected

    # Assert
    assert_mock_session(mock_session)


def test_params_der_settings_contents_model_has_correct_fields():
    """Ensures aliases for fields matches expected in the param definitions"""
    dscm = ParamsDERSettingsContents()

    assert sorted([f.alias or "" for f in dscm.__pydantic_fields__.values()]) == sorted(
        [f for f in CHECK_PARAMETER_SCHEMA["der-settings-contents"]]
    )


def test_params_der_capability_contents_model_has_correct_fields():
    """Ensures aliases for fields matches expected in the param definitions"""
    dccm = ParamsDERCapabilityContents()

    assert sorted([f.alias or "" for f in dccm.__pydantic_fields__.values()]) == sorted(
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
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, grad_w=12345))],
            {"setGradW": evaluator.ResolvedParam(1234)},
            False,
            r"setGradW [0-9]+ doesn't match (expected)?[: ]+[0-9]+",
        ),  # setGradW doesn't match value
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating),
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
                    nmi_checksum=None,
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                )
            ],
            {},
            False,
            r"No DERSetting found for [Ee]nd[Dd]evice [0-9]+",
        ),
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fe", 16)))],
            {"doeModesEnabled_set": evaluator.ResolvedParam("03")},
            False,
            r"doeModesEnabled_set.* minimum flag .* check hi.* failed",
        ),  # Bit flag 1 not set on actual value
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, modes_enabled=int("fe", 16)))],
            {"modesEnabled_set": evaluator.ResolvedParam("03")},
            False,
            r"modesEnabled_set.* minimum flag .* check hi.* failed",
        ),  # Bit flag 1 not set on actual value
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, doe_modes_enabled=int("fd", 16)))],
            {"doeModesEnabled_unset": evaluator.ResolvedParam("03")},
            False,
            r"doeModesEnabled_unset.* minimum flag .* check lo.* failed",
        ),  # Bit flag 1 set on actual value
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, modes_enabled=int("fd", 16)))],
            {"modesEnabled_unset": evaluator.ResolvedParam("03")},
            False,
            r"modesEnabled_unset.* minimum flag .* check lo.* failed",
        ),  # Bit flag 1 set on actual value
        (
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, max_va_value=12345))],
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
            [generate_site_with_der_setting(generate_class_instance(SiteDERSetting, max_va_value=12345))],
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
    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await check_der_settings_contents(backend, resolved_params)
        assert_check_result(result, expected)
        assert result.description is not None
        assert re.search(msg_regex, result.description) is not None, (
            f"'{msg_regex}' not found in '{result.description}'"
        )

    # At the moment the envoy admin api isn't expected to be used, this may change
    mock_admin_client.assert_not_called()


@pytest.mark.parametrize(
    "existing_sites, resolved_params, expected, msg_regex",
    [
        ([], {}, False, r"[Nn]o [Ee]nd ?[Dd]evice is currently registered"),
        (
            [
                generate_class_instance(
                    Site,
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_setting=generate_class_instance(SiteDERSetting),
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
                    nmi_checksum=None,
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
                    nmi_checksum=None,
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fe", 16)),
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fe", 16)),
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, doe_modes_supported=int("fd", 16)),
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, modes_supported=int("fd", 16)),
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, max_va_value=12345),
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
                    nmi_checksum=None,
                    seed=101,
                    aggregator_id=1,
                    site_der_rating=generate_class_instance(SiteDERRating, max_va_value=12345),
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
    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        session.add_all(existing_sites)
        await session.commit()

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await check_der_capability_contents(backend, resolved_params)
        assert_check_result(result, expected)
        assert result.description is not None
        assert re.search(msg_regex, result.description) is not None, (
            f"'{msg_regex}' not found in '{result.description}'"
        )

    # Currently not relying on admin api for checks. This may change.
    mock_admin_client.assert_not_called()


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
async def test_do_check_readings_for_duration_envoy(pg_base_config, srt_ids: list[int], expected_result: bool):
    """Tests that do_check_readings_for_duration validates time_period_seconds for envoy backend."""
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
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
        generate_class_instance(dtos.SiteReadingType, seed=srt_id, site_reading_type_id=srt_id) for srt_id in srt_ids
    ]

    mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await do_check_readings_for_duration(backend=backend, site_reading_types=faked_srts)
        assert_check_result(result, expected_result)

    # Currently not relying on admin api for checks. This may change
    mock_admin_client.assert_not_called()


@pytest.mark.parametrize(
    "configured_post_rate_seconds, reading_periods, expected_result",
    [
        (None, [], True),  # No readings, default post rate (60)
        (None, [60, 60], True),  # Matches default post rate (60)
        (None, [60, 120], False),  # Doesn't match default post rate (60)
        (300, [300, 300], True),  # Matches configured post rate
        (300, [300, 60], False),  # One matches, one doesn't
        (300, [60, 120], False),  # None match
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_match_post_rate(
    pg_base_config,
    configured_post_rate_seconds: int | None,
    reading_periods: list[int],
    expected_result: bool,
):
    """Tests that do_check_readings_match_post_rate validates time_period_seconds against mup_postrate_seconds"""
    base_time = datetime(2024, 1, 1, tzinfo=UTC)

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        srt = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        session.add_all([site, srt])

        for i, period in enumerate(reading_periods):
            session.add(
                generate_class_instance(
                    SiteReading,
                    seed=1000 + i,
                    site_reading_type=srt,
                    time_period_seconds=period,
                    time_period_start=base_time + timedelta(minutes=i),
                )
            )

        if configured_post_rate_seconds is not None:
            # Config must predate all the readings above to be considered "active" for the whole test
            session.add(
                RuntimeServerConfig(
                    mup_postrate_seconds=configured_post_rate_seconds, changed_time=base_time - timedelta(days=1)
                )
            )

        await session.commit()

    faked_srts = [generate_class_instance(dtos.SiteReadingType, seed=1, site_reading_type_id="1")]

    async with generate_async_session(pg_base_config) as session:
        mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await do_check_readings_match_post_rate(backend, site_reading_types=faked_srts)
        assert_check_result(result, expected_result)


@pytest.mark.anyio
async def test_do_check_readings_match_post_rate_mid_run_change(pg_base_config):
    """Regression test shaped like ALL-10 ('Update telemetry post rates'): the post rate changes mid test run
    (60s -> 300s -> 60s), and readings taken in each segment should be checked against the rate that was active
    at their own time_period_start, not whatever the rate happens to be by the time the check runs."""

    t0 = datetime(2024, 1, 1, tzinfo=UTC)  # 60s segment starts
    t1 = t0 + timedelta(hours=1)  # rate changed to 300s
    t2 = t1 + timedelta(hours=1)  # rate reverted to 60s

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        srt = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        session.add_all([site, srt])

        # Readings taken in each of the three segments - all should be considered valid
        session.add_all(
            [
                generate_class_instance(
                    SiteReading,
                    seed=1,
                    site_reading_type=srt,
                    time_period_seconds=60,
                    time_period_start=t0 + timedelta(minutes=10),
                ),
                generate_class_instance(
                    SiteReading,
                    seed=2,
                    site_reading_type=srt,
                    time_period_seconds=300,
                    time_period_start=t1 + timedelta(minutes=10),
                ),
                generate_class_instance(
                    SiteReading,
                    seed=3,
                    site_reading_type=srt,
                    time_period_seconds=60,
                    time_period_start=t2 + timedelta(minutes=10),
                ),
            ]
        )

        # The oldest state (60s) has been superseded twice, so it's archived
        session.add(
            ArchiveRuntimeServerConfig(
                runtime_server_config_id=1, mup_postrate_seconds=60, created_time=t0, changed_time=t0
            )
        )
        # The middle state (300s) has also been superseded, so it's archived too
        session.add(
            ArchiveRuntimeServerConfig(
                runtime_server_config_id=1, mup_postrate_seconds=300, created_time=t0, changed_time=t1
            )
        )
        # The current live state is the reverted 60s rate
        session.add(
            RuntimeServerConfig(runtime_server_config_id=1, mup_postrate_seconds=60, created_time=t0, changed_time=t2)
        )

        await session.commit()

    faked_srts = [generate_class_instance(dtos.SiteReadingType, seed=1, site_reading_type_id="1")]

    async with generate_async_session(pg_base_config) as session:
        mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await do_check_readings_match_post_rate(backend, site_reading_types=faked_srts)
        assert_check_result(result, True)


# Every reading in these tests covers 10:00:00 -> 10:01:00 (60s) unless stated otherwise
INTERVAL_START = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
INTERVAL_END = INTERVAL_START + timedelta(seconds=60)


@pytest.mark.parametrize(
    "time_period_start, time_period_seconds, received_at, expected_pass",
    [
        pytest.param(INTERVAL_START, 60, INTERVAL_END, True, id="received-exactly-at-interval-end"),
        pytest.param(INTERVAL_START, 60, INTERVAL_END + timedelta(seconds=5), True, id="received-after-interval-end"),
        pytest.param(INTERVAL_START, 60, INTERVAL_END + timedelta(hours=3), True, id="backfilled-hours-later"),
        pytest.param(INTERVAL_START, 60, INTERVAL_END - timedelta(seconds=10), True, id="10s-early-within-leeway"),
        pytest.param(INTERVAL_START, 60, INTERVAL_END - timedelta(seconds=11), False, id="11s-early-outside-leeway"),
        pytest.param(INTERVAL_START, 60, INTERVAL_START + timedelta(seconds=5), False, id="received-partway-through"),
        pytest.param(INTERVAL_START, 60, INTERVAL_START, False, id="received-at-interval-start"),
        pytest.param(INTERVAL_START, 60, INTERVAL_START - timedelta(minutes=5), False, id="start-time-in-future"),
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_received_after_interval_end(
    pg_base_config,
    time_period_start: datetime,
    time_period_seconds: int,
    received_at: datetime,
    expected_pass: bool,
):
    """A single reading, received (created_time) at received_at - it should only pass if received no more than 10s
    before its interval ended."""
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        srt = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        reading = generate_class_instance(
            SiteReading,
            seed=1,
            site_reading_type=srt,
            time_period_start=time_period_start,
            time_period_seconds=time_period_seconds,
            created_time=received_at,
        )
        session.add_all([site, srt, reading])
        await session.commit()

    faked_srts = [generate_class_instance(dtos.SiteReadingType, seed=1, site_reading_type_id="1")]

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        result = await do_check_readings_received_after_interval_end(backend, site_reading_types=faked_srts)

    assert_check_result(result, expected_pass)


@pytest.mark.parametrize(
    "original_received_at, repost_received_at, expected_early_count",
    [
        pytest.param(INTERVAL_START, INTERVAL_END, 1, id="early-post-later-corrected-by-repost"),
        pytest.param(INTERVAL_END, INTERVAL_END + timedelta(seconds=30), 0, id="on-time-post-then-on-time-retry"),
        pytest.param(INTERVAL_START, INTERVAL_START + timedelta(seconds=30), 2, id="early-post-then-early-repost"),
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_received_after_interval_end_overwritten_readings(
    pg_base_config,
    original_received_at: datetime,
    repost_received_at: datetime,
    expected_early_count: int,
):
    """The same interval posted twice: envoy archives the original and stores the repost. Same rules apply to timing."""
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        srt = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        repost = generate_class_instance(
            SiteReading,
            seed=1,
            site_reading_type=srt,
            time_period_start=INTERVAL_START,
            time_period_seconds=60,
            created_time=repost_received_at,
        )
        original = generate_class_instance(
            ArchiveSiteReading,
            seed=2,
            site_reading_type_id=1,
            time_period_start=INTERVAL_START,
            time_period_seconds=60,
            created_time=original_received_at,
            deleted_time=repost_received_at,
        )
        session.add_all([site, srt, repost, original])
        await session.commit()

    faked_srts = [generate_class_instance(dtos.SiteReadingType, seed=1, site_reading_type_id="1")]

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        result = await do_check_readings_received_after_interval_end(backend, site_reading_types=faked_srts)

    assert_check_result(result, expected_early_count == 0)
    if expected_early_count:
        assert result.description is not None
        assert result.description.startswith(f"{expected_early_count} readings were received before")
        assert "- 1 were later re-posted with the same start time" in result.description


@pytest.mark.anyio
async def test_do_check_readings_received_after_interval_end_error_message(pg_base_config):
    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        srt = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        partway = generate_class_instance(
            SiteReading,
            seed=1,
            site_reading_type=srt,
            time_period_start=INTERVAL_START,
            time_period_seconds=60,
            created_time=INTERVAL_START + timedelta(seconds=2),  # 58s before the interval ended
        )
        future = generate_class_instance(
            SiteReading,
            seed=2,
            site_reading_type=srt,
            time_period_start=INTERVAL_START + timedelta(minutes=10),
            time_period_seconds=60,
            created_time=INTERVAL_START + timedelta(minutes=9),  # Before the interval started
        )
        on_time = generate_class_instance(
            SiteReading,
            seed=3,
            site_reading_type=srt,
            time_period_start=INTERVAL_START + timedelta(minutes=20),
            time_period_seconds=60,
            created_time=INTERVAL_START + timedelta(minutes=21),
        )
        session.add_all([site, srt, partway, future, on_time])
        await session.commit()

    faked_srts = [generate_class_instance(dtos.SiteReadingType, seed=1, site_reading_type_id="1")]

    async with generate_async_session(pg_base_config) as session:
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock.Mock(spec=EnvoyAdminClient))
        result = await do_check_readings_received_after_interval_end(backend, site_reading_types=faked_srts)

    assert_check_result(result, False)
    assert result.description == (
        "2 readings were received before their interval had ended (10s leeway). Only post a reading once its interval "
        "is over.\n"
        "- 1 were received during their interval.\n"
        "- 1 had a start time in the future (check your clock is synced to /tm)."
    )


@pytest.mark.parametrize(
    "reading_offset, reading_duration, expected_result",
    [
        (timedelta(seconds=1), 60, True),  # Just after the change, still posted at the old (60s) rate - a straggler
        (timedelta(seconds=299), 60, True),  # Near the edge of the 300s window (max of 60/300) - still accepted
        (timedelta(seconds=301), 60, False),  # Outside the window - old rate no longer accepted
        (timedelta(seconds=-1), 300, True),  # Backdated: re-aggregated old samples into one new-rate (300s) reading
        (timedelta(seconds=-299), 300, True),  # Still within the window on the backdated side
        (timedelta(seconds=-301), 300, False),  # Outside the window - new rate not accepted this far back
    ],
)
@pytest.mark.anyio
async def test_do_check_readings_match_post_rate_near_transition(
    pg_base_config, reading_offset: timedelta, reading_duration: int, expected_result: bool
):
    """A reading taken close to a rate change (within the larger of the old/new rate) is allowed to match either
    rate - covering both a client lagging on adopting a new rate, and a client re-aggregating older fine-grained
    samples into one coarser, retroactively-dated reading."""

    t0 = datetime(2024, 1, 1, tzinfo=UTC)  # 60s rate starts
    t1 = t0 + timedelta(hours=1)  # rate changes to 300s

    async with generate_async_session(pg_base_config) as session:
        site = generate_class_instance(Site, nmi_checksum=None, aggregator_id=1, site_id=1)
        srt = generate_class_instance(SiteReadingType, seed=101, site_reading_type_id=1, aggregator_id=1, site=site)
        session.add_all([site, srt])

        session.add(
            generate_class_instance(
                SiteReading,
                seed=1,
                site_reading_type=srt,
                time_period_seconds=reading_duration,
                time_period_start=t1 + reading_offset,
            )
        )

        session.add(
            ArchiveRuntimeServerConfig(
                runtime_server_config_id=1, mup_postrate_seconds=60, created_time=t0, changed_time=t0
            )
        )
        session.add(
            RuntimeServerConfig(runtime_server_config_id=1, mup_postrate_seconds=300, created_time=t0, changed_time=t1)
        )

        await session.commit()

    faked_srts = [generate_class_instance(dtos.SiteReadingType, seed=1, site_reading_type_id="1")]

    async with generate_async_session(pg_base_config) as session:
        mock_admin_client = mock.Mock(spec=EnvoyAdminClient)
        backend = EnvoyBackend(session_factory=lambda: session, admin_client=mock_admin_client)
        result = await do_check_readings_match_post_rate(backend, site_reading_types=faked_srts)
        assert_check_result(result, expected_result)


@pytest.mark.parametrize(
    "request_path, expected",
    [
        ("/mup/1", True),  # Exact
        ("/other/endpoint", False),  # Non-matching endpoint
    ],
)
def test_check_all_polls_at_correct_time_path_matching(request_path: str, expected: bool):
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path=request_path,
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": poll_interval, "request_type_str": "GET"},
    )

    assert_check_result(result, expected)


def test_check_all_polls_at_correct_time_multiple_endpoints():
    """A list of distinct endpoint patterns can be checked together in a single check - each distinct concrete
    path across all given endpoints is validated independently at the same poll_interval_seconds."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    # /derp and /derc are distinct endpoints, each polled correctly every 60s
    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path="/derp",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
    ] + [
        generate_class_instance(
            RequestEntry,
            seed=100 + i,
            path="/derc",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/derp", "/derc"], "poll_interval_seconds": poll_interval, "request_type_str": "GET"},
    )

    assert_check_result(result, True)


def test_check_all_polls_at_correct_time_multiple_endpoints_one_fails():
    """With multiple endpoints, a single path missing polls causes an overall failure and is named in the result."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    # /derp polls correctly; /derc has two isolated misses landing in the same 300s window
    good_requests = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path="/derp",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
    ]
    bad_offsets = [0, 310, 550, 600]
    bad_requests = [
        generate_class_instance(
            RequestEntry,
            seed=100 + i,
            path="/derc",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=offset),
        )
        for i, offset in enumerate(bad_offsets)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        good_requests + bad_requests,
        {"endpoints": ["/derp", "/derc"], "poll_interval_seconds": poll_interval, "request_type_str": "GET"},
    )

    assert_check_result(result, False)
    assert result.description is not None
    assert "/derc" in result.description


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://envoy/mup/1", True),  # No s param — first page
        ("http://envoy/mup/1?s=0", True),  # Explicit s=0 — first page
        ("http://envoy/mup/1?s=0&l=10", True),  # s=0 with limit — first page
        ("http://envoy/mup/1?s=10", False),  # s=10 — not first page
        ("http://envoy/mup/1?s=1", False),  # s=1 — not first page
    ],
)
def test_check_all_polls_at_correct_time_pagination_filtering(url: str, expected: bool):
    """Requests with s>0 (subsequent pages) are excluded from poll timing counts."""
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            url=url,
            path="/mup/1",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": poll_interval, "request_type_str": "GET"},
    )

    assert_check_result(result, expected)


@pytest.mark.parametrize(
    "offsets_seconds, expected_passed, description_contains",
    [
        ([0, 60, 120, 180, 240], True, None),  # Consistent 60s cadence: fine
        # Two isolated misses (gap > 90s) both landing in the same 300s window: flagged
        ([0, 310, 550, 600], False, "missed poll(s) > 1 allowed"),
        # A single isolated miss is tolerated
        ([0, 200, 260, 320, 380], True, None),
    ],
)
def test_check_all_polls_at_correct_time_under_polling_gaps(
    offsets_seconds: list[int], expected_passed: bool, description_contains: str | None
):
    """Under-polling: a gap > 1.5x the poll interval is a missed poll; at most 1 miss is tolerated per 5x window."""
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path="/mup/1",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=offset),
        )
        for i, offset in enumerate(offsets_seconds)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": poll_interval, "request_type_str": "GET"},
    )

    assert_check_result(result, expected_passed)
    if description_contains is not None:
        assert result.description is not None
        assert description_contains in result.description


@pytest.mark.parametrize(
    "offsets_seconds, expected_passed, description_contains",
    [
        # 6 requests within a single 180s window: exactly at max
        ([0, 30, 60, 90, 120, 150, 400], True, None),
        # 7 requests within a single 180s window: one over
        ([0, 25, 50, 75, 100, 125, 150, 400], False, "poll(s) > 6 allowed"),
    ],
)
def test_check_all_polls_at_correct_time_over_polling_window(
    offsets_seconds: list[int], expected_passed: bool, description_contains: str | None
):
    """Over-polling: no more than floor(1.5 * 3 + 2) = 6 requests per 3x-interval (180s) window."""
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path="/mup/1",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=offset),
        )
        for i, offset in enumerate(offsets_seconds)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": poll_interval, "request_type_str": "GET"},
    )

    assert_check_result(result, expected_passed)
    if description_contains is not None:
        assert result.description is not None
        assert description_contains in result.description


def test_check_all_polls_at_correct_time_last_window_no_false_positive():
    """A compliant client whose last poll falls just into a new window should not be penalised."""
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    # Polls at 0, 60, ..., 480s then one at 541s (1s into window 4 which starts at 540s)
    offsets = list(range(0, 481, 60)) + [541]
    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path="/mup/1",
            method=http.HTTPMethod.GET,
            timestamp=base_time + timedelta(seconds=offset),
        )
        for i, offset in enumerate(offsets)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": poll_interval, "request_type_str": "GET"},
    )

    assert_check_result(result, True)


def test_check_all_polls_at_correct_time_filters_by_request_type():
    """Only requests matching request_type_str are counted - other methods are ignored."""
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    # GETs are correctly spaced, but if wrongly counted alongside the POSTs they wouldn't change the POST-only
    # outcome below - POSTs alone have two isolated misses (gap > 90s) landing in the same 300s window.
    request_history = [
        generate_class_instance(RequestEntry, seed=1, path="/mup/1", method=http.HTTPMethod.GET, timestamp=base_time),
        generate_class_instance(
            RequestEntry, seed=2, path="/mup/1", method=http.HTTPMethod.GET, timestamp=base_time + timedelta(seconds=60)
        ),
        generate_class_instance(RequestEntry, seed=3, path="/mup/1", method=http.HTTPMethod.POST, timestamp=base_time),
        generate_class_instance(
            RequestEntry,
            seed=4,
            path="/mup/1",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=310),
        ),
        generate_class_instance(
            RequestEntry,
            seed=5,
            path="/mup/1",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=550),
        ),
        generate_class_instance(
            RequestEntry,
            seed=6,
            path="/mup/1",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=600),
        ),
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": poll_interval, "request_type_str": "POST"},
    )

    assert_check_result(result, False)


@pytest.mark.parametrize(
    "request_type_str, request_method",
    [
        ("GET", http.HTTPMethod.GET),
        ("POST", http.HTTPMethod.POST),
        ("PUT", http.HTTPMethod.PUT),
    ],
)
def test_check_all_polls_at_correct_time_request_type_variants(request_type_str: str, request_method: http.HTTPMethod):
    """Each supported request_type_str correctly matches the corresponding HTTP method."""
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path="/mup/1",
            method=request_method,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": poll_interval, "request_type_str": request_type_str},
    )

    assert_check_result(result, True)


def test_check_all_polls_at_correct_time_wildcard_checks_each_path_independently():
    """With a wildcard endpoint, each distinct concrete path is validated independently.

    Two MUPs (/mup/2 and /mup/3) both posting at 60s are each valid individually even
    though their combined count per window would exceed the maximum.
    """
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    # Two MUPs each posting every 60 seconds
    request_history = [
        generate_class_instance(
            RequestEntry,
            seed=i * 2 + mup_idx,
            path=f"/mup/{mup_idx + 2}",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
        for mup_idx in range(2)
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/*"], "poll_interval_seconds": poll_interval, "request_type_str": "POST"},
    )

    assert_check_result(result, True)


def test_check_all_polls_at_correct_time_wildcard_fails_when_one_path_misses_polls():
    """With a wildcard endpoint, a single path missing polls causes an overall failure."""
    base_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    poll_interval = 60

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=base_time, step_status={}, finished_zip_path=None
    )

    # /mup/2 polls correctly; /mup/3 has a long gap (misses a window)
    good_requests = [
        generate_class_instance(
            RequestEntry,
            seed=i,
            path="/mup/2",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=i * poll_interval),
        )
        for i in range(10)
    ]
    bad_requests = [
        generate_class_instance(
            RequestEntry,
            seed=100,
            path="/mup/3",
            method=http.HTTPMethod.POST,
            timestamp=base_time,
        ),
        generate_class_instance(
            RequestEntry,
            seed=101,
            path="/mup/3",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=310),  # gap > 90s: missed poll
        ),
        generate_class_instance(
            RequestEntry,
            seed=102,
            path="/mup/3",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=550),  # another gap > 90s: second missed poll, same window
        ),
        generate_class_instance(
            RequestEntry,
            seed=103,
            path="/mup/3",
            method=http.HTTPMethod.POST,
            timestamp=base_time + timedelta(seconds=600),
        ),
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        good_requests + bad_requests,
        {"endpoints": ["/mup/*"], "poll_interval_seconds": poll_interval, "request_type_str": "POST"},
    )

    assert_check_result(result, False)
    assert result.description is not None
    assert "/mup/3" in result.description


@pytest.mark.parametrize(
    "params, description_contains",
    [
        ({"poll_interval_seconds": 60, "request_type_str": "GET"}, "No endpoints specified"),
        ({"endpoints": ["/mup/1"], "request_type_str": "GET"}, "No poll_interval_seconds specified"),
        ({"endpoints": ["/mup/1"], "poll_interval_seconds": 60}, "No request_type_str specified"),
    ],
)
def test_check_all_polls_at_correct_time_missing_params(params: dict, description_contains: str):
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        step_status={},
        finished_zip_path=None,
    )

    request_history = [
        generate_class_instance(
            RequestEntry,
            path="/mup/2",
            method=http.HTTPMethod.POST,
            timestamp=datetime.now(UTC),
        )
    ]

    result = check_all_polls_at_correct_time(active_test_procedure, request_history, params)

    assert_check_result(result, False)
    assert result.description is not None
    assert description_contains in result.description


def test_check_all_polls_at_correct_time_test_not_started_fails():
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=None, step_status={}, finished_zip_path=None
    )

    request_history = [
        generate_class_instance(
            RequestEntry,
            path="/mup/2",
            method=http.HTTPMethod.POST,
            timestamp=datetime.now(UTC),
        )
    ]

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/1"], "poll_interval_seconds": 60, "request_type_str": "GET"},
    )

    assert_check_result(result, False)
    assert result.description is not None
    assert "Test has not started" in result.description


@pytest.mark.parametrize(
    "request_history",
    [None, []],
)
def test_check_all_polls_at_correct_time_no_history(request_history: list | None):
    active_test_procedure = generate_class_instance(
        ActiveTestProcedure,
        started_at=datetime(2024, 1, 1, tzinfo=UTC),
        step_status={},
        finished_zip_path=None,
    )

    result = check_all_polls_at_correct_time(
        active_test_procedure,
        request_history,
        {"endpoints": ["/mup/*"], "poll_interval_seconds": 60, "request_type_str": "POST"},
    )

    assert_check_result(result, None)
    assert result.description is not None


@mock.patch("cactus_runner.app.check.run_check")
@pytest.mark.anyio
async def test_determine_check_results_distinguishes_same_type_checks(mock_run_check: mock.MagicMock):
    """repeat check type (eg response-contents) with different parameters - must not
    collide/overwrite each other in the returned dict (as previously happened when keying off check.type)"""

    checks = [
        Check(type="response-contents", parameters={"status": 1, "subject_tag": "DERC1"}),
        Check(type="response-contents", parameters={"status": 2, "subject_tag": "DERC1"}),
        Check(type="response-contents", parameters={"status": 3, "subject_tag": "DERC1"}),
        Check(type="all-steps-complete", parameters={}),
    ]
    results_by_check_type_and_status = {
        ("response-contents", 1): CheckResult(True, "received"),
        ("response-contents", 2): CheckResult(False, "not started"),
        ("response-contents", 3): CheckResult(False, "not completed"),
        ("all-steps-complete", None): CheckResult(True, "all steps done"),
    }

    async def run_check_se(check: Check, *args, **kwargs) -> CheckResult:
        return results_by_check_type_and_status[(check.type, check.parameters.get("status"))]

    mock_run_check.side_effect = run_check_se

    active_test_procedure = generate_class_instance(
        ActiveTestProcedure, started_at=datetime(2024, 1, 1, tzinfo=UTC), step_status={}, finished_zip_path=None
    )

    check_results = await determine_check_results(checks, active_test_procedure, mock.Mock(), None)

    assert len(check_results) == 4, "Every check should have its own entry - none should overwrite another"
    assert sum(1 for r in check_results.values() if not r.passed) == 2, "The two failing DERC1 checks must survive"


@pytest.mark.parametrize(
    "fmt, replace, expected",
    [
        ("", "", ""),
        ("", "aBc", ""),
        ("abc DEF", "abc", "abc DEF"),
        ("abc{var1}DEF{var2}", "*", "abc*DEF*"),
        ("/dcap", "*", "/dcap"),
        ("/edev/{site_id}/derp/{site_control_id}", "*", "/edev/*/derp/*"),
    ],
)
def test_resolve_format(fmt: str, replace: str, expected: str):
    actual = resolve_format(fmt, replace)
    assert isinstance(actual, str)
    assert actual == expected


def test_csip_aus_resource_to_match_uri():
    all_uris: set[str] = set()
    for r in CSIPAusResource:
        if r in {
            CSIPAusResource.Notification,  # No URI for Notificaions
            CSIPAusResource.UsagePointList,  # Unsupported in envoy
            CSIPAusResource.UsagePoint,  # Unsupported in envoy
            CSIPAusResource.MeterReadingList,  # Unsupported in envoy
            CSIPAusResource.MeterReading,  # Unsupported in envoy
            CSIPAusResource.ReadingType,  # Unsupported in envoy
            CSIPAusResource.CombinedTimeTariffIntervalList,  # Unsupported in v1.2
        }:
            with pytest.raises(ValueError):
                csip_aus_resource_to_match_uri(r)
            continue

        uri = csip_aus_resource_to_match_uri(r)
        assert isinstance(uri, str)

        assert uri == csip_aus_resource_to_match_uri(r.value), "Using a string equivalent gets the same result"  # ty:ignore[invalid-argument-type]

        # The resulting uri should look like an envoy URI with some wildcards
        assert re.match("[^a-z/\\*]", uri) is None, uri

        assert uri not in all_uris, r
        all_uris.add(uri)
    assert len(all_uris) > 10, "Sanity check to ensure we are looping"


@pytest.mark.parametrize(
    "request_paths, resources, minimum_count, maximum_count, expected",
    [
        ([], [], None, None, True),
        ([], [], 0, 0, True),
        ([], [], 1, 0, False),
        ([], [], 0, 99, True),
        (["/dcap", "/edev/1", "/edev", "/edev/2", "/edev/1/derp/2/derc"], ["EndDevice"], None, 2, True),
        (["/dcap", "/edev/1", "/edev", "/edev/2", "/edev/1/derp/2/derc"], [CSIPAusResource.EndDevice], None, 2, True),
        (["/dcap", "/edev/1", "/edev", "/edev/2", "/edev/1/derp/2/derc"], [CSIPAusResource.EndDevice], 2, None, True),
        (["/dcap", "/edev/1", "/edev", "/edev/2", "/edev/1/derp/2/derc"], [CSIPAusResource.EndDevice], 2, 2, True),
        (["/dcap", "/edev/1", "/edev", "/edev/2", "/edev/1/derp/2/derc"], [CSIPAusResource.EndDeviceList], 1, 1, True),
        (
            ["/dcap", "/edev/1", "/edev", "/edev/2", "/edev/1/derp/2/derc"],
            [CSIPAusResource.EndDeviceList, CSIPAusResource.EndDevice, CSIPAusResource.TariffProfile],
            3,
            3,
            True,
        ),
        (["/dcap", "/edev/1", "/edev/2", "/edev/1/derp"], [CSIPAusResource.EndDevice], 3, None, False),
        (["/dcap", "/edev/1", "/edev/2", "/edev/1/derp"], [CSIPAusResource.EndDevice], None, 1, False),
        (["/dcap", "/edev/1", "/edev/2", "/edev/1/derp"], [CSIPAusResource.EndDevice], 0, 3, True),
    ],
)
def test_check_resource_requests(
    request_paths: list[str],
    resources: list[CSIPAusResource],
    minimum_count: int | None,
    maximum_count: int | None,
    expected: bool,
):
    request_history = [generate_class_instance(RequestEntry, seed=idx, path=p) for idx, p in enumerate(request_paths)]
    params: dict = {"resources": resources}
    if minimum_count is not None:
        params["minimum_count"] = minimum_count
    if maximum_count is not None:
        params["maximum_count"] = maximum_count

    assert_check_result(check_resource_requests(params, request_history), expected)
