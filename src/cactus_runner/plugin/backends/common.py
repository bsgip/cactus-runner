from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from cactus_runner.models import ActiveTestProcedure
from cactus_runner.plugin import dtos
from cactus_runner.plugin.backends.models import RunnerBackendTestContext


def generate_plugin_context(test_procedure: ActiveTestProcedure) -> RunnerBackendTestContext:
    """Creates a suitable immutable context to be commmunicated with harness backend plugins."""
    return RunnerBackendTestContext(
        name=test_procedure.name,
        definition=test_procedure.definition,
        csip_aus_version=test_procedure.csip_aus_version,
        initialised_at=test_procedure.initialised_at,
        started_at=test_procedure.started_at,
        client_certificate_type=test_procedure.client_certificate_type,
        client_aggregator_id=test_procedure.client_aggregator_id,
        client_lfdi=test_procedure.client_lfdi,
        client_sfdi=test_procedure.client_sfdi,
        run_id=test_procedure.run_id,
        pen=test_procedure.pen,
        subscription_domain=test_procedure.subscription_domain,
        is_static_url=test_procedure.is_static_url,
        run_group_id=test_procedure.run_group_id,
        run_group_name=test_procedure.run_group_name,
        user_id=test_procedure.user_id,
        user_name=test_procedure.user_name,
        communications_disabled=test_procedure.communications_disabled,
    )


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

    # -----------------------------------------------------------------
    # Common lifecycle management hooks
    # -----------------------------------------------------------------

    async def set_test_context(self, context: RunnerBackendTestContext) -> None:
        """Backend hook to provide ability to determine context in which the test was started."""

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
    ) -> dtos.SiteDERSetting | None:
        """Return SiteDERSetting associated with a site.

        Cactus runner assumes only a single DER is created for a Site for any of the given tests.
        """
        ...

    async def get_der_capability(
        self,
        site_id: str,
    ) -> dtos.SiteDERRating | None:
        """Return the SiteDERRating associated with a site.

        Cactus runner assumes only a single DER is created for a Site for any of the given tests.
        """
        ...

    async def get_der_status(
        self,
        site_id: str,
    ) -> dtos.SiteDERStatus | None:
        """Return the most relevant DERStatus associated with a site.

        Cactus runner assumes only a single DER is created for a Site for any of the given tests.
        """
        ...

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------

    async def get_site_reading_types(
        self,
        site_ids: Sequence[str],
    ) -> Sequence[dtos.SiteReadingType]:
        """
        Return reading types matching the supplied criteria.

        Returns:
            expected site reading types
        """
        ...

    async def get_site_readings(
        self,
        site_reading_type_ids: Sequence[str],
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> Sequence[dtos.SiteReading]:
        """
        Return readings belonging to a SiteReadingType.

        The method passes in arguments that correspond to filtering based on limited set of SiteReadingType ids,
        The window of readings in a time are also specified. The returned readings should be in a window where
        the SiteReading.time_period_start + SiteReading.time_period_duration fall in so the overlap of durations within
        the defined window are considered for filtering purposes i.e. if there is an overlap the reading should be
        discarded.

        Args:
            - site_reading_type_ids: all site reading types that the readings should be filtered on.
            - start_time: optional start of the window within which readings time_period_start should be included.
            - end_time: optional end of the window within which readings time_period_start + time_period_duration
                should be included.

        Returns:
            site readings requested
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
        Return all controls relevant to the test.

        This has to include all controls that are active, scheduled, completed, deleted or archived during the test.
        If unsure it is best to return all controls objects and rely on the test's filtering to complete the checks.
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

    async def get_site_control_responses(
        self,
    ) -> Sequence[dtos.SiteControlResponse]:
        """
        Return all DERControl responses.
        """
        ...

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    async def get_subscription(self, subscription_id: str) -> dtos.Subscription | None:
        """Return the subscription for the given id."""
        ...

    async def get_subscriptions(self, aggregator_client_id: str | None = None) -> Sequence[dtos.Subscription]:
        """
        Returns all relevant subscriptions for the given aggregator client if provided.
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

    # ---------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------

    async def parse_subscription_href(self, href: str) -> dtos.SubscriptionHref:
        """Takes a subscription href provided by the test step and maps the necessary components.

        It is intentionally not a static method, as it may be necessary for a backend to access internal
        resources to fulfill the resulting DTO contract. Any mapping errors, this method should
        raise an envoy InvalidMappingError to allow calling functions to handle appropriately.

        Args:
            href: supplied by the calling function, expected to be provided by the test definition.

        Returns:
            resulting components DTO

        Raises:
            InvalidMappingError (envoy) when the supplied href is unable to be mapped to a subscription
        """
        ...
