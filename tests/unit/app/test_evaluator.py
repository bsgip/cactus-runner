import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions.variable_expressions import (
    Constant,
    Expression,
    NamedVariable,
    NamedVariableType,
    OperationType,
)
from cactus_test_definitions.errors import UnresolvableVariableError
from envoy.server.model.site import Site, SiteDER, SiteDERSetting
from freezegun import freeze_time

from cactus_runner.app.database import begin_session
from cactus_runner.app.evaluator import (
    is_resolvable_variable,
    resolve_variable,
    resolve_variable_expressions_from_parameters,
)


class MyTestingClass:
    field1: str
    field2: int


@pytest.mark.parametrize(
    "input, expected",
    [
        (None, False),
        ("", False),
        ("string value", False),
        (123, False),
        (1.23, False),
        (Decimal("1.2"), False),
        (datetime(2022, 11, 3), False),
        (timedelta(2), False),
        (MyTestingClass(), False),
        (NamedVariable(NamedVariableType.NOW), True),
        (NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W), True),
        (Constant(1.23), True),
        (Constant(timedelta(5)), True),
        (Expression(OperationType.ADD, Constant(1.23), NamedVariable(NamedVariableType.NOW)), True),
    ],
)
def test_is_resolvable_variable(input: Any, expected: bool):
    result = is_resolvable_variable(input)
    assert isinstance(result, bool)
    assert result == expected


@pytest.mark.parametrize("bad_type", [(None), ("string"), (datetime(2022, 3, 4)), (MyTestingClass())])
@pytest.mark.asyncio
async def test_resolve_variable_not_variable_expression(bad_type: Any):
    """Tests failure in a predictable fashion when the input type isn't recognized as an Expression"""

    mock_session = create_mock_session()
    with pytest.raises(UnresolvableVariableError):
        await resolve_variable(mock_session, bad_type)

    assert_mock_session(mock_session)


DATABASE_SET_MAX_W = 2020.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression, expected",
    [
        (Constant(1.23), 1.23),
        (Constant(123), 123),
        (Constant(timedelta(hours=1.23)), timedelta(hours=1.23)),
        (
            NamedVariable(NamedVariableType.NOW),
            datetime(2024, 9, 10, 1, 2, 3, tzinfo=timezone.utc),
        ),  # Time frozen to this
        (NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W), DATABASE_SET_MAX_W),  # DB fixed with this
        (
            Expression(OperationType.ADD, NamedVariable(NamedVariableType.NOW), Constant(timedelta(hours=1))),
            datetime(2024, 9, 10, 2, 2, 3, tzinfo=timezone.utc),
        ),
        (
            Expression(OperationType.SUBTRACT, NamedVariable(NamedVariableType.NOW), Constant(timedelta(hours=1))),
            datetime(2024, 9, 10, 0, 2, 3, tzinfo=timezone.utc),
        ),
        (
            Expression(OperationType.MULTIPLY, NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W), Constant(0.5)),
            1010.0,
        ),
        (
            Expression(OperationType.DIVIDE, NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W), Constant(2)),
            1010.0,
        ),
    ],
)
@freeze_time("2024-09-10T01:02:03Z")
async def test_resolve_variable_expected_use(
    pg_base_config, expression: Constant | NamedVariable | Expression, expected: Any
):
    """Tests the various ways expressions can be legitimately resolved"""

    # Preload the database with a setting (in case the expression needs it)
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site,
                site_id=None,
                aggregator_id=1,
                site_ders=[
                    generate_class_instance(
                        SiteDER,
                        site_id=None,
                        site_der_setting=generate_class_instance(
                            SiteDERSetting,
                            site_der_setting_id=None,
                            site_der_id=None,
                            max_w_value=DATABASE_SET_MAX_W,
                            max_w_multiplier=0,
                        ),
                    )
                ],
            )
        )
        await session.commit()

        async with begin_session() as session:
            result = await resolve_variable(session, expression)
            assert isinstance(result, type(expected))
            assert result == expected


@mock.patch("cactus_runner.app.evaluator.resolve_variable")
@pytest.mark.parametrize(
    "input_dict, variable_keys",
    [
        ({}, []),
        ({"k1": 123, "k2": datetime(2022, 11, 2)}, []),
        ({"k1": 123, "k2": datetime(2022, 11, 2), "k3": [1, 2]}, []),
        ({"k1": 123, "k2": datetime(2022, 11, 2), "k3": [1, 2], "k4": NamedVariable(NamedVariableType.NOW)}, ["k4"]),
        ({"k1": NamedVariable(NamedVariableType.NOW)}, ["k1"]),
        (
            {"k1": NamedVariable(NamedVariableType.NOW), "k2": NamedVariable(NamedVariableType.NOW), "k3": 123},
            ["k1", "k2"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_resolve_variable_expressions_from_parameters(
    mock_resolve_variable: mock.Mock, input_dict: dict[str, Any], variable_keys: list[str]
):
    """Sanity checks on the logic behind resolve_variable_expressions_from_parameters under various inputs"""
    MOCK_RESOLVED_VALUE = mock.Mock()

    mock_session = create_mock_session()
    mock_resolve_variable.return_value = MOCK_RESOLVED_VALUE

    actual_dict = await resolve_variable_expressions_from_parameters(mock_session, input_dict)

    assert isinstance(actual_dict, dict)
    assert actual_dict is not input_dict, "Should be different dict instances"
    assert len(input_dict) == len(actual_dict)
    for k, input_val in input_dict.items():
        assert k in actual_dict
        if k in variable_keys:
            assert actual_dict[k] is MOCK_RESOLVED_VALUE, "Resolved variables should be... resolved"
        else:
            assert actual_dict[k] is input_val, "All other variables/params should be shallow copied across"

    assert_mock_session(mock_session)
    assert mock_resolve_variable.call_count == len(variable_keys)
    assert all([a.args[0] == mock_session for a in mock_resolve_variable.call_args_list])
