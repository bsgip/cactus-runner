from dataclasses import dataclass

__all__ = ["SiteDERSettings", "SiteDERCapability", "SiteDERStatus"]


@dataclass(slots=True, frozen=True)
class SiteDERSettings:
    modes_enabled: int | None
    doe_modes_enabled: int | None
    max_w: int | None
    max_va: int | None
    max_var: int | None
    max_var_neg: int | None
    max_charge_rate_w: int | None
    max_discharge_rate_w: int | None
    grad_w: int | None


@dataclass(slots=True, frozen=True)
class SiteDERCapability:
    der_type: int | None
    modes_supported: int | None
    doe_modes_supported: int | None
    max_w: int | None
    max_va: int | None
    max_var: int | None
    max_var_neg: int | None
    max_charge_rate_w: int | None
    max_discharge_rate_w: int | None


@dataclass(slots=True, frozen=True)
class SiteDERStatus:
    alarm_status: int | None
    generator_connect_status: int | None
    storage_connect_status: int | None
    inverter_status: int | None
    operational_mode_status: int | None
    storage_mode_status: int | None
    local_control_mode_status: int | None
    manufacturer_status: str | None
    state_of_charge_status: int | None
