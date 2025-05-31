import unittest.mock as mock
from typing import Any

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from assertical.fixtures.postgres import generate_async_session
from cactus_test_definitions import Event, Step, TestProcedure
from envoy.server.model.site import (
    Site,
    SiteDER,
    SiteDERRating,
    SiteDERSetting,
    SiteDERStatus,
)

from cactus_runner.app.check import (
    CheckResult,
    check_all_steps_complete,
    check_connectionpoint_contents,
    check_der_capability_contents,
    check_der_settings_contents,
    check_der_status_contents,
)
from cactus_runner.models import ActiveTestProcedure, Listener


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
