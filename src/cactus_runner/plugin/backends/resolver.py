import datetime as dt
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def resolve_named_variable_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


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
