from collections.abc import Sequence
from enum import IntEnum
from typing import Protocol, runtime_checkable

from envoy_schema.server.schema.sep2.types import DataQualifierType, KindType, RoleFlagsType, UomType

from cactus_runner.plugin import dtos


class ReadingLocation(IntEnum):
    """This is a bitmask of a MUP roleflags that correspond with a "site" or "device" reading location. Combinations
    of bit masks are read from CSIP-Aus - ANNEX A - Reporting DER Data"""

    SITE_READING = int(RoleFlagsType.IS_MIRROR | RoleFlagsType.IS_PREMISES_AGGREGATION_POINT)
    DEVICE_READING = int(RoleFlagsType.IS_MIRROR | RoleFlagsType.IS_DER | RoleFlagsType.IS_SUBMETER)


@runtime_checkable
class RunnerBackend(Protocol):
    """
    Backend abstraction consumed by checks, actions, status generation,
    event processing and test execution logic.

    Implementations translate between their native storage or transport
    representation and the runner DTOs.

    Implementations may be backed by:

    - Envoy SQLAlchemy + Admin REST API
    - gRPC services
    - External APIs
    - Test doubles / mocks
    """

    # ------------------------------------------------------------------
    # Sites
    # ------------------------------------------------------------------

    async def get_active_site(
        self,
        include_der_settings: bool = False,
    ) -> dtos.Site | None:
        """
        Return the active site.

        The active site corresponds to the most recently modified
        EndDevice known to the backend.
        """
        ...

    async def get_all_sites(
        self,
    ) -> Sequence[dtos.Site]:
        """
        Return all registered sites.
        """
        ...

    async def register_site(
        self,
        site: dtos.Site,
    ) -> None:
        """
        Register a new site.
        """
        ...

    async def update_site_post_rate(
        self,
        site_id: str,
        post_rate_seconds: int,
    ) -> None:
        """
        Update a site's post rate.
        """
        ...

    # ------------------------------------------------------------------
    # DER
    # ------------------------------------------------------------------

    async def get_der_settings(
        self,
        site_id: str,
    ) -> dtos.SiteDERSettings | None:
        """
        Return DERSettings associated with a site.
        """
        ...

    async def get_der_capability(
        self,
        site_id: str,
    ) -> dtos.SiteDERCapability | None:
        """
        Return DERCapability associated with a site.
        """
        ...

    async def get_der_status(
        self,
        site_id: str,
    ) -> dtos.SiteDERStatus | None:
        """
        Return DERStatus associated with a site.
        """
        ...

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------

    async def get_site_reading_types(
        self,
        *,
        uom: UomType,
        location: ReadingLocation,
        kind: KindType,
        qualifier: DataQualifierType,
    ) -> tuple[
        Sequence[dtos.SiteReadingType],
        Sequence[dtos.SiteReadingType],
    ]:
        """
        Return reading types matching the supplied criteria.

        Returns:

        (
            matching_reading_types,
            incorrect_location_reading_types,
        )
        """
        ...

    async def get_site_readings(
        self,
        site_reading_type_id: str,
    ) -> Sequence[dtos.SiteReading]:
        """
        Return readings belonging to a SiteReadingType.
        """
        ...

    async def get_reading_counts_by_type(
        self,
    ) -> dict[int, int]:
        """
        Return reading counts keyed by SiteReadingType ID.
        """
        ...

    # ------------------------------------------------------------------
    # DER Programs
    # ------------------------------------------------------------------

    async def get_all_site_control_groups(
        self,
    ) -> Sequence[dtos.SiteControlGroup]:
        """
        Return all DERPrograms.
        """
        ...

    async def create_site_control_group(
        self,
        group: dtos.SiteControlGroup,
    ) -> int:
        """
        Create a DERProgram.

        Returns the generated site_control_group_id.
        """
        ...

    async def update_site_control_group(
        self,
        group_id: str,
        group: dtos.SiteControlGroup,
    ) -> None:
        """
        Update a DERProgram.
        """
        ...

    async def remove_function_set_assignment(
        self,
        fsa_id: str,
    ) -> None:
        """
        Remove a Function Set Assignment from matching DERPrograms.
        """
        ...

    # ------------------------------------------------------------------
    # DER Controls
    # ------------------------------------------------------------------

    async def create_site_control(
        self,
        control: dtos.SiteControl,
        *,
        site_control_group_id: str,
    ) -> int:
        """
        Create a DERControl.

        Returns the generated site_control_id.
        """
        ...

    async def get_site_controls(
        self,
    ) -> Sequence[dtos.SiteControl]:
        """
        Return active and archived DERControls.
        """
        ...

    async def count_site_controls(
        self,
        *,
        site_id: str | None,
    ) -> int:
        """
        Return total DERControl count including cancelled controls.
        """
        ...

    async def cancel_active_site_controls(
        self,
    ) -> None:
        """
        Cancel all active DERControls.
        """
        ...

    # ------------------------------------------------------------------
    # Default DER Controls
    # ------------------------------------------------------------------

    async def set_site_control_default(
        self,
        *,
        site_control_group_id: str,
        default: dtos.SiteControlGroupDefault,
    ) -> None:
        """
        Set or update a DefaultDERControl.
        """
        ...

    async def get_site_control_defaults(
        self,
    ) -> Sequence[dtos.SiteControlGroupDefault]:
        """
        Return current and historical DefaultDERControl values.
        """
        ...

    # ------------------------------------------------------------------
    # Responses
    # ------------------------------------------------------------------

    async def get_responses(
        self,
    ) -> Sequence[dtos.SiteControlResponse]:
        """
        Return all DERControl responses.
        """
        ...

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def find_subscription(
        self,
        *,
        aggregator_id: str,
        scoped_site_id: str | None,
        resource_type: str,
        resource_id: str | None,
    ) -> dtos.Subscription | None:
        """
        Locate a matching subscription.
        """
        ...

    async def get_notification_logs(
        self,
    ) -> Sequence[dtos.TransmitNotificationLog]:
        """
        Return notification transmission history.
        """
        ...

    # ------------------------------------------------------------------
    # Runtime Configuration
    # ------------------------------------------------------------------

    async def get_runtime_config(
        self,
    ) -> dtos.RuntimeConfig:
        """
        Return current runtime configuration.
        """
        ...

    async def update_runtime_config(
        self,
        config: dtos.RuntimeConfig,
    ) -> None:
        """
        Update runtime configuration.
        """
        ...

    # ------------------------------------------------------------------
    # Administrative cleanup
    # ------------------------------------------------------------------

    async def delete_all_site_control_groups(
        self,
    ) -> None:
        """
        Delete all DERPrograms and downstream resources.
        """
        ...

    async def delete_site(
        self,
        site_id: str,
    ) -> None:
        """
        Delete a site.
        """
        ...
