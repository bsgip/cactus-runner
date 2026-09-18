from dataclasses import dataclass
from datetime import datetime

__all__ = ["RuntimeConfig", "RuntimeConfigWrite"]


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    """Read DTO — mirrors the envoy RuntimeServerConfig ORM/response model."""

    dcap_pollrate_seconds: int | None
    edevl_pollrate_seconds: int | None
    derl_pollrate_seconds: int | None
    derpl_pollrate_seconds: int | None
    fsal_pollrate_seconds: int | None
    mup_postrate_seconds: int | None
    mupl_pollrate_seconds: int | None
    disable_edev_registration: bool | None
    site_control_pow10_encoding: int | None

    deleted_time: datetime | None
    archive_time: datetime | None
    created_time: datetime
    changed_time: datetime


@dataclass(slots=True, frozen=True)
class RuntimeConfigWrite:
    """Write DTO — maps to envoy_schema RuntimeServerConfigRequest."""

    dcap_pollrate_seconds: int | None = None
    edevl_pollrate_seconds: int | None = None
    derl_pollrate_seconds: int | None = None
    derpl_pollrate_seconds: int | None = None
    fsal_pollrate_seconds: int | None = None
    mup_postrate_seconds: int | None = None
    mupl_pollrate_seconds: int | None = None
    disable_edev_registration: bool | None = None
    site_control_pow10_encoding: int | None = None
