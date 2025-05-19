from datetime import datetime, timezone
from typing import Any

from cactus_test_definitions.variable_expressions import (
    Constant,
    Expression,
    NamedVariable,
    NamedVariableType,
    OperationType,
)
from cactus_test_definitions.errors import UnresolvableVariableError
from envoy.server.mapper.common import pow10_to_decimal_value
from envoy.server.model import SiteDERSetting
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def resolve_named_variable_now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def resolve_named_variable_der_setting_max_w(session: AsyncSession) -> float:
    # Fetch the most recently edited SiteDERSetting

    try:
        response = await session.execute(select(SiteDERSetting).order_by(SiteDERSetting.changed_time.desc()).limit(1))
        site_der_setting = response.scalar_one_or_none()
    except Exception as exc:
        raise UnresolvableVariableError(f"Unable to fetch DERSetting from database: {exc}")

    if site_der_setting is None:
        raise UnresolvableVariableError("Unable to find a suitable DERSetting to resolve setMaxW")

    set_max_w = pow10_to_decimal_value(site_der_setting.max_w_value, site_der_setting.max_w_multiplier)
    if set_max_w is None:
        raise UnresolvableVariableError("Unable to extract setMaxW from DERSetting")

    return float(set_max_w)


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
                return resolve_named_variable_now()
            case NamedVariableType.DERSETTING_SET_MAX_W:
                return await resolve_named_variable_der_setting_max_w(session)
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
