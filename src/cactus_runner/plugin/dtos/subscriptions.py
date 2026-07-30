from dataclasses import dataclass

__all__ = ["Subscription", "TransmitNotificationLog"]


@dataclass(slots=True, frozen=True)
class Subscription:
    subscription_id: int
    aggregator_id: int
    scoped_site_id: int | None
    resource_type: str
    resource_id: int | None


@dataclass(slots=True, frozen=True)
class TransmitNotificationLog:
    subscription_id: int
    http_status_code: int
