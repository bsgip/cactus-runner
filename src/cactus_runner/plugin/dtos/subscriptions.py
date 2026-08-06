from dataclasses import dataclass

from envoy.server.model import SubscriptionResource

__all__ = ["Subscription", "SubscriptionHref", "TransmitNotificationLog"]


@dataclass(slots=True, frozen=True)
class Subscription:
    subscription_id: str
    scoped_site_id: str | None  # Scoped site if any
    client_aggregator_id: str | None  # Client id or similar to facilitate appropriate subscription querying.
    # Envoy utilises aggregator only for subscription model.
    resource_type: SubscriptionResource  # What resource type is being subscribed to
    resource_id: (
        str | None
    )  # Represents the ID of a single resource being subscribed or if NULL all resources for the resource type
    notification_uri: str  # remote URI where notifications will be sent


@dataclass(slots=True, frozen=True)
class SubscriptionHref:
    resource_type: SubscriptionResource
    scoped_site_id: str | None
    resource_id: str | None


@dataclass(slots=True, frozen=True)
class TransmitNotificationLog:
    subscription_id: str  # Corresponding subscription
    http_status_code: int  # 4xx, 2xx etc
