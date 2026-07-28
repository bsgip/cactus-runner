from dataclasses import dataclass

__all__ = ["RuntimeConfig"]


@dataclass(slots=True)
class RuntimeConfig:
    dcap_pollrate_seconds: int | None = None
    edevl_pollrate_seconds: int | None = None
    derl_pollrate_seconds: int | None = None
    derpl_pollrate_seconds: int | None = None
    fsal_pollrate_seconds: int | None = None
    mup_postrate_seconds: int | None = None
    disable_edev_registration: bool | None = None
    site_control_pow10_encoding: int | None = None
