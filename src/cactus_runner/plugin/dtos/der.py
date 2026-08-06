from dataclasses import dataclass

from envoy_schema.server.schema.sep2.der import (
    AlarmStatusType,
    ConnectStatusType,
    DERControlType,
    DOESupportedMode,
    OperationalModeStatusType,
)

__all__ = ["SiteDERSetting", "SiteDERStatus", "SiteDERRating"]


@dataclass(slots=True, frozen=True)
class SiteDERSetting:
    """Represents the current setting values associated with a SiteDER. The SiteDERRating represents
    the ratings/limits while the settings represent the currently enabled functionality"""

    grad_w: int
    modes_enabled: DERControlType | None = None
    doe_modes_enabled: DOESupportedMode | None = None
    max_w_value: int | None = None
    max_va_value: int | None = None
    max_var_value: int | None = None
    max_var_neg_value: int | None = None
    max_charge_rate_w_value: int | None = None
    max_discharge_rate_w_value: int | None = None
    max_wh_value: int | None = None
    min_pf_over_excited_displacement: int | None = None
    min_pf_under_excited_displacement: int | None = None
    # Placeholders for the storage extension
    min_wh_value: int | None = None
    vpp_modes_enabled: int | None = None


@dataclass(slots=True, frozen=True)
class SiteDERRating:
    """Represents the nameplate rating values associated with a SiteDER. These are not expected to change
    after initially being set (excepting erroneous assignments). Only a single SiteDERRating should be assigned
    to a SiteDER"""

    modes_supported: DERControlType | None = None
    doe_modes_supported: DOESupportedMode | None = None
    max_w_value: int | None = None
    max_va_value: int | None = None
    max_var_value: int | None = None
    max_var_neg_value: int | None = None
    max_charge_rate_w_value: int | None = None
    max_discharge_rate_w_value: int | None = None
    max_wh_value: int | None = None
    min_pf_over_excited_displacement: int | None = None
    min_pf_under_excited_displacement: int | None = None
    # Placeholders for the storage extension
    vpp_modes_supported: int | None = None


@dataclass(slots=True, frozen=True)
class SiteDERStatus:
    """Represents the current status values associated with a SiteDER. Typically used for communicating
    the current snapshot of DER status"""

    # These values correspond to a flattened version of sep2 DERStatus
    alarm_status: AlarmStatusType | None = None
    generator_connect_status: ConnectStatusType | None = None
    operational_mode_status: OperationalModeStatusType | None = None
