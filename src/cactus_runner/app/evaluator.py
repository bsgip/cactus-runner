from typing import Any

from cactus_runner.app import resolvers

from cactus_test_definitions.variable_expressions import (
    Constant,
    Expression,
    NamedVariable,
    NamedVariableType,
    OperationType,
)
from cactus_test_definitions.errors import UnresolvableVariableError
from sqlalchemy.ext.asyncio import AsyncSession


def is_resolvable_variable(v: Any) -> bool:
    """Returns True if the supplied value is a variable definition that requires resolving"""
    return isinstance(v, NamedVariable) or isinstance(v, Expression) or isinstance(v, Constant)


async def resolve_variable(session: AsyncSession, v: NamedVariable | Expression | Constant) -> Any:
    """Attempts to resolve the specified variable (potentially from the database)

    raises UnresolvableVariableError if any errors are encountered

    The resolved value will be some form of primitive value (eg int, float, datetime, timedelta)"""

    if isinstance(v, Constant):
        return v.value
    elif isinstance(v, NamedVariable):
        match v.variable:
            case NamedVariableType.NOW:
                # Return the tz aware datetime "now"
                return resolvers.resolve_named_variable_now()
            case NamedVariableType.DERSETTING_SET_MAX_W:
                return await resolvers.resolve_named_variable_der_setting_max_w(session)
            case NamedVariableType.DERSETTING_SET_MAX_VA:
                return await resolvers.resolve_named_variable_der_setting_max_va(session)
            case NamedVariableType.DERSETTING_SET_MAX_VAR:
                return await resolvers.resolve_named_variable_der_setting_max_var(session)
            case NamedVariableType.DERSETTING_SET_MAX_CHARGE_RATE_W:
                return await resolvers.resolve_named_variable_der_setting_max_charge_rate_w(session)
            case NamedVariableType.DERSETTING_SET_MAX_DISCHARGE_RATE_W:
                return await resolvers.resolve_named_variable_der_setting_max_discharge_rate_w(session)
            case NamedVariableType.DERSETTING_SET_MAX_WH:
                return await resolvers.resolve_named_variable_der_setting_max_wh(session)
            case NamedVariableType.DERCAPABILITY_RTG_MAX_W:
                return await resolvers.resolve_named_variable_der_rating_max_w(session)
            case NamedVariableType.DERCAPABILITY_RTG_MAX_VA:
                return await resolvers.resolve_named_variable_der_rating_max_va(session)
            case NamedVariableType.DERCAPABILITY_RTG_MAX_VAR:
                return await resolvers.resolve_named_variable_der_rating_max_var(session)
            case NamedVariableType.DERCAPABILITY_RTG_MAX_CHARGE_RATE_W:
                return await resolvers.resolve_named_variable_der_rating_max_charge_rate_w(session)
            case NamedVariableType.DERCAPABILITY_RTG_MAX_DISCHARGE_RATE_W:
                return await resolvers.resolve_named_variable_der_rating_max_discharge_rate_w(session)
            case NamedVariableType.DERCAPABILITY_RTG_MAX_WH:
                return await resolvers.resolve_named_variable_der_rating_max_wh(session)
        raise UnresolvableVariableError(f"Unable to resolve NamedVariable of type {v.variable} ({int(v.variable)})")
    elif isinstance(v, Expression):
        lhs = await resolve_variable(session, v.lhs_operand)
        rhs = await resolve_variable(session, v.rhs_operand)

        try:
            match v.operation:
                case OperationType.ADD:
                    return lhs + rhs
                case OperationType.SUBTRACT:
                    return lhs - rhs
                case OperationType.MULTIPLY:
                    return lhs * rhs
                case OperationType.DIVIDE:
                    return lhs / rhs
                case OperationType.EQ:
                    return lhs == rhs
                case OperationType.NE:
                    return lhs != rhs
                case OperationType.LT:
                    return lhs < rhs
                case OperationType.LTE:
                    return lhs <= rhs
                case OperationType.GT:
                    return lhs > rhs
                case OperationType.GTE:
                    return lhs >= rhs
            raise ValueError(f"Unsupported operation {v.operation} ({int(v.operation)})")

        except Exception as exc:
            raise UnresolvableVariableError(f"Unable to apply {v.operation} to operands: {exc}")
    else:
        raise UnresolvableVariableError(f"Unsupported variable type {type(v)}")


async def resolve_variable_expressions_from_parameters(
    session: AsyncSession, parameters: dict[str, Any]
) -> dict[str, Any]:
    """Iterates parameters, finding any resolvable variables and then calling resolve_variable on it.

    parameters will NOT be mutated, a cloned set of "resolved" parameters (shallow copy) will be returned.

    raises UnresolvableVariableError on failure"""

    output_parameters: dict[str, Any] = {}
    for k, v in parameters.items():
        if is_resolvable_variable(v):
            output_parameters[k] = await resolve_variable(session, v)
        else:
            output_parameters[k] = v

    return output_parameters
