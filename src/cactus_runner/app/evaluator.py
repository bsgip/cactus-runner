from cactus_runner.plugin.backends.uri import resolve_random_uri
import dataclasses
from typing import Any

from cactus_test_definitions.errors import UnresolvableVariableError
from cactus_test_definitions.variable_expressions import (
    BaseExpression,
    Constant,
    Expression,
    NamedVariable,
    NamedVariableType,
    OperationType,
)

from cactus_runner.models import ActiveTestProcedure
from cactus_runner.plugin.backends.resolver import ExpressionResolver, resolve_named_variable_now



@dataclasses.dataclass
class ResolvedParam:
    """A dataclass for holding all metadata related to a resolved parameter"""

    value: Any
    original_expression: BaseExpression | None = None


def is_resolvable_variable(v: Any) -> bool:  # noqa: ANN401
    """Returns True if the supplied value is a variable definition that requires resolving"""
    return isinstance(v, NamedVariable) or isinstance(v, Expression) or isinstance(v, Constant)


async def resolve_variable(  # noqa: C901
    resolver: ExpressionResolver, active_test_procedure: ActiveTestProcedure, v: NamedVariable | Expression | Constant
) -> Any:  # noqa: ANN401
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
                return await resolver.resolve_named_variable_der_setting_max_w()
            case NamedVariableType.DERSETTING_SET_MAX_VA:
                return await resolver.resolve_named_variable_der_setting_max_va()
            case NamedVariableType.DERSETTING_SET_MAX_VAR:
                return await resolver.resolve_named_variable_der_setting_max_var()
            case NamedVariableType.DERSETTING_SET_MAX_VAR_NEG:
                return await resolver.resolve_named_variable_der_setting_max_var_neg()
            case NamedVariableType.DERSETTING_SET_MAX_CHARGE_RATE_W:
                return await resolver.resolve_named_variable_der_setting_max_charge_rate_w()
            case NamedVariableType.DERSETTING_SET_MAX_DISCHARGE_RATE_W:
                return await resolver.resolve_named_variable_der_setting_max_discharge_rate_w()
            case NamedVariableType.DERSETTING_SET_MIN_PF_OVER_EXCITED:
                return await resolver.resolve_named_variable_der_setting_min_pf_over_excited()
            case NamedVariableType.DERSETTING_SET_MIN_PF_UNDER_EXCITED:
                return await resolver.resolve_named_variable_der_setting_min_pf_under_excited()
            case NamedVariableType.DERSETTING_SET_MAX_WH:
                return await resolver.resolve_named_variable_der_setting_max_wh()
            case NamedVariableType.DERSETTING_MAX_IMPORT_W:
                return await resolver.resolve_named_variable_der_setting_max_import_w()
            case NamedVariableType.DERSETTING_MAX_EXPORT_W:
                return await resolver.resolve_named_variable_der_setting_max_export_w()
            case NamedVariableType.DERCAPABILITY_RTG_MAX_W:
                return await resolver.resolve_named_variable_der_rating_max_w()
            case NamedVariableType.DERCAPABILITY_RTG_MAX_VA:
                return await resolver.resolve_named_variable_der_rating_max_va()
            case NamedVariableType.DERCAPABILITY_RTG_MAX_VAR:
                return await resolver.resolve_named_variable_der_rating_max_var()
            case NamedVariableType.DERCAPABILITY_RTG_MAX_VAR_NEG:
                return await resolver.resolve_named_variable_der_rating_max_var_neg()
            case NamedVariableType.DERCAPABILITY_RTG_MAX_CHARGE_RATE_W:
                return await resolver.resolve_named_variable_der_rating_max_charge_rate_w()
            case NamedVariableType.DERCAPABILITY_RTG_MAX_DISCHARGE_RATE_W:
                return await resolver.resolve_named_variable_der_rating_max_discharge_rate_w()
            case NamedVariableType.DERCAPABILITY_RTG_MIN_PF_OVER_EXCITED:
                return await resolver.resolve_named_variable_der_rating_min_pf_over_excited()
            case NamedVariableType.DERCAPABILITY_RTG_MIN_PF_UNDER_EXCITED:
                return await resolver.resolve_named_variable_der_rating_min_pf_under_excited()
            case NamedVariableType.DERCAPABILITY_RTG_MAX_WH:
                return await resolver.resolve_named_variable_der_rating_max_wh()
            case NamedVariableType.RANDURI_1:
                return resolve_random_uri(active_test_procedure, "1")
            case NamedVariableType.RANDURI_2:
                return resolve_random_uri(active_test_procedure, "2")
            case NamedVariableType.RANDURI_3:
                return resolve_random_uri(active_test_procedure, "3")
        raise UnresolvableVariableError(f"Unable to resolve NamedVariable of type {v.variable} ({int(v.variable)})")
    elif isinstance(v, Expression):
        lhs = await resolve_variable(resolver, active_test_procedure, v.lhs_operand)
        rhs = await resolve_variable(resolver, active_test_procedure, v.rhs_operand)

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

        except Exception as err:
            raise UnresolvableVariableError(f"Unable to apply {v.operation} to operands: {err}") from err
    else:
        raise UnresolvableVariableError(f"Unsupported variable type {type(v)}")


async def _do_resolve(
    resolver: ExpressionResolver,
    active_test_procedure: ActiveTestProcedure,
    v: Any,  # noqa: ANN401
) -> tuple[ResolvedParam, BaseExpression | None]:
    if is_resolvable_variable(v):
        return (await resolve_variable(resolver, active_test_procedure, v), v)
    else:
        return (v, None)


async def resolve_variable_expressions_from_parameters(
    resolver: ExpressionResolver, active_test_procedure: ActiveTestProcedure, parameters: dict[str, Any]
) -> dict[str, ResolvedParam]:
    """Iterates parameters, finding any resolvable variables and then calling resolve_variable on it.

    parameters will NOT be mutated, a cloned set of "resolved" parameters (shallow copy) will be returned.

    raises UnresolvableVariableError on failure"""

    output_parameters: dict[str, ResolvedParam] = {}
    for k, v in parameters.items():
        if isinstance(v, list):
            resolved_list = []
            for list_entry in v:
                list_entry_value, _ = await _do_resolve(resolver, active_test_procedure, list_entry)
                resolved_list.append(list_entry_value)
            output_parameters[k] = ResolvedParam(value=resolved_list, original_expression=None)
        else:
            value, original_expr = await _do_resolve(resolver, active_test_procedure, v)
            output_parameters[k] = ResolvedParam(value=value, original_expression=original_expr)

    return output_parameters
