import unittest.mock as mock
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from assertical.asserts.type import assert_dict_type
from assertical.fake.generator import generate_class_instance
from assertical.fake.sqlalchemy import assert_mock_session, create_mock_session
from cactus_test_definitions.errors import UnresolvableVariableError
from cactus_test_definitions.variable_expressions import (
    BaseExpression,
    Constant,
    Expression,
    NamedVariable,
    NamedVariableType,
    Negate,
    OperationType,
)
from envoy.server.model.site import Site, SiteDERSetting
from freezegun import freeze_time

from cactus_runner.app.database import begin_session
from cactus_runner.app.evaluator import (
    ResolvedParam,
    is_resolvable_variable,
    resolve_variable,
    resolve_variable_expressions_from_parameters,
)
from cactus_runner.models import ActiveTestProcedure, RandomValues


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
        (Negate(NamedVariable(NamedVariableType.NOW)), True),
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
    atp = generate_class_instance(ActiveTestProcedure, optional_is_none=True)

    mock_session = create_mock_session()
    with pytest.raises(UnresolvableVariableError):
        await resolve_variable(mock_session, atp, bad_type)

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
            datetime(2024, 9, 10, 1, 2, 3, tzinfo=UTC),
        ),  # Time frozen to this
        (NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W), DATABASE_SET_MAX_W),  # DB fixed with this
        (
            Expression(OperationType.ADD, NamedVariable(NamedVariableType.NOW), Constant(timedelta(hours=1))),
            datetime(2024, 9, 10, 2, 2, 3, tzinfo=UTC),
        ),
        (
            Expression(OperationType.SUBTRACT, NamedVariable(NamedVariableType.NOW), Constant(timedelta(hours=1))),
            datetime(2024, 9, 10, 0, 2, 3, tzinfo=UTC),
        ),
        (
            Expression(OperationType.MULTIPLY, NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W), Constant(0.5)),
            1010.0,
        ),
        (
            Expression(OperationType.DIVIDE, NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W), Constant(2)),
            1010.0,
        ),
        (Negate(NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W)), -DATABASE_SET_MAX_W),
        (
            Negate(Expression(OperationType.MULTIPLY, Constant(1.05), NamedVariable(NamedVariableType.DERSETTING_SET_MAX_W))),  # noqa: E501
            -DATABASE_SET_MAX_W * 1.05,
        ),
    ],
)
@freeze_time("2024-09-10T01:02:03Z")
async def test_resolve_variable_expected_use(
    pg_base_config, expression: Constant | NamedVariable | Expression, expected: Any
):
    """Tests the various ways expressions can be legitimately resolved"""
    atp = generate_class_instance(ActiveTestProcedure, optional_is_none=True)

    # Preload the database with a setting (in case the expression needs it)
    async with begin_session() as session:
        session.add(
            generate_class_instance(
                Site,
                site_id=None,
                aggregator_id=1,
                site_der_setting=generate_class_instance(
                    SiteDERSetting,
                    site_der_setting_id=None,
                    site_id=None,
                    max_w_value=DATABASE_SET_MAX_W,
                    max_w_multiplier=0,
                ),
            )
        )
        await session.commit()

        async with begin_session() as session:
            result = await resolve_variable(session, atp, expression)
            assert isinstance(result, type(expected))
            assert result == expected


@pytest.mark.asyncio
async def test_resolve_variable_random_uris():
    """Tests the random URIs cache and properly segment from eachother"""
    atp = generate_class_instance(
        ActiveTestProcedure, optional_is_none=True, random_values=RandomValues(random_uri_by_key={})
    )
    mock_session = create_mock_session()

    # Act
    rand1_1 = await resolve_variable(mock_session, atp, NamedVariable(NamedVariableType.RANDURI_1))
    rand1_2 = await resolve_variable(mock_session, atp, NamedVariable(NamedVariableType.RANDURI_1))
    rand2_1 = await resolve_variable(mock_session, atp, NamedVariable(NamedVariableType.RANDURI_2))
    rand3_1 = await resolve_variable(mock_session, atp, NamedVariable(NamedVariableType.RANDURI_3))
    rand2_2 = await resolve_variable(mock_session, atp, NamedVariable(NamedVariableType.RANDURI_2))
    rand1_3 = await resolve_variable(mock_session, atp, NamedVariable(NamedVariableType.RANDURI_1))

    # Assert
    assert rand1_1 == rand1_2 == rand1_3
    assert rand2_1 == rand2_2
    assert len(set([rand1_1, rand2_1, rand3_1])) == 3


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
    atp = generate_class_instance(ActiveTestProcedure, optional_is_none=True)
    MOCK_RESOLVED_VALUE = mock.Mock()

    mock_session = create_mock_session()
    mock_resolve_variable.return_value = MOCK_RESOLVED_VALUE

    actual_dict = await resolve_variable_expressions_from_parameters(mock_session, atp, input_dict)
    assert_dict_type(str, ResolvedParam, actual_dict, count=len(input_dict))

    assert actual_dict is not input_dict, "Should be different dict instances"
    assert len(input_dict) == len(actual_dict)
    for k, input_val in input_dict.items():
        assert k in actual_dict
        if k in variable_keys:
            assert actual_dict[k].value is MOCK_RESOLVED_VALUE, "Resolved variables should be... resolved"
            assert actual_dict[k].original_expression is not None and isinstance(
                actual_dict[k].original_expression, BaseExpression
            )
        else:
            if isinstance(input_val, list):
                assert all([i is a for i, a in zip(input_val, actual_dict[k].value, strict=True)])
            else:
                assert actual_dict[k].value is input_val, "All other variables/params should be shallow copied across"
            assert actual_dict[k].original_expression is None

    assert_mock_session(mock_session)
    assert mock_resolve_variable.call_count == len(variable_keys)
    assert all([a.args[0] == mock_session for a in mock_resolve_variable.call_args_list])


@mock.patch("cactus_runner.app.evaluator.resolve_variable")
@pytest.mark.parametrize(
    "input_list, variable_indexes",
    [
        ([], []),
        (["abc", "def", "now"], []),
        ([NamedVariable(NamedVariableType.NOW), "abc", "def", NamedVariable(NamedVariableType.NOW)], [0, 3]),
        (["abc", "def", NamedVariable(NamedVariableType.NOW)], [2]),
        (["abc", NamedVariable(NamedVariableType.RANDURI_1), NamedVariable(NamedVariableType.NOW)], [1, 2]),
        ([NamedVariable(NamedVariableType.RANDURI_1)], [0]),
    ],
)
@pytest.mark.asyncio
async def test_resolve_variable_expressions_from_parameters_lists(
    mock_resolve_variable: mock.Mock, input_list: list, variable_indexes: list[int]
):
    """There can be lists of variables - each entry in the list needs to be resolved"""
    atp = generate_class_instance(ActiveTestProcedure, optional_is_none=True)
    MOCK_RESOLVED_VALUE = mock.Mock()

    mock_session = create_mock_session()
    mock_resolve_variable.return_value = MOCK_RESOLVED_VALUE

    actual_dict: dict[str, ResolvedParam] = await resolve_variable_expressions_from_parameters(
        mock_session,
        atp,
        {
            "my_key": input_list,
        },
    )

    assert_dict_type(str, ResolvedParam, actual_dict, count=1)
    actual_list: list = actual_dict["my_key"].value
    assert isinstance(actual_list, list)
    assert len(actual_list) == len(input_list)
    assert actual_list is not input_list, "The original list should NOT be mutated directly"

    for idx, item in enumerate(actual_list):
        if idx in variable_indexes:
            assert item is MOCK_RESOLVED_VALUE, "Resolved variables are resolved"
        else:
            assert item is input_list[idx], "Other list items should NOT be changed"

    assert_mock_session(mock_session)
    assert mock_resolve_variable.call_count == len(variable_indexes)
    assert all([a.args[0] == mock_session for a in mock_resolve_variable.call_args_list])
