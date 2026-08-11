import datetime as dt
import logging
import secrets
from typing import Protocol, runtime_checkable

from cactus_test_definitions import errors

from cactus_runner.models import ActiveTestProcedure

logger = logging.getLogger(__name__)


RANDOM_URI_ATTEMPTS = 20
RANDOM_URI_LENGTH = 16


def resolve_named_variable_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def candidate_random_uri() -> str:
    return f"/{secrets.token_urlsafe(RANDOM_URI_LENGTH)}"


def resolve_random_uri(active_test_procedure: ActiveTestProcedure, randuri_key: str) -> str:
    random_uri_value = active_test_procedure.random_values.random_uri_by_key.get(randuri_key)
    if random_uri_value is not None:
        return random_uri_value

    # We need to generate a unique candidate value
    existing_random_uris = set(active_test_procedure.random_values.random_uri_by_key.values())
    for i in range(RANDOM_URI_ATTEMPTS):
        candidate_value = candidate_random_uri()
        if candidate_value not in existing_random_uris:
            active_test_procedure.random_values.random_uri_by_key[randuri_key] = candidate_value
            return candidate_value
        logger.warning(
            f"[{i}] Collision in randuri '{randuri_key}' with '{candidate_value}'."
            + f" There are {len(existing_random_uris)} existing entries."
        )

    raise errors.UnresolvableVariableError(
        f"Unable to resolve random uri {randuri_key} after {RANDOM_URI_ATTEMPTS} attempts"
    )


@runtime_checkable
class ExpressionResolver(Protocol):
    """Separate Protocol to a RunnerBackend.

    It contains all method definitions for evaluating expressions for a test definition that use a
    '$somevar' syntax in value fields.
    """

    # ---------------------------------------------------------------
    # DER Settings
    # ---------------------------------------------------------------

    async def resolve_named_variable_der_setting_max_w(self) -> float:
        """Resolve the $setMaxW in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_va(self) -> float:
        """Resolve the $setMaxVA in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_var(self) -> float:
        """Resolve the $setMaxVar in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_var_neg(self) -> float:
        """Resolve the $setMaxVarNeg in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_charge_rate_w(self) -> float:
        """Resolve the $setMaxChargeRateW in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_discharge_rate_w(self) -> float:
        """Resolve the $setMaxDischargeRateW in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_import_w(self) -> float:
        """Resolve the $setMaxImportW in a test definition.
        Directional import limit: prefer the asymmetric setMaxChargeRateW, falling back to the mandatory setMaxW

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_export_w(self) -> float:
        """Resolve the $setMaxExportW in a test definition.
        Directional export limit: prefer the asymmetric setMaxDischargeRateW, falling back to the mandatory setMaxW

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_min_pf_over_excited(self) -> float:
        """Resolve the $setMinPFOverExcited in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_min_pf_under_excited(self) -> float:
        """Resolve the $setMinPFUnderExcited in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_setting_max_wh(self) -> float:
        """Resolve the $setMaxWh in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    # ---------------------------------------------------------------
    # DER Capability
    # ---------------------------------------------------------------

    async def resolve_named_variable_der_rating_max_w(self) -> float:
        """Resolve the $rtgMaxW in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_max_va(self) -> float:
        """Resolve the $rtgMaxVA in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_max_var(self) -> float:
        """Resolve the $rtgMaxVar in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_max_var_neg(self) -> float:
        """Resolve the $rtgMaxVarNeg in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_max_charge_rate_w(self) -> float:
        """Resolve the $rtgChargeRateW in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_max_discharge_rate_w(self) -> float:
        """Resolve the $rtgMaxDischargeRateW in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_min_pf_over_excited(self) -> float:
        """Resolve the $rtgMinPFOverExcited in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_min_pf_under_excited(self) -> float:
        """Resolve the $rtgMinPFUnderExcited in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...

    async def resolve_named_variable_der_rating_max_wh(self) -> float:
        """Resolve the $rtgMaxWh in a test definition.

        Raises:
            raise UnresolvableVariableError when unable to resolve
        """
        ...
